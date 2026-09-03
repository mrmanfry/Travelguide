"""Costanti di configurazione del motore di generazione."""

import os
from pathlib import Path

# Radice engine/ (config.py sta in engine/src/).
ENGINE_ROOT = Path(__file__).resolve().parents[1]


def output_root() -> Path:
    """Radice degli artefatti generati (stato, capitoli, guida, costi, asset).

    Default: engine/output (invariato per gli usi da CLI in locale). Su un host
    come Modal si punta a una Volume durevole impostando la variabile d'ambiente
    GUIDE_OUTPUT_ROOT (es. /data/output), senza toccare il codice.
    """
    env = os.environ.get("GUIDE_OUTPUT_ROOT")
    return Path(env) if env else ENGINE_ROOT / "output"


def assets_db_path() -> Path:
    """Percorso del DB SQLite degli asset, sotto la radice di output."""
    return output_root() / "assets.sqlite"


# Mix di modelli per ruolo. La scrittura del capitolo resta su Opus, la parte
# più costosa per intelligenza richiesta; critico e fixer vanno su Sonnet 5,
# che riverifica fatti e giudica a un costo per token nettamente inferiore.
MODEL_GENERATION = "claude-opus-4-8"
MODEL_CRITIC = "claude-sonnet-5"
MODEL_FIXER = "claude-sonnet-5"
# L'outline è ragionamento strutturale, senza ricerca: sta su Opus per qualità,
# ma è una sola chiamata piccola (pochi centesimi).
MODEL_OUTLINE = "claude-opus-4-8"
MAX_TOKENS_OUTLINE = 4000
# Intervista di intake: una conversazione breve che, partendo dal brief del
# form, chiede il poco che manca per tarare la guida. Nessuna ricerca, poche
# domande: Sonnet 5 dà domande sensate a costo contenuto.
MODEL_INTERVISTA = "claude-sonnet-5"
MAX_TOKENS_INTERVISTA = 4000
# Recupero del blocco META quando il capitolo è valido ma il generatore ha
# omesso solo il META finale: un compito puramente estrattivo (nessuna ricerca,
# nessuna riscrittura), affidato al modello più economico. ID verificato sui
# docs Anthropic: Haiku 4.5 = "claude-haiku-4-5".
MODEL_META_SALVAGE = "claude-haiku-4-5"
MAX_TOKENS_META_SALVAGE = 2000

WEB_SEARCH_TOOL_TYPE = "web_search_20260209"

MAX_TOKENS_CHAPTER = 16000
# Tetti generosi per i ruoli di verifica: su Sonnet 5 il thinking adattivo è
# attivo di default e consuma il budget di output prima della risposta. Con
# tetti bassi (es. 4000) il verdetto veniva troncato e perso.
MAX_TOKENS_CRITIC = 16000
# Il fixer non riemette più il capitolo: produce solo un oggetto JSON di patch
# (le sostituzioni testuali mirate), quindi un tetto molto più basso è sufficiente.
MAX_TOKENS_FIXER = 8000

# Interruttore del loop di correzione. Quando è False, il fixer E il secondo
# critico non vengono mai chiamati: resta solo la prima passata di critica, che
# produce gli alert. Un capitolo con alert non risolti viene comunque consegnato
# e marcato 'da_rivedere' in stato.json (la guida prosegue), e la lista dei
# problemi finisce in output/{brief_id}/da_rivedere.md. Per la guida che legge
# una sola persona serve SAPERE i problemi, non ripararli in automatico: il fixer
# è dove si concentravano costi e fallimenti del motore. Si riaccende (True)
# quando si spedisce a clienti paganti.
FIXER_ENABLED = False

# Ampiezza dell'assaggio gratuito. True: si scrive fino al PRIMO capitolo di
# tappa incluso (introduzione, contesto e la prima tappa vera) — l'assaggio che
# convince, perché parla dei loro luoghi, ma costa di più per utente gratuito.
# False: ci si ferma al primo capitolo (la sola introduzione), molto più
# economico ma meno convincente.
ANTEPRIMA_FINO_A_TAPPA = False
MAX_SEARCHES_PER_CHAPTER = 30
# I capitoli non di tappa (introduzione, contesto, collegamento, congedo,
# apparati) non raccomandano 5-6 nomi propri e non devono verificarne altrettanti:
# un tetto di ricerche più basso evita che un capitolo-ponte bruci ricerche.
MAX_SEARCHES_NON_TAPPA = 8
MAX_SEARCHES_CRITIC = 15
# La seconda passata di critica ha un mandato ristretto (solo le correzioni del
# fixer e i punti già segnalati), quindi un budget di ricerche molto più piccolo:
# non deve riverificare l'intero capitolo.
MAX_SEARCHES_CRITIC_2 = 6


# Tetto di spesa dell'intera guida in USD: controllato cumulativamente durante
# l'orchestrazione. Superato il tetto, la guida si ferma.
MAX_COSTO_GUIDA_USD = 40.0


def max_costo_guida_usd() -> float:
    """Tetto di spesa effettivo, con override da GUIDE_MAX_COSTO_USD.

    Default: MAX_COSTO_GUIDA_USD. La variabile d'ambiente permette un budget
    per-run senza modificare il codice (es. un tetto basso per un test di
    collegamento che genera solo l'outline e il primo capitolo). Letta a ogni
    controllo, così un container caldo rispetta comunque il valore del run.
    """
    val = os.environ.get("GUIDE_MAX_COSTO_USD")
    if val:
        try:
            return float(val)
        except ValueError:
            pass
    return MAX_COSTO_GUIDA_USD

# Tabella prezzi in USD per 1 milione di token, per modello (listino pubblico
# Anthropic). `cache_write` è il costo di scrittura in cache con TTL 5 minuti
# (1.25x l'input), `cache_read` è la lettura da cache (0.10x l'input). Serve a
# misurare i costi reali della pipeline, non a stimarli a mano.
PRICING = {
    "claude-opus-4-8": {
        "input": 5.00,
        "output": 25.00,
        "cache_write": 6.25,   # 1.25 * input (TTL 5m)
        "cache_read": 0.50,    # 0.10 * input
    },
    "claude-sonnet-5": {
        "input": 3.00,
        "output": 15.00,
        "cache_write": 3.75,   # 1.25 * input (TTL 5m)
        "cache_read": 0.30,    # 0.10 * input
    },
    "claude-haiku-4-5": {
        "input": 1.00,
        "output": 5.00,
        "cache_write": 1.25,   # 1.25 * input (TTL 5m)
        "cache_read": 0.10,    # 0.10 * input
    },
}

# Costo delle ricerche web server-side: 10 USD ogni 1000 ricerche.
WEB_SEARCH_COST_PER_SEARCH = 10.0 / 1000.0
