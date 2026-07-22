"""Generazione di un singolo capitolo via API Anthropic.

Il prefisso in cache (system a due blocchi) deve restare identico byte per
byte tra i capitoli della stessa guida: per questo la serializzazione del
brief è deterministica (sort_keys) e i file di prompt vengono letti così
come sono, senza contenuti volatili (date, id di richiesta, ecc.).
"""

import json
import os
import re
from datetime import timedelta
from pathlib import Path

import anthropic

from schema.brief import Brief, ChapterAssignment
from src import config
from src.assets import capture_assets

ENGINE_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ENGINE_ROOT / "prompts"

META_RE = re.compile(r"<!--META(.*?)META-->", re.DOTALL)


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


def generate_chapter(brief: Brief, assignment: ChapterAssignment) -> Path:
    """Genera il capitolo e lo salva su disco. Idempotente: se il file esiste, non rigenera."""
    cap_path, usage_path = chapter_paths(brief, assignment)
    if cap_path.exists():
        return cap_path
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
    user_message = {
        "role": "user",
        "content": stable_json(assignment.model_dump(mode="json")),
    }

    messages = [user_message]
    usage_log = []
    while True:
        response = client.messages.create(
            model=config.MODEL_GENERATION,
            max_tokens=config.MAX_TOKENS_CHAPTER,
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
        break

    testo = "".join(block.text for block in response.content if block.type == "text")
    cap_path.write_text(testo, encoding="utf-8")

    usage_path.write_text(
        json.dumps(
            {
                "model": response.model,
                "stop_reason": response.stop_reason,
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

    return cap_path
