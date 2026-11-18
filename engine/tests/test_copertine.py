"""Le copertine illustrate: libreria, scelta, disegno al volo, PDF."""

import json
import types

import pypdf
import pytest

from schema.brief import Brief
from src import config
from src import disegno_copertina as dc
from src import scelta_copertina as sc
from src.copertina_illustrata import scrivi_copertina, valida_progetto
from tests.conftest import brief_dict, controlla_messages, controlla_stream_beta, risposta


def _tutte():
    candidati = json.loads((config.ENGINE_ROOT / "stampa" / "sagome" / "candidati.json").read_text(encoding="utf-8"))
    return {**candidati, **sc.carica_libreria()}


@pytest.mark.parametrize("chiave", sorted(_tutte()))
def test_ogni_sagoma_valida_in_ogni_atmosfera(chiave):
    voce = _tutte()[chiave]
    for a in sc.ATMOSFERE:
        assert not valida_progetto(sc.componi(voce, a, "Prova")), (chiave, a)


def test_le_atmosfere_approvate_vincono():
    fuji = sc.carica_libreria()["fuji"]
    assert sc.componi(fuji, "notte", "Giappone")["cielo"] == sc.ATMOSFERE["alba"]["cielo"]


def test_validazione_bordo_sinistro_e_comandi():
    base = sc.componi(sc.carica_libreria()["fuji"], "alba", "X")
    assert valida_progetto({**base, "sagome": [{"d": "M0,300 L100,100 Z", "colore": "#111111"}]})
    assert valida_progetto({**base, "sagome": [{"d": "M10,400 l50,-50 Z", "colore": "#111111"}]})
    assert valida_progetto({**base, "titolo": ""})


def _brief(luogo, bid):
    return Brief.model_validate({**brief_dict(bid), "tappe": [{"ordine": 1, "luogo": luogo, "notti": 3}]})


def test_scelta_del_motore():
    lib = sc.carica_libreria()
    assert sc.scelta_del_motore(_brief("Monaco di Baviera", "m"), lib)["soggetto"] == "frauenkirche"
    assert sc.scelta_del_motore(_brief("New York (Manhattan)", "ny"), lib)["soggetto"] in ("empire_state", "liberta")
    assert sc.scelta_del_motore(_brief("Parigi", "p"), lib) is None


class ClientFinto:
    """Risponde alla scelta (messages.create) e al disegno (beta.messages.stream)."""

    def __init__(self, scelta, disegno=()):
        self.scelta, self.coda, self.flussi = scelta, list(disegno), []
        self.messages = types.SimpleNamespace(create=self._create)
        self.beta = types.SimpleNamespace(messages=types.SimpleNamespace(stream=self._stream))

    def _create(self, **kw):
        controlla_messages(kw)
        assert kw["model"] == sc.MODELLO
        return risposta(self.scelta, model=sc.MODELLO)

    def _stream(self, **kw):
        controlla_stream_beta(kw)
        self.flussi.append(kw)
        dati = self.coda.pop(0)

        class Flusso:
            def __enter__(s):
                return s

            def __exit__(s, *a):
                return False

            def get_final_message(s):
                return risposta(json.dumps(dati))

        return Flusso()


def test_scelta_del_modello_e_ripiego(uscita):
    m = _brief("Monaco di Baviera", "monaco")
    p = sc.prepara_copertina(m, {"capitoli": []}, client=ClientFinto(
        '{"soggetto": "frauenkirche", "atmosfera": "sera", "titolo": "Monaco di Baviera"}'))
    assert p["titolo"] == "Monaco di Baviera"
    assert json.loads(sc.file_copertina("monaco").read_text())["scelto_da"] == sc.MODELLO
    # Il modello sceglie un soggetto che non c'è: decide il motore.
    sc.prepara_copertina(m, {"capitoli": []}, rifai=True, client=ClientFinto(
        '{"soggetto": "colosseo", "atmosfera": "sera", "titolo": "Roma"}'))
    d = json.loads(sc.file_copertina("monaco").read_text())
    assert d["scelta"]["soggetto"] == "frauenkirche" and d["scelto_da"] == "motore"


TORRE = {"nessuno": False, "nome": "Torre di Galata", "luoghi": ["Istanbul", "Turchia"], "tratti": "la torre",
         "titolo": "Istanbul", "sagome": [{"d": "M450,400 L450,120 L500,60 L550,120 L550,400 Z", "colore": "#1c1a22"}],
         "astro": {"x": 0.7, "y": 0.42, "r": 14}, "uccelli": [[0.2, 0.4, 2]], "atmosfere": ["sera", "alba"]}


def test_disegno_al_volo(uscita):
    rotto = {**TORRE, "sagome": [{"d": "M0,300 L100,100 Z", "colore": "#1c1a22"}]}
    client = ClientFinto('{"soggetto": null}', [
        rotto,                                            # giro 1: non passa la validazione
        TORRE, {"luogo": "un razzo", "sicurezza": 2},     # giro 2: il lettore non lo riconosce
        {"sembra": "un razzo", "riconoscibile": False, "voto": 5, "problemi": ["troppo appuntita"]},
        TORRE, {"luogo": "Torre di Galata", "sicurezza": 8},
        {"sembra": "la torre di Galata", "riconoscibile": True, "voto": 9, "problemi": []},
    ])
    ist = _brief("Istanbul", "ist")
    p = sc.prepara_copertina(ist, {"capitoli": []}, client=client)
    assert p and p["titolo"] == "Istanbul"
    d = json.loads(sc.file_copertina("ist").read_text())
    assert d["scelta"]["soggetto"] == "torre_di_galata" and len(d["disegno"]["giri"]) == 3
    assert (uscita / "_copertine" / "torre_di_galata.png").exists()
    assert sorted(f.name for f in (uscita / "ist" / "copertina_giri").iterdir()) == ["giro_2.png", "giro_3.png"]
    # Il lettore alla cieca è sempre una conversazione nuova, a sforzo basso, e non vede il nome.
    ciechi = [k for k in client.flussi if k["output_config"]["format"]["schema"] is dc.SCHEMA_CIECO]
    assert len(ciechi) == 2 and all(len(k["messages"]) == 1 and k["output_config"]["effort"] == "low" for k in ciechi)
    assert "Galata" not in json.dumps(ciechi[0]["messages"][0]["content"][1])
    # Il disegnatore, al terzo giro, ha visto l'immagine del secondo.
    disegni = [k for k in client.flussi if k["output_config"]["format"]["schema"] is dc.SCHEMA_DISEGNO]
    assert any(m["role"] == "user" and isinstance(m["content"], list) and m["content"][0]["type"] == "image"
               for m in disegni[-1]["messages"])
    # Il libro dopo, nello stesso posto, la riusa.
    assert sc.scelta_del_motore(ist, sc.libreria_completa())["soggetto"] == "torre_di_galata"


def test_viaggio_senza_icona_tiene_la_mappa(uscita):
    client = ClientFinto('{"soggetto": null}', [{**TORRE, "nessuno": True, "tratti": "campagna"}])
    assert sc.prepara_copertina(_brief("Langhe", "langhe"), {"capitoli": []}, client=client) is None


def test_fronte_in_pdf_a5(tmp_path, brief):
    p = sc.componi(sc.carica_libreria()["belem"], "tramonto", "Portogallo")
    f = scrivi_copertina(brief, {"capitoli": []}, 0, p, tmp_path / "fronte.pdf", solo_fronte=True)
    pag = pypdf.PdfReader(str(f)).pages
    assert len(pag) == 1
    assert [round(float(v) / 72 * 25.4) for v in pag[0].mediabox[2:]] == [148, 210]
    assert "PORTOGALLO" in pag[0].extract_text().replace(" ", "").upper()
