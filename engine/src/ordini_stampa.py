"""Il libro stampato: preventivo con le date di consegna, file, ordine, stato.

Il patto col cliente è uno solo: sa PRIMA di pagare quando arriva. Il
preventivo dice, per ogni modo di spedire, la data più tarda prevista da Lulu
e se arriva prima della partenza; se non arriva in tempo, il libro resta in
PDF (o diventa il ricordo del ritorno). Il motore non decide per il cliente:
mette i numeri sul tavolo.

Cosa sta dove, nella cartella del lavoro:
    stampa/interno.pdf     la copia con l'abbondanza
    stampa/copertina.pdf   retro, dorso e fronte
    stampa/ordine.json     l'ordine: richiesta, risposta di Lulu, stati e tracking
"""

import json
import math
import os
import re
from datetime import date, datetime, timezone
from pathlib import Path

from src import config, lulu

# Il prezzo al cliente: costo di Lulu (stampa, servizio, spedizione e IVA) più
# un ricarico fisso, arrotondato in su al primo ,90 — 23,90 e non 23,28.
RICARICO_EUR = float(os.environ.get("GUIDE_STAMPA_RICARICO_EUR", "6"))


def prezzo_pulito(costo_lulu: float, ricarico: float | None = None) -> float:
    """Il primo prezzo che finisce in ,90 e non sta sotto costo più ricarico.

    In centesimi interi, per non far decidere un arrotondamento ai decimali
    binari: 17,28 + 6 = 23,28 → 23,90; 17,90 + 6 = 23,90 → 23,90; 18,95 + 6 → 25,90.
    """
    ricarico = RICARICO_EUR if ricarico is None else ricarico
    minimo = math.ceil(round((costo_lulu + ricarico) * 100, 4))
    prezzo = (minimo // 100) * 100 + 90
    if prezzo < minimo:
        prezzo += 100
    return prezzo / 100


def cartella(brief_id: str) -> Path:
    return config.output_root() / brief_id / "stampa"


def file_ordine(brief_id: str) -> Path:
    return cartella(brief_id) / "ordine.json"


def leggi_ordine(brief_id: str) -> dict | None:
    try:
        return json.loads(file_ordine(brief_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def _scrivi_ordine(brief_id: str, dati: dict) -> None:
    p = file_ordine(brief_id)
    p.parent.mkdir(parents=True, exist_ok=True)
    tmp = p.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(dati, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, p)


def conta_pagine(pdf: Path) -> int:
    """Le pagine di un PDF. WeasyPrint comprime la struttura: serve un lettore vero."""
    from pypdf import PdfReader

    return len(PdfReader(str(pdf)).pages)


def pagine_da_stampare(libro_pdf: Path, con_copertina: bool = False) -> int:
    """Le pagine della copia di stampa: quelle del libro, arrotondate al pari.

    L'abbondanza allarga il foglio ma non tocca il blocco del testo, quindi
    l'impaginazione è la stessa: per il preventivo non serve rifare il PDF.
    """
    n = conta_pagine(libro_pdf) - (1 if con_copertina else 0)  # la copertina sul libro stampato è a parte
    return n + (n % 2)


def _euro(v) -> float:
    return round(float(v), 2)


def preventivo(brief, libro_pdf: Path, indirizzo: dict) -> dict:
    """Per ogni modo di spedire: quanto costa, quando arriva, se arriva in tempo."""
    lulu.indirizzo_pulito(indirizzo)  # un indirizzo incompleto si dice subito, senza chiedere a Lulu
    from src.scelta_copertina import progetto_copertina

    pagine = pagine_da_stampare(libro_pdf, con_copertina=progetto_copertina(brief.brief_id) is not None)
    if pagine < lulu.PAGINE_MIN:
        raise ValueError(f"Il libro ha {pagine} pagine: per la brossura ne servono almeno {lulu.PAGINE_MIN}.")
    partenza = brief.date.inizio
    opzioni = []
    for s in lulu.spedizioni(indirizzo, pagine):
        livello = s.get("level")
        try:
            c = lulu.costo(indirizzo, pagine, livello)
        except lulu.ErroreLulu as exc:
            print(f"Preventivo {livello}: {exc}")
            continue
        totale = _euro(c["total_cost_incl_tax"])
        arrivo_max = s.get("max_delivery_date")
        in_tempo = None
        if partenza and arrivo_max:
            # Un giorno di margine: il libro arriva il giorno prima, non la mattina della partenza.
            in_tempo = date.fromisoformat(str(arrivo_max)[:10]) < partenza
        opzioni.append({
            "livello": livello,
            "consegna_min": str(s.get("min_delivery_date") or "")[:10] or None,
            "consegna_max": str(arrivo_max or "")[:10] or None,
            "tracciabile": bool(s.get("traceable")),
            "costo_lulu_eur": totale,
            "prezzo_eur": prezzo_pulito(totale),
            "in_tempo": in_tempo,
        })
    opzioni.sort(key=lambda o: o["prezzo_eur"])
    return {"pagine": pagine, "prodotto": lulu.PRODOTTO, "ambiente": lulu.ambiente(),
            "partenza": partenza.isoformat() if partenza else None, "opzioni": opzioni}


def prepara_file(brief, libro: dict) -> dict:
    """Interno con l'abbondanza e copertina col dorso che dice Lulu."""
    from src.copertina import scrivi_copertina
    from src.pdf import scrivi_pdf_stampa

    d = cartella(brief.brief_id)
    interno, pagine = scrivi_pdf_stampa(brief, libro, d / "interno.pdf")
    dorso = lulu.dorso_mm(pagine)
    from src.scelta_copertina import progetto_copertina

    progetto = progetto_copertina(brief.brief_id)
    if progetto:  # la copertina illustrata, se il libro ne ha una; altrimenti quella con la mappa
        from src.copertina_illustrata import scrivi_copertina as scrivi_illustrata

        copertina = scrivi_illustrata(brief, libro, dorso, progetto, d / "copertina.pdf")
    else:
        copertina = scrivi_copertina(brief, libro, dorso, d / "copertina.pdf")
    return {"interno": str(interno), "copertina": str(copertina), "pagine": pagine, "dorso_mm": dorso}


def ordina(brief, libro: dict, richiesta: dict, url_file, pubblica=lambda: None) -> dict:
    """Prepara i file e crea l'ordine su Lulu. Lo stesso ordine due volte non si stampa due volte.

    `richiesta`: {ordine_id, indirizzo, livello, email_contatto}. `url_file(nome)`
    dà l'indirizzo firmato da cui Lulu scarica interno e copertina.
    `pubblica()`: rende i file visibili a chi li servirà (il commit della Volume),
    PRIMA che l'ordine parta — Lulu li scarica subito.
    """
    esistente = leggi_ordine(brief.brief_id) or {}
    if esistente.get("ordine_id") == richiesta["ordine_id"] and esistente.get("lulu", {}).get("id"):
        print(f"Stampa {brief.brief_id}: ordine {richiesta['ordine_id']} già creato, non lo ripeto.")
        return esistente
    lulu.indirizzo_pulito(richiesta["indirizzo"])  # un indirizzo rotto si scopre prima dei file
    email = (richiesta.get("email_contatto") or config.STAMPA_EMAIL or "").strip()
    if not re.fullmatch(r"[^@\s]+@[^@\s]+\.[^@\s]+", email):
        raise ValueError("Email di contatto per Lulu mancante o non valida: mettine una vera in "
                         "GUIDE_STAMPA_EMAIL nel secret lulu-secret (è a lei che Lulu scrive se "
                         "un ordine ha un problema).")
    richiesta = {**richiesta, "email_contatto": email}
    file = prepara_file(brief, libro)
    pubblica()
    from src.pdf import titolo_libro

    riferimento = f"{brief.brief_id}.{richiesta['ordine_id']}"
    risposta = lulu.crea_ordine(
        titolo=titolo_libro(brief, libro), url_interno=url_file("interno.pdf"),
        url_copertina=url_file("copertina.pdf"), indirizzo=richiesta["indirizzo"],
        livello=richiesta["livello"], email_contatto=richiesta.get("email_contatto") or config.STAMPA_EMAIL,
        riferimento=riferimento)
    dati = {"ordine_id": richiesta["ordine_id"], "livello": richiesta["livello"],
            "creato_il": datetime.now(timezone.utc).isoformat(), "file": file,
            "lulu": {"id": risposta.get("id"), "stato": (risposta.get("status") or {}).get("name"),
                     "ambiente": lulu.ambiente()},
            "stati": [{"nome": (risposta.get("status") or {}).get("name"),
                       "quando": datetime.now(timezone.utc).isoformat()}]}
    _scrivi_ordine(brief.brief_id, dati)
    return dati


def aggiorna_da_lulu(lavoro: dict) -> tuple[str, dict] | None:
    """Un webhook (o una lettura) di Lulu dentro ordine.json. Ritorna (job_id, ordine)."""
    riferimento = str(lavoro.get("external_id") or "")
    job_id = riferimento.split(".", 1)[0]
    if not re.fullmatch(r"[A-Za-z0-9_-]{1,80}", job_id or ""):
        return None
    ordine = leggi_ordine(job_id)
    if not ordine:
        return None
    stato = lavoro.get("status") or {}
    nome = stato.get("name")
    ordine["lulu"]["stato"] = nome
    ordine.setdefault("stati", []).append({"nome": nome, "quando": stato.get("changed")
                                           or datetime.now(timezone.utc).isoformat(),
                                           "messaggio": stato.get("message")})
    tracking = []
    for li in stato.get("line_item_statuses") or []:
        m = li.get("messages") or {}
        for url in m.get("tracking_urls") or []:
            tracking.append({"url": url, "corriere": m.get("carrier_name"), "codice": m.get("tracking_id")})
    if tracking:
        ordine["tracking"] = tracking
    stime = lavoro.get("estimated_shipping_dates") or {}
    if stime:
        ordine["consegna_stimata"] = {"min": stime.get("arrival_min"), "max": stime.get("arrival_max")}
    _scrivi_ordine(job_id, ordine)
    return job_id, ordine


def ordine_pubblico(ordine: dict | None) -> dict:
    """Quello che il sito può mostrare: niente indirizzo, niente percorsi interni."""
    if not ordine:
        return {"ordine": None}
    return {"ordine": {
        "stato": (ordine.get("lulu") or {}).get("stato"),
        "livello": ordine.get("livello"),
        "creato_il": ordine.get("creato_il"),
        "consegna_stimata": ordine.get("consegna_stimata"),
        "tracking": ordine.get("tracking") or [],
    }}
