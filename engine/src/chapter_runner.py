"""Generazione di un singolo capitolo via API Anthropic.

Il prefisso in cache (system a due blocchi) deve restare identico byte per
byte tra i capitoli della stessa guida: per questo la serializzazione del
brief è deterministica (sort_keys) e i file di prompt vengono letti così
come sono, senza contenuti volatili (date, id di richiesta, ecc.).
"""

import json
import os
import re
import sys
from datetime import timedelta
from pathlib import Path

import anthropic

from schema.brief import Brief, ChapterAssignment
from src import config
from src.assets import capture_assets

ENGINE_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ENGINE_ROOT / "prompts"

META_RE = re.compile(r"<!--META(.*?)META-->", re.DOTALL)
CITE_RE = re.compile(r"</?cite[^>]*>")
TITLE_RE = re.compile(r"^# ", re.MULTILINE)

MAX_GEN_ATTEMPTS = 3  # 1 tentativo + 2 rigenerazioni per la lunghezza


def make_client() -> anthropic.Anthropic:
    """Costruisce il client con chiave e base_url espliciti.

    L'ambiente sandbox ignora ANTHROPIC_API_KEY e imposta un ANTHROPIC_BASE_URL
    che punta al proxy interno: per questo la chiave arriva da GUIDE_ENGINE_KEY
    e il base_url è fissato all'API pubblica, così il client non eredita nulla
    dalle variabili d'ambiente del sandbox.
    """
    api_key = os.environ.get("GUIDE_ENGINE_KEY")
    if not api_key:
        raise RuntimeError(
            "Variabile d'ambiente GUIDE_ENGINE_KEY mancante: impostala con la "
            "API key Anthropic da usare per il motore (non viene mai stampata)."
        )
    return anthropic.Anthropic(api_key=api_key, base_url="https://api.anthropic.com")


def stable_json(obj) -> str:
    """Serializzazione JSON deterministica (byte-stabile per il prompt caching)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


GIORNI_IT = ["lunedì", "martedì", "mercoledì", "giovedì", "venerdì", "sabato", "domenica"]

CALENDAR_HEADER = (
    "# CALENDARIO DEL VIAGGIO "
    "(precalcolato — unica fonte ammessa per i giorni della settimana)"
)


def build_calendar_block(brief: Brief) -> str:
    """Calendario del viaggio calcolato in Python, mai dal modello.

    Una riga per ogni data del viaggio con giorno della settimana in italiano
    e tappa corrispondente. Il risultato è una funzione pura del brief, quindi
    identico byte per byte tra tutte le chiamate della stessa guida (writer e
    critico compresi), come richiede il prompt caching.
    """
    if not (brief.date.inizio and brief.date.fine):
        return (
            CALENDAR_HEADER
            + "\nDate non fissate: non fare riferimento a giorni della settimana specifici."
        )

    totale = (brief.date.fine - brief.date.inizio).days + 1

    # Tappa per ogni giorno: la tappa i copre le sue notti; il giorno di
    # partenza finale resta sull'ultima tappa.
    luoghi_per_giorno: list[str] = []
    for tappa in brief.tappe:
        luoghi_per_giorno.extend([tappa.luogo] * tappa.notti)
    ultima = brief.tappe[-1].luogo if brief.tappe else ""

    righe = [CALENDAR_HEADER]
    for i in range(totale):
        giorno = brief.date.inizio + timedelta(days=i)
        luogo = luoghi_per_giorno[i] if i < len(luoghi_per_giorno) else ultima
        riga = f"{giorno.isoformat()} — {GIORNI_IT[giorno.weekday()]} — giorno {i + 1} di {totale}"
        if luogo:
            riga += f" — {luogo}"
        righe.append(riga)
    return "\n".join(righe)


def build_system_blocks(brief: Brief, system_file: str = "chapter_system.md") -> list[dict]:
    """Costruisce il system a due blocchi, entrambi marcati per il caching.

    Blocco 1: style_guide.md + separatore + system_file (con {{LINGUA}} risolto).
    Blocco 2: il brief serializzato in JSON deterministico + calendario del
    viaggio precalcolato in Python (i giorni della settimana non vanno mai
    lasciati calcolare al modello).
    """
    style_guide = (PROMPTS_DIR / "style_guide.md").read_text(encoding="utf-8")
    system_prompt = (PROMPTS_DIR / system_file).read_text(encoding="utf-8")
    system_prompt = system_prompt.replace("{{LINGUA}}", brief.lingua_guida)

    return [
        {
            "type": "text",
            "text": style_guide + "\n\n---\n\n" + system_prompt,
            "cache_control": {"type": "ephemeral"},
        },
        {
            "type": "text",
            "text": (
                "# BRIEF DEL VIAGGIO\n\n"
                + stable_json(brief.model_dump(mode="json"))
                + "\n\n"
                + build_calendar_block(brief)
            ),
            "cache_control": {"type": "ephemeral"},
        },
    ]


def chapter_paths(brief: Brief, assignment: ChapterAssignment) -> tuple[Path, Path]:
    out_dir = ENGINE_ROOT / "output" / brief.brief_id
    stem = f"cap_{assignment.numero:02d}"
    return out_dir / f"{stem}.md", out_dir / f"{stem}.usage.json"


def strip_preamble(testo: str) -> tuple[str, bool]:
    """Elimina tutto ciò che precede la prima riga che inizia con '# '.

    Ritorna (testo_ripulito, ok). Se nessuna riga inizia con '# ', ok è False
    e il testo è restituito immutato (caso d'errore da segnalare a monte).
    """
    m = TITLE_RE.search(testo)
    if not m:
        return testo, False
    return testo[m.start():], True


def strip_cite_tags(testo: str) -> str:
    """Rimuove i tag <cite ...> e </cite> conservando il testo che contengono."""
    return CITE_RE.sub("", testo)


def truncate_after_meta(testo: str) -> str:
    """Tronca il testo subito dopo la chiusura del blocco META.

    Tutto ciò che segue `META-->` (postscript, riepiloghi, note del modello) va
    eliminato: il file è un capitolo, non deve avere coda. Se il blocco META è
    assente il testo è restituito immutato — la sua obbligatorietà è gestita a
    monte nel loop dei tentativi.
    """
    m = META_RE.search(testo)
    if not m:
        return testo
    return testo[: m.end()]


def clean_chapter(grezzo: str) -> tuple[str, bool]:
    """Pulizie deterministiche condivise tra generatore e correttore.

    In ordine: taglia il preambolo prima del titolo, rimuove i tag cite, tronca
    il postscript dopo `META-->`. Ritorna (testo, titolo_ok): se non c'è alcuna
    riga di titolo '# ', titolo_ok è False e il grezzo è restituito immutato,
    perché senza titolo non ha senso applicare le altre pulizie.
    """
    testo, titolo_ok = strip_preamble(grezzo)
    if not titolo_ok:
        return grezzo, False
    testo = strip_cite_tags(testo)
    testo = truncate_after_meta(testo)
    return testo, True


def chapter_word_count(testo: str) -> int:
    """Conta le parole del capitolo, dal titolo escluso il blocco META.

    Il testo in ingresso è già ripulito dal preambolo (inizia con '# ').
    """
    body = testo.split("<!--META", 1)[0]
    return len(body.split())


def parse_meta(testo: str) -> dict | None:
    """Estrae e parsa il blocco META come dict; None se assente o non parsabile."""
    m = META_RE.search(testo)
    if not m:
        return None
    try:
        obj = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return None
    return obj if isinstance(obj, dict) else None


def total_web_searches(usage_log: list[dict]) -> int:
    """Somma le ricerche web di tutte le risposte di un tentativo (pause_turn incluse)."""
    tot = 0
    for u in usage_log:
        stu = u.get("server_tool_use") or {}
        tot += stu.get("web_search_requests") or 0
    return tot


def run_one_generation(
    client,
    system,
    tools,
    user_content: str,
    model: str | None = None,
    max_tokens: int | None = None,
):
    """Un singolo tentativo di generazione, gestendo i pause_turn della ricerca.

    Ritorna (response, usage_log): la risposta finale e la lista degli usage di
    tutte le chiamate (una sola, o più se il turno è stato messo in pausa).
    `model` e `max_tokens` sono parametrizzati perché lo stesso ciclo serve sia
    il generatore (Opus) sia il correttore (Sonnet), con modelli diversi.
    """
    model = model or config.MODEL_GENERATION
    max_tokens = max_tokens or config.MAX_TOKENS_CHAPTER
    user_message = {"role": "user", "content": user_content}
    messages = [user_message]
    usage_log = []
    while True:
        response = client.messages.create(
            model=model,
            max_tokens=max_tokens,
            system=system,
            tools=tools,
            messages=messages,
        )
        usage_log.append(response.usage.model_dump())
        if response.stop_reason == "pause_turn":
            # La ricerca web server-side ha messo in pausa il turno:
            # si rimanda la conversazione così com'è per farla riprendere.
            messages = [user_message, {"role": "assistant", "content": response.content}]
            continue
        return response, usage_log


def run_verification_call(client, system, tools, user_content, model, max_tokens):
    """Chiamata di verifica (critico/fixer) con un ritentativo su troncatura.

    Come run_one_generation gestisce i pause_turn della ricerca; in più, se il
    turno finale è troncato (stop_reason 'max_tokens' — tipico su Sonnet 5, dove
    il thinking adattivo può esaurire il budget di output prima della risposta)
    ritenta UNA sola volta con il tetto raddoppiato. Ritorna
    (response, usage_log, info): usage_log include tutte le risposte, ritentativo
    compreso, così il costo del ritentativo resta tracciato; info riporta se c'è
    stata troncatura e/o ritentativo.
    """
    response, usage_log = run_one_generation(
        client, system, tools, user_content, model=model, max_tokens=max_tokens
    )
    retried = False
    if response.stop_reason == "max_tokens":
        retried = True
        response, usage_log_2 = run_one_generation(
            client, system, tools, user_content, model=model, max_tokens=max_tokens * 2
        )
        usage_log = usage_log + usage_log_2
    info = {
        "retried": retried,
        "truncated": response.stop_reason == "max_tokens",
        "stop_reason": response.stop_reason,
    }
    return response, usage_log, info


def generate_chapter(
    brief: Brief, assignment: ChapterAssignment, force: bool = False
) -> tuple[Path, list[str], dict]:
    """Genera il capitolo, lo ripulisce in modo deterministico e lo salva.

    Ritorna (percorso_capitolo, warnings, gen_info). Idempotente: se il file
    esiste già e `force` è False, non rigenera. La pulizia (preambolo, tag cite)
    e il controllo di lunghezza con rigenerazione avvengono qui, non nel prompt.
    `gen_info` espone i fatti che servono al gate a valle (ricerche totali, se
    sono esaurite, se il modello ha dichiarato `verifica_incompleta`). Se qualcosa
    non torna (nessun titolo, lunghezza fuori banda dopo i tentativi, META
    mancante) il file viene salvato comunque ma si scrive cap_NN.WARNING.txt e si
    popola la lista warnings.
    """
    cap_path, usage_path = chapter_paths(brief, assignment)
    if cap_path.exists() and not force:
        return cap_path, [], {}
    cap_path.parent.mkdir(parents=True, exist_ok=True)

    client = make_client()
    system = build_system_blocks(brief)
    tools = [
        {
            "type": config.WEB_SEARCH_TOOL_TYPE,
            "name": "web_search",
            "max_uses": config.MAX_SEARCHES_PER_CHAPTER,
        }
    ]
    base_user = stable_json(assignment.model_dump(mode="json"))

    budget = assignment.budget_parole
    lo, hi = round(budget * 0.85), round(budget * 1.15)

    warnings: list[str] = []
    extra_note = ""
    # Valori dell'ultimo tentativo, tenuti per il salvataggio finale.
    response = None
    usage_log: list[dict] = []
    testo = ""

    for tentativo in range(1, MAX_GEN_ATTEMPTS + 1):
        user_content = base_user + extra_note
        response, usage_log = run_one_generation(client, system, tools, user_content)
        grezzo = "".join(block.text for block in response.content if block.type == "text")

        testo, titolo_ok = clean_chapter(grezzo)
        if not titolo_ok:
            # Nessuna riga di titolo: è un errore. Salvo il grezzo e segnalo.
            testo = grezzo
            warnings.append(
                "Nessuna riga di titolo '# ' trovata nell'output: salvato il testo "
                "grezzo così com'è, senza pulizia del preambolo."
            )
            break

        parole = chapter_word_count(testo)
        meta = parse_meta(testo)
        lunghezza_ok = lo <= parole <= hi
        meta_ok = meta is not None

        # Il capitolo è valido solo se rientra nella banda di lunghezza E porta
        # un blocco META parsabile: senza META il controllo sulle ricerche è
        # inerte e la libreria asset resta vuota, quindi è motivo di rigenerazione.
        if lunghezza_ok and meta_ok:
            break

        if tentativo < MAX_GEN_ATTEMPTS:
            note_parti = []
            if not lunghezza_ok:
                verso = "più lungo" if parole < lo else "più corto"
                note_parti.append(
                    f"REVISIONE LUNGHEZZA: il tentativo precedente era di {parole} parole, "
                    f"fuori dalla banda ammessa ({lo}-{hi}) per il budget di {budget}. "
                    f"Riscrivi il capitolo {verso}, puntando a circa {budget} parole, "
                    f"senza sacrificare i nomi concreti né il box GLI IMMOBILI."
                )
            if not meta_ok:
                note_parti.append(
                    "BLOCCO META MANCANTE O NON PARSABILE: il tentativo precedente non "
                    "conteneva un blocco META valido. Devi EMETTERE il blocco nel formato "
                    "richiesto — delimitato da `<!--META` e `META-->`, con dentro JSON valido "
                    "(riassunto, fatti_verificati, claims_da_verificare, assets) — non "
                    "descriverlo a parole né riassumerlo in un postscript. È l'ultimo blocco "
                    "del file: dopo `META-->` non deve seguire nient'altro."
                )
            extra_note = "\n\n" + "\n\n".join(note_parti)
        else:
            if not lunghezza_ok:
                warnings.append(
                    f"Lunghezza fuori banda dopo {MAX_GEN_ATTEMPTS} tentativi: "
                    f"{parole} parole (banda ammessa {lo}-{hi} per budget {budget})."
                )
            if not meta_ok:
                warnings.append(
                    f"Blocco META assente o non parsabile dopo {MAX_GEN_ATTEMPTS} tentativi: "
                    "il capitolo è salvato come grezzo ma non è valido (controllo ricerche "
                    "inerte, nessun asset estratto)."
                )

    # Segnali sull'ultimo tentativo salvato. NOTA: la presenza di voci in
    # `claims_da_verificare` NON è un difetto — per progetto è la lista che lo
    # scrittore passa al critico, quindi è normale che sia piena. L'unico segnale
    # di generazione che vale è `verifica_incompleta: true`, dichiarato dal
    # modello quando esaurisce le ricerche prima di verificare i nomi che
    # intendeva citare. La condizione "ricerche esaurite + claim non verificati"
    # è valutata a valle dal gate, che ha in mano anche l'esito del critico.
    meta = parse_meta(testo)
    verifica_incompleta = bool((meta or {}).get("verifica_incompleta"))
    ricerche = total_web_searches(usage_log)
    if verifica_incompleta:
        warnings.append(
            "Il modello ha dichiarato 'verifica_incompleta: true' nel blocco META: "
            "ha esaurito le ricerche prima di verificare tutti i nomi che intendeva citare."
        )

    gen_info = {
        "ricerche": ricerche,
        "ricerche_esaurite": ricerche >= config.MAX_SEARCHES_PER_CHAPTER,
        "verifica_incompleta": verifica_incompleta,
    }

    cap_path.write_text(testo, encoding="utf-8")
    usage_path.write_text(
        json.dumps(
            {
                "model": response.model,
                "stop_reason": response.stop_reason,
                "truncated": response.stop_reason == "max_tokens",
                "retried": False,
                "chiamate": usage_log,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    meta_match = META_RE.search(testo)
    if meta_match:
        capture_assets(brief, assignment, meta_match.group(1).strip())

    if warnings:
        warning_path = cap_path.with_name(f"cap_{assignment.numero:02d}.WARNING.txt")
        warning_path.write_text(
            "CAPITOLO NON VALIDO — problemi rilevati:\n\n"
            + "\n".join(f"- {w}" for w in warnings)
            + "\n",
            encoding="utf-8",
        )
        print(
            f"ATTENZIONE: capitolo {assignment.numero:02d} non valido, "
            f"{len(warnings)} problema/i — vedi {warning_path.name}:",
            file=sys.stderr,
        )
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)

    return cap_path, warnings, gen_info
