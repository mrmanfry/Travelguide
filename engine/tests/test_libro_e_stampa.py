"""Il libro in PDF e il libro stampato: copertina, pagine, preventivo, ordine, stato."""

import hashlib
import hmac
import json
from pathlib import Path

import pypdf
import pytest

from src import lulu
from src import ordini_stampa as o
from src import scelta_copertina as sc
from src.pdf import scrivi_pdf

INDIRIZZO = {"name": "Mario Rossi", "street1": "Via Nazionale 1", "city": "Roma", "postcode": "00184",
             "state_code": "RM", "country_code": "it", "phone_number": "+39 06 1234567", "email": "x@y"}


def mm(pagina):
    return [round(float(v) / 72 * 25.4, 1) for v in pagina.mediabox[2:]]


@pytest.fixture
def con_copertina(uscita, brief):
    assert sc.prepara_copertina(brief, {"capitoli": []}) is not None  # Lisbona → Torre di Belém, scelta del motore
    return brief


def test_pdf_comincia_con_la_copertina(con_copertina, libro, tmp_path):
    r = pypdf.PdfReader(str(scrivi_pdf(con_copertina, libro, tmp_path / "libro.pdf")))
    assert [round(x) for x in mm(r.pages[0])] == [148, 210]
    assert "LISBONA" in r.pages[0].extract_text().replace(" ", "").upper()
    assert len(r.outline) >= 3  # i segnalibri sopravvivono all'aggiunta della copertina


@pytest.mark.parametrize("costo, prezzo", [(17.28, 23.90), (17.90, 23.90), (18.95, 25.90), (0.0, 6.90)])
def test_prezzo_pulito(costo, prezzo):
    assert o.prezzo_pulito(costo, ricarico=6) == prezzo


def test_firma_dei_webhook_lulu(monkeypatch):
    monkeypatch.setenv("LULU_CLIENT_KEY", "chiave-di-prova")
    monkeypatch.setenv("LULU_CLIENT_SECRET", "segreto-di-prova")
    corpo = b'{"topic":"PRINT_JOB_STATUS_CHANGED","data":{}}'
    f = hmac.new(b"segreto-di-prova", corpo, hashlib.sha256).hexdigest()
    assert lulu.firma_valida(corpo, f)
    assert not lulu.firma_valida(corpo, "00" * 32) and not lulu.firma_valida(corpo, "")
    assert not lulu.firma_valida(corpo + b" ", f)


def test_indirizzo_pulito():
    pulito = lulu.indirizzo_pulito(INDIRIZZO)
    assert pulito["country_code"] == "IT" and "email" not in pulito
    with pytest.raises(ValueError):
        lulu.indirizzo_pulito({"name": "x"})


def _lulu_finto(monkeypatch, creati):
    monkeypatch.setattr(lulu, "spedizioni", lambda ind, p: [
        {"level": "MAIL", "min_delivery_date": "2026-09-25", "max_delivery_date": "2026-10-02", "traceable": False},
        {"level": "EXPRESS", "min_delivery_date": "2026-09-08", "max_delivery_date": "2026-09-10", "traceable": True}])
    monkeypatch.setattr(lulu, "costo", lambda ind, p, liv: {"total_cost_incl_tax": {"MAIL": "11.04", "EXPRESS": "31.50"}[liv]})
    monkeypatch.setattr(lulu, "dorso_mm", lambda p: 5.4)
    monkeypatch.setattr(lulu, "crea_ordine", lambda **k: (creati.append(k), {"id": 4242, "status": {"name": "CREATED"}})[1])


def test_preventivo_date_e_pagine(con_copertina, monkeypatch, tmp_path):
    _lulu_finto(monkeypatch, [])
    monkeypatch.setattr(o, "conta_pagine", lambda pdf: 85)  # 84 pagine di libro + la copertina
    prev = o.preventivo(con_copertina, tmp_path / "libro.pdf", INDIRIZZO)
    assert prev["pagine"] == 84  # la copertina del PDF non si stampa come pagina interna
    # Partenza il 18 settembre: arriva in tempo solo l'EXPRESS.
    assert {x["livello"]: x["in_tempo"] for x in prev["opzioni"]} == {"MAIL": False, "EXPRESS": True}
    assert all(str(x["prezzo_eur"]).endswith(".9") for x in prev["opzioni"])


def test_preventivo_libro_troppo_corto(con_copertina, monkeypatch, tmp_path):
    _lulu_finto(monkeypatch, [])
    monkeypatch.setattr(o, "conta_pagine", lambda pdf: 20)
    with pytest.raises(ValueError):
        o.preventivo(con_copertina, tmp_path / "libro.pdf", INDIRIZZO)


def test_ordine_file_idempotenza_e_stato(con_copertina, libro, monkeypatch):
    creati, pubblicato = [], []
    _lulu_finto(monkeypatch, creati)
    b = con_copertina
    ric = {"ordine_id": "cs_test_1", "indirizzo": INDIRIZZO, "livello": "EXPRESS", "email_contatto": "noi@esempio.it"}
    d = o.ordina(b, libro, ric, lambda nome: f"https://motore/stampa-file/{b.brief_id}/{nome}?firma=x",
                 lambda: pubblicato.append(1))
    assert pubblicato == [1] and len(creati) == 1 and creati[0]["riferimento"] == f"{b.brief_id}.cs_test_1"
    assert Path(d["file"]["interno"]).exists() and Path(d["file"]["copertina"]).exists()
    assert d["file"]["pagine"] % 2 == 0
    # La copertina di stampa è distesa (retro, dorso, fronte) con l'abbondanza.
    w, h = mm(pypdf.PdfReader(d["file"]["copertina"]).pages[0])
    assert abs(w - (2 * 148 + 5.4 + 2 * 3.175)) < 0.6 and abs(h - (210 + 2 * 3.175)) < 0.6
    # Lo stesso ordine due volte non si stampa due volte.
    o.ordina(b, libro, ric, lambda n: "u", lambda: None)
    assert len(creati) == 1
    # Lo stato arriva da Lulu e il sito vede solo quello che può mostrare.
    job, ordine = o.aggiorna_da_lulu({
        "external_id": f"{b.brief_id}.cs_test_1",
        "status": {"name": "SHIPPED", "changed": "2026-09-10T10:00:00Z", "line_item_statuses": [
            {"messages": {"tracking_urls": ["https://track/1"], "carrier_name": "DHL", "tracking_id": "T1"}}]},
        "estimated_shipping_dates": {"arrival_min": "2026-09-12", "arrival_max": "2026-09-14"}})
    pub = o.ordine_pubblico(ordine)
    assert job == b.brief_id and pub["ordine"]["stato"] == "SHIPPED" and pub["ordine"]["tracking"][0]["corriere"] == "DHL"
    assert "Mario" not in json.dumps(pub)
    assert o.aggiorna_da_lulu({"external_id": "../etc.x"}) is None


def test_ordine_senza_email_valida(con_copertina, libro, monkeypatch):
    _lulu_finto(monkeypatch, [])
    monkeypatch.setattr(o.config, "STAMPA_EMAIL", "")
    with pytest.raises(ValueError):
        o.ordina(con_copertina, libro, {"ordine_id": "x", "indirizzo": INDIRIZZO, "livello": "MAIL",
                                         "email_contatto": "non-una-email"}, lambda n: "u")
