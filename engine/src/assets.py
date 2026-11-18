"""Persistenza degli asset estratti dai capitoli (blocco META) su SQLite."""

import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from schema.brief import Brief, ChapterAssignment
from src import config

ENGINE_ROOT = Path(__file__).resolve().parents[1]

_SCHEMA = """
CREATE TABLE IF NOT EXISTS assets (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    destinazione TEXT,
    tipo TEXT,
    titolo TEXT,
    deperibilita TEXT,
    testo TEXT,
    meta_raw TEXT,
    fonte_capitolo INTEGER,
    brief_id TEXT,
    created_at TEXT,
    last_verified_at TEXT,
    reuse_count INTEGER NOT NULL DEFAULT 0,
    refinement_count INTEGER NOT NULL DEFAULT 0,
    quality_score REAL
)
"""


def _connect() -> sqlite3.Connection:
    db_path = config.assets_db_path()
    db_path.parent.mkdir(parents=True, exist_ok=True)
    con = sqlite3.connect(db_path)
    con.execute(_SCHEMA)
    return con


def _parse_meta(meta: str) -> list[dict]:
    """Interpreta il blocco META come JSON.

    Il formato atteso (da prompts/chapter_system.md) è un oggetto con la lista
    di asset sotto la chiave "assets"; per robustezza si accettano anche una
    lista di asset nudi o un singolo oggetto asset. Se non è JSON valido,
    restituisce una lista vuota: la riga viene comunque salvata con il solo
    meta_raw, così nessun dato va perso.
    """
    try:
        parsed = json.loads(meta)
    except (json.JSONDecodeError, TypeError):
        return []
    if isinstance(parsed, dict):
        assets_list = parsed.get("assets")
        if isinstance(assets_list, list):
            return [item for item in assets_list if isinstance(item, dict)]
        return [parsed]
    if isinstance(parsed, list):
        return [item for item in parsed if isinstance(item, dict)]
    return []


def delete_chapter_assets(brief_id: str, fonte_capitolo: int) -> int:
    """Cancella tutte le righe asset di un capitolo (per brief_id + numero).

    Serve alla deduplica: quando il fixer riscrive il capitolo, le righe catturate
    dalla versione v1 vanno rimosse prima di inserire quelle della versione finale,
    così in libreria resta una sola serie di asset per capitolo.
    """
    con = _connect()
    try:
        cur = con.execute(
            "DELETE FROM assets WHERE brief_id = ? AND fonte_capitolo = ?",
            (brief_id, fonte_capitolo),
        )
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def cleanup_degenerate_assets() -> int:
    """Pulizia una-tantum delle righe degeneri: senza tipo né titolo.

    Sono un residuo storico di catture da blocchi META privi di 'assets' (prima
    della guardia introdotta nel fixer). Restituisce il numero di righe rimosse.
    """
    con = _connect()
    try:
        cur = con.execute(
            "DELETE FROM assets WHERE tipo IS NULL AND (titolo IS NULL OR titolo = '')"
        )
        con.commit()
        return cur.rowcount
    finally:
        con.close()


def capture_assets(brief: Brief, assignment: ChapterAssignment, meta: str) -> int:
    """Salva gli asset del blocco META nel database. Restituisce il numero di righe inserite."""
    now = datetime.now(timezone.utc).isoformat()
    destinazione_default: Optional[str] = assignment.tappa.luogo if assignment.tappa else None

    items = _parse_meta(meta)
    if not items:
        items = [{}]  # meta non parsabile: salva comunque una riga con meta_raw

    rows = [
        (
            item.get("destinazione", destinazione_default),
            item.get("tipo"),
            item.get("titolo"),
            item.get("deperibilita"),
            item.get("testo"),
            meta,
            assignment.numero,
            brief.brief_id,
            now,
            now,
            0,
            0,
            item.get("quality_score"),
        )
        for item in items
    ]

    con = _connect()
    try:
        con.executemany(
            """
            INSERT INTO assets (
                destinazione, tipo, titolo, deperibilita, testo, meta_raw,
                fonte_capitolo, brief_id, created_at, last_verified_at,
                reuse_count, refinement_count, quality_score
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            rows,
        )
        con.commit()
    finally:
        con.close()
    return len(rows)
