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


def _impronta(v: str) -> str:
    """Le prime otto cifre dello sha256: dice se due segreti sono lo STESSO.

    La lunghezza non basta — due segreti diversi lunghi uguale sono il caso
    normale, non l'eccezione. L'impronta si confronta, non si inverte, quindi
    puo' stare nei log. E' calcolata esattamente come la calcola il sito
    (sha256 utf-8, primi 8 caratteri esadecimali), cosi' le due righe si
    leggono affiancate: impronte uguali = stesso segreto, e il problema e'
    altrove.
    """
    import hashlib

    return hashlib.sha256(v.encode("utf-8")).hexdigest()[:8]


def _url_evento() -> str:
    """L'indirizzo a cui bussare quando un lavoro cambia fase.

    Era un percorso cucito nel codice sotto SITO_BASE_URL, ed e' esattamente
    la trappola in cui siamo caduti: se la porta non abita su quel dominio,
    l'unico modo di spostarla era un redeploy. Con SITO_EVENTO_URL si punta a
    un indirizzo qualsiasi — tipico il caso di una funzione Supabase, che vive
    su https://<ref>.supabase.co/functions/v1/<nome> e non sul dominio del
    sito — cambiando un secret su Modal.
    """
    import os

    esplicito = (os.environ.get("SITO_EVENTO_URL") or "").strip()
    if esplicito:
        return esplicito
    base = (os.environ.get("SITO_BASE_URL") or "").strip().rstrip("/")
    return f"{base}/api/public/motore-evento" if base else ""


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
    import urllib.error
    import urllib.request

    url = _url_evento()
    segreto = (os.environ.get("GATE_SECRET") or "").strip()
    if not url:
        print(
            f"avviso al sito saltato ({fase}): ne' SITO_EVENTO_URL ne' SITO_BASE_URL impostati",
            file=sys.stderr,
        )
        return
    if not segreto:
        print(f"avviso al sito saltato ({fase}): GATE_SECRET non impostato", file=sys.stderr)
        return

    # Il nostro segreto viaggia sempre in X-Gate-Secret. Authorization e'
    # ambiguo: se davanti c'e' il gateway di Supabase, quell'header lo legge lui
    # e si aspetta la chiave anon, non il nostro segreto — glielo diamo solo se
    # ce l'hanno detto (SITO_EVENTO_APIKEY). Altrimenti resta il ripiego per le
    # piattaforme che filtrano gli header non standard.
    apikey = (os.environ.get("SITO_EVENTO_APIKEY") or "").strip()
    headers = {
        "Content-Type": "application/json",
        "X-Gate-Secret": segreto,
        # Senza uno User-Agent da browser molte protezioni di frontiera
        # rispondono 403 a "Python-urllib" senza mai passare la richiesta
        # all'app.
        "User-Agent": "AtelierDelViaggio-Motore/1.0",
        "Accept": "application/json",
    }
    if apikey:
        headers["apikey"] = apikey
        headers["Authorization"] = f"Bearer {apikey}"
    else:
        headers["Authorization"] = f"Bearer {segreto}"

    richiesta = urllib.request.Request(
        url,
        data=json.dumps({"job_id": job_id, "fase": fase}).encode("utf-8"),
        headers=headers,
        method="POST",
    )
    try:
        risposta = urllib.request.urlopen(richiesta, timeout=20)
        print(f"avviso al sito ({fase}): {risposta.status}")
    except urllib.error.HTTPError as exc:
        try:
            corpo = exc.read().decode("utf-8", "replace")[:400]
        except Exception:
            corpo = "(corpo non leggibile)"
        print(
            f"avviso al sito RIFIUTATO ({fase}): HTTP {exc.code} da {url}\n"
            f"  server: {exc.headers.get('server', '?')}\n"
            f"  content-type: {exc.headers.get('content-type', '?')}\n"
            f"  segreto inviato: impronta {_impronta(segreto)} / {len(segreto)} caratteri"
            f"{' + apikey ' + str(len(apikey)) + ' caratteri' if apikey else ''}\n"
            f"  corpo: {corpo}",
            file=sys.stderr,
        )
        _controprova(url, exc.code, corpo)
    except Exception as exc:
        print(f"avviso al sito non riuscito ({fase}): {type(exc).__name__}: {exc}", file=sys.stderr)


def _controprova(url: str, codice: int, corpo: str) -> None:
    """Dice se la porta esiste, bussando a una che di sicuro non esiste.

    Distinguere «l'app ci ha respinti» da «la richiesta non e' mai arrivata
    all'app» guardando solo la risposta e' indovinare: un 403 con scritto
    "Forbidden" lo produce sia un controllo del segreto scritto male sia una
    piattaforma che non ha nessun gestore per quel percorso. La differenza si
    misura, e costa una richiesta sola: si bussa a un indirizzo inventato,
    senza segreto. Se risponde IDENTICO, non stiamo parlando con la nostra
    app — su quel dominio non c'e' nessuna porta, e il segreto e' innocente.
    """
    import sys
    import urllib.error
    import urllib.request

    finto = url.rsplit("/", 1)[0] + "/questa-porta-non-esiste-mai"
    try:
        r = urllib.request.urlopen(
            urllib.request.Request(
                finto,
                data=b"{}",
                headers={"Content-Type": "application/json", "User-Agent": "AtelierDelViaggio-Motore/1.0"},
                method="POST",
            ),
            timeout=20,
        )
        codice_finto, corpo_finto = r.status, r.read().decode("utf-8", "replace")[:200]
    except urllib.error.HTTPError as exc:
        codice_finto = exc.code
        try:
            corpo_finto = exc.read().decode("utf-8", "replace")[:200]
        except Exception:
            corpo_finto = ""
    except Exception as exc:
        print(f"  controprova non eseguita: {type(exc).__name__}: {exc}", file=sys.stderr)
        return

    uguale = codice_finto == codice and corpo_finto.strip()[:200] == corpo.strip()[:200]
    print(
        f"  controprova su un indirizzo inventato: HTTP {codice_finto} {corpo_finto!r}\n"
        f"  verdetto: "
        + (
            "IDENTICA alla nostra — la porta non esiste su questo dominio, "
            "la richiesta non raggiunge mai l'app e il segreto non c'entra"
            if uguale
            else "DIVERSA dalla nostra — la porta esiste e ci ha respinti davvero: "
            "il problema e' il segreto o il modo in cui lo leggiamo"
        ),
        file=sys.stderr,
    )


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
    # Impaginazione del PDF: WeasyPrint disegna il testo attraverso Pango, che è
    # una libreria di sistema. E senza i caratteri Noto, un nome giapponese come
    # 新幹線 esce nel libro come una fila di rettangoli vuoti.
    .apt_install(
        "libpango-1.0-0",
        "libpangoft2-1.0-0",
        "libharfbuzz0b",
        "libffi8",
        "libjpeg62-turbo",
        "shared-mime-info",
        "fonts-noto-core",
        "fonts-noto-cjk",
    )
    .pip_install(
        "anthropic>=0.40",
        "pydantic>=2.7",
        "fastapi[standard]",
        "weasyprint>=66",
        "markdown>=3.6",
    )
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
    # Quanto dura davvero: un capitolo costa 15-20 minuti (venti-quaranta
    # andate e ritorni col modello, ognuna con le sue ricerche, tutte in fila).
    # Un libro da 14 capitoli sono quindi 4-5 ore, e il tetto di 2 ore che c'era
    # prima lo tagliava a metà — puntualmente, verso il settimo capitolo, senza
    # lasciare traccia se non un input cancellato. Sei ore danno margine anche a
    # un libro lungo con qualche ritentativo.
    timeout=6 * 60 * 60,
    cpu=1.0,
    memory=2048,
)
def genera_guida(
    brief_dict: dict,
    job_id: str,
    tetto_usd: float | None = None,
    anteprima: bool = False,
    effort: str | None = None,
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
    # Sforzo del singolo run: serve a confrontare due configurazioni sullo
    # stesso brief senza ridiployare e senza toccare il secret — cioè senza il
    # rischio di credere di star provando 'medium' mentre un container caldo
    # sta ancora girando a 'high'. Il motore lo rilegge a ogni chiamata.
    if effort:
        os.environ["GUIDE_EFFORT_GENERAZIONE"] = str(effort)

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
               "modo": "intake"|"correzione", "avvisi": [{...}], "impronta": "a1b2c3d4"}
        Ritorna {"azione": "domanda"|"fine", "messaggio": "...",
        "opzioni": ["..."], "brief": {...}|null, "impronta": "a1b2c3d4"}.
        `opzioni` sono risposte brevi tappabili (chip) che accompagnano una
        domanda; [] quando non servono o alla chiusura. A ogni turno il frontend
        accoda la domanda dell'AI e la risposta dell'utente in `messaggi` e
        richiama; a "fine" usa il brief arricchito.

        `impronta` dice DI QUALE viaggio è la conversazione. Il motore la
        restituisce a ogni turno; il frontend la rimanda al turno dopo. Se non
        corrisponde alle destinazioni della scheda, la conversazione è il residuo
        di un altro viaggio e il motore la scarta invece di continuarla — è
        successo davvero: una scheda dell'Umbria con sopra l'intervista del
        Giappone. Campo facoltativo: chi non lo manda si comporta come prima.
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
        impronta = payload.get("impronta")
        impronta = impronta.strip() if isinstance(impronta, str) else None
        from src.intervista import passo_intervista

        return passo_intervista(brief, messaggi, modo, avvisi, impronta)

    @api.post("/prova-avviso")
    def prova_avviso(request: Request, payload: dict | None = None):
        """Manda al sito un avviso finto, per provare il ciclo delle email.

        Finora l'unico modo di verificare l'avviso motore→sito era scrivere un
        capitolo e aspettare: un dollaro e venti minuti per sapere se un header
        arriva a destinazione. Qui l'avviso parte subito, con la stessa identica
        funzione usata in produzione, e la diagnosi finisce nei log.

        body opzionale: {"job_id": "...", "fase": "anteprima"|"completa"|"interrotta"}
        """
        _controlla_porta(request)
        dati = payload if isinstance(payload, dict) else {}
        job_id = str(dati.get("job_id") or "prova")
        fase = str(dati.get("fase") or "anteprima")
        _avvisa_sito(job_id, fase)
        return {
            "ok": True,
            "inviato_a": _url_evento() or None,
            "fase": fase,
            "nota": "L'esito e' nei log di Modal: cerca 'avviso al sito'.",
        }

    @api.post("/coerenza")
    def coerenza(payload: dict, request: Request):
        """Controlla una scheda PRIMA di scrivere, e ritorna i punti che non tornano.

        body: {"brief": {...}} → {"avvisi": [{campo, gravita, problema, domanda}]}

        Esiste per una ragione di sequenza. Lo stesso controllo gira anche dentro
        la generazione, ma lì è tardi: quando gli avvisi compaiono, il motore ha
        già cominciato a scrivere sulla scheda sbagliata. Chiamato qui — dopo il
        form e l'intervista, prima di /generate — il viaggiatore corregge le date
        mentre correggere non costa ancora niente.

        Costa millesimi di dollaro e una decina di secondi. Se fallisce, ritorna
        una lista vuota: non deve mai impedire di partire.
        """
        _controlla_porta(request)
        if not isinstance(payload, dict):
            raise HTTPException(status_code=400, detail="Payload non valido.")
        dato = payload.get("brief") or {}
        if not isinstance(dato, dict) or not dato:
            raise HTTPException(status_code=400, detail="Brief mancante o non valido.")

        from schema.brief import Brief
        from src.coerenza import verifica_coerenza

        dato = dict(dato)
        dato.setdefault("brief_id", "controllo")
        try:
            brief = Brief.model_validate(dato)
        except Exception as exc:
            raise HTTPException(status_code=400, detail=f"Scheda non valida: {exc}")
        esito = verifica_coerenza(brief)
        return {"avvisi": esito.get("avvisi") or []}

    @api.post("/generate")
    def generate(brief: dict, request: Request):
        """Avvia una generazione. Ritorna il job_id per il polling.

        Campi opzionali nel body, estratti e non passati al motore come parte
        del brief:

        * `_tetto_usd` — budget di spesa del run (test a basso costo).
        * `_effort` — sforzo di scrittura per QUESTO run (low|medium|high|
          xhigh|max). Serve a confrontare due configurazioni sullo stesso brief
          senza ridiployare.
        """
        _controlla_porta(request)
        if not isinstance(brief, dict) or not brief:
            raise HTTPException(status_code=400, detail="Brief mancante o non valido.")
        brief = dict(brief)
        tetto_usd = brief.pop("_tetto_usd", None)
        effort = brief.pop("_effort", None)
        brief.pop("_anteprima", None)  # ignorato di proposito, vedi sotto
        job_id = uuid.uuid4().hex[:12]
        # Da questa porta esce SOLO l'assaggio, qualunque cosa venga chiesta.
        # Il libro intero (~17 $) si scrive unicamente da /completa, che richiede
        # il segreto ed è chiamato solo dal webhook del pagamento. Così la via
        # costosa è irraggiungibile per costruzione, e non dipende dal fatto che
        # il codice del sito ricordi di chiedere l'assaggio.
        genera_guida.spawn(brief, job_id, tetto_usd, True, effort)
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

    @api.get("/jobs/{job_id}/libro.pdf")
    def scarica_pdf(job_id: str):
        """Il libro impaginato: A5, testatine, capolettera, box, segnalibri.

        Se il PDF non è ancora stato prodotto (libri finiti prima che esistesse,
        o impaginazione fallita in coda alla scrittura) lo si compone qui, in
        una cartella temporanea: la Volume può essere in mano a una scrittura in
        corso e scaricare un libro non deve mai disturbarla.
        """
        from fastapi.responses import FileResponse

        output_volume.reload()
        d = _job_dir(job_id)

        # Il nome del file è il titolo del libro: nella cartella Download deve
        # comparire «Il Giappone in quattro (poi in due).pdf», non il nome della
        # ditta che l'ha stampato.
        def _nome(fallback: str = "Il vostro libro.pdf") -> str:
            try:
                from src.pdf import nome_file, titolo_libro

                libro_json = _leggi_json(os.path.join(d, "libro.json"))
                if libro_json:
                    from schema.brief import Brief

                    b = dict(_leggi_json(os.path.join(d, "brief.json")) or {})
                    b["brief_id"] = job_id
                    return nome_file(titolo_libro(Brief.model_validate(b), libro_json))
            except Exception:
                pass
            return fallback

        path = os.path.join(d, "libro.pdf")
        if os.path.exists(path):
            return FileResponse(path, media_type="application/pdf", filename=_nome())

        brief_dict = _leggi_json(os.path.join(d, "brief.json"))
        stato = _leggi_json(os.path.join(d, "stato.json"))
        if not brief_dict or not stato:
            raise HTTPException(status_code=404, detail="Nessun libro da impaginare.")
        try:
            import tempfile
            from pathlib import Path

            from schema.brief import Brief
            from src.guide_runner import costruisci_libro
            from src.outline import carica_outline
            from src.pdf import scrivi_pdf

            brief_dict = dict(brief_dict)
            brief_dict["brief_id"] = job_id
            brief = Brief.model_validate(brief_dict)
            assignments = carica_outline(brief)
            if not assignments:
                raise HTTPException(status_code=404, detail="Nessun libro da impaginare.")
            libro = costruisci_libro(brief, assignments, stato)
            if not libro["capitoli"]:
                raise HTTPException(status_code=404, detail="Nessun libro da impaginare.")
            from src.pdf import nome_file, titolo_libro

            temporaneo = os.path.join(tempfile.mkdtemp(), "libro.pdf")
            scrivi_pdf(brief, libro, Path(temporaneo))
            return FileResponse(
                temporaneo,
                media_type="application/pdf",
                filename=nome_file(titolo_libro(brief, libro)),
            )
        except HTTPException:
            raise
        except Exception as exc:
            print(f"Job {job_id}: impaginazione non riuscita: {exc}", file=sys.stderr)
            raise HTTPException(status_code=404, detail="Nessun libro da impaginare.")

    @api.get("/jobs/{job_id}/libro.json")
    def scarica_libro(job_id: str):
        """Il libro con la sua struttura: capitoli, sezioni, corpo in markdown.

        Da preferire sempre a guida.md per mostrare il libro: quel file e'
        prosa, e ricavarne l'indice contando i cancelletti mescola i capitoli
        con le loro sezioni.
        """
        output_volume.reload()
        d = _job_dir(job_id)
        path = os.path.join(d, "libro.json")
        if os.path.exists(path):
            with open(path, encoding="utf-8") as f:
                return json.load(f)

        # Il file non c'è: lo si ricostruisce dai capitoli, che ci sono. Vale per
        # i libri finiti prima che questo formato esistesse — sarebbero rimasti
        # illeggibili per sempre, con la pagina che dice "il libro non è ancora
        # pronto" davanti a tredici capitoli scritti e pagati. Si costruisce in
        # memoria e non si scrive: la Volume può essere in mano a una scrittura
        # in corso, e leggere un libro non deve mai disturbarla.
        brief_dict = _leggi_json(os.path.join(d, "brief.json"))
        stato = _leggi_json(os.path.join(d, "stato.json"))
        if not brief_dict or not stato:
            raise HTTPException(status_code=404, detail="Nessun libro da leggere.")
        try:
            from schema.brief import Brief
            from src.guide_runner import costruisci_libro
            from src.outline import carica_outline

            brief_dict = dict(brief_dict)
            brief_dict["brief_id"] = job_id
            brief = Brief.model_validate(brief_dict)
            assignments = carica_outline(brief)
            if not assignments:
                raise HTTPException(status_code=404, detail="Nessun libro da leggere.")
            return costruisci_libro(brief, assignments, stato)
        except HTTPException:
            raise
        except Exception as exc:
            print(f"Job {job_id}: libro.json non ricostruibile: {exc}", file=sys.stderr)
            raise HTTPException(status_code=404, detail="Nessun libro da leggere.")

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
