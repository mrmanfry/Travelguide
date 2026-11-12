"""Disegna la mappa di un capitolo da mappa_dati.json, nei colori del libro.

Uso: python3 disegna_mappa.py mappa_dati.json testo_capitolo.txt cartella_uscita

Il disegno non chiede niente a nessun modello: geometrie da OpenStreetMap,
coordinate dal geocoder, zone calcolate dalle sezioni del capitolo.
"""

import json
import math
import sys
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib import font_manager
from matplotlib.collections import LineCollection
from matplotlib.patches import Circle, PathPatch
from matplotlib.path import Path as MPath
from shapely import STRtree
from shapely.geometry import LineString, MultiPoint, Point, Polygon, box
from shapely.ops import polygonize, unary_union

FONTS = Path(__file__).resolve().parents[1] / "stampa" / "fonts"
for f in FONTS.glob("*.ttf"):
    font_manager.fontManager.addfont(str(f))
plt.rcParams["svg.fonttype"] = "path"

# La tavolozza è quella del libro (stampa/libro.css), schiarita per i fondi.
CARTA, ACQUA, PARCO = "#fffdf9", "#dde5e7", "#e6e8d9"
STRADA, STRADA_P = "#ede6db", "#d8cdbe"
INCH, INCH2, GRIGIO, TERRA, ARDESIA = "#241f1a", "#3d352d", "#7e7469", "#b4522f", "#4a6670"

KM = 1 / 111.32           # un chilometro, nelle unità della proiezione
MM = 1 / 25.4             # millimetri → pollici, per le misure della figura
LARGHEZZA_MM, ALTEZZA_MM = 117, 168   # il blocco di testo di una pagina A5

SEZIONI = {
    "I": "Times Square, da sola, la prima notte",
    "II": "Grand Central e il Flatiron",
    "III": "East Village",
    "IV": "Il ponte, guardando indietro",
    "V": "Harlem",
    "VI": "Downtown",
    "VII": "Ghisa, cannoli, dim sum, e tre volte in cima",
}


class Proiezione:
    """Equirettangolare locale: a scala di città basta, e conserva le proporzioni."""

    def __init__(self, lat0: float):
        self.k = math.cos(math.radians(lat0))

    def xy(self, lon: float, lat: float) -> tuple[float, float]:
        return (lon * self.k, lat)

    def linea(self, punti: list) -> list[tuple[float, float]]:
        return [self.xy(*p) for p in punti]


def _poligoni(el: dict, pr: Proiezione) -> list:
    if el["t"] == "w":
        g = el["g"]
        if len(g) >= 4 and g[0] == g[-1]:
            p = Polygon(pr.linea(g))
            return [p if p.is_valid else p.buffer(0)]
        return []
    if el["t"] == "r":
        def anelli(ruoli):
            linee = [LineString(pr.linea(m["g"])) for m in el["m"] if m["ruolo"] in ruoli and len(m["g"]) >= 2]
            return unary_union(list(polygonize(unary_union(linee)))) if linee else None
        esterno = anelli(("outer", ""))
        if esterno is None or esterno.is_empty:
            return []
        interno = anelli(("inner",))
        if interno is not None and not interno.is_empty:
            esterno = esterno.difference(interno)
        return [esterno]
    return []


def _terra(costa: list[LineString], cornice: Polygon, punti_strada: list[Point]) -> list:
    """La terraferma dentro la cornice, dalla linea di costa.

    Regola di OpenStreetMap: la costa è disegnata con la terra a SINISTRA del
    verso di percorrenza. La cornice viene tagliata dalla costa in pezzi, e ogni
    pezzo vota terra o acqua secondo da che parte stanno i suoi bordi di costa.
    Un pezzo che non vota si decide dalle strade: dove ce ne sono, è terra.
    """
    if not costa:
        return [cornice]
    tagliate = [c.intersection(cornice) for c in costa]
    rete = unary_union([cornice.exterior] + [t for t in tagliate if not t.is_empty])
    pezzi = list(polygonize(rete))
    if not pezzi:
        return [cornice]
    albero = STRtree(pezzi)
    voti = [0] * len(pezzi)
    for linea in costa:
        c = list(linea.coords)
        passo = max(1, len(c) // 40)
        for a, b in list(zip(c[:-1], c[1:]))[::passo]:
            mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
            dx, dy = b[0] - a[0], b[1] - a[1]
            n = math.hypot(dx, dy) or 1.0
            ox, oy = -dy / n * 2e-5, dx / n * 2e-5
            for punto, segno in ((Point(mx + ox, my + oy), 1), (Point(mx - ox, my - oy), -1)):
                for i in albero.query(punto, predicate="intersects"):
                    voti[i] += segno
    albero_strade = STRtree(punti_strada) if punti_strada else None
    terra = []
    for i, p in enumerate(pezzi):
        if voti[i] > 0:
            terra.append(p)
        elif voti[i] == 0 and albero_strade is not None:
            if len(albero_strade.query(p, predicate="contains")) > 20:
                terra.append(p)
    return terra


def _disegna_area(ax, geom, **stile) -> None:
    if geom is None or geom.is_empty:
        return
    for p in getattr(geom, "geoms", [geom]):
        if p.geom_type != "Polygon":
            continue
        vertici, codici = [], []
        for anello in [p.exterior, *p.interiors]:
            c = list(anello.coords)
            vertici += c
            codici += [MPath.MOVETO] + [MPath.LINETO] * (len(c) - 2) + [MPath.CLOSEPOLY]
        ax.add_patch(PathPatch(MPath(vertici, codici), **stile))


def _separa(punti: list, distanza: float, giri: int = 300) -> list:
    """Allontana i puntini che si sovrappongono, il minimo indispensabile."""
    pos = [list(p) for p in punti]
    for _ in range(giri):
        mosso = False
        for i in range(len(pos)):
            for j in range(i + 1, len(pos)):
                dx, dy = pos[j][0] - pos[i][0], pos[j][1] - pos[i][1]
                d = math.hypot(dx, dy)
                if d < distanza:
                    if d < 1e-12:
                        dx, dy, d = distanza, 0.0, distanza
                    spinta = (distanza - d) / 2 + 1e-9
                    ux, uy = dx / d, dy / d
                    pos[i][0] -= ux * spinta
                    pos[i][1] -= uy * spinta
                    pos[j][0] += ux * spinta
                    pos[j][1] += uy * spinta
                    mosso = True
        if not mosso:
            break
    return pos


def _gruppi(punti: list, soglia: float) -> list[list]:
    """Raggruppa i punti a catena: due punti più vicini della soglia stanno insieme."""
    gruppi: list[list] = []
    for p in punti:
        vicini = [g for g in gruppi if any(math.hypot(p[0] - q[0], p[1] - q[1]) <= soglia for q in g)]
        unito = [p] + [q for g in vicini for q in g]
        gruppi = [g for g in gruppi if g not in vicini] + [unito]
    return gruppi


def _spaziato(testo: str) -> str:
    return " ".join(testo.upper())


ZONA_MAX = 1.3 * KM        # più larga di così non è una zona: è una camminata lunga
COLLEGAMENTO = 1.8 * KM    # due luoghi più vicini di così stanno nello stesso gruppo
LONTANO = 3.0 * KM         # un gruppo più distante di così va nel riquadro
RAGGIO_MM = 1.75           # il puntino


def _distanza(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def _cornice(punti: list, larghezza_mm: float, altezza_mm: float, margine: float):
    xs, ys = [p[0] for p in punti], [p[1] for p in punti]
    x0, x1, y0, y1 = min(xs) - margine, max(xs) + margine, min(ys) - margine, max(ys) + margine
    rapporto = larghezza_mm / altezza_mm
    if (x1 - x0) / (y1 - y0) < rapporto:
        extra = ((y1 - y0) * rapporto - (x1 - x0)) / 2
        x0, x1 = x0 - extra, x1 + extra
    else:
        extra = ((x1 - x0) / rapporto - (y1 - y0)) / 2
        y0, y1 = y0 - extra, y1 + extra
    return x0, y0, x1, y1


def _leggi_osm(dati: dict, pr: Proiezione) -> dict:
    geo = {"strade": {"principali": [], "secondarie": [], "minori": []}, "costa": [],
           "acque": [], "parchi": [], "quartieri": [], "punti_strada": []}
    for el in dati["osm"]:
        tags = el["tags"]
        if el["t"] == "w" and "highway" in tags:
            linea = pr.linea(el["g"])
            h = tags["highway"]
            classe = ("principali" if h in ("motorway", "trunk", "primary")
                      else "secondarie" if h in ("secondary", "tertiary") else "minori")
            geo["strade"][classe].append(linea)
            geo["punti_strada"].extend(Point(p) for p in linea[:: max(1, len(linea) // 3)])
        elif el["t"] == "w" and tags.get("natural") == "coastline":
            geo["costa"].append(LineString(pr.linea(el["g"])))
        elif tags.get("natural") == "water":
            geo["acque"].extend(_poligoni(el, pr))
        elif tags.get("leisure") == "park":
            geo["parchi"].extend(_poligoni(el, pr))
        elif el["t"] == "n" and tags.get("name") and tags.get("place") in ("neighbourhood", "quarter"):
            # Solo quartieri: il nome della città o del borough in mezzo alla
            # mappa non orienta nessuno.
            geo["quartieri"].append((tags["name"], pr.xy(*el["p"])))
    return geo


def _fondo(ax, geo: dict, cornice) -> None:
    x0, y0, x1, y1 = cornice
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, color=ACQUA, zorder=0))
    rettangolo = box(x0, y0, x1, y1)
    for t in _terra(geo["costa"], rettangolo, geo["punti_strada"]):
        _disegna_area(ax, t, facecolor=CARTA, edgecolor="none", zorder=1)
    for p in geo["parchi"]:
        if p.area > (0.04 * KM) ** 2:  # i giardinetti sporcano e basta
            _disegna_area(ax, p, facecolor=PARCO, edgecolor="none", zorder=2)
    for a in geo["acque"]:
        _disegna_area(ax, a, facecolor=ACQUA, edgecolor="none", zorder=3)
    for classe, colore, spessore in (("minori", STRADA, 0.22), ("secondarie", STRADA_P, 0.32),
                                     ("principali", STRADA_P, 0.5)):
        ax.add_collection(LineCollection(geo["strade"][classe], colors=colore,
                                         linewidths=spessore, capstyle="round", zorder=4))


def _zone(ax, luoghi: list, upm: float) -> None:
    """Una zona per ogni gruppo compatto di luoghi della stessa sezione."""
    for sez in SEZIONI:
        pts = [l["xy"] for l in luoghi if l["sezione"] == sez and l["tipo"] == "luogo"]
        for gruppo in _gruppi(pts, COLLEGAMENTO):
            larghezza = max((_distanza(a, b) for a in gruppo for b in gruppo), default=0)
            if len(gruppo) < 2 or larghezza > ZONA_MAX:
                continue
            zona = MultiPoint(gruppo).convex_hull.buffer(0.3 * KM, quad_segs=16)
            _disegna_area(ax, zona, facecolor=TERRA, alpha=0.06, edgecolor="none", zorder=5)
            _disegna_area(ax, zona, facecolor="none", edgecolor=TERRA, linewidth=0.6,
                          linestyle=(0, (3, 2)), alpha=0.7, zorder=5)
            cima = max(zona.exterior.coords, key=lambda c: c[1])
            ax.text(cima[0], cima[1] + 1.2 * upm, sez, ha="center", va="bottom",
                    fontfamily="Archivo", fontweight="semibold", fontsize=7.5, color=TERRA, zorder=8)


def _quartieri(ax, geo: dict, testo: str, luoghi: list, cornice, upm: float) -> None:
    """I quartieri che il capitolo nomina, e mai sotto un puntino."""
    x0, y0, x1, y1 = cornice
    visti = set()
    for nome, (qx, qy) in geo["quartieri"]:
        chiave = nome.lower()
        if chiave in visti or chiave not in testo or not (x0 < qx < x1 and y0 < qy < y1):
            continue
        if any(_distanza((qx, qy), l["xy"]) < 4.2 * upm for l in luoghi):
            continue
        visti.add(chiave)
        ax.text(qx, qy, _spaziato(nome), ha="center", va="center", fontfamily="Archivo",
                fontweight="medium", fontsize=4.6, color=GRIGIO, alpha=0.85, zorder=6)


def _puntini(ax, luoghi: list, upm: float) -> None:
    raggio = RAGGIO_MM * upm
    spostati = _separa([l["xy"] for l in luoghi], 2.15 * raggio)
    for l, (px, py) in zip(luoghi, spostati):
        vx, vy = l["xy"]
        if math.hypot(px - vx, py - vy) > 0.4 * raggio:
            ax.plot([vx, px], [vy, py], color=INCH2, linewidth=0.35, zorder=9)
            ax.add_patch(Circle((vx, vy), 0.35 * upm, color=INCH2, zorder=9))
        colore = INCH if l["tipo"] == "albergo" else TERRA
        ax.add_patch(Circle((px, py), raggio, facecolor=colore, edgecolor=CARTA,
                            linewidth=0.5, zorder=10))
        ax.text(px, py - 0.05 * upm, "H" if l["tipo"] == "albergo" else str(l["numero"]),
                ha="center", va="center", fontfamily="Archivo", fontweight="semibold",
                fontsize=4.6, color=CARTA, zorder=11)


def _angolo_libero(cornice, luoghi: list, upm: float, w_mm: float, h_mm: float,
                   candidati: list[str], occupati: set[str]) -> str:
    """L'angolo in cui un riquadro di w×h millimetri copre meno puntini."""
    x0, y0, x1, y1 = cornice
    pad = 3 * upm

    def impronta(a):
        w, h = w_mm * upm, h_mm * upm
        sx = x0 + pad if "s" in a else x1 - pad - w
        sy = y0 + pad if "b" in a else y1 - pad - h
        return box(sx, sy, sx + w, sy + h)

    liberi = [a for a in candidati if a not in occupati]
    return min(liberi, key=lambda a: (sum(impronta(a).buffer(3 * upm).contains(Point(l["xy"]))
                                          for l in luoghi), candidati.index(a)))


def _origine(cornice, angolo: str, upm: float, w_mm: float, h_mm: float):
    x0, y0, x1, y1 = cornice
    pad = 3 * upm
    sx = x0 + pad if "s" in angolo else x1 - pad - w_mm * upm
    sy = y0 + pad if "b" in angolo else y1 - pad - h_mm * upm
    return sx, sy


def disegna(dati: dict, testo_capitolo: str, uscita: Path) -> list[dict]:
    luoghi = [l for l in dati["luoghi"] if "lat" in l]
    pr = Proiezione(sum(l["lat"] for l in luoghi) / len(luoghi))
    for l in luoghi:
        l["xy"] = pr.xy(l["lon"], l["lat"])
    numerati = [l for l in luoghi if l["tipo"] == "luogo"]
    for n, l in enumerate(numerati, 1):
        l["numero"] = n
    testo = testo_capitolo.lower()
    geo = _leggi_osm(dati, pr)

    # Il gruppo principale decide la scala. Un gruppo lontano (Harlem, a sei
    # chilometri) non deve schiacciare tutto il resto: va in un riquadro.
    gruppi = _gruppi([l["xy"] for l in luoghi], COLLEGAMENTO)
    principale = max(gruppi, key=len)
    vicini, lontani = list(principale), []
    for g in gruppi:
        if g is principale:
            continue
        d = min(_distanza(a, b) for a in g for b in principale)
        (vicini if d <= LONTANO else lontani).extend(g)
    in_mappa = [l for l in luoghi if l["xy"] in vicini]
    nel_riquadro = [l for l in luoghi if l["xy"] in lontani]

    cornice = _cornice(vicini, LARGHEZZA_MM, ALTEZZA_MM, 0.45 * KM)
    upm = (cornice[2] - cornice[0]) / LARGHEZZA_MM
    fig = plt.figure(figsize=(LARGHEZZA_MM * MM, ALTEZZA_MM * MM), dpi=300)
    ax = fig.add_axes([0, 0, 1, 1])
    _fondo(ax, geo, cornice)
    _zone(ax, in_mappa, upm)
    _quartieri(ax, geo, testo, in_mappa, cornice, upm)
    _puntini(ax, in_mappa, upm)

    occupati: set[str] = set()
    if nel_riquadro:
        rw, rh = 38, 46
        angolo = _angolo_libero(cornice, in_mappa, upm, rw, rh, ["ad", "as", "bd", "bs"], occupati)
        occupati.add(angolo)
        ox, oy = _origine(cornice, angolo, upm, rw, rh)
        fx = (ox - cornice[0]) / (cornice[2] - cornice[0])
        fy = (oy - cornice[1]) / (cornice[3] - cornice[1])
        axr = fig.add_axes([fx, fy, rw / LARGHEZZA_MM, rh / ALTEZZA_MM])
        cr = _cornice([l["xy"] for l in nel_riquadro], rw, rh, 0.5 * KM)
        upm_r = (cr[2] - cr[0]) / rw
        _fondo(axr, geo, cr)
        _zone(axr, nel_riquadro, upm_r)
        _quartieri(axr, geo, testo, nel_riquadro, cr, upm_r)
        _puntini(axr, nel_riquadro, upm_r)
        axr.add_patch(plt.Rectangle((cr[0], cr[1]), cr[2] - cr[0], cr[3] - cr[1], fill=False,
                                    edgecolor=INCH2, linewidth=0.7, zorder=20))
        # Quanto è lontano, e da che parte: il riquadro non è a scala con la mappa.
        cx = sum(l["xy"][0] for l in nel_riquadro) / len(nel_riquadro)
        cy = sum(l["xy"][1] for l in nel_riquadro) / len(nel_riquadro)
        mx, my = (cornice[0] + cornice[2]) / 2, (cornice[1] + cornice[3]) / 2
        km = round(_distanza((cx, cy), (mx, my)) / KM)
        ang = math.degrees(math.atan2(cy - my, cx - mx)) % 360
        verso = ["est", "nord-est", "nord", "nord-ovest", "ovest", "sud-ovest", "sud", "sud-est"][
            int(((ang + 22.5) % 360) // 45)]
        etichetta = f"{km} km più a {verso}" if verso in ("nord", "sud", "est", "ovest") else f"{km} km a {verso}"
        ax.text(ox + rw * upm / 2, oy - 1.2 * upm, _spaziato(etichetta), ha="center", va="top",
                fontfamily="Archivo", fontweight="medium", fontsize=4.4, color=INCH2, zorder=21)

    # Scala e nord insieme, nell'angolo più libero in basso.
    angolo = _angolo_libero(cornice, in_mappa, upm, 26, 12, ["bs", "bd"], occupati)
    sx, sy = _origine(cornice, angolo, upm, 26, 12)
    sy += 3 * upm
    ax.plot([sx, sx + KM], [sy, sy], color=INCH2, linewidth=0.8, zorder=12)
    for xx in (sx, sx + KM):
        ax.plot([xx, xx], [sy - 0.8 * upm, sy + 0.8 * upm], color=INCH2, linewidth=0.6, zorder=12)
    ax.text(sx + KM / 2, sy + 1.6 * upm, "1 km", ha="center", va="bottom",
            fontfamily="Archivo", fontsize=5, color=INCH2, zorder=12)
    nx = sx + KM + 6 * upm
    ax.annotate("", xy=(nx, sy + 4 * upm), xytext=(nx, sy - 1 * upm),
                arrowprops={"arrowstyle": "-|>", "color": INCH2, "linewidth": 0.6}, zorder=12)
    ax.text(nx, sy + 5 * upm, "N", ha="center", va="bottom", fontfamily="Archivo",
            fontweight="semibold", fontsize=5.5, color=INCH2, zorder=12)
    x0, y0, x1, y1 = cornice
    ax.text(x1 - 3 * upm, y0 + 2.5 * upm, dati.get("attribuzione", ""),
            ha="right", va="bottom", fontfamily="Archivo", fontsize=3.8, color=GRIGIO, zorder=12)

    uscita.mkdir(parents=True, exist_ok=True)
    fig.savefig(uscita / "mappa.svg", facecolor=ACQUA)
    fig.savefig(uscita / "mappa.png", facecolor=ACQUA, dpi=220)
    plt.close(fig)
    return numerati + [l for l in luoghi if l["tipo"] == "albergo"]


def pagina_a5(tutti: list[dict], uscita: Path) -> Path:
    """Mappa e legenda in due pagine A5, col foglio di stile del libro."""
    from weasyprint import HTML

    righe = []
    for sez, titolo in SEZIONI.items():
        voci = [l for l in tutti if l["sezione"] == sez and l["tipo"] == "luogo"]
        if not voci:
            continue
        elenco = "".join(
            f'<li><span class="n">{l["numero"]}</span>{l["etichetta"]}</li>' for l in voci
        )
        righe.append(f'<div class="gruppo"><p class="sez"><span class="r">{sez}</span>'
                     f'{titolo}</p><ul>{elenco}</ul></div>')
    albergo = next((l for l in tutti if l["tipo"] == "albergo"), None)
    if albergo:
        righe.append(f'<div class="gruppo"><p class="sez"><span class="h">H</span>'
                     f'Il tuo albergo: {albergo["etichetta"]}</p></div>')

    html = f"""<html><head><style>
@font-face {{ font-family: G; src: url("{FONTS}/EBGaramond-Regular.ttf"); }}
@font-face {{ font-family: A; src: url("{FONTS}/Archivo-Medium.ttf"); font-weight: 500; }}
@font-face {{ font-family: A; src: url("{FONTS}/Archivo-SemiBold.ttf"); font-weight: 600; }}
@page {{ size: 148mm 210mm; margin: 16mm 14mm 17mm 17mm; background: {CARTA}; }}
body {{ margin: 0; font-family: G, serif; color: {INCH2}; }}
.mappa {{ width: 117mm; height: 168mm; display: block; }}
.legenda {{ break-before: page; }}
.occhio {{ font-family: A; font-weight: 500; font-size: 7pt; letter-spacing: .22em;
          text-transform: uppercase; color: {TERRA}; margin: 0 0 3mm; }}
h2 {{ font-weight: 400; font-size: 17pt; margin: 0 0 7mm; color: {INCH}; }}
.colonne {{ columns: 2; column-gap: 7mm; }}
.gruppo {{ break-inside: avoid; margin: 0 0 4.5mm; }}
.sez {{ font-family: A; font-weight: 600; font-size: 6.8pt; letter-spacing: .12em;
       text-transform: uppercase; color: {INCH2}; margin: 0 0 1.5mm; line-height: 1.35; }}
.sez .r {{ color: {TERRA}; margin-right: 1.6mm; }}
.sez .h {{ display: inline-block; width: 3.6mm; height: 3.6mm; border-radius: 50%;
          background: {INCH}; color: {CARTA}; text-align: center; line-height: 3.6mm;
          margin-right: 1.6mm; font-size: 5.5pt; letter-spacing: 0; }}
ul {{ list-style: none; margin: 0; padding: 0; }}
li {{ font-size: 9.2pt; line-height: 1.35; margin: 0 0 .8mm; padding-left: 5.2mm; text-indent: -5.2mm; }}
.n {{ display: inline-block; width: 3.6mm; height: 3.6mm; border-radius: 50%; background: {TERRA};
     color: {CARTA}; font-family: A; font-weight: 600; font-size: 5.3pt; text-align: center;
     line-height: 3.6mm; margin-right: 1.6mm; text-indent: 0; vertical-align: .3mm; }}
</style></head><body>
<img class="mappa" src="mappa.svg">
<section class="legenda"><p class="occhio">La mappa del capitolo</p>
<h2>New York a piedi</h2><div class="colonne">{"".join(righe)}</div></section>
</body></html>"""
    pdf = uscita / "pagina_prova.pdf"
    HTML(string=html, base_url=str(uscita) + "/").write_pdf(str(pdf))
    return pdf


if __name__ == "__main__":
    dati = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
    testo = Path(sys.argv[2]).read_text(encoding="utf-8") if len(sys.argv) > 2 else ""
    uscita = Path(sys.argv[3]) if len(sys.argv) > 3 else Path("mappa_uscita")
    tutti = disegna(dati, testo, uscita)
    print("scritto", pagina_a5(tutti, uscita))
