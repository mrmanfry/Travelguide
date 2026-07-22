"""Misura dei costi in dollari di una pipeline di capitolo.

Aggrega, per ogni chiamata (generazione, critico 1, fixer, secondo critico), i
token per categoria e le ricerche web, e calcola il costo stimato dalla tabella
prezzi in config.py. Legge gli artefatti di usage già scritti a valle della
pipeline, così la misura è basata sui dati reali del run, non su una stima.
"""

import json
from pathlib import Path

from schema.brief import Brief, ChapterAssignment
from src import config
from src.chapter_runner import ENGINE_ROOT


def costo_chiamata(model: str, usage: dict) -> dict:
    """Costo di una singola risposta API a partire dal suo usage.

    Ritorna una voce con i token per categoria, le ricerche e il costo in USD.
    Se il modello non è in tabella prezzi, `costo_usd` è None e si annota il
    motivo, senza far fallire il resto della misura.
    """
    server = usage.get("server_tool_use") or {}
    voce = {
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        "cache_write_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
        "web_search_requests": server.get("web_search_requests", 0) or 0,
    }
    prezzo = config.PRICING.get(model)
    if prezzo is None:
        voce["costo_usd"] = None
        voce["nota"] = f"Prezzo non disponibile per il modello '{model}'."
        return voce
    costo = (
        voce["input_tokens"] / 1_000_000 * prezzo["input"]
        + voce["output_tokens"] / 1_000_000 * prezzo["output"]
        + voce["cache_write_tokens"] / 1_000_000 * prezzo["cache_write"]
        + voce["cache_read_tokens"] / 1_000_000 * prezzo["cache_read"]
        + voce["web_search_requests"] * config.WEB_SEARCH_COST_PER_SEARCH
    )
    voce["costo_usd"] = round(costo, 6)
    return voce


_CAMPI_TOKEN = (
    "input_tokens",
    "output_tokens",
    "cache_write_tokens",
    "cache_read_tokens",
    "web_search_requests",
)


def _somma_voci(voci: list[dict]) -> dict:
    """Somma più voci (risposte o chiamate) in un unico totale.

    Se anche una sola voce ha `costo_usd` None, il totale è None: meglio un
    buco esplicito che un numero falsamente completo.
    """
    tot = {campo: 0 for campo in _CAMPI_TOKEN}
    tot["costo_usd"] = 0.0
    costo_incompleto = False
    for v in voci:
        for campo in _CAMPI_TOKEN:
            tot[campo] += v.get(campo, 0) or 0
        if v.get("costo_usd") is None:
            costo_incompleto = True
        else:
            tot["costo_usd"] += v["costo_usd"]
    tot["costo_usd"] = None if costo_incompleto else round(tot["costo_usd"], 6)
    return tot


def _voce_multipla(model: str, chiamate: list[dict]) -> dict:
    """Voce per generatore/fixer, il cui usage è una lista di risposte (pause_turn)."""
    voce = _somma_voci([costo_chiamata(model, u) for u in chiamate])
    voce["modello"] = model
    voce["chiamate_api"] = len(chiamate)
    return voce


def _voce_singola(model: str, usage: dict) -> dict:
    """Voce per i critici, il cui usage è una singola risposta."""
    voce = costo_chiamata(model, usage)
    voce["modello"] = model
    voce["chiamate_api"] = 1
    return voce


def _leggi_json(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return None


def costruisci_costi(brief: Brief, assignment: ChapterAssignment) -> dict:
    """Compone la struttura dei costi leggendo gli artefatti di usage del capitolo."""
    numero = assignment.numero
    out_dir = ENGINE_ROOT / "output" / brief.brief_id
    stem = f"cap_{numero:02d}"

    chiamate: dict[str, dict] = {}

    gen = _leggi_json(out_dir / f"{stem}.usage.json")
    if gen:
        chiamate["generazione"] = _voce_multipla(
            gen.get("model", ""), gen.get("chiamate") or []
        )

    crit1 = _leggi_json(out_dir / f"{stem}.critic.json")
    if crit1 and crit1.get("usage"):
        chiamate["critico_1"] = _voce_singola(crit1.get("model", ""), crit1["usage"])

    fix = _leggi_json(out_dir / f"{stem}.fix.usage.json")
    if fix:
        chiamate["fixer"] = _voce_multipla(
            fix.get("model", ""), fix.get("chiamate") or []
        )

    crit2 = _leggi_json(out_dir / f"{stem}.critic2.json")
    if crit2 and crit2.get("usage"):
        chiamate["critico_2"] = _voce_singola(crit2.get("model", ""), crit2["usage"])

    return {
        "brief_id": brief.brief_id,
        "capitolo": numero,
        "chiamate": chiamate,
        "totale": _somma_voci(list(chiamate.values())),
    }


def scrivi_costi(brief: Brief, assignment: ChapterAssignment) -> tuple[Path, dict]:
    """Scrive output/{brief_id}/costi.json con l'aggregato dei costi. Ritorna (path, dati)."""
    dati = costruisci_costi(brief, assignment)
    out_dir = ENGINE_ROOT / "output" / brief.brief_id
    out_dir.mkdir(parents=True, exist_ok=True)
    costi_path = out_dir / "costi.json"
    costi_path.write_text(
        json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return costi_path, dati
