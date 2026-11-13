# TravelGuide

Motore di generazione di guide di viaggio personalizzate: da un *brief* (chi
viaggia, tappe, date, mezzo, passioni) produce una guida-libro di più capitoli,
verificata con ricerca web, con critica, gate strutturale, misura dei costi,
ripresa dallo stato e lista di revisione.

## Struttura

- `engine/` — il motore Python (nessuna dipendenza da un host).
  - `schema/brief.py` — contratto dati del brief (Pydantic).
  - `src/` — pipeline: `outline` → `guide_runner` (orchestrazione) → per capitolo
    `run_chapter` (genera → critica → eventuale fixer → gate) con `chapter_runner`,
    `fix_chapter`, `costs`, `assets`, `qa_report`.
  - `prompts/` — system prompt e style guide.
  - `fixtures/` — brief di esempio (es. `grecia.json`).
- `modal_app.py` — wrapper che espone il motore come servizio su [Modal](https://modal.com).

## Uso da riga di comando (sviluppo)

```bash
cd engine
pip install -r requirements.txt
export GUIDE_ENGINE_KEY=sk-ant-...        # chiave Anthropic usata dal motore
python -m src.guide_runner fixtures/grecia.json
python -m src.qa_report   fixtures/grecia.json
```

Gli artefatti finiscono in `engine/output/{brief_id}/` (`guida.md`,
`da_rivedere.md`, `stato.json`, `costi_guida.json`). La cartella di output è
configurabile con la variabile d'ambiente `GUIDE_OUTPUT_ROOT` (usata dal deploy
Modal per puntare a una Volume durevole).

Il loop di correzione (`fixer` + secondo critico) è governato da
`FIXER_ENABLED` in `engine/src/config.py`: spento (default), i capitoli con
alert non risolti vengono comunque consegnati e marcati `da_rivedere`, e la
lista dei problemi finisce in `da_rivedere.md`.

## Deploy su Modal

Il motore è una pipeline lunga (~30-45 min): gira come funzione Modal, non
dentro una richiesta HTTP. `modal_app.py` espone un'app FastAPI che avvia il job
(`spawn`) e ne riporta l'avanzamento leggendo lo stato da una Volume durevole.

```bash
pip install modal
modal setup                                            # oppure MODAL_TOKEN_ID / MODAL_TOKEN_SECRET
modal secret create anthropic-guide GUIDE_ENGINE_KEY=sk-ant-...
modal deploy modal_app.py
```

Il deploy stampa l'URL pubblico dell'endpoint `web`. Contratto:

- `POST /generate` — body: il brief (JSON conforme a `schema/brief.py`). Ritorna
  `{"job_id": "..."}`.
- `GET /jobs/{job_id}` — `fase` (`in_coda` | `in_corso` | `completa` |
  `interrotta`), avanzamento per capitolo, totali e, a fine, i link di download.
- `GET /jobs/{job_id}/guida.md` e `/da_rivedere.md` — gli artefatti in Markdown.

Nota costi: il credito Modal copre solo il calcolo del container; il costo delle
generazioni (~$17 a guida) è sulla chiave Anthropic, separato.
