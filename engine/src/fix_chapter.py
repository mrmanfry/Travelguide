"""Loop di correzione mirata di un capitolo già criticato.

Chiude il ciclo rilevamento→correzione: prende il capitolo e la lista degli
alert del critico e restituisce una versione corretta, riverificando i fatti
con la web search invece di fidarsi ciecamente degli alert. Riusa il prefisso
system in cache (style guide + brief + calendario) del generatore, così il
correttore lavora con lo stesso contesto di chi ha scritto e chi ha criticato.
"""

import json
import sys
from pathlib import Path

from schema.brief import Brief, ChapterAssignment
from src import config
from src.assets import capture_assets
from src.chapter_runner import (
    META_RE,
    build_system_blocks,
    chapter_word_count,
    clean_chapter,
    make_client,
    parse_meta,
    run_verification_call,
)


def format_alerts(alerts: list[dict]) -> str:
    """Rende la lista degli alert del critico in un blocco leggibile per il correttore."""
    parti = []
    for i, a in enumerate(alerts, 1):
        if not isinstance(a, dict):
            continue
        parti.append(
            f"Alert {i} — gravità: {a.get('gravita', '?')}, tipo: {a.get('tipo', '?')}\n"
            f"  posizione: {a.get('posizione', '')}\n"
            f"  problema: {a.get('problema', '')}\n"
            f"  evidenza: {a.get('evidenza', '')}\n"
            f"  correzione proposta: {a.get('correzione_proposta', '')}"
        )
    return "\n\n".join(parti)


def build_fixer_user(chapter_text: str, alerts: list[dict]) -> str:
    """Messaggio user del correttore: capitolo integrale + alert da correggere."""
    return (
        "CAPITOLO DA CORREGGERE (integrale, nello stesso formato in cui va restituito):\n\n"
        f"{chapter_text}\n\n"
        "---\n\n"
        "ALERT EMESSI DALL'EDITOR DI VERIFICA (riverificali prima di correggere):\n\n"
        f"{format_alerts(alerts)}"
    )


def apply_corrections(
    brief: Brief,
    assignment: ChapterAssignment,
    chapter_path: Path,
    verdict: dict | None,
) -> tuple[Path, int, dict]:
    """Applica le correzioni del critico al capitolo e lo riscrive.

    Salva la versione pre-correzione in `cap_NN.v1.md`, poi sovrascrive
    `cap_NN.md` con la versione corretta, ripassata per le stesse pulizie
    deterministiche del generatore (preambolo, cite, troncamento post-META).
    Il correttore ha accesso alla web search (tetto `MAX_SEARCHES_CRITIC`) per
    riverificare i fatti, e al proprio tetto di token `MAX_TOKENS_FIXER` (più
    ampio di quello del critico, perché deve riemettere un capitolo intero più
    il META) con ritentativo automatico su troncatura. Ritorna
    (percorso, n_parole, info): info riporta troncatura/ritentativo del correttore.
    """
    numero = assignment.numero
    chapter_text = chapter_path.read_text(encoding="utf-8")

    # Versione intermedia prima della correzione, per poter confrontare.
    v1_path = chapter_path.with_name(f"cap_{numero:02d}.v1.md")
    v1_path.write_text(chapter_text, encoding="utf-8")

    alerts = [a for a in ((verdict or {}).get("alerts") or []) if isinstance(a, dict)]

    client = make_client()
    system = build_system_blocks(brief, system_file="fixer_system.md")
    tools = [
        {
            "type": config.WEB_SEARCH_TOOL_TYPE,
            "name": "web_search",
            "max_uses": config.MAX_SEARCHES_CRITIC,
        }
    ]

    user_content = build_fixer_user(chapter_text, alerts)
    response, usage_log, info = run_verification_call(
        client,
        system,
        tools,
        user_content,
        model=config.MODEL_FIXER,
        max_tokens=config.MAX_TOKENS_FIXER,
    )
    grezzo = "".join(block.text for block in response.content if block.type == "text")
    if info["truncated"]:
        print(
            f"ATTENZIONE: correzione del capitolo {numero:02d} troncata (max_tokens) "
            "anche dopo il ritentativo col tetto raddoppiato: capitolo corretto "
            "probabilmente incompleto.",
            file=sys.stderr,
        )

    testo, titolo_ok = clean_chapter(grezzo)
    if not titolo_ok:
        # Senza titolo la pulizia non si applica: salvo il grezzo così com'è.
        testo = grezzo

    chapter_path.write_text(testo, encoding="utf-8")

    usage_path = chapter_path.with_name(f"cap_{numero:02d}.fix.usage.json")
    usage_path.write_text(
        json.dumps(
            {
                "model": response.model,
                "stop_reason": info["stop_reason"],
                "truncated": info["truncated"],
                "retried": info["retried"],
                "chiamate": usage_log,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    # Il META corretto è quello autoritativo: aggiorna la libreria asset, ma
    # SOLO se il blocco porta una lista `assets` non vuota. Altrimenti si
    # scriverebbe una riga degenere (senza tipo né titolo): meglio non toccare
    # il database e segnalare l'anomalia.
    meta_match = META_RE.search(testo)
    meta = parse_meta(testo)
    assets = meta.get("assets") if isinstance(meta, dict) else None
    if meta_match and isinstance(assets, list) and assets:
        capture_assets(brief, assignment, meta_match.group(1).strip())
    else:
        print(
            f"ATTENZIONE: capitolo corretto {numero:02d}: blocco META assente o senza "
            "chiave 'assets' valorizzata — nessun asset scritto nel database "
            "(evitata la riga degenere).",
            file=sys.stderr,
        )

    return chapter_path, chapter_word_count(testo), info
