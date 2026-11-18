"""L'app web del motore (modal_app.web), fuori da Modal: Volume, spawn e Lulu finti.

Si salta da sola se modal o fastapi non sono installati.
"""

import hashlib
import hmac
import json
import time
import types
from urllib.parse import parse_qs

import pytest

pytest.importorskip("modal")
pytest.importorskip("fastapi")
from fastapi.testclient import TestClient  # noqa: E402

from tests.conftest import brief_dict  # noqa: E402

GATE = "segreto-della-porta"
PORTA = {"X-Gate-Secret": GATE}
JOB = "abc123def456"
INDIRIZZO = {"name": "Mario Rossi", "street1": "Via Nazionale 1", "city": "Roma", "postcode": "00184",
             "state_code": "RM", "country_code": "IT", "phone_number": "+39 06 1234567"}


@pytest.fixture
def motore(tmp_path, monkeypatch, uscita):
    """Costruisce l'app con le variabili date: torna (client, modulo, cartella del lavoro, spawn)."""
    import modal_app as m

    def fai(letture_chiuse=False):
        monkeypatch.setenv("GATE_SECRET", GATE)
        monkeypatch.setenv("LULU_CLIENT_KEY", "k")
        monkeypatch.setenv("LULU_CLIENT_SECRET", "segreto-lulu")
        if letture_chiuse:
            monkeypatch.setenv("GATE_LETTURE", "1")
        else:
            monkeypatch.delenv("GATE_LETTURE", raising=False)
        monkeypatch.setattr(m, "OUTPUT_ROOT", str(uscita))
        monkeypatch.setattr(m, "_prepara_ambiente", lambda: None)
        monkeypatch.setattr(m, "output_volume", types.SimpleNamespace(reload=lambda: None, commit=lambda: None))
        spawn = []
        monkeypatch.setattr(m, "ordina_stampa", types.SimpleNamespace(
            spawn=lambda *a: (spawn.append(a), types.SimpleNamespace(object_id="fc-prova"))[1]))
        from src import lulu
        monkeypatch.setattr(lulu, "spedizioni", lambda ind, p: [
            {"level": "MAIL", "min_delivery_date": "2026-09-01", "max_delivery_date": "2026-09-08"}])
        monkeypatch.setattr(lulu, "costo", lambda ind, p, liv: {"total_cost_incl_tax": "11.04"})
        d = uscita / JOB
        d.mkdir(exist_ok=True)
        (d / "brief.json").write_text(json.dumps(brief_dict(JOB)), encoding="utf-8")
        raw = getattr(m.web, "_raw_f_", None) or m.web.info.raw_f
        return TestClient(raw()), m, d, spawn

    return fai


def test_health_dice_lo_stato_della_porta(motore):
    c, *_ = motore(letture_chiuse=True)
    assert c.get("/health").json() == {"ok": True, "porta": "attiva", "letture": "chiuse"}


@pytest.mark.parametrize("percorso", ["/generate", "/intervista", "/coerenza", f"/jobs/{JOB}/completa", f"/jobs/{JOB}/mappe"])
def test_chiamate_che_spendono_chiedono_la_porta(motore, percorso):
    c, *_ = motore()
    assert c.post(percorso, json={}).status_code == 403
    assert c.post(percorso, json={}, headers={"X-Gate-Secret": "sbagliato"}).status_code == 403


LETTURE = [f"/jobs/{JOB}", f"/jobs/{JOB}/libro.pdf", f"/jobs/{JOB}/libro.json", f"/jobs/{JOB}/guida.md",
           f"/jobs/{JOB}/stampa"]


@pytest.mark.parametrize("percorso", LETTURE)
def test_letture_chiuse(motore, percorso):
    c, *_ = motore(letture_chiuse=True)
    assert c.get(percorso).status_code == 403
    assert c.get(percorso, headers=PORTA).status_code != 403


def test_letture_aperte_finche_non_si_chiude(motore):
    c, *_ = motore(letture_chiuse=False)
    assert all(c.get(p).status_code != 403 for p in LETTURE)


def test_link_firmato(motore):
    c, *_ = motore(letture_chiuse=True)
    assert c.post(f"/jobs/{JOB}/link").status_code == 403
    link = c.post(f"/jobs/{JOB}/link", headers=PORTA, json={"minuti": 5}).json()
    assert link["pdf"].startswith("https://") and f"/jobs/{JOB}/libro.pdf?" in link["pdf"]
    q = link["query"]
    assert c.get(f"/jobs/{JOB}?{q}").status_code != 403
    assert c.get(f"/jobs/altro0000000?{q}").status_code == 403  # vale solo per quel libro
    scaduto = int(time.time()) - 5
    firma = parse_qs(q)["firma"][0]
    assert c.get(f"/jobs/{JOB}?scade={scaduto}&firma={firma}").status_code == 403
    # Un 403 della porta porta comunque le intestazioni CORS: il browser mostra il 403.
    r = c.get(f"/jobs/{JOB}/libro.pdf", headers={"Origin": "https://sito.example"})
    assert r.status_code == 403 and r.headers.get("access-control-allow-origin") == "*"


def test_stampa_preventivo_ordine_file_e_webhook(motore, libro, monkeypatch):
    c, m, d, spawn = motore()
    assert c.post(f"/jobs/{JOB}/stampa/preventivo", json={"indirizzo": INDIRIZZO}).status_code == 403
    assert c.post(f"/jobs/{JOB}/stampa/preventivo", json={"indirizzo": INDIRIZZO}, headers=PORTA).status_code == 409
    # Un libro finito: guida e PDF ci sono.
    (d / "guida.md").write_text("x", encoding="utf-8")
    from schema.brief import Brief
    from src.pdf import scrivi_pdf
    from src import ordini_stampa as o
    scrivi_pdf(Brief.model_validate(brief_dict(JOB)), libro, d / "libro.pdf")
    monkeypatch.setattr(o, "conta_pagine", lambda pdf: 40)  # il libro finto è corto: si finge un libro vero
    r = c.post(f"/jobs/{JOB}/stampa/preventivo", json={"indirizzo": INDIRIZZO}, headers=PORTA)
    assert r.status_code == 200 and r.json()["pagine"] == 40
    assert c.post(f"/jobs/{JOB}/stampa/preventivo", json={"indirizzo": {"name": "x"}}, headers=PORTA).status_code == 400

    r = c.post(f"/jobs/{JOB}/stampa", json={"ordine_id": "cs_1", "indirizzo": INDIRIZZO, "livello": "MAIL"}, headers=PORTA)
    assert r.status_code == 200 and spawn[0][0] == JOB and spawn[0][2].startswith("https://")
    assert c.post(f"/jobs/{JOB}/stampa", json={"ordine_id": "cs_1", "livello": "MAIL"}, headers=PORTA).status_code == 400
    assert c.post(f"/jobs/{JOB}/stampa", json={"ordine_id": "cs_1", "indirizzo": INDIRIZZO, "livello": "MAIL"}).status_code == 403

    # I file per Lulu: solo con una firma giusta, non scaduta, e solo i file di stampa.
    (d / "stampa").mkdir()
    (d / "stampa" / "interno.pdf").write_bytes(b"%PDF-finto")
    scade = int(time.time()) + 3600
    firma = m._firma_file(JOB, "interno.pdf", scade)
    assert c.get(f"/stampa-file/{JOB}/interno.pdf?scade={scade}&firma={firma}").content == b"%PDF-finto"
    assert c.get(f"/stampa-file/{JOB}/interno.pdf?scade={scade}&firma={'0' * 64}").status_code == 404
    assert c.get(f"/stampa-file/{JOB}/interno.pdf?scade={scade + 1}&firma={firma}").status_code == 404
    vecchio = int(time.time()) - 10
    assert c.get(f"/stampa-file/{JOB}/interno.pdf?scade={vecchio}&firma={m._firma_file(JOB, 'interno.pdf', vecchio)}").status_code == 404
    assert c.get(f"/stampa-file/{JOB}/brief.json?scade={scade}&firma={m._firma_file(JOB, 'brief.json', scade)}").status_code == 404

    # Il webhook di Lulu: firmato, aggiorna lo stato che il sito legge.
    (d / "stampa" / "ordine.json").write_text(json.dumps(
        {"ordine_id": "cs_1", "livello": "MAIL", "lulu": {"id": 7, "stato": "CREATED"}}), encoding="utf-8")
    evento = json.dumps({"topic": "PRINT_JOB_STATUS_CHANGED", "data": {
        "external_id": f"{JOB}.cs_1", "status": {"name": "IN_PRODUCTION", "changed": "2026-09-01T10:00:00Z"}}}).encode()
    assert c.post("/lulu/webhook", content=evento, headers={"Lulu-HMAC-SHA256": "00"}).status_code == 401
    fw = hmac.new(b"segreto-lulu", evento, hashlib.sha256).hexdigest()
    assert c.post("/lulu/webhook", content=evento, headers={"Lulu-HMAC-SHA256": fw}).status_code == 200
    assert c.get(f"/jobs/{JOB}/stampa").json()["ordine"]["stato"] == "IN_PRODUCTION"
    r = c.post(f"/jobs/{JOB}/stampa", json={"ordine_id": "cs_1", "indirizzo": INDIRIZZO, "livello": "MAIL"}, headers=PORTA).json()
    assert r["gia_ordinato"] is True and len(spawn) == 1
