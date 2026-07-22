"""Costanti di configurazione del motore di generazione."""

# Mix di modelli per ruolo. La scrittura del capitolo resta su Opus, la parte
# più costosa per intelligenza richiesta; critico e fixer vanno su Sonnet 5,
# che riverifica fatti e giudica a un costo per token nettamente inferiore.
MODEL_GENERATION = "claude-opus-4-8"
MODEL_CRITIC = "claude-sonnet-5"
MODEL_FIXER = "claude-sonnet-5"

WEB_SEARCH_TOOL_TYPE = "web_search_20260209"

MAX_TOKENS_CHAPTER = 16000
MAX_TOKENS_CRITIC = 4000
MAX_SEARCHES_PER_CHAPTER = 20
MAX_SEARCHES_CRITIC = 15

ASSETS_DB = "output/assets.sqlite"

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
}

# Costo delle ricerche web server-side: 10 USD ogni 1000 ricerche.
WEB_SEARCH_COST_PER_SEARCH = 10.0 / 1000.0
