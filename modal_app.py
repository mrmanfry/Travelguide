"""Wrapper Modal del motore di generazione guide.

Espone il motore Python (engine/) come servizio su Modal:

* `genera_guida` — funzione lunga (~30-45 min) che esegue l'orchestrazione di
  una guida intera su una Volume durevole, isolata per `job_id`. Commit della
  Volume a ogni capitolo, così l'avanzamento è leggibile dal vivo.
* `web` — app FastAPI con tre endpoint: avvio del job, stato/avanzamento,
  download degli artefatti (guida.md e da_rivedere.md).

La generazione dura molto più del timeout di una richiesta HTTP: per questo
l'endpoint di avvio fa `spawn()` della funzione e restituisce subito un
`job_id`; il frontend fa polling di `GET /jobs/{job_id}`.

Prerequisiti di deploy:
    pip install modal
    modal setup                       # oppure MODAL_TOKEN_ID/SECRET in env
    # Secret con la chiave Anthropic del motore (NON esposta al frontend):
    modal secret create anthropic-guide GUIDE_ENGINE_KEY=sk-ant-...
    modal deploy modal_app.py

Il deploy stampa l'URL pubblico dell'endpoint `web`, da passare al frontend.
"""

import modal

app = modal.App("travelguide")

# Radice degli artefatti sulla Volume durevole (sopravvive a riavvii e redeploy).
OUTPUT_ROOT = "/data/output"

# Immagine: dipendenze del motore + FastAPI, e la cartella engine/ montata a
# /root/engine (prompt, schema e src inclusi).
engine_image = (
    modal.Image.debian_slim(python_version="3.11")
    .pip_install("anthropic>=0.40", "pydantic>=2.7", "fastapi[standard]")
    .add_local_dir("engine", remote_path="/root/engine")
)

# Volume per gli artefatti (stato.json, capitoli, guida.md, da_rivedere.md, costi).
output_volume = modal.Volume.from_name("travelguide-output", create_if_missing=True)

# Secret con la chiave Anthropic usata dal motore. Il secret di tipo "Anthropic"
# di Modal espone la chiave come ANTHROPIC_API_KEY; il motore legge invece
# GUIDE_ENGINE_KEY. Il ponte in genera_guida accetta l'uno o l'altro nome.
anthropic_secret = modal.Secret.from_name("anthropic-secret")


def _prepara_ambiente() -> None:
    """Mette engine/ sul path, ci si posiziona dentro e punta l'output alla Volume."""
    import os
    import sys

    if "/root/engine" not in sys.path:
        sys.path.insert(0, "/root/engine")
    os.chdir("/root/engine")
    os.environ["GUIDE_OUTPUT_ROOT"] = OUTPUT_ROOT


@app.function(
    image=engine_image,
    volumes={"/data": output_volume},
    secrets=[anthropic_secret],
    # La guida intera può durare a lungo (generazione + doppia verifica per
    # capitolo, più eventuali ritentativi): tetto largo per non troncarla.
    timeout=2 * 60 * 60,
    cpu=1.0,
    memory=2048,
)
def genera_guida(brief_dict: dict, job_id: str, tetto_usd: float | None = None) -> int:
    """Esegue la guida intera per un brief. Ritorna il codice d'uscita del motore.

    `brief_id` viene forzato a `job_id`: gli artefatti del job vivono in
    OUTPUT_ROOT/{job_id}, isolati e ritrovabili dallo stato. La Volume viene
    committata a ogni capitolo (via on_progress) e alla fine, così il polling
    dell'endpoint vede l'avanzamento e poi gli artefatti finali. `tetto_usd`, se
    fornito, impone un budget di spesa al run (utile per un test di collegamento
    a basso costo: la guida si ferma dopo l'outline e il primo capitolo).
    """
    _prepara_ambiente()

    # Ponte sulla chiave: il motore legge GUIDE_ENGINE_KEY, il secret Anthropic
    # di Modal fornisce ANTHROPIC_API_KEY. Accetta l'uno o l'altro.
    import os

    chiave = os.environ.get("GUIDE_ENGINE_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if chiave:
        os.environ["GUIDE_ENGINE_KEY"] = chiave
    if tetto_usd is not None:
        os.environ["GUIDE_MAX_COSTO_USD"] = str(tetto_usd)

    from schema.brief import Brief
    from src.guide_runner import orchestrazione

    brief_dict = dict(brief_dict)
    brief_dict["brief_id"] = job_id
    brief = Brief.model_validate(brief_dict)

    def _pubblica(_stato: dict) -> None:
        # Rende visibile stato.json (e i file già scritti) all'endpoint web.
        output_volume.commit()

    rc = orchestrazione(brief, on_progress=_pubblica)
    output_volume.commit()
    return rc


@app.function(image=engine_image, volumes={"/data": output_volume}, secrets=[anthropic_secret])
@modal.asgi_app()
def web():
    """App FastAPI: intervista di intake, avvio job, stato/avanzamento, artefatti."""
    import json
    import os
    import uuid

    from fastapi import FastAPI, HTTPException
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import PlainTextResponse

    # Prepara l'ambiente per importare il motore (l'intervista chiama Claude) e
    # fai il ponte sulla chiave: make_client legge GUIDE_ENGINE_KEY, il secret
    # fornisce ANTHROPIC_API_KEY.
    _prepara_ambiente()
    _chiave = os.environ.get("GUIDE_ENGINE_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if _chiave:
        os.environ["GUIDE_ENGINE_KEY"] = _chiave

    api = FastAPI(title="TravelGuide Engine")
    # Prototipo: il frontend (Lovable) sta su un altro dominio → CORS aperto.
    api.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_methods=["*"],
        allow_headers=["*"],
    )

    def _job_dir(job_id: str) -> str:
        return os.path.join(OUTPUT_ROOT, job_id)

    def _leggi_json(path: str):
        try:
            with open(path, encoding="utf-8") as f:
                return json.load(f)
        except (OSError, json.JSONDecodeError):
            return None

    @api.get("/health")
    def health():
        return {"ok": True}

    @api.post("/intervista")
    def intervista(payload: dict):
        """Un turno di intervista di intake sul brief del form.

        body: {"brief": {...}, "messaggi": [{"ruolo": "assistant"|"user", "testo": "..."}]}
        Ritorna {"azione": "domanda"|"fine", "messaggio": "...",
        "opzioni": ["..."], "brief": {...}|null}. `opzioni` sono risposte brevi
        tappabili (chip) che accompagnano una domanda; [] quando non servono o
        alla chiusura. A ogni turno il frontend accoda la domanda dell'AI e la
        risposta dell'utente in `messaggi` e richiama; a "fine" usa il brief
        arricchito.
        """
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload non valido.")
        brief = payload.get("brief") or {}
        messaggi = payload.get("messaggi") or []
        if not isinstance(brief, dict) or not brief:
            raise HTTPException(status_code=400, detail="Brief mancante o non valido.")
        from src.intervista import passo_intervista

        return passo_intervista(brief, messaggi)

    @api.post("/generate")
    def generate(brief: dict):
        """Avvia una generazione. Ritorna il job_id per il polling.

        Campo opzionale `_tetto_usd` nel body: budget di spesa del run (per un
        test di collegamento a basso costo). Viene estratto e non fa parte del
        brief passato al motore.
        """
        if not isinstance(brief, dict) or not brief:
            raise HTTPException(status_code=400, detail="Brief mancante o non valido.")
        brief = dict(brief)
        tetto_usd = brief.pop("_tetto_usd", None)
        job_id = uuid.uuid4().hex[:12]
        genera_guida.spawn(brief, job_id, tetto_usd)
        return {"job_id": job_id}

    @api.get("/jobs/{job_id}")
    def job_status(job_id: str):
        """Stato e avanzamento del job, letti dagli artefatti sulla Volume."""
        output_volume.reload()
        d = _job_dir(job_id)
        stato = _leggi_json(os.path.join(d, "stato.json"))
        if stato is None:
            # Nessuno stato ancora scritto: job in coda o appena partito.
            return {"job_id": job_id, "fase": "in_coda", "capitoli": [], "totali": {}}

        caps_raw = stato.get("capitoli", {})
        capitoli = [
            {
                "numero": int(n),
                "stato": e.get("stato"),
                "costo_usd": e.get("costo"),
                "punti_revisione": len(e.get("problemi_revisione") or []),
            }
            for n, e in sorted(caps_raw.items(), key=lambda kv: int(kv[0]))
        ]
        consegnati = sum(1 for c in capitoli if c["stato"] in ("approvato", "da_rivedere"))
        costo = sum((c["costo_usd"] or 0.0) for c in capitoli) + float(
            stato.get("costo_outline") or 0.0
        )

        completa = os.path.exists(os.path.join(d, "guida.md"))
        arresto = None
        arresto_path = os.path.join(d, "ARRESTO.txt")
        if os.path.exists(arresto_path):
            with open(arresto_path, encoding="utf-8") as f:
                arresto = f.read()

        if completa:
            fase = "completa"
        elif arresto is not None:
            fase = "interrotta"
        else:
            fase = "in_corso"

        risposta = {
            "job_id": job_id,
            "fase": fase,
            "capitoli": capitoli,
            "totali": {
                "consegnati": consegnati,
                "totale": len(capitoli),
                "da_rivedere": sum(1 for c in capitoli if c["stato"] == "da_rivedere"),
                "costo_usd": round(costo, 4),
            },
        }
        if completa:
            risposta["download"] = {
                "guida": f"/jobs/{job_id}/guida.md",
                "da_rivedere": f"/jobs/{job_id}/da_rivedere.md",
            }
        if arresto is not None:
            risposta["arresto"] = arresto
        return risposta

    def _servi_file(job_id: str, nome: str) -> PlainTextResponse:
        output_volume.reload()
        path = os.path.join(_job_dir(job_id), nome)
        if not os.path.exists(path):
            raise HTTPException(status_code=404, detail=f"{nome} non ancora disponibile.")
        with open(path, encoding="utf-8") as f:
            return PlainTextResponse(f.read(), media_type="text/markdown; charset=utf-8")

    @api.get("/jobs/{job_id}/guida.md")
    def scarica_guida(job_id: str):
        return _servi_file(job_id, "guida.md")

    @api.get("/jobs/{job_id}/da_rivedere.md")
    def scarica_da_rivedere(job_id: str):
        return _servi_file(job_id, "da_rivedere.md")

    return api
