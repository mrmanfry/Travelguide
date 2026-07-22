"""Generazione di un singolo capitolo via API Anthropic.

Il prefisso in cache (system a due blocchi) deve restare identico byte per
byte tra i capitoli della stessa guida: per questo la serializzazione del
brief è deterministica (sort_keys) e i file di prompt vengono letti così
come sono, senza contenuti volatili (date, id di richiesta, ecc.).
"""

import json
import re
from pathlib import Path

import anthropic

from schema.brief import Brief, ChapterAssignment
from src import config
from src.assets import capture_assets

ENGINE_ROOT = Path(__file__).resolve().parents[1]
PROMPTS_DIR = ENGINE_ROOT / "prompts"

META_RE = re.compile(r"<!--META(.*?)META-->", re.DOTALL)


def stable_json(obj) -> str:
    """Serializzazione JSON deterministica (byte-stabile per il prompt caching)."""
    return json.dumps(obj, ensure_ascii=False, sort_keys=True, indent=2)


def build_system_blocks(brief: Brief, system_file: str = "chapter_system.md") -> list[dict]:
    """Costruisce il system a due blocchi, entrambi marcati per il caching.

    Blocco 1: style_guide.md + separatore + system_file (con {{LINGUA}} risolto).
    Blocco 2: il brief serializzato in JSON deterministico.
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
            "text": "# BRIEF DEL VIAGGIO\n\n" + stable_json(brief.model_dump(mode="json")),
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

    client = anthropic.Anthropic()  # legge ANTHROPIC_API_KEY dall'ambiente
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
