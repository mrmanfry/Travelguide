"""Il contratto della scheda: quello che il sito manda davvero non deve fermare un libro."""

from schema.brief import Brief
from tests.conftest import brief_dict


def test_fixture_valida():
    b = Brief.model_validate(brief_dict())
    assert b.tappe[0].luogo == "Lisbona" and b.giorni == 4


def test_passioni_come_testo():
    # Il sito a volte manda le passioni come parole semplici: un tester ha
    # fermato un libro pagato così.
    b = Brief.model_validate(brief_dict(passioni=["birra e birrerie storiche", {"tema": "arte"}]))
    assert [p.tema for p in b.passioni] == ["birra e birrerie storiche", "arte"]


def test_oro_un_solo_testo():
    b = Brief.model_validate(brief_dict(oro={"da_non_perdere": "il fado vero", "da_evitare": ""}))
    assert b.oro.da_non_perdere == ["il fado vero"] and b.oro.da_evitare == []


def test_nomi_conservati():
    assert Brief.model_validate(brief_dict()).viaggiatori.nomi == ["Anna", "Marco"]
