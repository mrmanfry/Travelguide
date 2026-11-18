"""Il blocco DA FARE: dove è ammesso, come si ripara, come si conta, come si stampa."""

import re

import pytest

from schema.brief import ChapterAssignment
from src import chapter_runner as cr
from src.pdf import _corpo_capitolo
from tests.conftest import CAPITOLO_TAPPA, controlla_messages, risposta


def assegnato(tipo: str, budget: int = 300) -> ChapterAssignment:
    return ChapterAssignment(numero=3, titolo_provvisorio="T", tipo=tipo, budget_parole=budget)


CORPO = "# Monaco\n\n## I. Uno\n\n" + "parola " * 300 + "\n\n"
DAFARE = ("> **DA FARE — Monaco**\n>\n> *Da vedere e da fare*\n>\n"
          "> - **Residenz**: x.\n> - **Alter Peter**: y.\n")
IMMOBILI = "> **GLI IMMOBILI — Monaco**\n>\n> - **Domenica**: chiuso.\n"
META = '\n<!--META\n{"riassunto": "r"}\nMETA-->\n'


def test_tappa_completa_in_regola():
    c = cr.controlli_struttura(assegnato("tappa"), CORPO + DAFARE + "\n" + IMMOBILI + META)
    assert c["forma_ok"] and c["dafare_ok"] and c["immobili_ok"]


def test_da_fare_minuscolo_nella_prosa_non_conta():
    c = cr.controlli_struttura(assegnato("tappa"), CORPO + "le cose da fare\n" + IMMOBILI + META)
    assert not c["dafare_ok"] and not c["forma_ok"]


def test_fuori_dalle_tappe_si_tolgono_entrambi(brief):
    testo = CORPO + DAFARE + "\n" + IMMOBILI + META
    a = assegnato("contesto")
    t, c = cr.ripara_forma(brief, a, testo, cr.controlli_struttura(a, testo))
    assert c["forma_ok"] and "DA FARE" not in t and "GLI IMMOBILI" not in t and "META-->" in t


def test_togliere_da_fare_lascia_gli_immobili():
    assert "GLI IMMOBILI" in cr.rimuovi_box_dafare(CORPO + DAFARE + "\n" + IMMOBILI + META)


def test_il_blocco_non_conta_nel_budget():
    # Il riepilogo è un'aggiunta: contato dentro, toglieva al capitolo le storie.
    lungo = DAFARE + "".join(f"> - **Posto {i}**: " + "parola " * 30 + "\n" for i in range(10))
    assert cr.chapter_word_count(CORPO + IMMOBILI + META) == cr.chapter_word_count(CORPO + lungo + "\n" + IMMOBILI + META)


class _ClientHaiku:
    def __init__(self, testo):
        self.testo = testo
        self.messages = self

    def create(self, **kw):
        controlla_messages(kw)
        return risposta(self.testo, model="claude-haiku-4-5")


BLOCCO_RICAVATO = ("> **DA FARE — Monaco**\n>\n> *Da vedere e da fare*\n>\n> - **Residenz**: a.\n"
                   "> - **Frauenkirche**: b.\n> - **Hofgarten**: c.\n>\n> *Da mangiare e bere*\n>\n"
                   "> - **Augustiner Stammhaus**: d.")


def test_tappa_senza_blocco_lo_ricava_prima_degli_immobili(brief, uscita, monkeypatch):
    (uscita / brief.brief_id).mkdir()
    monkeypatch.setattr(cr, "make_client", lambda: _ClientHaiku(BLOCCO_RICAVATO))
    a = assegnato("tappa")
    senza = CORPO + IMMOBILI + META
    t, c = cr.ripara_forma(brief, a, senza, cr.controlli_struttura(a, senza))
    assert c["forma_ok"] and t.index("DA FARE") < t.index("GLI IMMOBILI") and t.rstrip().endswith("META-->")
    # La chiamata si paga: finisce nei costi del capitolo.
    assert (uscita / brief.brief_id / "cap_03.dafare.usage.json").exists()


def test_blocco_ricavato_povero_si_scarta(brief, uscita, monkeypatch):
    (uscita / brief.brief_id).mkdir()
    monkeypatch.setattr(cr, "make_client", lambda: _ClientHaiku("> **DA FARE — Monaco**\n> - **Residenz**: a."))
    a = assegnato("tappa")
    senza = CORPO + IMMOBILI + META
    _, c = cr.ripara_forma(brief, a, senza, cr.controlli_struttura(a, senza))
    assert not c["dafare_ok"]


@pytest.fixture
def html_tappa():
    md = CAPITOLO_TAPPA.format(prosa="Testo.", breve="Breve.").split("\n", 1)[1]
    return _corpo_capitolo(md)[1]


def test_pdf_blocco_con_gruppi_ed_elenchi(html_tappa):
    box = re.search(r'<aside class="box box--dafare">.*?</aside>', html_tappa, re.S).group(0)
    assert box.count('<p class="gruppo">') == 3
    assert box.count("<li>") == 4 and "- <strong>" not in box  # elenchi veri, niente trattini stampati


def test_pdf_nomi_linkati_a_google_maps(html_tappa):
    assert ('href="https://www.google.com/maps/search/?api=1&amp;query=Miradouro+de+Santa+Luzia%2C+Lisbona"'
            in html_tappa)


def test_pdf_due_box_attaccati_restano_due(html_tappa):
    # DA FARE e GLI IMMOBILI uno dopo l'altro: markdown li fondeva in un box solo.
    assert html_tappa.count('<aside class="box box--dafare">') == 1
    assert html_tappa.count('<aside class="box box--immobili">') == 1
    dafare = re.search(r'<aside class="box box--dafare">.*?</aside>', html_tappa, re.S).group(0)
    assert "IMMOBILI" not in dafare


def test_pdf_elenco_sotto_una_riga_di_testo_nel_box():
    # Difetto vecchio: un elenco subito sotto una riga di testo usciva con i trattini.
    md = ("## I. Uno\n\nTesto.\n\n> **GLI IMMOBILI — X**\n>\n> Le sole cose che decidono le date.\n"
          "> - **Lunedì**: chiuso.\n> - **Martedì**: festa.\n")
    html = _corpo_capitolo(md)[1]
    assert html.count("<li>") == 2 and "- <strong>" not in html
