"""Il disegno della mappa di un capitolo, nei colori del libro.

Solo disegno: niente rete, niente modelli. Riceve le geometrie di
OpenStreetMap, i luoghi già geocodificati e le sezioni del capitolo, e scrive
un PNG pronto per il PDF. Tutto quello che c'è sulla mappa viene da dati veri:
le coordinate dal geocoder, le zone dalle sezioni del libro.

Le regole qui sotto sono uscite dalla prova sul capitolo di New York, e sono
generali, non cucite su una città:

- la scala la decide il gruppo principale dei luoghi; un gruppo lontano va in
  un riquadro, perché due puntini a nove chilometri non schiaccino il resto;
- una zona si disegna solo se si gira a piedi; una sezione che sta in due posti
  ha due zone con lo stesso numero; il resto sono puntini;
- nessun nome sotto un puntino, solo quartieri e solo quelli che il capitolo
  nomina; scala e nord nell'angolo più libero.
"""

import math
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

from src import config

FONTS = config.ENGINE_ROOT / "stampa" / "fonts"
for _f in FONTS.glob("*.ttf"):
    font_manager.fontManager.addfont(str(_f))

# La tavolozza del libro (stampa/libro.css), schiarita per i fondi.
CARTA, ACQUA, PARCO = "#fffdf9", "#dde5e7", "#e6e8d9"
STRADA, STRADA_P = "#ede6db", "#d8cdbe"
INCH, INCH2, GRIGIO, TERRA = "#241f1a", "#3d352d", "#7e7469", "#b4522f"

KM = 1 / 111.32           # un chilometro, nelle unità della proiezione
MM = 1 / 25.4             # millimetri → pollici
LARGHEZZA_MM, ALTEZZA_MM = 117, 168   # il blocco di testo di una pagina A5
ZONA_MAX = 1.3 * KM       # più larga di così non è una zona: è una camminata lunga
COLLEGAMENTO = 1.8 * KM   # due luoghi più vicini di così stanno nello stesso gruppo
LONTANO = 3.0 * KM        # un gruppo più distante di così va nel riquadro
RAGGIO_MM = 1.75          # il puntino
RIQUADRO_MM = (38, 46)    # il riquadro per il gruppo lontano
RIQUADRO_MAX_LUOGHI = 4   # oltre, non è un gruppetto lontano: è un'altra metà della città


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
            linee = [LineString(pr.linea(m["g"])) for m in el["m"]
                     if m["ruolo"] in ruoli and len(m["g"]) >= 2]
            return unary_union(list(polygonize(unary_union(linee)))) if linee else None

        esterno = anelli(("outer", ""))
        if esterno is None or esterno.is_empty:
            return []
        interno = anelli(("inner",))
        if interno is not None and not interno.is_empty:
            esterno = esterno.difference(interno)
        return [esterno]
    return []


def terra(costa: list[LineString], cornice: Polygon, punti_strada: list[Point]) -> list:
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
    risultato = []
    for i, p in enumerate(pezzi):
        if voti[i] > 0:
            risultato.append(p)
        elif voti[i] == 0 and albero_strade is not None:
            if len(albero_strade.query(p, predicate="contains")) > 20:
                risultato.append(p)
    return risultato


def _area(ax, geom, **stile) -> None:
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


def _distanza(a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1])


def gruppi(punti: list, soglia: float) -> list[list]:
    """Raggruppa i punti a catena: due punti più vicini della soglia stanno insieme."""
    risultato: list[list] = []
    for p in punti:
        vicini = [g for g in risultato if any(_distanza(p, q) <= soglia for q in g)]
        unito = [p] + [q for g in vicini for q in g]
        risultato = [g for g in risultato if g not in vicini] + [unito]
    return risultato


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


def _spaziato(testo: str) -> str:
    return " ".join(testo.upper())


def _cornice(punti: list, larghezza_mm: float, altezza_mm: float, margine: float):
    xs, ys = [p[0] for p in punti], [p[1] for p in punti]
    x0, x1 = min(xs) - margine, max(xs) + margine
    y0, y1 = min(ys) - margine, max(ys) + margine
    rapporto = larghezza_mm / altezza_mm
    if (x1 - x0) / (y1 - y0) < rapporto:
        extra = ((y1 - y0) * rapporto - (x1 - x0)) / 2
        x0, x1 = x0 - extra, x1 + extra
    else:
        extra = ((x1 - x0) / rapporto - (y1 - y0)) / 2
        y0, y1 = y0 - extra, y1 + extra
    return x0, y0, x1, y1


def _leggi_osm(elementi: list, pr: Proiezione) -> dict:
    geo = {"strade": {"principali": [], "secondarie": [], "minori": []}, "costa": [],
           "acque": [], "parchi": [], "quartieri": [], "punti_strada": []}
    for el in elementi:
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
        elif el["t"] == "n" and tags.get("place") in ("neighbourhood", "quarter"):
            # Solo quartieri: il nome della città in mezzo alla mappa non orienta.
            # Tutti i nomi che ha: a Tokyo «name» è 浅草, e il capitolo dice Asakusa.
            nomi = [tags[k] for k in ("name:it", "name:en", "name") if tags.get(k)]
            if nomi:
                geo["quartieri"].append((nomi, pr.xy(*el["p"])))
    return geo


def _fondo(ax, geo: dict, cornice) -> None:
    x0, y0, x1, y1 = cornice
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, color=ACQUA, zorder=0))
    for t in terra(geo["costa"], box(x0, y0, x1, y1), geo["punti_strada"]):
        _area(ax, t, facecolor=CARTA, edgecolor="none", zorder=1)
    for p in geo["parchi"]:
        if p.area > (0.04 * KM) ** 2:  # i giardinetti sporcano e basta
            _area(ax, p, facecolor=PARCO, edgecolor="none", zorder=2)
    for a in geo["acque"]:
        _area(ax, a, facecolor=ACQUA, edgecolor="none", zorder=3)
    for classe, colore, spessore in (("minori", STRADA, 0.22), ("secondarie", STRADA_P, 0.32),
                                     ("principali", STRADA_P, 0.5)):
        ax.add_collection(LineCollection(geo["strade"][classe], colors=colore,
                                         linewidths=spessore, capstyle="round", zorder=4))


def _zone(ax, luoghi: list, sezioni: list[str], upm: float) -> None:
    """Una zona per ogni gruppo compatto di luoghi della stessa sezione."""
    for sez in sezioni:
        pts = [l["xy"] for l in luoghi if l["sezione"] == sez and l["tipo"] == "luogo"]
        for gruppo in gruppi(pts, COLLEGAMENTO):
            larghezza = max((_distanza(a, b) for a in gruppo for b in gruppo), default=0)
            if len(gruppo) < 2 or larghezza > ZONA_MAX:
                continue
            zona = MultiPoint(gruppo).convex_hull.buffer(0.3 * KM, quad_segs=16)
            _area(ax, zona, facecolor=TERRA, alpha=0.06, edgecolor="none", zorder=5)
            _area(ax, zona, facecolor="none", edgecolor=TERRA, linewidth=0.6,
                  linestyle=(0, (3, 2)), alpha=0.7, zorder=5)
            cima = max(zona.exterior.coords, key=lambda c: c[1])
            ax.text(cima[0], cima[1] + 1.2 * upm, sez, ha="center", va="bottom",
                    fontfamily="Archivo", fontweight="semibold", fontsize=7.5, color=TERRA, zorder=8)


def _quartieri(ax, geo: dict, testo: str, luoghi: list, cornice, upm: float) -> None:
    """I quartieri che il capitolo nomina, e mai sotto un puntino."""
    x0, y0, x1, y1 = cornice
    visti = set()
    for nomi, (qx, qy) in geo["quartieri"]:
        # Il nome con cui lo chiama il capitolo, e solo se lo chiama.
        nome = next((n for n in nomi if n.lower() in testo), None)
        if nome is None:
            continue
        chiave = nome.lower()
        if chiave in visti or not (x0 < qx < x1 and y0 < qy < y1):
            continue
        if any(_distanza((qx, qy), l["xy"]) < 4.2 * upm for l in luoghi):
            continue
        # Un omonimo lontano non è il quartiere del capitolo: «Downtown» c'è anche
        # a Jersey City, di là dal fiume. Si scrive solo vicino a un luogo del libro.
        if min(_distanza((qx, qy), l["xy"]) for l in luoghi) > 1.0 * KM:
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


def _impronta(cornice, angolo: str, upm: float, w_mm: float, h_mm: float):
    x0, y0, x1, y1 = cornice
    pad = 3 * upm
    sx = x0 + pad if "s" in angolo else x1 - pad - w_mm * upm
    sy = y0 + pad if "b" in angolo else y1 - pad - h_mm * upm
    return sx, sy


def _angolo_libero(cornice, luoghi: list, upm: float, w_mm: float, h_mm: float,
                   candidati: list[str], occupati: set[str]) -> str:
    """L'angolo in cui un riquadro di w×h millimetri copre meno puntini.

    Angoli: «as» alto a sinistra, «ad» alto a destra, «bs», «bd» in basso.
    """
    def coperti(angolo):
        sx, sy = _impronta(cornice, angolo, upm, w_mm, h_mm)
        area = box(sx, sy, sx + w_mm * upm, sy + h_mm * upm).buffer(3 * upm)
        return sum(area.contains(Point(l["xy"])) for l in luoghi)

    liberi = [a for a in candidati if a not in occupati] or candidati
    return min(liberi, key=lambda a: (coperti(a), candidati.index(a)))


def _verso(dx: float, dy: float) -> str:
    ang = math.degrees(math.atan2(dy, dx)) % 360
    return ["est", "nord-est", "nord", "nord-ovest", "ovest", "sud-ovest", "sud", "sud-est"][
        int(((ang + 22.5) % 360) // 45)]


def _cornice_sicura(punti: list, larghezza_mm: float, altezza_mm: float, margine_km: float):
    """La cornice con abbastanza aria intorno: la zona tratteggiata di un luogo
    sul bordo, e il suo numero romano sopra, devono stare dentro la mappa."""
    margine = margine_km * KM
    for _ in range(3):
        c = _cornice(punti, larghezza_mm, altezza_mm, margine)
        upm = (c[2] - c[0]) / larghezza_mm
        serve = 0.3 * KM + 6 * upm  # il bordo della zona, più il suo numero
        if serve <= margine:
            return c
        margine = serve
    return c


def nome_tavola(base: Path, indice: int) -> Path:
    """cap_04.png, poi cap_04_b.png per la seconda tavola dello stesso capitolo."""
    return base if indice == 0 else base.with_name(f"{base.stem}_{'abcdefgh'[indice]}{base.suffix}")


def componi(luoghi: list[dict]) -> dict:
    """Decide le tavole: quante, cosa c'è in ognuna, e le loro cornici.

    - Una tavola sola quando i luoghi stanno insieme.
    - Una tavola con un riquadro quando lontano c'è solo un gruppetto (Harlem
      da Midtown): il riquadro è sempre uno zoom AVANTI su quel gruppetto.
    - Due tavole quando la città si divide in due metà vere (Tokyo: Shibuya e
      Shinjuku da una parte, Asakusa, Ginza e Tsukiji dall'altra). Stipare una
      metà in un riquadro la rimpicciolisce invece di ingrandirla, e il
      lettore non capisce più come le due mappe stiano insieme.

    Sta a sé perché serve due volte: al disegno, e prima ancora a chi scarica i
    dati di OpenStreetMap, che deve chiederli solo per i rettangoli che si
    vedranno.
    """
    luoghi = [dict(l) for l in luoghi if "lat" in l and "lon" in l]
    if not luoghi:
        raise ValueError("nessun luogo da disegnare")
    pr = Proiezione(sum(l["lat"] for l in luoghi) / len(luoghi))
    for l in luoghi:
        l["xy"] = pr.xy(l["lon"], l["lat"])
    numerati = [l for l in luoghi if l["tipo"] != "albergo"]
    for n, l in enumerate(numerati, 1):
        l["numero"] = n
    albergo = next((l["xy"] for l in luoghi if l["tipo"] == "albergo"), None)

    # Il gruppo più ricco (a parità, quello dove si dorme) e i gruppi entro
    # LONTANO da lui fanno la tavola principale. Un passo solo, non a catena: a
    # catena Manhattan si salda da Battery Park a Harlem e la mappa si perde.
    tutti_i_gruppi = gruppi([l["xy"] for l in luoghi], COLLEGAMENTO)
    principale = max(tutti_i_gruppi, key=lambda g: (len(g), albergo in g))
    vicini, resto = list(principale), []
    for g in tutti_i_gruppi:
        if g is principale:
            continue
        d = min(_distanza(a, b) for a in g for b in principale)
        (vicini if d <= LONTANO else resto).extend(g)
    qui = [l for l in luoghi if l["xy"] in vicini]
    la = [l for l in luoghi if l["xy"] in resto]

    def tavola(ls, riquadro=None):
        return {"luoghi": ls, "cornice": _cornice_sicura([l["xy"] for l in ls], LARGHEZZA_MM,
                                                          ALTEZZA_MM, 0.45),
                "riquadro": riquadro}

    if not la:
        tavole = [tavola(qui)]
    else:
        prima = tavola(qui)
        tavole = None
        if len(la) <= RIQUADRO_MAX_LUOGHI and len(la) <= 0.35 * len(luoghi):
            cr = _cornice_sicura([l["xy"] for l in la], RIQUADRO_MM[0], RIQUADRO_MM[1], 0.4)
            upm = (prima["cornice"][2] - prima["cornice"][0]) / LARGHEZZA_MM
            upm_r = (cr[2] - cr[0]) / RIQUADRO_MM[0]
            if upm_r <= upm:  # solo se ingrandisce: un riquadro che rimpicciolisce confonde
                prima["riquadro"] = {"luoghi": la, "cornice": cr}
                tavole = [prima]
        if tavole is None:
            tavole = [prima, tavola(la)]

    insieme = None
    if len(tavole) > 1:
        xs = [v for t in tavole for v in (t["cornice"][0], t["cornice"][2])]
        ys = [v for t in tavole for v in (t["cornice"][1], t["cornice"][3])]
        m = 0.04 * max(max(xs) - min(xs), max(ys) - min(ys))
        insieme = (min(xs) - m, min(ys) - m, max(xs) + m, max(ys) + m)
    return {"pr": pr, "luoghi": luoghi, "numerati": numerati, "tavole": tavole, "insieme": insieme}


def _in_gradi(pr: Proiezione, c) -> tuple[float, float, float, float]:
    x0, y0, x1, y1 = c
    return (y0, x0 / pr.k, y1, x1 / pr.k)


def rettangoli_geografici(luoghi: list[dict]) -> list[tuple[float, float, float, float]]:
    """I rettangoli (sud, ovest, nord, est) che le tavole mostreranno, in gradi."""
    c = componi(luoghi)
    fuori = []
    for t in c["tavole"]:
        fuori.append(_in_gradi(c["pr"], t["cornice"]))
        if t["riquadro"]:
            fuori.append(_in_gradi(c["pr"], t["riquadro"]["cornice"]))
    return fuori


def rettangolo_insieme(luoghi: list[dict]) -> tuple[float, float, float, float] | None:
    """Con due tavole, il rettangolo dell'insieme per la mappina d'orientamento."""
    c = componi(luoghi)
    return _in_gradi(c["pr"], c["insieme"]) if c["insieme"] else None


def _scala_e_nord(ax, cornice, upm: float, angolo: str) -> None:
    sx, sy = _impronta(cornice, angolo, upm, 26, 12)
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


def _orientamento(fig, cornice, upm: float, angolo: str, w_mm: float, h_mm: float,
                  insieme, geo_insieme: dict, tavole: list, indice: int, albergo) -> None:
    """La mappina di tutta la città, con le due tavole: questa in terracotta."""
    ox, oy = _impronta(cornice, angolo, upm, w_mm, h_mm)
    axo = fig.add_axes([(ox - cornice[0]) / (cornice[2] - cornice[0]),
                        (oy - cornice[1]) / (cornice[3] - cornice[1]),
                        w_mm / LARGHEZZA_MM, h_mm / ALTEZZA_MM])
    x0, y0, x1, y1 = insieme
    axo.set_xlim(x0, x1)
    axo.set_ylim(y0, y1)
    axo.set_aspect("equal")
    axo.axis("off")
    axo.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, color=ACQUA, zorder=0))
    for t in terra(geo_insieme["costa"], box(x0, y0, x1, y1), geo_insieme["punti_strada"]):
        _area(axo, t, facecolor=CARTA, edgecolor="none", zorder=1)
    for a in geo_insieme["acque"]:
        _area(axo, a, facecolor=ACQUA, edgecolor="none", zorder=2)
    axo.add_collection(LineCollection(geo_insieme["strade"]["principali"], colors=STRADA_P,
                                      linewidths=0.3, zorder=3))
    for i, t in enumerate(tavole):
        tx0, ty0, tx1, ty1 = t["cornice"]
        if i == indice:
            axo.add_patch(plt.Rectangle((tx0, ty0), tx1 - tx0, ty1 - ty0, facecolor=TERRA,
                                        alpha=0.22, edgecolor="none", zorder=4))
            axo.add_patch(plt.Rectangle((tx0, ty0), tx1 - tx0, ty1 - ty0, fill=False,
                                        edgecolor=TERRA, linewidth=0.9, zorder=5))
        else:
            axo.add_patch(plt.Rectangle((tx0, ty0), tx1 - tx0, ty1 - ty0, fill=False,
                                        edgecolor=INCH2, linewidth=0.5, linestyle=(0, (2, 1.5)),
                                        zorder=5))
    if albergo is not None:
        axo.add_patch(Circle(albergo, 0.012 * (x1 - x0), color=INCH, zorder=6))
    axo.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, fill=False, edgecolor=INCH2,
                                linewidth=0.7, zorder=7))


def _salva(fig, destinazione: Path) -> None:
    # Un'immagine raster opaca, non il vettoriale: diciassettemila strade in un
    # flusso solo fanno arrendere molti lettori PDF, che mostrano una pagina
    # bianca — e il libro lo si apre soprattutto dal telefono. 400 dpi è
    # risoluzione di stampa; senza canale alfa, nessuna maschera nel PDF.
    from PIL import Image

    destinazione.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destinazione, facecolor=ACQUA, dpi=400)
    plt.close(fig)
    Image.open(destinazione).convert("RGB").save(destinazione, optimize=True)


def disegna(osm: list, luoghi: list[dict], sezioni: list[str], testo_capitolo: str,
            destinazione: Path, osm_insieme: list | None = None,
            attribuzione: str = "© OpenStreetMap contributors") -> tuple[list[dict], list[Path]]:
    """Disegna le tavole della mappa. Ritorna (luoghi numerati, file scritti).

    `luoghi`: dizionari con lat, lon, sezione, etichetta, tipo ("luogo" | "albergo").
    I luoghi sono numerati nell'ordine in cui arrivano; l'albergo porta una H.
    La prima tavola va in `destinazione`, la seconda accanto (nome_tavola).
    `osm_insieme`: le geometrie essenziali di tutta la città, per la mappina
    d'orientamento quando le tavole sono due.
    """
    c = componi(luoghi)
    pr, luoghi, numerati, tavole = c["pr"], c["luoghi"], c["numerati"], c["tavole"]
    testo = testo_capitolo.lower()
    geo = _leggi_osm(osm, pr)
    geo_insieme = _leggi_osm(osm_insieme, pr) if (osm_insieme and c["insieme"]) else None
    albergo = next((l["xy"] for l in luoghi if l["tipo"] == "albergo"), None)
    scritti = []

    for indice, t in enumerate(tavole):
        cornice, in_mappa = t["cornice"], t["luoghi"]
        upm = (cornice[2] - cornice[0]) / LARGHEZZA_MM
        fig = plt.figure(figsize=(LARGHEZZA_MM * MM, ALTEZZA_MM * MM), dpi=400)
        ax = fig.add_axes([0, 0, 1, 1])
        _fondo(ax, geo, cornice)
        _zone(ax, in_mappa, sezioni, upm)
        _quartieri(ax, geo, testo, in_mappa, cornice, upm)
        _puntini(ax, in_mappa, upm)
        occupati: set[str] = set()

        if t["riquadro"]:
            nel_riquadro, cr = t["riquadro"]["luoghi"], t["riquadro"]["cornice"]
            rw, rh = RIQUADRO_MM
            cx = sum(l["xy"][0] for l in nel_riquadro) / len(nel_riquadro)
            cy = sum(l["xy"][1] for l in nel_riquadro) / len(nel_riquadro)
            mx, my = (cornice[0] + cornice[2]) / 2, (cornice[1] + cornice[3]) / 2
            # A parità di puntini coperti, il riquadro va dalla parte in cui il
            # gruppo sta davvero: Harlem in alto, non in basso a sinistra.
            versi = {"ad": (1, 1), "as": (-1, 1), "bd": (1, -1), "bs": (-1, -1)}
            candidati = sorted(versi, key=lambda a: -(versi[a][0] * (cx - mx) + versi[a][1] * (cy - my)))
            angolo = _angolo_libero(cornice, in_mappa, upm, rw, rh, candidati, occupati)
            occupati.add(angolo)
            ox, oy = _impronta(cornice, angolo, upm, rw, rh)
            axr = fig.add_axes([(ox - cornice[0]) / (cornice[2] - cornice[0]),
                                (oy - cornice[1]) / (cornice[3] - cornice[1]),
                                rw / LARGHEZZA_MM, rh / ALTEZZA_MM])
            upm_r = (cr[2] - cr[0]) / rw
            _fondo(axr, geo, cr)
            _zone(axr, nel_riquadro, sezioni, upm_r)
            _quartieri(axr, geo, testo, nel_riquadro, cr, upm_r)
            _puntini(axr, nel_riquadro, upm_r)
            axr.add_patch(plt.Rectangle((cr[0], cr[1]), cr[2] - cr[0], cr[3] - cr[1], fill=False,
                                        edgecolor=INCH2, linewidth=0.7, zorder=20))
            # Quanto è lontano e da che parte: il riquadro non è in scala con la mappa.
            km = max(1, round(_distanza((cx, cy), (mx, my)) / KM))
            etichetta = f"{km} km a {_verso(cx - mx, cy - my)}"
            sopra = "b" in angolo  # in basso, la scritta va sopra il riquadro
            ty = oy + rh * upm + 1.2 * upm if sopra else oy - 1.2 * upm
            ax.text(ox + rw * upm / 2, ty, _spaziato(etichetta), ha="center",
                    va="bottom" if sopra else "top", fontfamily="Archivo", fontweight="medium",
                    fontsize=4.4, color=INCH2, zorder=21)

        if geo_insieme is not None:
            x0, y0, x1, y1 = c["insieme"]
            r = (x1 - x0) / (y1 - y0)
            w_mm, h_mm = (32.0, max(16.0, 32.0 / r)) if r >= 1 else (max(16.0, 32.0 * r), 32.0)
            angolo = _angolo_libero(cornice, in_mappa, upm, w_mm, h_mm, ["ad", "as", "bd", "bs"], occupati)
            occupati.add(angolo)
            _orientamento(fig, cornice, upm, angolo, w_mm, h_mm, c["insieme"], geo_insieme,
                          tavole, indice, albergo)

        # Scala e nord insieme, nell'angolo più libero in basso.
        angolo = _angolo_libero(cornice, in_mappa, upm, 26, 12, ["bs", "bd"], occupati)
        _scala_e_nord(ax, cornice, upm, angolo)
        x0, y0, x1, y1 = cornice
        ax.text(x1 - 3 * upm, y0 + 2.5 * upm, attribuzione, ha="right", va="bottom",
                fontfamily="Archivo", fontsize=3.8, color=GRIGIO, zorder=12)

        percorso = nome_tavola(destinazione, indice)
        _salva(fig, percorso)
        scritti.append(percorso)

    return ([{k: v for k, v in l.items() if k != "xy"} for l in numerati
             + [l for l in luoghi if l["tipo"] == "albergo"]], scritti)
