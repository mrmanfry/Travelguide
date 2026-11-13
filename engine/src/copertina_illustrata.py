"""La copertina illustrata: un cielo in sfumatura, la sagoma del luogo, i nomi.

Lo stile della collana è fisso, e lo decide questo file; ogni libro porta solo
il suo «progetto», un dizionario piccolo:

    {
      "titolo": "GIAPPONE",                 # il luogo, in maiuscolo
      "sopra": "una guida di viaggio",
      "cielo": ["#1f2547", "#4b3a6b", "#c76a7a", "#f2b38a"],   # dall'alto all'orizzonte
      "sagome": [{"d": "<path in 1000×400, base a y=400>", "colore": "#2b2340"}, ...],
      "primo_piano": "#15112a",             # la fascia sotto l'orizzonte
      "riflesso": "#2a2245",                # l'ovale più chiaro sotto la sagoma
      "testo": "#f4e6dc",                   # il colore delle scritte
      "accento": "#f2b38a",                 # filetto e rombo
      "sole": {"x": 0.66, "y": 0.47, "r": 16, "colore": "#e25b45"},   # facoltativo
      "stelle": [[0.78, 0.10, 0.5], ...],   # facoltative: x, y in frazioni del fronte, raggio mm
      "uccelli": [[0.30, 0.38, 3.0], ...]   # facoltativi
    }

Cosa resta fermo, e perché:
- l'orizzonte al 61% dell'altezza, perché la sagoma possa crescere fino a 60 mm
  senza toccare il titolo;
- il cielo e la fascia scura attraversano retro e dorso, perché il libro chiuso
  sembri un oggetto solo;
- la sagoma, il sole e le stelle stanno solo sul fronte;
- le scritte sono sempre le stesse, negli stessi posti: è la collana.
"""

import html
import re
from pathlib import Path

from schema.brief import Brief
from src import config
from src.copertina import ABBONDANZA_MM, DORSO_MIN_TESTO_MM, PAGINA_MM, _attacco, _date
from src.pdf import STAMPA_DIR, _elenco_nomi, titolo_libro

ORIZZONTE = 0.61      # frazione dell'altezza della pagina, dall'alto
SAGOMA_MM = 60.0      # altezza massima della sagoma sopra l'orizzonte
COLORE_RE = re.compile(r"^#[0-9a-fA-F]{6}$")
# Solo comandi assoluti (maiuscoli): così ogni numero è una coordinata vera, e
# il controllo che la sagoma stia nel riquadro 1000×400 è un controllo vero.
# Niente H e V, che hanno un numero solo: così i numeri vanno sempre a coppie x,y.
PATH_RE = re.compile(r"^[MLCSQZ0-9\s,.\-]+$")


def valida_progetto(p: dict) -> list[str]:
    """I difetti di un progetto: colori non validi, tracciati sporchi, campi mancanti."""
    errori = []
    for campo in ("primo_piano", "riflesso", "testo", "accento"):
        if not COLORE_RE.match(str(p.get(campo, ""))):
            errori.append(f"{campo}: colore non valido")
    cielo = p.get("cielo") or []
    if not (3 <= len(cielo) <= 6) or not all(COLORE_RE.match(str(c)) for c in cielo):
        errori.append("cielo: servono da 3 a 6 colori #rrggbb")
    sagome = p.get("sagome") or []
    if not (1 <= len(sagome) <= 6):
        errori.append("sagome: da 1 a 6")
    for i, s in enumerate(sagome):
        d = str(s.get("d", ""))
        if not d or len(d) > 6000 or not PATH_RE.match(d):
            errori.append(f"sagome[{i}]: tracciato non valido")
        if not COLORE_RE.match(str(s.get("colore", ""))):
            errori.append(f"sagome[{i}]: colore non valido")
        numeri = [float(n) for n in re.findall(r"-?\d+(?:\.\d+)?", d)]
        # Ai bordi del fronte una sagoma tocca solo la linea di base: altrimenti,
        # dove il fronte incontra il dorso, la forma si interrompe a scalino.
        coppie = list(zip(numeri[0::2], numeri[1::2]))
        if any((x <= 2 or x >= 998) and y < 398 for x, y in coppie):
            errori.append(f"sagome[{i}]: ai bordi (x=0 e x=1000) deve stare sulla base, y=400")
        if numeri and (min(numeri) < -5 or max(numeri) > 1005):
            errori.append(f"sagome[{i}]: esce dal riquadro 1000×400")
    titolo = str(p.get("titolo", "")).strip()
    if not titolo or len(titolo) > 28:
        errori.append("titolo: da 1 a 28 caratteri")
    return errori


def _svg(progetto: dict, larghezza: float, altezza: float, fronte_x: float,
         ab: float = ABBONDANZA_MM) -> str:
    """Il fondo di tutta la copertina distesa, in millimetri."""
    pw, ph = PAGINA_MM
    oriz = ab + ph * ORIZZONTE
    cielo = progetto["cielo"]
    fermate = "".join(f'<stop offset="{i / (len(cielo) - 1):.3f}" stop-color="{c}"/>'
                      for i, c in enumerate(cielo))
    parti = [
        f'<svg xmlns="http://www.w3.org/2000/svg" width="{larghezza:.3f}mm" height="{altezza:.3f}mm" '
        f'viewBox="0 0 {larghezza:.3f} {altezza:.3f}">',
        f'<defs><linearGradient id="cielo" x1="0" y1="0" x2="0" y2="{oriz:.3f}" '
        f'gradientUnits="userSpaceOnUse">{fermate}</linearGradient></defs>',
        f'<rect x="0" y="0" width="{larghezza:.3f}" height="{oriz + 1:.3f}" fill="url(#cielo)"/>',
    ]
    # Il cielo del fronte: stelle e sole.
    for x, y, r in progetto.get("stelle") or []:
        parti.append(f'<circle cx="{fronte_x + float(x) * pw:.3f}" cy="{ab + float(y) * ph:.3f}" '
                     f'r="{max(0.15, min(1.2, float(r))):.3f}" fill="{progetto["testo"]}" opacity="0.9"/>')
    sole = progetto.get("sole")
    if sole:
        parti.append(f'<circle cx="{fronte_x + float(sole["x"]) * pw:.3f}" cy="{ab + float(sole["y"]) * ph:.3f}" '
                     f'r="{max(4.0, min(30.0, float(sole["r"]))):.3f}" fill="{sole["colore"]}" opacity="0.92"/>')
    for x, y, s in progetto.get("uccelli") or []:
        cx, cy, s = fronte_x + float(x) * pw, ab + float(y) * ph, max(1.0, min(5.0, float(s)))
        parti.append(f'<path d="M{cx - s:.3f},{cy:.3f} q{s / 2:.3f},{-s * 0.6:.3f} {s:.3f},0 '
                     f'q{s / 2:.3f},{-s * 0.6:.3f} {s:.3f},0" fill="none" stroke="{progetto["primo_piano"]}" '
                     f'stroke-width="0.35" stroke-linecap="round" opacity="0.8"/>')
    # La sagoma: 1000×400 unità, base sull'orizzonte, larga quanto il fronte
    # più l'abbondanza a destra (il disegno arriva al taglio).
    k = (pw + ab) / 1000.0
    k = min(k, SAGOMA_MM / 400.0 * (pw + ab) / pw)
    alto = oriz - 400 * k
    for s in progetto["sagome"]:
        parti.append(f'<path transform="translate({fronte_x:.3f},{alto:.3f}) scale({k:.5f})" '
                     f'd="{s["d"]}" fill="{s["colore"]}"/>')
    # La fascia sotto l'orizzonte, su tutta la copertina, e il riflesso.
    parti.append(f'<rect x="0" y="{oriz:.3f}" width="{larghezza:.3f}" height="{altezza - oriz:.3f}" '
                 f'fill="{progetto["primo_piano"]}"/>')
    parti.append(f'<ellipse cx="{fronte_x + pw / 2:.3f}" cy="{oriz + 0.5:.3f}" rx="{pw * 0.36:.3f}" '
                 f'ry="{ph * 0.032:.3f}" fill="{progetto["riflesso"]}"/>')
    parti.append("</svg>")
    return "".join(parti)


def costruisci_html(brief: Brief, libro: dict, dorso_mm: float, progetto: dict,
                    cartella_svg: Path, solo_fronte: bool = False) -> str:
    """La copertina distesa per la tipografia; con `solo_fronte`, la prima
    pagina del PDF digitale: il fronte da solo, 148×210, senza abbondanza."""
    ab = 0.0 if solo_fronte else ABBONDANZA_MM
    pw, ph = PAGINA_MM
    if solo_fronte:
        dorso_mm = 0.0
    larghezza = pw if solo_fronte else 2 * ab + 2 * pw + dorso_mm
    altezza = 2 * ab + ph
    fronte_x = 0.0 if solo_fronte else ab + pw + dorso_mm
    dorso_x = ab + pw
    oriz = ab + ph * ORIZZONTE
    t = progetto["testo"]

    cartella_svg.mkdir(parents=True, exist_ok=True)
    svg = cartella_svg / ("copertina_fronte.svg" if solo_fronte else "copertina_fondo.svg")
    svg.write_text(_svg(progetto, larghezza, altezza, fronte_x, ab), encoding="utf-8")

    nomi = _elenco_nomi(brief.viaggiatori.nomi)
    date = _date(brief)
    visti, tappe = set(), []
    for tp in sorted(brief.tappe, key=lambda x: x.ordine):
        nome = re.sub(r"\s*\(.*?\)", "", tp.luogo).strip()
        if nome.lower() not in visti:
            visti.add(nome.lower())
            tappe.append(nome)
    itinerario = " · ".join(tappe) if len(tappe) > 1 else ""
    titolo = str(progetto["titolo"]).strip().upper()
    corpo_titolo = min(34.0, 300.0 / max(6, len(titolo)))   # i nomi lunghi si stringono
    attacco = _attacco(libro, parole=60)
    per_chi = nomi or "voi"
    libro_titolo = titolo_libro(brief, libro)

    dorso = ""
    if not solo_fronte and dorso_mm >= DORSO_MIN_TESTO_MM:
        corpo_pt = min(8.5, (dorso_mm - 2.2) / 25.4 * 72)
        dorso = f"""
<div class="dorso" style="left:{dorso_x + dorso_mm / 2:.3f}mm; top:{ab + ph / 2:.3f}mm;
     width:{ph - 30:.1f}mm; font-size:{corpo_pt:.1f}pt">
  <span class="marchio-dorso">{html.escape(config.MARCHIO)}</span>
  <span class="titolo-dorso">{html.escape(libro_titolo)}</span>
</div>"""

    retro = "" if solo_fronte else f"""<div class="retro-testo" style="left:{ab + 20:.3f}mm; top:{ab + 30:.3f}mm">
  {f'<p class="attacco">{html.escape(attacco)}</p>' if attacco else ''}
</div>
<div class="retro-testo" style="left:{ab + 20:.3f}mm; top:{oriz + 17:.3f}mm">
  <p class="dedica">Questo libro è stato scritto per {html.escape(per_chi)}, per questo viaggio e per
  queste date: non esiste in nessun'altra copia.</p>
</div>
<div class="retro-testo marchio" style="left:{ab + 20:.3f}mm; top:{ab + ph - 14:.3f}mm; text-align:left">{html.escape(config.MARCHIO)}</div>
"""

    return f"""<!doctype html><html lang="it"><head><meta charset="utf-8">
<link rel="stylesheet" href="libro.css">
<style>
@page {{ size: {larghezza:.3f}mm {altezza:.3f}mm; margin: 0; }}
@page :left, @page :right {{ margin: 0; @top-left {{ content: none; }} @top-right {{ content: none; }}
  @bottom-left {{ content: none; }} @bottom-right {{ content: none; }} }}
@page :first {{ margin: 0; @top-left {{ content: none; }} @top-right {{ content: none; }}
  @bottom-left {{ content: none; }} @bottom-right {{ content: none; }} }}
html, body {{ margin: 0; }}
body {{ position: relative; width: {larghezza:.3f}mm; height: {altezza:.3f}mm; color: {t}; }}
.fondo {{ position: absolute; left: 0; top: 0; width: {larghezza:.3f}mm; height: {altezza:.3f}mm; }}
.blocco {{ position: absolute; width: {pw:.3f}mm; text-align: center; }}
.sopra {{ font-family: "Garamond libro", serif; font-style: italic; font-size: 12pt; opacity: 0.92;
         margin: 0; text-indent: 0; text-align: center; }}
.titolo {{ font-family: "Garamond libro", serif; font-size: {corpo_titolo:.1f}pt; letter-spacing: 0.2em;
          margin: 1.5mm 0 0 0.2em; line-height: 1.05; text-indent: 0; text-align: center; hyphens: none; }}
.regola {{ position: relative; width: 62mm; height: 0.4pt; background: {progetto["accento"]};
          margin: 5mm auto 0; opacity: 0.85; }}
.rombo {{ position: absolute; left: 50%; top: -1.1mm; width: 2.2mm; height: 2.2mm;
         margin-left: -1.1mm; background: {progetto["accento"]}; transform: rotate(45deg); }}
.nomi {{ font-family: "Garamond libro", serif; font-style: italic; font-size: 17pt; margin: 0;
        text-indent: 0; text-align: center; }}
.date {{ font-family: "Garamond libro", serif; font-style: italic; font-size: 10pt; opacity: 0.85;
        margin: 1.5mm 0 0; text-indent: 0; text-align: center; }}
.itinerario {{ font-family: "Archivo libro", sans-serif; font-size: 6.2pt; letter-spacing: 0.18em;
              text-transform: uppercase; opacity: 0.85; margin: 5mm 12mm 0; text-indent: 0;
              text-align: center; line-height: 1.7; }}
.marchio {{ font-family: "Archivo libro", sans-serif; font-weight: 500; font-size: 6.5pt;
           letter-spacing: 0.3em; text-transform: uppercase; opacity: 0.75; text-align: center; }}
.retro-testo {{ position: absolute; width: {pw - 40:.3f}mm; text-align: left; }}
.attacco {{ font-family: "Garamond libro", serif; font-style: italic; font-size: 11pt; line-height: 1.5;
           margin: 0; text-indent: 0; text-align: left; hyphens: auto; }}
.dedica {{ font-family: "Garamond libro", serif; font-size: 9.5pt; line-height: 1.5; opacity: 0.85;
          margin: 0; text-indent: 0; text-align: left; }}
.dorso {{ position: absolute; height: 0; transform-origin: 0 0;
         transform: rotate(-90deg) translate(-50%, -0.5em); white-space: nowrap;
         display: flex; justify-content: space-between; align-items: center; }}
.titolo-dorso {{ font-family: "Garamond libro", serif; }}
.marchio-dorso {{ font-family: "Archivo libro", sans-serif; font-size: 0.72em; font-weight: 500;
                 letter-spacing: 0.2em; text-transform: uppercase; opacity: 0.8; }}
</style></head><body>
<img class="fondo" src="{svg.as_uri()}" alt="">
<div class="blocco" style="left:{fronte_x:.3f}mm; top:{ab + 30:.3f}mm">
  <p class="sopra">{html.escape(str(progetto.get("sopra") or "una guida di viaggio"))}</p>
  <p class="titolo">{html.escape(titolo)}</p>
  <div class="regola"><div class="rombo"></div></div>
</div>
<div class="blocco" style="left:{fronte_x:.3f}mm; top:{oriz + 17:.3f}mm">
  {f'<p class="nomi">{html.escape(nomi)}</p>' if nomi else ''}
  {f'<p class="date">{html.escape(date)}</p>' if date else ''}
  {f'<p class="itinerario">{html.escape(itinerario)}</p>' if itinerario else ''}
</div>
<div class="blocco marchio" style="left:{fronte_x:.3f}mm; top:{ab + ph - 14:.3f}mm">{html.escape(config.MARCHIO)}</div>
{retro}
{dorso}
</body></html>"""


def scrivi_copertina(brief: Brief, libro: dict, dorso_mm: float, progetto: dict,
                     destinazione: Path, solo_fronte: bool = False) -> Path:
    """La copertina in PDF: distesa per la tipografia, o il solo fronte per il PDF."""
    from weasyprint import HTML

    errori = valida_progetto(progetto)
    if errori:
        raise ValueError("Progetto di copertina non valido: " + "; ".join(errori))
    destinazione.parent.mkdir(parents=True, exist_ok=True)
    HTML(string=costruisci_html(brief, libro, dorso_mm, progetto, destinazione.parent, solo_fronte),
         base_url=str(STAMPA_DIR) + "/").write_pdf(str(destinazione))
    return destinazione
