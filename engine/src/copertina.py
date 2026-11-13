"""La copertina del libro stampato: retro, dorso e fronte in un foglio solo.

La tipografia vuole la copertina distesa: da sinistra il retro, poi il dorso,
poi il fronte, più l'abbondanza tutto intorno. Lo spessore del dorso dipende
dalle pagine e dalla carta, e lo dice Lulu (cover-dimensions): qui arriva già
calcolato.

Sul fronte quello che c'è nel frontespizio — marchio, titolo, per chi, quando
— e sotto la mappa del viaggio; per un viaggio in una città sola, la mappa
della città. Sul retro l'attacco dell'introduzione e la dedica del colophon.
Sul dorso il titolo, letto dal basso verso l'alto come nei libri italiani,
solo se il dorso è abbastanza spesso da ospitarlo.
"""

import html
import re
from pathlib import Path

from schema.brief import Brief
from src import config
from src.pdf import MESI, STAMPA_DIR, _elenco_nomi, _lega_ultime, titolo_libro

ABBONDANZA_MM = 3.175
PAGINA_MM = (148.0, 210.0)
DORSO_MIN_TESTO_MM = 6.0   # sotto, il titolo sul dorso finirebbe storto sulla piega


def _date(brief: Brief) -> str:
    """«13–16 novembre 2026», «29 ottobre – 2 novembre 2026»."""
    a, b = brief.date.inizio, brief.date.fine
    if not (a and b):
        return ""
    if (a.year, a.month) == (b.year, b.month):
        return f"{a.day}–{b.day} {MESI[a.month - 1]} {a.year}"
    if a.year == b.year:
        return f"{a.day} {MESI[a.month - 1]} – {b.day} {MESI[b.month - 1]} {b.year}"
    return f"{a.day} {MESI[a.month - 1]} {a.year} – {b.day} {MESI[b.month - 1]} {b.year}"


def _attacco(libro: dict, parole: int = 75) -> str:
    """Il primo capoverso dell'introduzione, chiuso a fine frase."""
    capitoli = libro.get("capitoli") or []
    intro = next((c for c in capitoli if c.get("tipo") == "introduzione"), None)
    if not intro:
        return ""
    for blocco in re.split(r"\n\s*\n", intro.get("corpo_md") or ""):
        b = blocco.strip()
        if not b or b.startswith(("#", ">", "|", "-", "*", "!")) and not b.startswith("**"):
            continue
        testo = re.sub(r"[*_`]+", "", b)
        testo = re.sub(r"\[([^\]]*)\]\([^)]*\)", r"\1", testo)
        testo = re.sub(r"\s+", " ", testo).strip()
        if len(testo.split()) < 12:
            continue
        if len(testo.split()) <= parole:
            return testo
        corto = " ".join(testo.split()[:parole])
        fine = max(corto.rfind(". "), corto.rfind("? "), corto.rfind("! "))
        return corto[: fine + 1] if fine > len(corto) // 2 else corto.rstrip(",;:") + "…"
    return ""


def _mappa_di_copertina(brief: Brief) -> str | None:
    """La mappa del viaggio; se non c'è, la prima tavola della prima città."""
    from src.mappe import mappa_del_paese, mappe_del_libro

    viaggio = mappa_del_paese(brief.brief_id)
    if viaggio:
        return viaggio["png"]
    citta = mappe_del_libro(brief.brief_id)
    if citta:
        prima = citta[min(citta)]
        return (prima.get("tavole_png") or [prima["png"]])[0]
    return None


def costruisci_html(brief: Brief, libro: dict, dorso_mm: float) -> str:
    ab = ABBONDANZA_MM
    pw, ph = PAGINA_MM
    larghezza = 2 * ab + 2 * pw + dorso_mm
    altezza = 2 * ab + ph
    titolo = titolo_libro(brief, libro)
    nomi = _elenco_nomi(brief.viaggiatori.nomi)
    date = _date(brief)
    mappa = _mappa_di_copertina(brief)
    attacco = _attacco(libro)
    per_chi = nomi or "voi"

    retro_x = ab
    dorso_x = ab + pw
    fronte_x = ab + pw + dorso_mm

    fronte = f"""
<div class="fronte" style="left:{fronte_x:.3f}mm; top:{ab:.3f}mm">
  <div class="marchio">{html.escape(config.MARCHIO)}</div>
  <h1>{html.escape(_lega_ultime(titolo))}</h1>
  {f'<p class="per">per {html.escape(nomi)}</p>' if nomi else ''}
  {f'<p class="quando">{html.escape(date)}</p>' if date else ''}
  <div class="filetto"></div>
</div>"""
    # La mappa sta direttamente sulla pagina, con le sue coordinate: annidata
    # in un blocco assoluto con padding, WeasyPrint la sposta e la allarga.
    mx, my, mw, mh = fronte_x + 14, ab + ph - 16 - 86, pw - 28, 86
    if mappa:
        fronte += (f'<div class="mappa" style="left:{mx:.3f}mm; top:{my:.3f}mm; width:{mw:.3f}mm; '
                   f'height:{mh:.3f}mm"><img src="{Path(mappa).as_uri()}" alt="" '
                   f'style="width:{mw:.3f}mm; height:{mh:.3f}mm"></div>')

    retro = f"""
<div class="retro" style="left:{retro_x:.3f}mm; top:{ab:.3f}mm">
  {f'<p class="attacco">{html.escape(attacco)}</p>' if attacco else ''}
  <div class="filetto"></div>
  <p class="dedica">Questo libro è stato scritto per {html.escape(per_chi)}, per questo viaggio
  e per queste date: non esiste in nessun'altra copia.</p>
  <div class="marchio-retro">{html.escape(config.MARCHIO)}</div>
</div>"""

    dorso = ""
    if dorso_mm >= DORSO_MIN_TESTO_MM:
        corpo_pt = min(8.5, (dorso_mm - 2.2) / 25.4 * 72)
        dorso = f"""
<div class="dorso" style="left:{dorso_x + dorso_mm / 2:.3f}mm; top:{ab + ph / 2:.3f}mm;
     width:{ph - 30:.1f}mm; font-size:{corpo_pt:.1f}pt">
  <span class="marchio-dorso">{html.escape(config.MARCHIO)}</span>
  <span class="titolo-dorso">{html.escape(titolo)}</span>
</div>"""

    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<link rel="stylesheet" href="libro.css">
<style>
@page {{ size: {larghezza:.3f}mm {altezza:.3f}mm; margin: 0; background: var(--carta); }}
@page :left, @page :right {{ margin: 0; @top-left {{ content: none; }} @top-right {{ content: none; }}
         @bottom-left {{ content: none; }} @bottom-right {{ content: none; }} }}
@page :first {{ margin: 0; @top-left {{ content: none; }} @top-right {{ content: none; }}
         @bottom-left {{ content: none; }} @bottom-right {{ content: none; }} }}
html, body {{ margin: 0; }}
body {{ position: relative; width: {larghezza:.3f}mm; height: {altezza:.3f}mm; }}
.fronte, .retro {{ position: absolute; width: {pw}mm; height: {ph}mm; box-sizing: border-box;
                  text-align: center; }}
.fronte {{ padding: 26mm 16mm 0; }}
.marchio {{ font-family: "Archivo libro", sans-serif; font-size: 7.5pt; font-weight: 500;
           letter-spacing: 0.26em; text-transform: uppercase; color: var(--cornice); }}
.fronte h1 {{ font-size: 27pt; font-weight: 400; line-height: 1.12; color: var(--inchiostro);
             margin: 13mm 0 0; text-align: center; hyphens: none; string-set: none; bookmark-level: none; }}
.per {{ font-size: 13pt; font-style: italic; color: var(--inchiostro-2); margin: 6mm 0 0;
       text-indent: 0; text-align: center; }}
.quando {{ font-family: "Archivo libro", sans-serif; font-size: 8pt; letter-spacing: 0.1em;
          color: var(--grigio); margin: 3mm 0 0; text-indent: 0; text-align: center; }}
.filetto {{ width: 18mm; height: 1.2pt; background: var(--terracotta); margin: 9mm auto; }}
.mappa {{ position: absolute; overflow: hidden; outline: 0.5pt solid var(--filetto); }}
.mappa img {{ object-fit: cover; display: block; }}
.retro {{ padding: 36mm 20mm 0; text-align: left; }}
.attacco {{ font-size: 11.5pt; font-style: italic; line-height: 1.5; color: var(--inchiostro-2);
           text-indent: 0; text-align: left; hyphens: auto; margin: 0; }}
.retro .filetto {{ margin: 10mm 0; }}
.dedica {{ font-size: 9.5pt; line-height: 1.5; color: var(--grigio); text-indent: 0;
          text-align: left; margin: 0; }}
.marchio-retro {{ position: absolute; left: 20mm; bottom: 18mm; font-family: "Archivo libro", sans-serif;
                 font-size: 7pt; font-weight: 500; letter-spacing: 0.24em; text-transform: uppercase;
                 color: var(--cornice); }}
.dorso {{ position: absolute; height: 0; transform-origin: 0 0;
         transform: rotate(-90deg) translate(-50%, -0.5em); white-space: nowrap;
         display: flex; justify-content: space-between; align-items: center; }}
.titolo-dorso {{ font-family: "Garamond libro", serif; color: var(--inchiostro); }}
.marchio-dorso {{ font-family: "Archivo libro", sans-serif; font-size: 0.72em; font-weight: 500;
                 letter-spacing: 0.2em; text-transform: uppercase; color: var(--cornice); }}
</style></head><body>{retro}{dorso}{fronte}</body></html>"""


def scrivi_copertina(brief: Brief, libro: dict, dorso_mm: float, destinazione: Path) -> Path:
    """La copertina distesa in PDF, con l'abbondanza, per un dorso di `dorso_mm`."""
    from weasyprint import HTML

    destinazione.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=costruisci_html(brief, libro, dorso_mm),
         base_url=str(STAMPA_DIR) + "/").write_pdf(str(destinazione))
    return destinazione
