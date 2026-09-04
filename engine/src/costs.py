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


def _prezzo_modello(model: str) -> dict | None:
    """Listino per un id modello, tollerante al suffisso di data.

    L'API echeggia in `response.model` l'id concreto dello snapshot, che può
    portare un suffisso di data (es. 'claude-haiku-4-5-20251001'), mentre la
    tabella PRICING è indicizzata sugli id senza data ('claude-haiku-4-5'). Si
    prova prima la corrispondenza esatta; poi la chiave di PRICING che è prefisso
    dell'id — la più lunga, per evitare match spuri se un giorno convivessero
    'claude-x' e 'claude-x-y'. Senza il suffisso, un capitolo che passa dal META
    salvage risultava a costo None e azzerava l'intero costo del capitolo.
    """
    prezzo = config.PRICING.get(model)
    if prezzo is not None:
        return prezzo
    candidati = [k for k in config.PRICING if model.startswith(k)]
    if not candidati:
        return None
    return config.PRICING[max(candidati, key=len)]


def costo_chiamata(model: str, usage: dict) -> dict:
    """Costo di una singola risposta API a partire dal suo usage.

    Ritorna una voce con i token per categoria, le ricerche e il costo in USD.
    Se il modello non è in tabella prezzi, `costo_usd` è None e si annota il
    motivo, senza far fallire il resto della misura.
    """
    server = usage.get("server_tool_use") or {}
    details = usage.get("output_tokens_details") or {}
    voce = {
        "input_tokens": usage.get("input_tokens", 0) or 0,
        "output_tokens": usage.get("output_tokens", 0) or 0,
        # I thinking_tokens sono un di-cui degli output_tokens: servono a capire
        # quanto pesa il ragionamento (rilevante sui ruoli di verifica su Sonnet).
        "thinking_tokens": details.get("thinking_tokens", 0) or 0,
        "cache_write_tokens": usage.get("cache_creation_input_tokens", 0) or 0,
        "cache_read_tokens": usage.get("cache_read_input_tokens", 0) or 0,
        "web_search_requests": server.get("web_search_requests", 0) or 0,
    }
    prezzo = _prezzo_modello(model)
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
    "thinking_tokens",
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


def _voce_da_artefatto(art: dict) -> dict:
    """Voce di costo per una chiamata a partire dal suo artefatto di usage.

    L'artefatto ha `chiamate` (lista di risposte, pause_turn e ritentativo
    inclusi) e i flag `truncated`/`retried`. Per retro-compatibilità accetta
    anche un vecchio `usage` singolo. Riporta i token per categoria (thinking
    compreso), il costo, e se la chiamata è stata troncata o ritentata.
    """
    model = art.get("model", "")
    chiamate = art.get("chiamate")
    if chiamate is None and art.get("usage"):
        chiamate = [art["usage"]]  # schema vecchio (critici pre-revisione)
    chiamate = chiamate or []
    voce = _somma_voci([costo_chiamata(model, u) for u in chiamate])
    voce["modello"] = model
    voce["chiamate_api"] = len(chiamate)
    voce["troncata"] = bool(art.get("truncated"))
    voce["ritentata"] = bool(art.get("retried"))
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
    out_dir = config.output_root() / brief.brief_id
    stem = f"cap_{numero:02d}"

    # (ruolo, nome file) nell'ordine della pipeline.
    fonti = [
        ("generazione", f"{stem}.usage.json"),
        ("meta_salvage", f"{stem}.meta.usage.json"),
        ("critico_1", f"{stem}.critic.json"),
        ("fixer", f"{stem}.fix.usage.json"),
        ("critico_2", f"{stem}.critic2.json"),
    ]

    chiamate: dict[str, dict] = {}
    for ruolo, nome in fonti:
        art = _leggi_json(out_dir / nome)
        if art and (art.get("chiamate") or art.get("usage")):
            chiamate[ruolo] = _voce_da_artefatto(art)

    return {
        "brief_id": brief.brief_id,
        "capitolo": numero,
        "chiamate": chiamate,
        "totale": _somma_voci(list(chiamate.values())),
    }


def scrivi_costi(brief: Brief, assignment: ChapterAssignment) -> tuple[Path, dict]:
    """Scrive output/{brief_id}/costi.json con l'aggregato dei costi. Ritorna (path, dati)."""
    dati = costruisci_costi(brief, assignment)
    out_dir = config.output_root() / brief.brief_id
    out_dir.mkdir(parents=True, exist_ok=True)
    costi_path = out_dir / "costi.json"
    costi_path.write_text(
        json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8"
    )
    return costi_path, dati
