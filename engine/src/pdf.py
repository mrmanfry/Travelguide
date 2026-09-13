"""Impaginazione del libro in PDF.

Il PDF è il prodotto: è la cosa che il lettore si porta dietro, stampa, tiene.
Perciò non è un markdown convertito al volo, è un libro impaginato — formato
A5, margini speculari, testatine correnti, capolettera, box tipizzati,
segnalibri navigabili.

La resa è affidata a WeasyPrint, che è l'unico strumento libero a implementare
davvero le CSS delle pagine: numeri di pagina, testatine che cambiano col
capitolo, box che non si spezzano a metà, righe orfane e vedove tenute a bada.
Un PDF fatto stampando una pagina web non ha niente di tutto questo.

Il foglio di stile sta in stampa/libro.css, i caratteri in stampa/fonts/: la
grafica si cambia lì, senza toccare questo file.
"""

import html
import re
from pathlib import Path

import markdown as md

from schema.brief import Brief
from src import config

STAMPA_DIR = config.ENGINE_ROOT / "stampa"

MESI = [
    "gennaio", "febbraio", "marzo", "aprile", "maggio", "giugno",
    "luglio", "agosto", "settembre", "ottobre", "novembre", "dicembre",
]
ROMANI = [
    "", "I", "II", "III", "IV", "V", "VI", "VII", "VIII", "IX", "X",
    "XI", "XII", "XIII", "XIV", "XV", "XVI", "XVII", "XVIII", "XIX", "XX",
    "XXI", "XXII", "XXIII", "XXIV", "XXV", "XXVI", "XXVII", "XXVIII", "XXIX", "XXX",
]

# Una sezione è un titolo di secondo livello che comincia con un numero romano
# ("## III. Le taverne"). Un titolo di secondo livello che NON comincia così è
# l'occhiello del capitolo, la tesi che sta sotto al titolo.
_SEZIONE_RE = re.compile(r"^\s*([IVXLC]+)[.)]\s*(.+)$")
_BLOCKQUOTE_RE = re.compile(r"<blockquote>(.*?)</blockquote>", re.DOTALL)
_ETICHETTA_RE = re.compile(
    r"^\s*<p>\s*<strong>([^<]+?)</strong>\s*(?:<br\s*/?>)?\s*", re.IGNORECASE
)
_ASIDE_RE = re.compile(r"<aside\b.*?</aside>", re.DOTALL)
_OL_RE = re.compile(r"<ol>(.*?)</ol>", re.DOTALL)
_LI_RE = re.compile(r"<li>(.*?)</li>", re.DOTALL)
_TAG_RE = re.compile(r"<[^>]+>")

# Quando un elenco numerato diventa una griglia di fatti. I due limiti non sono
# arbitrari: sotto le tre voci una griglia non è una griglia, sopra le otto
# diventa un modulo; e una voce che supera i 170 caratteri, in mezza giustezza,
# occupa più di cinque righe e la griglia si sfalsa. Fuori da questa finestra
# l'elenco resta un elenco — è la regola che tiene in piedi tutto il resto:
# il layout segue il testo, non il contrario.
GRIGLIA_MIN_VOCI = 3
GRIGLIA_MAX_VOCI = 8
GRIGLIA_MAX_CARATTERI = 170

# La tinta del capitolo. Tre, non una per capitolo: dicono in che punto del
# viaggio si è — dove si sta (tappa), come ci si sposta (collegamento), e la
# cornice che apre e chiude il libro. Tredici colori diversi sarebbero un
# volantino; tre legati alla funzione sono un sistema.
TINTA_PER_TIPO = {
    "tappa": "tappa",
    "collegamento": "collegamento",
    "trasferimento": "collegamento",
}


def romano(n: int) -> str:
    return ROMANI[n] if 0 < n < len(ROMANI) else str(n)


def _data_estesa(d) -> str:
    return f"{d.day} {MESI[d.month - 1]} {d.year}" if d else ""


def _classe_box(etichetta: str) -> str:
    """La classe CSS del box, dedotta dalla sua etichetta.

    Le etichette sono adattabili alla destinazione (BUSH TIP diventa CITY TIP,
    TRAIL TIP): si riconoscono i due casi che hanno una grafica propria, tutto
    il resto usa la famiglia comune.
    """
    e = etichetta.upper()
    if "IMMOBIL" in e:
        return "box box--immobili"
    if "ATTENZIONE" in e:
        return "box box--attenzione"
    return "box"


def _dividi_etichetta(testo: str) -> tuple[str, str]:
    """Da 'A TAVOLA — il pesce del mattino' a ('A TAVOLA', 'il pesce del mattino')."""
    for sep in ("—", "–", " - ", ":"):
        if sep in testo:
            a, b = testo.split(sep, 1)
            return a.strip(), b.strip()
    return testo.strip(), ""


def _trasforma_box(frammento: str) -> str:
    """I blockquote del generatore diventano box tipizzati.

    Il generatore emette i box come citazioni con il titolo in grassetto nella
    forma «TIPO — titolo». Qui quella convenzione diventa struttura: etichetta,
    titolo e corpo separati, così il foglio di stile può trattarli davvero come
    box e non come citazioni.
    """

    def sostituisci(m: re.Match) -> str:
        dentro = m.group(1).strip()
        cappello = _ETICHETTA_RE.match(dentro)
        if not cappello:
            return f'<aside class="box">{dentro}</aside>'
        etichetta, titolo = _dividi_etichetta(html.unescape(cappello.group(1)))
        resto = dentro[cappello.end():]
        # Se dopo l'etichetta il paragrafo era vuoto, togli il <p> spaiato.
        resto = re.sub(r"^\s*</p>", "", resto, count=1).strip()
        if resto and not resto.lstrip().startswith("<"):
            resto = f"<p>{resto}"
        # Etichetta e titolo in un blocco a sé: è quel blocco a portare il
        # divieto di restare solo in fondo a una pagina. Metterlo sui singoli
        # elementi non bastava — l'etichetta di ATTENZIONE è inline-block, per
        # poter avere il fondo pieno, e su un elemento inline la regola non si
        # applica affatto: infatti era rimasta sola dentro un box vuoto.
        pezzi = [f'<aside class="{_classe_box(etichetta)}">', '<div class="cappello">']
        pezzi.append(f'<p class="etichetta">{html.escape(etichetta)}</p>')
        if titolo:
            pezzi.append(f'<p class="titolo">{html.escape(titolo)}</p>')
        pezzi.append("</div>")
        pezzi.append(resto)
        pezzi.append("</aside>")
        return "".join(pezzi)

    return _BLOCKQUOTE_RE.sub(sostituisci, frammento)


def _famiglia(tipo: str | None) -> str:
    """La famiglia grafica del capitolo, dedotta dal suo tipo."""
    return TINTA_PER_TIPO.get((tipo or "").strip().lower(), "cornice")


def _testo_nudo(frammento: str) -> str:
    return html.unescape(_TAG_RE.sub("", frammento)).strip()


def _griglia_fatti(frammento: str) -> str:
    """Gli elenchi numerati brevi diventano una griglia di fatti.

    «Cinque cose da sapere prima» scritto come elenco puntato è un elenco
    puntato: si legge come una lista della spesa. Le stesse cinque righe in
    griglia, ognuna col suo numero in tondo, si leggono a colpo d'occhio — è il
    modo in cui le guide vere mettono i fatti secchi, ed è l'unico elemento da
    rivista che il nostro testo produce già da solo, senza che il generatore
    debba imparare niente di nuovo.

    La trasformazione avviene SOLO se l'elenco ha la forma giusta (poche voci,
    tutte brevi) e mai dentro un box, dove la giustezza è già dimezzata. Un
    elenco che non rientra nei limiti resta un elenco: un template riempito male
    si vede molto più di un template che non c'è.
    """
    vietati = [m.span() for m in _ASIDE_RE.finditer(frammento)]

    def dentro_un_box(pos: int) -> bool:
        return any(a <= pos < b for a, b in vietati)

    def sostituisci(m: re.Match) -> str:
        if dentro_un_box(m.start()):
            return m.group(0)
        voci = _LI_RE.findall(m.group(1))
        if not (GRIGLIA_MIN_VOCI <= len(voci) <= GRIGLIA_MAX_VOCI):
            return m.group(0)
        if any(len(_testo_nudo(v)) > GRIGLIA_MAX_CARATTERI for v in voci):
            return m.group(0)
        if any("<p>" in v or "<ul>" in v or "<ol>" in v for v in voci):
            return m.group(0)
        celle = "".join(
            f'<div class="voce"><span class="n">{i}</span>'
            f'<span class="t">{v.strip()}</span></div>'
            for i, v in enumerate(voci, 1)
        )
        return f'<div class="griglia">{celle}</div>'

    return _OL_RE.sub(sostituisci, frammento)


def _capolettera(frammento: str) -> str:
    """Capolettera di tre righe sulla prima lettera del primo capoverso."""
    m = re.search(r"<p>\s*([A-ZÀÈÉÌÒÙ«\"'])", frammento)
    if not m:
        return frammento
    i = m.start(1)
    return (
        frammento[:i]
        + f'<span class="capolettera">{frammento[i]}</span>'
        + frammento[i + 1 :]
    )


def _stacca_titolo_box(testo: str) -> str:
    """Nei box, stacca il titolo dal corpo con una riga vuota.

    Il generatore scrive il box così:

        > **GLI IMMOBILI — Kyoto, 28 ottobre**
        > * il mercato chiude il mercoledì

    Senza una riga vuota dopo il titolo, markdown legge le righe successive
    come continuazione dello stesso capoverso: l'elenco non diventa un elenco e
    gli asterischi finiscono stampati nel libro. Una riga vuota di citazione
    ('>') rimette le cose a posto senza toccare quello che scrive il modello.
    """
    righe = testo.split("\n")
    fuori: list[str] = []
    for i, riga in enumerate(righe):
        fuori.append(riga)
        if not riga.startswith(">"):
            continue
        dentro = riga.lstrip(">").strip()
        if not (dentro.startswith("**") and dentro.endswith("**")):
            continue
        prossima = righe[i + 1].lstrip(">").strip() if i + 1 < len(righe) else ""
        if prossima:
            fuori.append(">")
    return "\n".join(fuori)


def _corpo_capitolo(corpo_md: str) -> tuple[str, str]:
    """Da markdown a HTML del capitolo. Ritorna (occhiello, corpo)."""
    testo = _stacca_titolo_box(corpo_md.strip())

    # L'occhiello: il primo '## ' che non è una sezione numerata.
    occhiello = ""
    righe = testo.split("\n")
    for i, riga in enumerate(righe):
        if riga.startswith("## "):
            if not _SEZIONE_RE.match(riga[3:]):
                occhiello = riga[3:].strip()
                righe.pop(i)
                testo = "\n".join(righe)
            break

    frammento = md.markdown(testo, extensions=["extra", "sane_lists"])
    frammento = _trasforma_box(frammento)

    # I titoli di sezione: il numero romano si stacca dal testo per poterlo
    # colorare, e il titolo perde il punto finale del numero.
    def titolo(m: re.Match) -> str:
        dentro = m.group(1).strip()
        s = _SEZIONE_RE.match(dentro)
        if s:
            return (
                f'<h2 class="sezione"><span class="romano">{html.escape(s.group(1))}</span>'
                f"{html.escape(s.group(2))}</h2>"
            )
        return f'<h2 class="sezione">{dentro}</h2>'

    frammento = re.sub(r"<h2>(.*?)</h2>", titolo, frammento, flags=re.DOTALL)
    frammento = re.sub(r"<h3>(.*?)</h3>", r'<h2 class="sezione">\1</h2>', frammento)
    frammento = _griglia_fatti(frammento)
    frammento = _capolettera(frammento)
    return occhiello, frammento


def _frontespizio(brief: Brief, titolo: str) -> str:
    luoghi = [t.luogo for t in brief.tappe]
    # Le tappe ripetute (si torna a Tokyo) si dicono una volta sola.
    visti, itinerario = set(), []
    for l in luoghi:
        if l not in visti:
            visti.add(l)
            itinerario.append(l)

    righe = []
    if itinerario:
        righe.append(" · ".join(itinerario))
    if brief.date.inizio and brief.date.fine:
        righe.append(
            f"dal {_data_estesa(brief.date.inizio)} al {_data_estesa(brief.date.fine)}"
        )
    n = brief.viaggiatori.adulti + len(brief.viaggiatori.bambini_eta)
    if n:
        righe.append("per una persona" if n == 1 else f"per {n} persone")

    dati = "<br>".join(html.escape(r) for r in righe)
    return f"""
<section class="frontespizio">
  <div class="editore">Atelier del Viaggio</div>
  <h1>{html.escape(titolo)}</h1>
  <p class="sottotitolo">Una guida scritta per voi soli</p>
  <div class="filetto"></div>
  <p class="dati">{dati}</p>
</section>
"""


def _indice(capitoli: list[dict]) -> str:
    voci = []
    for c in capitoli:
        # Il numero prende la tinta della famiglia: così l'indice è anche la
        # legenda delle linguette sul taglio, e si impara il codice dei colori
        # senza che nessuno lo spieghi.
        voci.append(
            f'<li><span class="numero numero--{_famiglia(c.get("tipo"))}">'
            f'{romano(c["numero"])}</span>'
            f'<span class="voce">{html.escape(c["titolo"])}</span>'
            f'<a class="pagina" href="#cap-{c["numero"]}"></a></li>'
        )
    return (
        '<section class="indice"><h2>Indice</h2><ol>'
        + "".join(voci)
        + "</ol></section>"
    )


def _colophon(brief: Brief) -> str:
    return f"""
<section class="colophon">
  <h2>Nota finale</h2>
  <p>Questo libro è stato scritto per {html.escape(
      ', '.join(t.luogo for t in brief.tappe[:1]) or 'il vostro viaggio')} e per
  le vostre date: non esiste in nessun'altra copia.</p>
  <p>Orari, aperture e prezzi cambiano, e nessuna guida stampata può seguirli.
  Le poche cose che le vostre date decidono al posto vostro le trovate nei
  riquadri «Gli immobili», in fondo ai capitoli sulle tappe: quelle vale la pena
  ricontrollarle prima di partire. Tutto il resto è vostro da improvvisare.</p>
  <p>Atelier del Viaggio</p>
</section>
"""


def titolo_libro(brief: Brief, libro: dict) -> str:
    """Il titolo del libro.

    È quello del capitolo di introduzione: il generatore lo scrive come promessa
    del viaggio, ed è già un titolo da copertina («Il Giappone in quattro (poi in
    due)»). Il nome della prima tappa è un ripiego, non un titolo: «Tokyo» su un
    libro che attraversa mezzo Giappone sarebbe sbagliato.
    """
    capitoli = libro.get("capitoli") or []
    intro = next((c for c in capitoli if c.get("tipo") == "introduzione"), None) or (
        capitoli[0] if capitoli else None
    )
    if intro and intro.get("titolo"):
        return intro["titolo"]
    if brief.tappe:
        return brief.tappe[0].luogo
    return "Il vostro viaggio"


def nome_file(titolo: str) -> str:
    """Il titolo trasformato in un nome di file che ogni sistema accetta."""
    # I caratteri vietati si sostituiscono con uno spazio, non si cancellano:
    # togliendoli si incollano le parole ("templi/giardini" -> "templigiardini").
    pulito = re.sub(r'[\\/:*?"<>|]', " ", titolo)
    pulito = re.sub(r"\s+", " ", pulito).strip().strip(".")
    return (pulito[:80].strip() or "Il vostro libro") + ".pdf"


def costruisci_html(brief: Brief, libro: dict, titolo: str | None = None) -> str:
    """Il libro come pagina HTML pronta per l'impaginazione."""
    capitoli = libro.get("capitoli") or []
    if titolo is None:
        titolo = titolo_libro(brief, libro)

    parti = [_frontespizio(brief, titolo), _indice(capitoli)]
    for c in capitoli:
        occhiello, corpo = _corpo_capitolo(c.get("corpo_md") or "")
        fam = _famiglia(c.get("tipo"))
        num = romano(c["numero"])
        # La tacca porta il numero del capitolo nella linguetta sul taglio:
        # sfogliando si sa dove si è senza leggere niente. Sta nel flusso (e non
        # in un attributo) perché solo un elemento vero può alimentare string().
        parti.append(
            f'<section class="capitolo capitolo--{fam}" id="cap-{c["numero"]}">'
            f'<header class="apre">'
            f'<span class="tacca">{num}</span>'
            f'<div class="numero">Capitolo {num}</div>'
            f'<div class="filetto"></div>'
            f'<h1>{html.escape(c["titolo"])}</h1>'
            + (f'<p class="occhiello">{html.escape(occhiello)}</p>' if occhiello else "")
            + "</header>"
            f'<div class="corpo">{corpo}</div></section>'
        )
    parti.append(_colophon(brief))

    return (
        '<!doctype html><html lang="it"><head><meta charset="utf-8">'
        f"<title>{html.escape(titolo)}</title>"
        '<link rel="stylesheet" href="libro.css"></head>'
        "<body>" + "".join(parti) + "</body></html>"
    )


def scrivi_pdf(brief: Brief, libro: dict, destinazione: Path | None = None) -> Path:
    """Impagina il libro e scrive libro.pdf accanto agli altri artefatti."""
    from weasyprint import HTML

    documento = costruisci_html(brief, libro)
    path = destinazione or (config.output_root() / brief.brief_id / "libro.pdf")
    path.parent.mkdir(parents=True, exist_ok=True)
    # base_url sulla cartella di stampa: da lì si risolvono il foglio di stile
    # e i caratteri, che vengono incorporati nel PDF.
    HTML(string=documento, base_url=str(STAMPA_DIR) + "/").write_pdf(str(path))
    return path
