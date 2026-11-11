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

MAX_GEN_ATTEMPTS = 3  # 1 tentativo + 2 rigenerazioni (titolo, META, troppo corto, box)


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


MEZZO_HEADER = "# MEZZO DI TRASPORTO (vincolo strutturale, non un dettaglio)"


def build_mezzo_block(brief: Brief) -> str:
    """Blocco sul mezzo di trasporto, da mettere nel contesto in cache.

    Il mezzo filtra ogni raccomandazione (bagaglio, spostamenti, parcheggi,
    traghetti, tipo di strada): va nel prefisso cached accanto al calendario, non
    lasciato implicito nel brief.
    """
    righe = [MEZZO_HEADER, f"Mezzo: {brief.mezzo}"]
    if brief.note_mezzo:
        righe.append(f"Note: {brief.note_mezzo}")
    return "\n".join(righe)


def build_system_blocks(brief: Brief, system_file: str = "chapter_system.md") -> list[dict]:
    """Costruisce il system a due blocchi, entrambi marcati per il caching.

    Blocco 1: style_guide.md + separatore + system_file (con {{LINGUA}} risolto).
    Blocco 2: il brief serializzato in JSON deterministico + calendario del
    viaggio precalcolato in Python (i giorni della settimana non vanno mai
    lasciati calcolare al modello) + il blocco MEZZO (vincolo strutturale).
    """
    style_guide = (PROMPTS_DIR / "style_guide.md").read_text(encoding="utf-8")
    system_prompt = (PROMPTS_DIR / system_file).read_text(encoding="utf-8")
    system_prompt = system_prompt.replace("{{LINGUA}}", brief.lingua_guida)

    return [
        {
            "type": "text",
            "text": style_guide + "\n\n---\n\n" + system_prompt,
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
        {
            "type": "text",
            "text": (
                "# BRIEF DEL VIAGGIO\n\n"
                + stable_json(brief.model_dump(mode="json"))
                + "\n\n"
                + build_calendar_block(brief)
                + "\n\n"
                + build_mezzo_block(brief)
            ),
            "cache_control": {"type": "ephemeral", "ttl": "1h"},
        },
    ]


def chapter_paths(brief: Brief, assignment: ChapterAssignment) -> tuple[Path, Path]:
    out_dir = config.output_root() / brief.brief_id
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


SEZIONE_NUDA_RE = re.compile(r"^##\s*[IVXLC]+[.)]?\s*$", re.MULTILINE)


def sezioni_senza_titolo(testo: str) -> list[str]:
    """I titoli di sezione ridotti al solo numero romano («## II.»).

    Non è un vezzo tipografico: quei titoli finiscono nell'indice del libro
    stampato e nella barra di navigazione del lettore online. Un indice fatto di
    «I. II. III.» non fa ritrovare niente a nessuno. Non è riparabile in codice
    — un titolo non si inventa — quindi si segnala e basta, senza far
    rigenerare il capitolo: costerebbe un dollaro e mezzo per un difetto di
    forma, che è esattamente lo scambio che abbiamo deciso di non fare.
    """
    corpo = testo.split("<!--META", 1)[0]
    return [m.strip() for m in SEZIONE_NUDA_RE.findall(corpo)]


# Il vocabolario dell'officina. Se compare in una riga del capitolo, quella riga
# non è prosa per il lettore: è il modello che parla a noi.
_GERGO_INTERNO = (
    "nota di lavoro",
    "nota per il critico",
    "nota al revisore",
    "nota tecnica interna",
    "fuori dal file consegnato",
    "blocco meta",
    "claims_da_verificare",
    "verifica_incompleta",
    "passaggio critico",
    "style guide",
    "budget parole",
)


def rimuovi_note_di_lavoro(testo: str) -> str:
    """Toglie dal capitolo le note che il modello scrive a noi, non al lettore.

    Capita che il generatore chiuda con una «Nota di lavoro (fuori dal file
    consegnato)» in cui racconta cosa non è riuscito a verificare, citando la
    style guide e i campi del blocco META. Crede di parlare fuori dal file; il
    file è uno solo, e quella nota è finita stampata nel libro di un cliente.

    Il criterio è il vocabolario: un capitolo di viaggio non nomina mai la style
    guide né i campi del META. Trovata una riga così, si taglia da lì fino alla
    fine della prosa — queste note stanno sempre in coda, e quello che le segue
    è altra roba dello stesso genere. Il blocco META, che è nostro e serve, resta.
    """
    corpo, sep, coda = testo.partition("<!--META")
    righe = corpo.split("\n")
    for i, riga in enumerate(righe):
        pulita = riga.strip().strip("*_# ").lower()
        if not pulita:
            continue
        if any(marca in pulita for marca in _GERGO_INTERNO):
            tagliato = "\n".join(righe[:i]).rstrip() + "\n"
            return tagliato + (("\n" + sep + coda) if sep else "")
    # Nessuna nota: il testo torna identico, byte per byte. Ricomporlo comunque
    # aggiungerebbe un a-capo a ogni lettura.
    return testo


def clean_chapter(grezzo: str) -> tuple[str, bool]:
    """Pulizie deterministiche condivise tra generatore e correttore.

    In ordine: taglia il preambolo prima del titolo, rimuove i tag cite, toglie
    le note di lavoro rivolte a noi, tronca il postscript dopo `META-->`.
    Ritorna (testo, titolo_ok): se non c'è alcuna riga di titolo '# ', titolo_ok
    è False e il grezzo è restituito immutato, perché senza titolo non ha senso
    applicare le altre pulizie.
    """
    testo, titolo_ok = strip_preamble(grezzo)
    if not titolo_ok:
        return grezzo, False
    testo = strip_cite_tags(testo)
    testo = rimuovi_note_di_lavoro(testo)
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


IMMOBILI_MARK = "GLI IMMOBILI"


def tetto_ricerche(assignment: ChapterAssignment) -> int:
    """Tetto di ricerche per tipo di capitolo.

    Pieno (`MAX_SEARCHES_PER_CHAPTER`) per le tappe, che raccomandano 5-6 nomi
    propri da verificare; ridotto (`MAX_SEARCHES_NON_TAPPA`) per tutti gli altri
    tipi (introduzione, contesto, collegamento, congedo, apparati), che non hanno
    quel requisito e non devono bruciare ricerche.
    """
    return (
        config.MAX_SEARCHES_PER_CHAPTER
        if assignment.tipo == "tappa"
        else config.MAX_SEARCHES_NON_TAPPA
    )


def immobili_conforme(assignment: ChapterAssignment, testo: str) -> bool:
    """Regola del box GLI IMMOBILI: presente se e solo se il capitolo è una tappa.

    Vietato in introduzione, contesto, collegamento, congedo, apparati. Controlla
    la sola prosa del capitolo, escludendo il blocco META (dove il titolo del box
    potrebbe comparire come voce `assets`).
    """
    corpo = testo.split("<!--META", 1)[0]
    presente = IMMOBILI_MARK in corpo
    return presente if assignment.tipo == "tappa" else not presente


def controlli_struttura(assignment: ChapterAssignment, testo: str) -> dict:
    """Controlli deterministici condivisi tra generatore e correttore.

    Presuppone un testo che inizia col titolo '# '. Ritorna un dict con parole,
    META, banda ammessa e gli esiti dei singoli controlli (lunghezza, META,
    box GLI IMMOBILI). Serve sia al loop di generazione sia alla validazione
    dell'output del fixer prima di promuoverlo.
    """
    budget = assignment.budget_parole
    lo, hi = round(budget * 0.85), round(budget * 1.15)
    parole = chapter_word_count(testo)
    meta = parse_meta(testo)
    return {
        "parole": parole,
        "meta": meta,
        "banda": (lo, hi),
        "lunghezza_ok": lo <= parole <= hi,
        "meta_ok": meta is not None,
        "immobili_ok": immobili_conforme(assignment, testo),
    }


def rimuovi_box_immobili(testo: str) -> str | None:
    """Toglie il box GLI IMMOBILI dalla prosa. Ritorna None se non è sicuro farlo.

    Il box è un elemento chiuso e riconoscibile: una riga che porta la dicitura,
    e sotto l'elenco dei vincoli, fino al titolo successivo o alla fine della
    prosa. Quando finisce in un capitolo che non è una tappa è un errore di
    forma, non di sostanza: toglierlo è un'operazione meccanica, e costa zero
    contro la rigenerazione di un intero capitolo su Opus.

    Prudenza: se il taglio porterebbe via più di un quarto del capitolo, la
    dicitura non stava delimitando un box e si rinuncia (None) — meglio
    rigenerare che consegnare un capitolo mutilato in silenzio.
    """
    corpo, sep, coda = testo.partition("<!--META")
    righe = corpo.split("\n")
    inizio = next((i for i, r in enumerate(righe) if IMMOBILI_MARK in r), None)
    if inizio is None:
        return None

    # Il box finisce al titolo successivo (di qualunque livello); se non ce n'è,
    # arriva in fondo alla prosa.
    fine = len(righe)
    for i in range(inizio + 1, len(righe)):
        if re.match(r"^#{1,6} ", righe[i]):
            fine = i
            break

    rimaste = righe[:inizio] + righe[fine:]
    nuovo_corpo = "\n".join(rimaste).rstrip() + "\n"
    tolte = len(" ".join(righe[inizio:fine]).split())
    if tolte > 0.25 * len(corpo.split()):
        return None
    return nuovo_corpo + ("\n" + sep + coda if sep else "")


def nota_revisione_immobili(assignment: ChapterAssignment) -> str:
    """Istruzione di rigenerazione quando il box GLI IMMOBILI non è conforme."""
    if assignment.tipo == "tappa":
        return (
            "BOX GLI IMMOBILI MANCANTE: questo è un capitolo di tappa e DEVE "
            "contenere il box «GLI IMMOBILI» (i vincoli che le date del cliente "
            "decidono, presi dal calendario). Inseriscilo."
        )
    return (
        f"BOX GLI IMMOBILI VIETATO: questo capitolo è di tipo '{assignment.tipo}', "
        "non una tappa, quindi NON deve contenere il box «GLI IMMOBILI». Rimuovilo "
        "e, se servono vincoli di calendario, integrali nella prosa."
    )


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
    effort: str | None = None,
):
    """Un singolo tentativo di generazione, gestendo i pause_turn della ricerca.

    Ritorna (response, usage_log): la risposta finale e la lista degli usage di
    tutte le chiamate (una sola, o più se il turno è stato messo in pausa).
    `model`, `max_tokens` ed `effort` sono parametrizzati perché lo stesso ciclo
    serve sia il generatore (Opus, sforzo alto) sia il critico (Sonnet, sforzo
    basso: verificare che un luogo esista non è ragionamento profondo).
    """
    model = model or config.MODEL_GENERATION
    max_tokens = max_tokens or config.MAX_TOKENS_CHAPTER
    effort = effort or config.effort_generazione()
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
            thinking=config.THINKING_ADATTIVO,
            output_config={"effort": effort},
        )
        usage_log.append(response.usage.model_dump())
        if response.stop_reason == "refusal":
            # I classificatori di sicurezza hanno declinato. Su una guida di
            # viaggio non dovrebbe succedere: se succede va detto a chiare
            # lettere, perché il contenuto torna vuoto e senza questa riga
            # sembrerebbe un capitolo mal scritto invece di una richiesta
            # rifiutata.
            dettagli = getattr(response, "stop_details", None)
            print(
                f"Chiamata declinata dal modello {model} "
                f"(categoria: {getattr(dettagli, 'category', '?')}).",
                file=sys.stderr,
            )
            return response, usage_log
        if response.stop_reason == "pause_turn":
            # La ricerca web server-side ha messo in pausa il turno: si accoda
            # la risposta e si richiama per farlo riprendere.
            #
            # ACCODARE, non ricostruire. Prima qui si riscriveva
            # `[user_message, ultima_risposta]`: alla SECONDA pausa il turno
            # della prima spariva, e con lui le ricerche già fatte — il modello
            # le rifaceva, pagandole due volte e scrivendo con meno contesto di
            # quanto credesse di avere. Sui capitoli lunghi, quelli con più
            # ricerche, era anche il caso peggiore. In più i modelli recenti
            # legano i blocchi di ragionamento alla conversazione che li ha
            # prodotti: una storia riscritta invece che accresciuta è
            # esattamente ciò che il controllo sulle modifiche rifiuta.
            messages = messages + [
                {"role": "assistant", "content": response.content}
            ]
            continue
        return response, usage_log


def run_verification_call(
    client, system, tools, user_content, model, max_tokens, effort=None
):
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
        client, system, tools, user_content, model=model, max_tokens=max_tokens,
        effort=effort or config.effort_critico(),
    )
    retried = False
    if response.stop_reason == "max_tokens":
        retried = True
        response, usage_log_2 = run_one_generation(
            client, system, tools, user_content, model=model,
            max_tokens=max_tokens * 2, effort=effort or config.effort_critico(),
        )
        usage_log = usage_log + usage_log_2
    info = {
        "retried": retried,
        "truncated": response.stop_reason == "max_tokens",
        "stop_reason": response.stop_reason,
    }
    return response, usage_log, info


def _chiedi_meta(client, istruzioni: str, chapter_text: str, usi: list[dict]) -> dict | None:
    """Una chiamata a Haiku per il META completo. None se non è leggibile."""
    try:
        response = client.messages.create(
            model=config.MODEL_META_SALVAGE,
            # Nessun output_config qui: Haiku 4.5 non accetta 'effort' e la
            # chiamata verrebbe rifiutata. Il compito è estrattivo, non serve.
            max_tokens=config.MAX_TOKENS_META_SALVAGE,
            messages=[
                {
                    "role": "user",
                    "content": istruzioni + "\n\n---\n\nTESTO DEL CAPITOLO:\n\n" + chapter_text,
                }
            ],
        )
    except Exception as exc:  # rete/API: si passa alla scialuppa più piccola
        print(f"Recupero META: chiamata fallita ({exc}).", file=sys.stderr)
        return None
    usi.append(response.usage.model_dump())
    grezzo = "".join(b.text for b in response.content if b.type == "text")
    meta = parse_meta(grezzo)
    if meta is None:
        # Il modello potrebbe aver reso il solo JSON, senza i delimitatori.
        try:
            obj = json.loads(grezzo.strip())
            meta = obj if isinstance(obj, dict) else None
        except json.JSONDecodeError:
            meta = None
    if not isinstance(meta, dict) or not str(meta.get("riassunto") or "").strip():
        # Finora questo ramo era muto: il recupero falliva e non si sapeva
        # perché. Il motivo tipico è una risposta troncata — un capitolo pieno
        # di nomi produce un elenco lungo, e il JSON si interrompe a metà.
        print(
            f"Recupero META: risposta non leggibile (stop {response.stop_reason}, "
            f"{len(grezzo)} caratteri).",
            file=sys.stderr,
        )
        return None
    return meta


def _chiedi_riassunto(client, chapter_text: str, usi: list[dict]) -> str | None:
    """La scialuppa più piccola: solo il riassunto, in prosa, senza JSON.

    È l'unica cosa del META che serve davvero alla catena — il capitolo dopo
    eredita il riassunto, non gli asset. Tre frasi in prosa non si troncano e non
    si sbagliano di formato: se anche questa fallisce, il problema non è il META.
    """
    try:
        response = client.messages.create(
            model=config.MODEL_META_SALVAGE,
            max_tokens=600,
            messages=[
                {
                    "role": "user",
                    "content": (
                        "Riassumi in due o tre frasi, al massimo 120 parole, questo "
                        "capitolo di una guida di viaggio: di cosa parla e cosa "
                        "consiglia. Solo il riassunto, senza titolo, preamboli o "
                        "formattazione.\n\n---\n\n" + chapter_text
                    ),
                }
            ],
        )
    except Exception as exc:
        print(f"Recupero riassunto: chiamata fallita ({exc}).", file=sys.stderr)
        return None
    usi.append(response.usage.model_dump())
    testo = "".join(b.text for b in response.content if b.type == "text").strip()
    return testo if len(testo) >= 40 else None


def salvage_meta(
    brief: Brief, assignment: ChapterAssignment, chapter_text: str
) -> dict | None:
    """Ricostruisce il blocco META di un capitolo a cui manca.

    Il META è l'unica cosa che può fermare il libro: senza il riassunto, il
    capitolo dopo non eredita nulla e la catena si spezza. Quindi non si butta un
    capitolo scritto perché gli manca la coda: la si ricostruisce con Haiku,
    SENZA ricerca e SENZA riscrivere niente, in due tentativi di ampiezza
    decrescente. Prima il META completo; se non torna leggibile, il solo
    riassunto con le liste vuote — che è tutto quello che serve alla catena.

    Ritorna il META (con `meta_ricostruito: true`) oppure None. L'usage di tutte
    le chiamate fatte va in cap_NN.meta.usage.json, anche quando falliscono: si
    pagano lo stesso.
    """
    client = make_client()
    istruzioni = (
        "Ricevi il TESTO di un capitolo di una guida di viaggio a cui manca il "
        "blocco META finale. Il tuo compito NON è riscrivere il capitolo: è "
        "produrre soltanto il blocco META che lo chiude, ricavandolo dal testo.\n\n"
        "Restituisci ESCLUSIVAMENTE un blocco delimitato da `<!--META` e `META-->`, "
        "senza nulla prima o dopo, contenente JSON valido con i campi:\n"
        "- `riassunto`: 2-3 frasi che riassumono il capitolo, massimo 120 parole\n"
        "- `claims_da_verificare`: al massimo 10 affermazioni che, se sbagliate, "
        "rovinerebbero il viaggio — dove si trova un luogo, se un collegamento "
        "esiste, fatti storici, chiusure e stagioni legate alle date. Niente "
        "prezzi né orari.\n"
        "- `assets`: al massimo 12 oggetti {tipo, titolo, sezione, deperibilita} "
        "ricavati dalle sezioni e dai box del capitolo\n"
        "- `verifica_incompleta`: false\n"
        "- `fatti_verificati`: [] (lista vuota: il blocco è ricostruito a posteriori "
        "dal testo, non da una verifica con ricerca)\n\n"
        "Non inventare dati non presenti nel testo. Nessuna ricerca. Liste brevi: "
        "meglio un blocco corto e completo che uno lungo e interrotto."
    )
    usi: list[dict] = []
    meta = _chiedi_meta(client, istruzioni, chapter_text, usi)
    if meta is None:
        riassunto = _chiedi_riassunto(client, chapter_text, usi)
        if riassunto is not None:
            meta = {"riassunto": riassunto, "claims_da_verificare": [], "assets": []}
            print(
                f"Capitolo {assignment.numero:02d}: META completo non recuperabile, "
                "ricostruito il solo riassunto.",
                file=sys.stderr,
            )

    if usi:
        usage_path = chapter_paths(brief, assignment)[0].with_name(
            f"cap_{assignment.numero:02d}.meta.usage.json"
        )
        usage_path.write_text(
            json.dumps(
                {
                    "model": config.MODEL_META_SALVAGE,
                    "truncated": False,
                    "retried": len(usi) > 1,
                    "chiamate": usi,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )
    if meta is None:
        return None

    meta["meta_ricostruito"] = True
    meta["verifica_incompleta"] = False
    meta.setdefault("fatti_verificati", [])
    return meta


def _con_meta(testo: str, meta: dict) -> str:
    """Accoda un blocco META ricostruito alla prosa."""
    return (
        testo.rstrip()
        + "\n\n<!--META\n"
        + json.dumps(meta, ensure_ascii=False, indent=2)
        + "\nMETA-->"
    )


def _recupera_bozza(
    brief: Brief, assignment: ChapterAssignment, cap_path: Path, usage_path: Path, lo: int
) -> tuple[str, list[dict]] | None:
    """Riprende il capitolo già scritto da un run precedente, invece di riscriverlo.

    Un libro si ferma su un capitolo — per il riassunto mancante, per il tetto di
    spesa — e il testo resta su disco. Alla ripresa lo si rigenerava da zero:
    un'altra scrittura su Opus con tutte le sue ricerche, per un capitolo che
    c'era già. Qui lo si rilegge e lo si sottopone agli STESSI controlli di un
    capitolo appena scritto: titolo, lunghezza minima, box GLI IMMOBILI, META
    (ricostruito con Haiku se manca). Se passa, si va al critico come sempre;
    se no, si riscrive. Non si salta nessun controllo: si salta solo la spesa.

    Ritorna (testo, usage della scrittura originale) oppure None.
    """
    if not cap_path.exists():
        return None
    try:
        grezzo = cap_path.read_text(encoding="utf-8")
    except OSError:
        return None
    testo, titolo_ok = clean_chapter(grezzo)
    if not titolo_ok:
        return None
    c = controlli_struttura(assignment, testo)
    if not c["immobili_ok"] and assignment.tipo != "tappa":
        ripulito = rimuovi_box_immobili(testo)
        if ripulito is not None:
            testo, c = ripulito, controlli_struttura(assignment, ripulito)
    if c["parole"] < lo or not c["immobili_ok"]:
        return None
    if not c["meta_ok"]:
        meta_rec = salvage_meta(brief, assignment, testo)
        if meta_rec is None:
            return None
        testo = _con_meta(testo, meta_rec)
        if not controlli_struttura(assignment, testo)["meta_ok"]:
            return None

    usage_log: list[dict] = []
    try:
        vecchio = json.loads(usage_path.read_text(encoding="utf-8"))
        usage_log = list(vecchio.get("chiamate") or [])
    except (OSError, ValueError):
        pass
    print(
        f"Capitolo {assignment.numero:02d}: ripreso dalla bozza già scritta "
        f"({c['parole']} parole) invece di riscriverlo.",
        file=sys.stderr,
    )
    return testo, usage_log


def generate_chapter(
    brief: Brief, assignment: ChapterAssignment
) -> tuple[Path, list[str], dict]:
    """Genera il capitolo, lo ripulisce in modo deterministico e lo salva.

    Ritorna (percorso_capitolo, warnings, gen_info). NON è idempotente rispetto
    al file su disco: rigenera sempre, sovrascrivendo l'eventuale capitolo già
    presente. La decisione di saltare un capitolo (perché già approvato) spetta a
    monte all'orchestratore, che si fida di stato.json — non dell'esistenza del
    file — così un capitolo fallito non viene mai riusato saltando i controlli di
    generazione. La pulizia (preambolo, tag cite) e i controlli di validità con
    rigenerazione (lunghezza, META, box GLI IMMOBILI) avvengono qui, non nel
    prompt. `gen_info` espone i fatti che servono al gate a valle (ricerche
    totali, tetto applicato, se sono esaurite, se il modello ha dichiarato
    `verifica_incompleta`). Se qualcosa non torna dopo i tentativi il file viene
    salvato comunque ma si scrive cap_NN.WARNING.txt e si popola la lista warnings.
    """
    cap_path, usage_path = chapter_paths(brief, assignment)
    cap_path.parent.mkdir(parents=True, exist_ok=True)

    client = make_client()
    system = build_system_blocks(brief)
    tetto = tetto_ricerche(assignment)
    tools = [
        {
            "type": config.WEB_SEARCH_TOOL_TYPE,
            "name": "web_search",
            "max_uses": tetto,
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
    # TUTTE le risposte di TUTTI i tentativi. Prima si teneva solo l'ultimo:
    # un capitolo riscritto tre volte risultava costare come una, e il tetto di
    # spesa contava un terzo di quello che la fattura poi addebitava.
    usage_totale: list[dict] = []
    testo = ""
    titolo_ok = False

    # `valido` = l'ultima versione salvata ha superato TUTTI i controlli
    # strutturali (titolo, META, banda, box). È un'informazione per i rapporti:
    # a fermare il libro è solo `consegnabile`, cioè il META.
    valido = False

    # Prima di spendere: se un run precedente si è fermato su questo capitolo, il
    # testo è già su disco. Lo si riprende, se regge gli stessi controlli.
    recuperato = _recupera_bozza(brief, assignment, cap_path, usage_path, lo)
    if recuperato is not None:
        testo, usage_log = recuperato
        titolo_ok = True
        c = controlli_struttura(assignment, testo)
        valido = c["lunghezza_ok"] and c["meta_ok"] and c["immobili_ok"]
    tentativi = range(1, MAX_GEN_ATTEMPTS + 1) if recuperato is None else range(0)

    for tentativo in tentativi:
        user_content = base_user + extra_note
        response, usage_log = run_one_generation(client, system, tools, user_content)
        usage_totale.extend(usage_log)
        troncato = response.stop_reason == "max_tokens"
        grezzo = "".join(block.text for block in response.content if block.type == "text")

        testo, titolo_ok = clean_chapter(grezzo)
        note_parti: list[str] = []

        if not titolo_ok:
            # Output degenere senza titolo: NON è più un'uscita immediata dal ciclo,
            # rientra nei tentativi come ogni altro output non valido.
            testo = grezzo
            note_parti.append(
                "OUTPUT SENZA TITOLO: il tentativo precedente non iniziava con una riga "
                "di titolo '# '. Il file è un capitolo di libro: deve iniziare col titolo "
                "del luogo (riga che inizia con '# '), senza preamboli, commenti o "
                "resoconti del lavoro svolto."
            )
        else:
            c = controlli_struttura(assignment, testo)
            # Riparazione meccanica prima di ogni giudizio: un box GLI IMMOBILI
            # finito in un capitolo che non è una tappa si toglie, non si fa
            # riscrivere. Spesso sistema anche la lunghezza (il box pesa), e
            # rigenerare un capitolo su Opus costa quanto tutto il resto della
            # pipeline messo insieme.
            if not c["immobili_ok"] and assignment.tipo != "tappa":
                ripulito = rimuovi_box_immobili(testo)
                if ripulito is not None:
                    dopo = controlli_struttura(assignment, ripulito)
                    if dopo["immobili_ok"]:
                        testo, c = ripulito, dopo
                        print(
                            f"Capitolo {assignment.numero:02d}: box GLI IMMOBILI "
                            f"rimosso (tipo '{assignment.tipo}', non ammesso).",
                            file=sys.stderr,
                        )
            parole = c["parole"]
            # Si accetta quando c'è il META, il box è giusto e il capitolo NON è
            # troppo corto. Troppo lungo si consegna: novanta parole oltre la
            # banda non valgono un'altra scrittura su Opus con tutte le ricerche
            # — e il testo scritto in più è testo che il lettore riceve. Troppo
            # corto invece si riscrive: un capitolo smilzo è un difetto vero.
            corto = parole < lo
            if not corto and c["meta_ok"] and c["immobili_ok"]:
                valido = c["lunghezza_ok"]
                break
            # Recupero del META, tentato ogni volta che manca — non più solo
            # quando è l'unico problema. Il META è l'unica cosa che può fermare
            # il libro (senza, il capitolo dopo non eredita il riassunto e la
            # catena si spezza), quindi la scialuppa va calata sempre: costa
            # una chiamata a Haiku contro una rigenerazione su Opus.
            if not c["meta_ok"]:
                meta_rec = salvage_meta(brief, assignment, testo)
                if meta_rec is not None:
                    testo = _con_meta(testo, meta_rec)
                    c = controlli_struttura(assignment, testo)
                    if c["meta_ok"]:
                        print(
                            f"Capitolo {assignment.numero:02d}: META ricostruito via "
                            f"{config.MODEL_META_SALVAGE}.",
                            file=sys.stderr,
                        )
                    if not corto and c["immobili_ok"] and c["meta_ok"]:
                        valido = c["lunghezza_ok"]
                        break
            # La nota sulla lunghezza solo se serve davvero: troppo corto, o
            # troppo lungo E senza META — perché lì la lunghezza è spesso la
            # causa: il capitolo ha esaurito lo spazio prima di arrivare al META.
            if corto or (parole > hi and not c["meta_ok"]):
                verso = "più lungo" if corto else "più corto"
                note_parti.append(
                    f"REVISIONE LUNGHEZZA: il tentativo precedente era di {parole} parole, "
                    f"fuori dalla banda ammessa ({lo}-{hi}) per il budget di {budget}. "
                    f"Riscrivi il capitolo {verso}, puntando a circa {budget} parole, "
                    f"senza sacrificare i nomi concreti."
                )
            if not c["meta_ok"]:
                note_parti.append(
                    (
                        "RISPOSTA TRONCATA: il tentativo precedente ha esaurito lo spazio "
                        "prima di arrivare al blocco META. Scrivi più asciutto e chiudi "
                        "sempre col META. "
                        if troncato
                        else ""
                    )
                    + "BLOCCO META MANCANTE O NON PARSABILE: il tentativo precedente non "
                    "conteneva un blocco META valido. Devi EMETTERE il blocco nel formato "
                    "richiesto — delimitato da `<!--META` e `META-->`, con dentro JSON valido "
                    "(riassunto, fatti_verificati, claims_da_verificare, assets) — non "
                    "descriverlo a parole né riassumerlo in un postscript. È l'ultimo blocco "
                    "del file: dopo `META-->` non deve seguire nient'altro."
                )
            if not c["immobili_ok"]:
                note_parti.append(nota_revisione_immobili(assignment))

        if tentativo < MAX_GEN_ATTEMPTS:
            extra_note = "\n\n" + "\n\n".join(note_parti)
        else:
            # Ultimo tentativo ancora non valido: registra i warning definitivi.
            if not titolo_ok:
                warnings.append(
                    f"Nessuna riga di titolo '# ' nell'output dopo {MAX_GEN_ATTEMPTS} "
                    "tentativi: il file non è un capitolo valido."
                )
            else:
                if parole < lo:
                    warnings.append(
                        f"Troppo corto dopo {MAX_GEN_ATTEMPTS} tentativi: "
                        f"{parole} parole (banda ammessa {lo}-{hi} per budget {budget})."
                    )
                if not c["meta_ok"]:
                    warnings.append(
                        f"Blocco META assente o non parsabile dopo {MAX_GEN_ATTEMPTS} "
                        "tentativi"
                        + (", con la risposta troncata per esaurimento dello spazio" if troncato else "")
                        + ": il capitolo è salvato come grezzo ma non è valido "
                        "(controllo ricerche inerte, nessun asset estratto)."
                    )
                if not c["immobili_ok"]:
                    if assignment.tipo == "tappa":
                        warnings.append(
                            f"Box GLI IMMOBILI assente dopo {MAX_GEN_ATTEMPTS} tentativi in "
                            "un capitolo di tappa: manca l'elemento strutturale obbligatorio."
                        )
                    else:
                        warnings.append(
                            f"Box GLI IMMOBILI presente dopo {MAX_GEN_ATTEMPTS} tentativi in "
                            f"un capitolo di tipo '{assignment.tipo}' (non tappa): è vietato."
                        )

    # Più lungo della banda: si consegna, e lo si dice in da_rivedere.md — è un
    # dato da conoscere, non un difetto che valga una riscrittura.
    if titolo_ok:
        finale = controlli_struttura(assignment, testo)
        if finale["parole"] > hi:
            warnings.append(
                f"Più lungo della banda: {finale['parole']} parole (banda {lo}-{hi} per "
                f"budget {budget}). Consegnato così, non riscritto."
            )

    # Segnali sull'ultimo tentativo salvato. NOTA: la presenza di voci in
    # `claims_da_verificare` NON è un difetto — per progetto è la lista che lo
    # scrittore passa al critico, quindi è normale che sia piena. L'unico segnale
    # di generazione che vale è `verifica_incompleta: true`, dichiarato dal
    # modello quando esaurisce le ricerche prima di verificare i nomi che
    # intendeva citare. La condizione "ricerche esaurite + claim non verificati"
    # è valutata a valle dal gate, che ha in mano anche l'esito del critico.
    nude = sezioni_senza_titolo(testo)
    if nude:
        warnings.append(
            f"{len(nude)} sezione/i senza titolo (solo il numero romano): "
            "nell'indice del libro compaiono come voci vuote."
        )

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
        "tetto_ricerche": tetto,
        "ricerche_esaurite": ricerche >= tetto,
        "verifica_incompleta": verifica_incompleta,
        "valido": valido,
        # Consegnabile ≠ valido. Un capitolo fuori banda del 3%, o con un box
        # fuori posto, è un difetto da segnalare al lettore in da_rivedere.md —
        # non un motivo per fermare un libro già pagato. L'unica cosa che ferma
        # davvero è l'assenza del META: senza riassunto il capitolo successivo
        # non eredita nulla e la catena si spezza.
        "consegnabile": meta is not None,
    }

    cap_path.write_text(testo, encoding="utf-8")
    # Il file d'uso si riscrive solo se si è scritto davvero. Un capitolo ripreso
    # dalla bozza tiene l'usage della scrittura originale: quel costo è stato
    # pagato, e cancellarlo lo farebbe sparire dal conto.
    if response is not None:
        usage_path.write_text(
            json.dumps(
                {
                    "model": response.model,
                    "stop_reason": response.stop_reason,
                    "truncated": response.stop_reason == "max_tokens",
                    "retried": len(usage_totale) > len(usage_log),
                    "chiamate": usage_totale,
                },
                ensure_ascii=False,
                indent=2,
            ),
            encoding="utf-8",
        )

    meta_match = META_RE.search(testo)
    if meta_match:
        capture_assets(brief, assignment, meta_match.group(1).strip())

    warning_path = cap_path.with_name(f"cap_{assignment.numero:02d}.WARNING.txt")
    if not warnings and warning_path.exists():
        # Il rapporto di un run precedente non vale più: questo capitolo l'ha
        # superato. Lasciarlo direbbe «non consegnabile» di un capitolo che lo è.
        warning_path.unlink()
    if warnings:
        intestazione = (
            "CAPITOLO CONSEGNATO CON DIFETTI DI FORMA — da rivedere:"
            if meta is not None
            else "CAPITOLO NON CONSEGNABILE — manca il riassunto:"
        )
        warning_path.write_text(
            intestazione
            + "\n\n"
            + "\n".join(f"- {w}" for w in warnings)
            + "\n",
            encoding="utf-8",
        )
        print(
            f"ATTENZIONE: capitolo {assignment.numero:02d}, "
            f"{len(warnings)} problema/i — vedi {warning_path.name}:",
            file=sys.stderr,
        )
        for w in warnings:
            print(f"  - {w}", file=sys.stderr)

    return cap_path, warnings, gen_info
