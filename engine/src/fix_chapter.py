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
from src.assets import capture_assets, delete_chapter_assets
from src.chapter_runner import (
    META_RE,
    build_system_blocks,
    chapter_word_count,
    clean_chapter,
    controlli_struttura,
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


MAX_FIX_ATTEMPTS = 2  # 1 tentativo + 1 solo ritentativo se il candidato non passa


def _problemi_candidato(
    assignment: ChapterAssignment, testo: str, titolo_ok: bool, truncated: bool
) -> list[str]:
    """Valida l'output del fixer con gli stessi controlli della generazione.

    Un candidato è valido solo se: inizia col titolo '# ', porta un blocco META
    parsabile, rientra nella banda di lunghezza, rispetta la regola del box GLI
    IMMOBILI e attesta almeno una correzione nel campo META `correzioni_applicate`.
    Quest'ultimo controllo intercetta il caso in cui il fixer restituisce un
    resoconto narrativo invece del capitolo corretto: proprio il difetto che ha
    ridotto un capitolo da 1208 a 234 parole senza META. Ritorna la lista dei
    problemi (vuota = candidato valido).
    """
    problemi: list[str] = []
    if truncated:
        problemi.append("output troncato (max_tokens) anche dopo il ritentativo interno")
    if not titolo_ok:
        problemi.append("nessuna riga di titolo '# ': l'output non è un capitolo")
        return problemi  # senza titolo gli altri controlli non hanno senso
    c = controlli_struttura(assignment, testo)
    lo, hi = c["banda"]
    if not c["meta_ok"]:
        problemi.append("blocco META assente o non parsabile")
    if not c["lunghezza_ok"]:
        problemi.append(
            f"lunghezza fuori banda: {c['parole']} parole (banda ammessa {lo}-{hi})"
        )
    if not c["immobili_ok"]:
        if assignment.tipo == "tappa":
            problemi.append("box GLI IMMOBILI assente in un capitolo di tappa")
        else:
            problemi.append(
                f"box GLI IMMOBILI presente in un capitolo di tipo '{assignment.tipo}'"
            )
    meta = c["meta"] or {}
    corr = meta.get("correzioni_applicate")
    if not (isinstance(corr, list) and corr):
        problemi.append(
            "campo META 'correzioni_applicate' vuoto o assente: il fixer non "
            "attesta alcuna correzione applicata (possibile resoconto narrativo "
            "al posto del capitolo)"
        )
    return problemi


def apply_corrections(
    brief: Brief,
    assignment: ChapterAssignment,
    chapter_path: Path,
    verdict: dict | None,
) -> tuple[Path, int, dict]:
    """Applica le correzioni del critico, ma promuove il risultato solo se valido.

    L'output del fixer viene scritto prima su un file candidato
    (`cap_NN.fix_candidate.md`) e validato con gli stessi controlli della
    generazione (titolo, META parsabile, banda di lunghezza, box GLI IMMOBILI,
    campo `correzioni_applicate` non vuoto). Solo se passa, il candidato è
    promosso a `cap_NN.md`. Se non passa si concede un solo nuovo tentativo; se
    fallisce ancora si tiene la versione originale (mai sovrascritta da un output
    non validato), si segnala l'anomalia in `cap_NN.WARNING.txt` e il capitolo è
    destinato allo stato fallito. La versione pre-correzione resta in
    `cap_NN.v1.md`. Ritorna (percorso, n_parole, info): `info` include
    `promoted` (bool) e `fix_problemi` (list[str]) oltre a troncatura/ritentativo.
    """
    numero = assignment.numero
    original_text = chapter_path.read_text(encoding="utf-8")

    # Versione pre-correzione, per record e confronto.
    v1_path = chapter_path.with_name(f"cap_{numero:02d}.v1.md")
    v1_path.write_text(original_text, encoding="utf-8")

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
    user_content = build_fixer_user(original_text, alerts)
    candidate_path = chapter_path.with_name(f"cap_{numero:02d}.fix_candidate.md")

    usage_totale: list[dict] = []
    ultimo_response = None
    ultimo_info: dict = {"stop_reason": None, "truncated": False, "retried": False}
    testo_valido: str | None = None
    problemi_finali: list[str] = []

    for tentativo in range(1, MAX_FIX_ATTEMPTS + 1):
        response, usage_log, info = run_verification_call(
            client,
            system,
            tools,
            user_content,
            model=config.MODEL_FIXER,
            max_tokens=config.MAX_TOKENS_FIXER,
        )
        usage_totale.extend(usage_log)
        ultimo_response = response
        ultimo_info = info

        grezzo = "".join(block.text for block in response.content if block.type == "text")
        testo, titolo_ok = clean_chapter(grezzo)
        if not titolo_ok:
            testo = grezzo
        # Scrivo SEMPRE su candidato, mai sull'originale, prima della validazione.
        candidate_path.write_text(testo, encoding="utf-8")

        problemi_finali = _problemi_candidato(assignment, testo, titolo_ok, info["truncated"])
        if not problemi_finali:
            testo_valido = testo
            break

        print(
            f"ATTENZIONE: correzione del capitolo {numero:02d}, tentativo "
            f"{tentativo}/{MAX_FIX_ATTEMPTS} non valido: "
            + "; ".join(problemi_finali),
            file=sys.stderr,
        )

    usage_path = chapter_path.with_name(f"cap_{numero:02d}.fix.usage.json")
    usage_path.write_text(
        json.dumps(
            {
                "model": ultimo_response.model if ultimo_response else config.MODEL_FIXER,
                "stop_reason": ultimo_info.get("stop_reason"),
                "truncated": ultimo_info.get("truncated"),
                "retried": ultimo_info.get("retried"),
                "promoted": testo_valido is not None,
                "chiamate": usage_totale,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    info_ritorno = dict(ultimo_info)
    info_ritorno["promoted"] = testo_valido is not None
    info_ritorno["fix_problemi"] = [] if testo_valido is not None else problemi_finali

    if testo_valido is None:
        # Nessun candidato valido dopo i tentativi: NON sovrascrivo l'originale.
        warning_path = chapter_path.with_name(f"cap_{numero:02d}.WARNING.txt")
        warning_path.write_text(
            f"CORREZIONE NON VALIDA dopo {MAX_FIX_ATTEMPTS} tentativi — "
            "originale mantenuto:\n\n"
            + "\n".join(f"- {p}" for p in problemi_finali)
            + f"\n\nL'output del fixer è in {candidate_path.name} (non promosso).\n",
            encoding="utf-8",
        )
        print(
            f"ATTENZIONE: capitolo {numero:02d}: nessuna correzione valida dopo "
            f"{MAX_FIX_ATTEMPTS} tentativi — tengo l'originale, capitolo destinato "
            "allo stato fallito.",
            file=sys.stderr,
        )
        return chapter_path, chapter_word_count(original_text), info_ritorno

    # Candidato valido → promozione a cap_NN.md.
    chapter_path.write_text(testo_valido, encoding="utf-8")

    # Il META corretto è quello autoritativo: aggiorna la libreria asset, ma SOLO
    # se il blocco porta una lista `assets` non vuota (altrimenti si scriverebbe
    # una riga degenere).
    meta_match = META_RE.search(testo_valido)
    meta = parse_meta(testo_valido)
    assets = meta.get("assets") if isinstance(meta, dict) else None
    if meta_match and isinstance(assets, list) and assets:
        delete_chapter_assets(brief.brief_id, numero)
        capture_assets(brief, assignment, meta_match.group(1).strip())
    else:
        print(
            f"ATTENZIONE: capitolo corretto {numero:02d}: blocco META senza chiave "
            "'assets' valorizzata — asset della v1 mantenuti, nessuna cattura né "
            "deduplica.",
            file=sys.stderr,
        )

    return chapter_path, chapter_word_count(testo_valido), info_ritorno
