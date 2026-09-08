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


def _avvisa_sito(job_id: str, fase: str) -> None:
    """Dice al sito che un lavoro ha cambiato fase, così può mandare l'email.

    Senza questo, un cambio di fase lo nota solo il browser mentre fa polling —
    cioè proprio nel caso in cui l'email NON serve. Il motore è l'unico che sa
    davvero quando l'assaggio è pronto o il libro è finito, quindi è lui a
    bussare. Un avviso fallito non deve mai far fallire la generazione: si
    registra e si tira dritto.
    """
    import json
    import os
    import sys
    import urllib.request

    base = (os.environ.get("SITO_BASE_URL") or "").strip().rstrip("/")
    segreto = (os.environ.get("GATE_SECRET") or "").strip()
    if not base or not segreto:
        return
    try:
        richiesta = urllib.request.Request(
            f"{base}/api/public/motore-evento",
            data=json.dumps({"job_id": job_id, "fase": fase}).encode("utf-8"),
            headers={"Content-Type": "application/json", "X-Gate-Secret": segreto},
            method="POST",
        )
        urllib.request.urlopen(richiesta, timeout=20).read()
    except Exception as exc:
        print(f"avviso al sito non riuscito ({fase}): {exc}", file=sys.stderr)


def prezzo_eur(capitoli: int) -> int:
    """Prezzo del libro in base alla sua dimensione (numero di capitoli).

    Il costo di produzione cresce col numero di capitoli, quindi il prezzo lo
    segue: un weekend non paga come un viaggio di tre settimane. Il numero
    esatto lo conosciamo dopo l'outline, quindi al paywall si mostra il prezzo
    di QUESTO libro, non un listino astratto.
    """
    if capitoli <= 7:
        return 19
    if capitoli <= 12:
        return 29
    if capitoli <= 18:
        return 39
    return 49

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
def genera_guida(
    brief_dict: dict,
    job_id: str,
    tetto_usd: float | None = None,
    anteprima: bool = False,
) -> int:
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

    import json
    from datetime import datetime, timezone

    dir_job = os.path.join(OUTPUT_ROOT, job_id)

    def _battito() -> None:
        """Segno di vita del job: se si ferma, l'endpoint se ne accorge."""
        os.makedirs(dir_job, exist_ok=True)
        with open(os.path.join(dir_job, "battito.json"), "w", encoding="utf-8") as f:
            json.dump({"ts": datetime.now(timezone.utc).isoformat()}, f)

    # Il brief resta accanto al job: dopo il pagamento si completa il libro senza
    # che il client debba rimandarlo (e senza potersi inventare un brief diverso).
    os.makedirs(dir_job, exist_ok=True)

    # Un run che riparte cancella i segni della fermata precedente. Senza
    # questo, ARRESTO.txt resta sul disco e l'endpoint continua a dichiarare la
    # fase 'interrotta' per tutto il tempo in cui il motore sta scrivendo: la UI
    # mostrerebbe "la scrittura si è interrotta" mentre i capitoli arrivano.
    for segno in ("ARRESTO.txt", "ERRORE.txt"):
        try:
            os.remove(os.path.join(dir_job, segno))
        except FileNotFoundError:
            pass

    with open(os.path.join(dir_job, "brief.json"), "w", encoding="utf-8") as f:
        json.dump(brief_dict, f, ensure_ascii=False)

    def _pubblica(_stato: dict) -> None:
        # Rende visibile stato.json (e i file già scritti) all'endpoint web.
        _battito()
        output_volume.commit()

    _battito()
    output_volume.commit()
    try:
        rc = orchestrazione(brief, on_progress=_pubblica, anteprima=anteprima)
    except Exception as exc:
        # Un crash (credito esaurito, errore API, ecc.) non deve lasciare la UI
        # a girare a vuoto: lascia una traccia leggibile dall'endpoint.
        with open(os.path.join(dir_job, "ERRORE.txt"), "w", encoding="utf-8") as f:
            f.write(f"{type(exc).__name__}: {exc}\n")
        output_volume.commit()
        _avvisa_sito(job_id, "interrotta")
        raise
    output_volume.commit()

    # 0 = libro completo, 2 = assaggio pronto, 1 = fermata (tetto, gate, riassunto).
    _avvisa_sito(job_id, {0: "completa", 2: "anteprima"}.get(rc, "interrotta"))
    return rc


@app.function(image=engine_image, volumes={"/data": output_volume}, secrets=[anthropic_secret])
@modal.asgi_app()
def web():
    """App FastAPI: intervista di intake, avvio job, stato/avanzamento, artefatti."""
    import json
    import os
    import sys
    import uuid
    from datetime import datetime, timezone

    from fastapi import FastAPI, HTTPException, Request
    from fastapi.middleware.cors import CORSMiddleware
    from fastapi.responses import PlainTextResponse

    # Prepara l'ambiente per importare il motore (l'intervista chiama Claude) e
    # fai il ponte sulla chiave: make_client legge GUIDE_ENGINE_KEY, il secret
    # fornisce ANTHROPIC_API_KEY.
    _prepara_ambiente()
    _chiave = os.environ.get("GUIDE_ENGINE_KEY") or os.environ.get("ANTHROPIC_API_KEY")
    if _chiave:
        os.environ["GUIDE_ENGINE_KEY"] = _chiave

    # Segreto della "porta": gli endpoint che spendono (intervista, generate)
    # accettano solo chiamate col header X-Gate-Secret corrispondente. Finché
    # GATE_SECRET non è configurato nel secret Modal, il controllo è inattivo:
    # così il cutover si fa senza finestre rotte (prima si costruisce la porta,
    # poi si imposta il segreto e si ridiploya).
    # Confronto insensibile a spazi e a-capo: incollando il segreto nei pannelli
    # di Modal/Supabase ci finisce spesso un carattere invisibile in coda, e il
    # sintomo (403 con il segreto "giusto") è impossibile da diagnosticare a occhio.
    _gate = (os.environ.get("GATE_SECRET") or "").strip()
    if not _gate:
        # Stato pericoloso e silenzioso: senza segreto gli endpoint che spendono
        # sono aperti a chiunque. Va gridato nei log e visibile da /health.
        print(
            "ATTENZIONE: PORTA DISATTIVATA — GATE_SECRET assente: "
            "/intervista e /generate sono aperti al pubblico.",
            file=sys.stderr,
        )

    def _controlla_porta(request: Request) -> None:
        if not _gate:
            return
        ricevuto = (request.headers.get("X-Gate-Secret") or "").strip()
        if ricevuto != _gate:
            # Solo le lunghezze: aiutano a capire (0 = header assente) senza
            # esporre il segreto nei log.
            print(
                f"porta: segreto non corrispondente "
                f"(atteso {len(_gate)} caratteri, ricevuti {len(ricevuto)})",
                file=sys.stderr,
            )
            raise HTTPException(status_code=403, detail="Accesso non consentito.")

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
        # `porta` dice se il lucchetto è attivo, senza rivelare il segreto:
        # basta un curl per accorgersi di averlo perso in un redeploy.
        return {"ok": True, "porta": "attiva" if _gate else "DISATTIVATA"}

    @api.post("/intervista")
    def intervista(payload: dict, request: Request):
        """Un turno di conversazione sulla scheda del viaggio.

        Due modi, col campo opzionale `modo`:

        * assente o "intake" — l'intervista di conoscenza prima di scrivere.
        * "correzione" — la scheda esiste già e il viaggiatore sta rispondendo
          ai punti che non tornavano. Mandato diverso, prompt diverso: qui non
          si fanno domande sui gusti e si chiude appena la scheda è a posto.
          Il campo opzionale `avvisi` porta i punti aperti (le voci di
          `avvisi_brief`), così l'autore sa a cosa si sta rispondendo.

        body: {"brief": {...}, "messaggi": [{"ruolo": "assistant"|"user", "testo": "..."}],
               "modo": "intake"|"correzione", "avvisi": [{...}]}
        Ritorna {"azione": "domanda"|"fine", "messaggio": "...",
        "opzioni": ["..."], "brief": {...}|null}. `opzioni` sono risposte brevi
        tappabili (chip) che accompagnano una domanda; [] quando non servono o
        alla chiusura. A ogni turno il frontend accoda la domanda dell'AI e la
        risposta dell'utente in `messaggi` e richiama; a "fine" usa il brief
        arricchito.
        """
        _controlla_porta(request)
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload non valido.")
        brief = payload.get("brief") or {}
        messaggi = payload.get("messaggi") or []
        if not isinstance(brief, dict) or not brief:
            raise HTTPException(status_code=400, detail="Brief mancante o non valido.")
        modo = "correzione" if payload.get("modo") == "correzione" else "intake"
        avvisi = payload.get("avvisi")
        avvisi = avvisi if isinstance(avvisi, list) else None
        from src.intervista import passo_intervista

        return passo_intervista(brief, messaggi, modo, avvisi)

    @api.post("/generate")
    def generate(brief: dict, request: Request):
        """Avvia una generazione. Ritorna il job_id per il polling.

        Campo opzionale `_tetto_usd` nel body: budget di spesa del run (per un
        test di collegamento a basso costo). Viene estratto e non fa parte del
        brief passato al motore.
        """
        _controlla_porta(request)
        if not isinstance(brief, dict) or not brief:
            raise HTTPException(status_code=400, detail="Brief mancante o non valido.")
        brief = dict(brief)
        tetto_usd = brief.pop("_tetto_usd", None)
        brief.pop("_anteprima", None)  # ignorato di proposito, vedi sotto
        job_id = uuid.uuid4().hex[:12]
        # Da questa porta esce SOLO l'assaggio, qualunque cosa venga chiesta.
        # Il libro intero (~17 $) si scrive unicamente da /completa, che richiede
        # il segreto ed è chiamato solo dal webhook del pagamento. Così la via
        # costosa è irraggiungibile per costruzione, e non dipende dal fatto che
        # il codice del sito ricordi di chiedere l'assaggio.
        genera_guida.spawn(brief, job_id, tetto_usd, True)
        return {"job_id": job_id}

    @api.post("/jobs/{job_id}/completa")
    def completa(job_id: str, request: Request, payload: dict | None = None):
        """Completa un libro già iniziato: scrive i capitoli che mancano.

        Va chiamato SOLO dalla porta, dopo un pagamento verificato (il segreto
        lo garantisce). Riprende lo stesso job: i capitoli già consegnati non si
        rigenerano, quindi l'assaggio già scritto non si ripaga.

        Il brief è quello salvato accanto al job. L'UNICA eccezione è il campo
        opzionale `brief` nel corpo: la scheda corretta dal viaggiatore quando
        l'autore gli ha chiesto conto di un volo, di una data o di chi lascia il
        gruppo. Arriva dal sito, che l'ha già legata al job e all'email
        verificata, e passa comunque dalla porta col segreto.

        Cosa cambia e cosa no: la scheda corretta vale per i capitoli ANCORA DA
        SCRIVERE. L'outline resta congelato e i capitoli già consegnati non si
        toccano — è la promessa fatta al lettore ("le prime pagine restano come
        sono"). Una correzione non ridisegna il libro: lo scrive giusto da qui
        in avanti.
        """
        _controlla_porta(request)
        output_volume.reload()
        d = _job_dir(job_id)
        if os.path.exists(os.path.join(d, "guida.md")):
            return {"ok": True, "gia_completo": True}
        brief = _leggi_json(os.path.join(d, "brief.json"))
        if not brief:
            raise HTTPException(status_code=404, detail="Lavoro non trovato.")

        corretto = payload.get("brief") if isinstance(payload, dict) else None
        brief_aggiornato = False
        if isinstance(corretto, dict) and corretto:
            corretto = dict(corretto)
            corretto["brief_id"] = job_id
            # Validare qui e non nel container: se la scheda corretta è rotta il
            # sito deve saperlo subito con un 400, non scoprirlo mezz'ora dopo
            # da un job che muore.
            try:
                from schema.brief import Brief

                verificato = Brief.model_validate(corretto)
            except Exception as exc:
                raise HTTPException(
                    status_code=400, detail=f"Scheda corretta non valida: {exc}"
                )
            # Lo schema ha un default per quasi ogni campo: una scheda svuotata
            # per sbaglio lo supererebbe, e i capitoli restanti parlerebbero di
            # un viaggio senza luoghi. Un itinerario vuoto non è una correzione.
            if not verificato.tappe:
                raise HTTPException(
                    status_code=400,
                    detail="Scheda corretta senza tappe: il viaggio sparirebbe.",
                )
            # La scheda originale si conserva una volta sola: serve a capire, poi,
            # che cosa il viaggiatore ha corretto e perché.
            originale = os.path.join(d, "brief.originale.json")
            if not os.path.exists(originale):
                with open(originale, "w", encoding="utf-8") as f:
                    json.dump(brief, f, ensure_ascii=False)
            with open(os.path.join(d, "brief.json"), "w", encoding="utf-8") as f:
                json.dump(corretto, f, ensure_ascii=False)
            # Gli avvisi di coerenza si riferivano alla scheda vecchia: buttarli
            # fa sì che il controllo giri di nuovo sulla scheda corretta, per
            # pochi millesimi, invece di lasciare in giro allarmi già risolti.
            try:
                os.remove(os.path.join(d, "coerenza.json"))
            except FileNotFoundError:
                pass
            brief = corretto
            brief_aggiornato = True
            print(f"Job {job_id}: scheda corretta dal viaggiatore, adottata.")

        # Segna che il completamento è stato autorizzato: da qui la fase torna
        # 'in_corso' anche se l'anteprima è già sul disco.
        with open(os.path.join(d, "completamento.json"), "w", encoding="utf-8") as f:
            json.dump({"ts": datetime.now(timezone.utc).isoformat()}, f)
        output_volume.commit()
        genera_guida.spawn(brief, job_id, None, False)
        return {
            "ok": True,
            "gia_completo": False,
            "brief_aggiornato": brief_aggiornato,
        }

    @api.get("/jobs/{job_id}")
    def job_status(job_id: str):
        """Stato e avanzamento del job, letti dagli artefatti sulla Volume."""
        output_volume.reload()
        d = _job_dir(job_id)
        stato = _leggi_json(os.path.join(d, "stato.json"))
        if stato is None:
            # Nessuno stato ancora scritto: job in coda o appena partito.
            return {"job_id": job_id, "fase": "in_coda", "capitoli": [], "totali": {}}

        # Titoli veri dall'outline congelato. Senza questi il sito non ha modo
        # di sapere come si chiama un capitolo e finisce per inventarli dalle
        # tappe del brief — che sono un'altra cosa e in un altro ordine.
        outline = _leggi_json(os.path.join(d, "outline.json")) or []
        titoli = {
            int(v.get("numero")): (v.get("titolo_provvisorio"), v.get("tipo"))
            for v in outline
            if isinstance(v, dict) and v.get("numero") is not None
        }

        caps_raw = stato.get("capitoli", {})
        capitoli = []
        for n, e in sorted(caps_raw.items(), key=lambda kv: int(kv[0])):
            numero = int(n)
            titolo, tipo = titoli.get(numero, (None, None))
            capitoli.append(
                {
                    "numero": numero,
                    "titolo": titolo,
                    "tipo": tipo,
                    "stato": e.get("stato"),
                    "costo_usd": e.get("costo"),
                    "punti_revisione": len(e.get("problemi_revisione") or []),
                }
            )
        consegnati = sum(1 for c in capitoli if c["stato"] in ("approvato", "da_rivedere"))
        costo = (
            sum((c["costo_usd"] or 0.0) for c in capitoli)
            + float(stato.get("costo_outline") or 0.0)
            + float(stato.get("costo_coerenza") or 0.0)
        )

        completa = os.path.exists(os.path.join(d, "guida.md"))
        arresto = None
        arresto_path = os.path.join(d, "ARRESTO.txt")
        if os.path.exists(arresto_path):
            with open(arresto_path, encoding="utf-8") as f:
                arresto = f.read()

        # Rilevamento dei guasti: un crash lascia ERRORE.txt; un kill duro non
        # lascia nulla, ma il battito smette di aggiornarsi. In entrambi i casi
        # la fase diventa 'interrotta' con un messaggio comprensibile, così la
        # UI non resta a girare a vuoto per ore.
        errore = os.path.exists(os.path.join(d, "ERRORE.txt"))
        anteprima_pronta = os.path.exists(os.path.join(d, "anteprima.md"))
        # Completamento autorizzato dal pagamento: il libro intero è in scrittura.
        completamento = os.path.exists(os.path.join(d, "completamento.json"))

        # Lo stallo NON va calcolato su un assaggio in attesa di pagamento: lì il
        # silenzio è normale (il motore ha finito il suo compito), e senza questa
        # guardia dopo 25 minuti l'assaggio si trasformerebbe in "interrotta".
        stallo = False
        if (
            not completa
            and arresto is None
            and not errore
            and not (anteprima_pronta and not completamento)
        ):
            ultimo = None
            ts = (_leggi_json(os.path.join(d, "battito.json")) or {}).get("ts")
            if ts:
                try:
                    ultimo = datetime.fromisoformat(ts)
                except ValueError:
                    ultimo = None
            if ultimo is None:
                # Job avviati prima del battito: vale l'ultimo aggiornamento di stato.
                p_stato = os.path.join(d, "stato.json")
                if os.path.exists(p_stato):
                    ultimo = datetime.fromtimestamp(os.path.getmtime(p_stato), timezone.utc)
            if ultimo is not None:
                stallo = (datetime.now(timezone.utc) - ultimo).total_seconds() > 25 * 60

        if completa:
            fase = "completa"
        elif anteprima_pronta and not completamento and arresto is None and not errore:
            # Fermata voluta: l'assaggio è leggibile, il resto si sblocca pagando.
            fase = "anteprima"
        elif arresto is not None:
            fase = "interrotta"
        elif errore or stallo:
            fase = "interrotta"
            arresto = (
                "La scrittura si è fermata per un problema tecnico, non per "
                "qualcosa che avete fatto voi. I capitoli già pronti restano "
                "disponibili: potete riprovare più tardi."
            )
        else:
            fase = "in_corso"

        # Quanto aspettare prima di richiedere questo stato. Un capitolo si
        # scrive in dieci-venti minuti: chiedere ogni cinque secondi non mostra
        # nulla di nuovo, tiene sveglio un container e consuma il credito Modal
        # più della scrittura stessa. Zero significa: hai finito, smetti.
        attesa = {
            "in_coda": 5,
            "in_corso": 20,
            "anteprima": 0,
            "completa": 0,
            "interrotta": 0,
        }.get(fase, 20)

        risposta = {
            "job_id": job_id,
            "fase": fase,
            # Il client deve rispettare questo intervallo (secondi) prima della
            # prossima chiamata; 0 = stato finale, non richiamare più.
            "attesa_s": attesa,
            "capitoli": capitoli,
            "totali": {
                "consegnati": consegnati,
                "totale": len(capitoli),
                "da_rivedere": sum(1 for c in capitoli if c["stato"] == "da_rivedere"),
            },
            # Dimensione del libro finito e prezzo per sbloccarlo: noti già
            # dall'outline, quindi disponibili fin dall'assaggio (per il paywall).
            "libro": {
                "capitoli": len(capitoli),
                "prezzo_eur": prezzo_eur(len(capitoli)),
            },
        }
        # Avvisi sul brief (contraddizioni interne, residui di un altro viaggio).
        # Vanno mostrati sulla pagina dell'assaggio, dove si decide se pagare:
        # è lì che serve poter dire "abbiamo notato che...", non dopo.
        coerenza = _leggi_json(os.path.join(d, "coerenza.json")) or {}
        avvisi = [
            {
                "campo": a.get("campo"),
                "gravita": a.get("gravita"),
                "problema": a.get("problema"),
                "domanda": a.get("domanda"),
            }
            for a in (coerenza.get("avvisi") or [])
            if isinstance(a, dict)
        ]
        if avvisi:
            risposta["avvisi_brief"] = avvisi

        if completa:
            risposta["download"] = {
                "guida": f"/jobs/{job_id}/guida.md",
                "da_rivedere": f"/jobs/{job_id}/da_rivedere.md",
            }
        elif fase == "interrotta" and os.path.exists(os.path.join(d, "parziale.md")):
            # Interrotta ma con capitoli scritti: il lettore deve poterli leggere,
            # non trovare un pulsante che porta a un 404.
            risposta["download"] = {
                "parziale": f"/jobs/{job_id}/parziale.md",
                "da_rivedere": f"/jobs/{job_id}/da_rivedere.md",
            }
        elif anteprima_pronta:
            risposta["download"] = {"anteprima": f"/jobs/{job_id}/anteprima.md"}
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

    @api.get("/jobs/{job_id}/anteprima.md")
    def scarica_anteprima(job_id: str):
        return _servi_file(job_id, "anteprima.md")

    @api.get("/jobs/{job_id}/parziale.md")
    def scarica_parziale(job_id: str):
        return _servi_file(job_id, "parziale.md")

    @api.get("/jobs/{job_id}/guida.md")
    def scarica_guida(job_id: str):
        return _servi_file(job_id, "guida.md")

    @api.get("/jobs/{job_id}/da_rivedere.md")
    def scarica_da_rivedere(job_id: str):
        return _servi_file(job_id, "da_rivedere.md")

    return api
