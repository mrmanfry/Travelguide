"""La mappa del viaggio: il paese, le tappe, il percorso e i capitoli.

Solo disegno, come mappa_disegno: niente rete, niente modelli. I confini, le
regioni, i laghi e i nomi dei mari vengono da Natural Earth (dominio pubblico);
le tappe e le gite arrivano già geocodificate da OpenStreetMap.

Cosa dice la mappa, e perché così:

- le regioni toccate dal viaggio in tinta leggera, le altre appena accennate:
  a colpo d'occhio si vede che pezzo di paese si attraversa;
- il percorso è una linea morbida da tappa a tappa, con il verso: dice l'ordine
  del viaggio, non finge di essere il binario;
- accanto a ogni tappa i numeri dei capitoli che ne parlano, nella tinta della
  loro famiglia (terracotta le tappe, ardesia i collegamenti): la mappa è anche
  un indice;
- le gite in giornata sono cerchi vuoti legati alla loro base con un tratteggio.
"""

import json
import math
from functools import lru_cache
from pathlib import Path

import matplotlib

matplotlib.use("Agg")
import matplotlib.pyplot as plt
from matplotlib.patches import Circle, FancyArrowPatch
from matplotlib.path import Path as MPath
from shapely.geometry import Point, box, shape
from shapely.ops import polylabel, unary_union

from src.mappa_disegno import (ACQUA, CARTA, GRIGIO, INCH, INCH2, MM, STRADA_P, TERRA, Proiezione,
                               _area, _spaziato)

ARDESIA = "#4a6670"         # la tinta dei collegamenti, come nel libro
ALTRA_TERRA = "#f1ece3"     # i paesi vicini: terra, ma non la vostra
LARGHEZZA_MM = 117
UNIRE_KM = 8                # due tappe più vicine di così sono lo stesso puntino


@lru_cache(maxsize=8)
def _leggi(cartella: str, nome: str) -> list[dict]:
    return json.loads((Path(cartella) / f"{nome}.geojson").read_text(encoding="utf-8"))["features"]


def _km(pr: Proiezione, a, b) -> float:
    return math.hypot(a[0] - b[0], a[1] - b[1]) * 111.32


def _cornice(punti_xy: list, larghezza_mm: float, altezza_mm: float, margine: float):
    xs, ys = [p[0] for p in punti_xy], [p[1] for p in punti_xy]
    x0, x1, y0, y1 = min(xs) - margine, max(xs) + margine, min(ys) - margine, max(ys) + margine
    rapporto = larghezza_mm / altezza_mm
    if (x1 - x0) / (y1 - y0) < rapporto:
        extra = ((y1 - y0) * rapporto - (x1 - x0)) / 2
        x0, x1 = x0 - extra, x1 + extra
    else:
        extra = ((x1 - x0) / rapporto - (y1 - y0)) / 2
        y0, y1 = y0 - extra, y1 + extra
    return x0, y0, x1, y1


def proporzioni(fermate: list[dict], gite: list[dict]) -> float:
    """Larghezza su altezza del viaggio, margini compresi: decide l'impaginazione."""
    pr = Proiezione(sum(f["lat"] for f in fermate) / len(fermate))
    pts = [pr.xy(p["lon"], p["lat"]) for p in fermate + gite]
    xs, ys = [p[0] for p in pts], [p[1] for p in pts]
    m = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.14 + 0.25
    return (max(xs) - min(xs) + 2 * m) / (max(ys) - min(ys) + 2 * m)


def _geometrie(cartella: str, codici: frozenset, cornice_geo, pr: Proiezione):
    """Le geometrie nella cornice, già proiettate."""
    from shapely import affinity

    def proietta(g):
        return affinity.scale(g, xfact=pr.k, yfact=1.0, origin=(0, 0))

    def nella_cornice(features, filtro=lambda p: True):
        fuori = []
        for f in features:
            if not filtro(f["properties"]):
                continue
            g = shape(f["geometry"])
            if g.intersects(cornice_geo):
                fuori.append((f["properties"], g.intersection(cornice_geo)))
        return fuori

    paesi = nella_cornice(_leggi(cartella, "ne_10m_admin_0_countries"))
    def nostro_paese(p):
        return p.get("ISO_A2_EH") in codici or p.get("ISO_A2") in codici

    nostro = [g for p, g in paesi if nostro_paese(p)]
    altri = [g for p, g in paesi if not nostro_paese(p)]
    regioni = nella_cornice(_leggi(cartella, "ne_10m_admin_1_states_provinces"),
                            lambda p: p.get("iso_a2") in codici)
    laghi = nella_cornice(_leggi(cartella, "ne_10m_lakes"))
    mari = nella_cornice(_leggi(cartella, "ne_10m_geography_marine_polys"))
    return {
        "nostro": proietta(unary_union(nostro)) if nostro else None,
        "altri": [proietta(g) for g in altri],
        "regioni": [(p, proietta(g)) for p, g in regioni],
        "laghi": [proietta(g) for _, g in laghi],
        "mari": [(p, proietta(g)) for p, g in mari],
    }


def _bezier(a, b, piega: float = 0.14):
    """Una curva morbida da a a b, piegata a sinistra del verso di marcia."""
    mx, my = (a[0] + b[0]) / 2, (a[1] + b[1]) / 2
    dx, dy = b[0] - a[0], b[1] - a[1]
    c = (mx - dy * piega, my + dx * piega)

    def punto(t):
        return ((1 - t) ** 2 * a[0] + 2 * (1 - t) * t * c[0] + t ** 2 * b[0],
                (1 - t) ** 2 * a[1] + 2 * (1 - t) * t * c[1] + t ** 2 * b[1])

    return MPath([a, c, b], [MPath.MOVETO, MPath.CURVE3, MPath.CURVE3]), punto


class _Etichette:
    """Mette le etichette dove non coprono niente: puntini, altre etichette, bordo."""

    # (dx, dy, allineamento orizzontale, verticale), in ordine di preferenza
    POSTI = [(1, 0, "left", "center"), (-1, 0, "right", "center"), (0.75, 0.75, "left", "bottom"),
             (-0.75, 0.75, "right", "bottom"), (0.75, -0.75, "left", "top"),
             (-0.75, -0.75, "right", "top"), (0, 1, "center", "bottom"), (0, -1, "center", "top")]

    def __init__(self, fig, ax, upm: float):
        self.fig, self.ax, self.upm = fig, ax, upm
        self.r = fig.canvas.get_renderer()
        self.occupati = []  # (rettangolo in coordinate dei dati, peso)
        self.punti = []     # tutti i luoghi con un nome: un nome non deve sembrare di un altro

    def occupa_punto(self, xy, raggio_mm: float, peso: int = 5) -> None:
        if peso >= 5:
            self.punti.append(xy)
        r = raggio_mm * self.upm
        self.occupati.append((box(xy[0] - r, xy[1] - r, xy[0] + r, xy[1] + r), peso))

    def _misura(self, t) -> tuple[float, float]:
        e = t.get_window_extent(self.r).transformed(self.ax.transData.inverted())
        return e.width, e.height

    def metti(self, xy, parti: list[tuple[str, dict]], distanza_mm: float) -> None:
        """Il posto che copre meno: un nome pesa più di un puntino, un puntino più
        di un tratto di linea; fuori dal bordo non si va mai. A parità, il posto
        più vicino e nell'ordine di preferenza."""
        testi = [self.ax.text(0, 0, s, **stile, zorder=30) for s, stile in parti]
        misure = [self._misura(t) for t in testi]
        spazio = 0.9 * self.upm
        w = sum(m[0] for m in misure) + spazio * (len(testi) - 1)
        h = max(m[1] for m in misure)
        (x0, x1), (y0, y1) = self.ax.get_xlim(), self.ax.get_ylim()
        dentro = box(x0 + self.upm, y0 + self.upm, x1 - self.upm, y1 - self.upm)
        migliore = None
        for giro, scala in enumerate((1.0, 1.9)):
            for ordine, (dx, dy, ha, va) in enumerate(self.POSTI):
                d = distanza_mm * scala * self.upm
                ax_, ay_ = xy[0] + dx * d, xy[1] + dy * d
                sx = ax_ if ha == "left" else ax_ - w if ha == "right" else ax_ - w / 2
                sy = ay_ - h / 2 if va == "center" else ay_ if va == "bottom" else ay_ - h
                rett = box(sx, sy, sx + w, sy + h)
                if not dentro.contains(rett):
                    continue
                costo = sum(peso for o, peso in self.occupati if rett.intersects(o))
                # Il nome sta più vicino a un altro luogo che al suo: sembra suo.
                mio = rett.distance(Point(xy))
                if any(rett.distance(Point(q)) < mio - 0.3 * self.upm for q in self.punti if q != xy):
                    costo += 15
                chiave = (costo, giro, ordine)
                if migliore is None or chiave < migliore[0]:
                    migliore = (chiave, sx, sy, rett)
        if migliore is None:  # più grande della mappa: non succede, ma non si cade
            migliore = ((0, 0, 0), xy[0], xy[1], box(xy[0], xy[1], xy[0] + w, xy[1] + h))
        (_, giro, _), sx, sy, rett = migliore
        if giro:  # lontano dal suo luogo: un filo lo lega
            from shapely.ops import nearest_points

            vicino = nearest_points(rett, Point(xy))[0]
            self.ax.plot([xy[0], vicino.x], [xy[1], vicino.y], color=GRIGIO, linewidth=0.35, zorder=29)
        cx = sx
        for t, (mw, _) in zip(testi, misure):
            t.set_position((cx, sy + h / 2))
            t.set_ha("left")
            t.set_va("center")
            cx += mw + spazio
        self.occupati.append((rett.buffer(0.5 * self.upm), 20))


def _scala_km(larghezza_km: float) -> int:
    obiettivo = larghezza_km / 5
    for passo in (10, 20, 25, 50, 100, 200, 250, 500, 1000):
        if passo >= obiettivo * 0.7:
            return passo
    return 1000


def disegna_paese(fermate: list[dict], gite: list[dict], paesi: set[str], cartella_ne: str | Path,
                  destinazione: Path, altezza_mm: float) -> None:
    """Disegna la mappa del viaggio in un PNG largo come il blocco di testo.

    `fermate`: in ordine di viaggio, {nome, lat, lon, capitoli: [(romano, famiglia)]};
    la stessa città due volte (Tokyo all'andata e al ritorno) è un puntino solo.
    `gite`: {nome, lat, lon, base: indice della fermata da cui si parte}.
    `paesi`: i codici ISO a due lettere dei paesi del viaggio (uno, di solito).
    """
    pr = Proiezione(sum(f["lat"] for f in fermate) / len(fermate))
    for p in fermate + gite:
        p["xy"] = pr.xy(p["lon"], p["lat"])

    # I puntini: le fermate vicine diventano una, e ne sommano i capitoli.
    puntini: list[dict] = []
    for i, f in enumerate(fermate):
        stesso = next((q for q in puntini if _km(pr, q["xy"], f["xy"]) < UNIRE_KM), None)
        if stesso:
            stesso["capitoli"] += [c for c in f["capitoli"] if c not in stesso["capitoli"]]
            f["puntino"] = stesso
        else:
            f["puntino"] = {"nome": f["nome"], "xy": f["xy"], "capitoli": list(f["capitoli"])}
            puntini.append(f["puntino"])

    tutti = [p["xy"] for p in fermate + gite]
    xs, ys = [p[0] for p in tutti], [p[1] for p in tutti]
    margine = max(max(xs) - min(xs), max(ys) - min(ys)) * 0.14 + 0.25
    cornice = _cornice(tutti, LARGHEZZA_MM, altezza_mm, margine)
    x0, y0, x1, y1 = cornice
    upm = (x1 - x0) / LARGHEZZA_MM
    geo = _geometrie(str(cartella_ne), frozenset(c.upper() for c in paesi), box(x0 / pr.k, y0, x1 / pr.k, y1), pr)

    fig = plt.figure(figsize=(LARGHEZZA_MM * MM, altezza_mm * MM), dpi=400)
    ax = fig.add_axes([0, 0, 1, 1])
    ax.set_xlim(x0, x1)
    ax.set_ylim(y0, y1)
    ax.set_aspect("equal")
    ax.axis("off")
    ax.add_patch(plt.Rectangle((x0, y0), x1 - x0, y1 - y0, color=ACQUA, zorder=0))

    for g in geo["altri"]:
        _area(ax, g, facecolor=ALTRA_TERRA, edgecolor="none", zorder=1)
    _area(ax, geo["nostro"], facecolor=CARTA, edgecolor="none", zorder=1)

    # Le regioni toccate: quelle che contengono una tappa o una gita. Una gita su
    # un'isoletta (Miyajima) può cadere un soffio fuori dal poligono semplificato.
    toccate = []
    for props, g in geo["regioni"]:
        if any(g.distance(Point(p["xy"])) < 4 * upm for p in fermate + gite):
            toccate.append((props, g))
            _area(ax, g, facecolor=TERRA, alpha=0.09, edgecolor="none", zorder=2)
    for _, g in geo["regioni"]:
        _area(ax, g, facecolor="none", edgecolor=STRADA_P, linewidth=0.3, zorder=3)
    for g in geo["laghi"]:
        _area(ax, g, facecolor=ACQUA, edgecolor="none", zorder=4)

    # I mari, col loro nome italiano, dove c'è più acqua libera.
    terra = unary_union([g for g in geo["altri"]] + ([geo["nostro"]] if geo["nostro"] else []))
    for props, g in sorted(geo["mari"], key=lambda m: m[0].get("scalerank", 9)):
        nome = props.get("name_it") or props.get("name")
        acqua = g.difference(terra.buffer(6 * upm)) if not terra.is_empty else g
        pezzi = [p for p in getattr(acqua, "geoms", [acqua]) if p.geom_type == "Polygon"]
        if not nome or not pezzi:
            continue
        pezzo = max(pezzi, key=lambda p: p.area)
        if pezzo.area < (25 * upm) ** 2:
            continue
        c = polylabel(pezzo, tolerance=upm)
        ax.text(c.x, c.y, _spaziato(nome[0].upper() + nome[1:]), ha="center", va="center",
                fontfamily="EB Garamond", fontstyle="italic", fontsize=6.5, color="#8fa3aa", zorder=5)

    # Il percorso, tappa dopo tappa, con il verso a metà di ogni tratto.
    tracciati = []
    for a, b in zip(fermate[:-1], fermate[1:]):
        pa, pb = a["puntino"]["xy"], b["puntino"]["xy"]
        if pa == pb:
            continue
        percorso, punto = _bezier(pa, pb)
        tracciati.append(punto)
        ax.add_patch(matplotlib.patches.PathPatch(percorso, facecolor="none", edgecolor=ARDESIA,
                                                  linewidth=1.0, capstyle="round", zorder=10))
        if math.hypot(pb[0] - pa[0], pb[1] - pa[1]) < 14 * upm:
            continue  # su un tratto corto la freccia finirebbe sopra il puntino
        ax.add_patch(FancyArrowPatch(punto(0.47), punto(0.53), arrowstyle="-|>", mutation_scale=6,
                                     color=ARDESIA, linewidth=0, zorder=11))

    # Le gite: un tratteggio dalla base, un cerchio vuoto.
    for g in gite:
        base = fermate[g["base"]]["puntino"]["xy"]
        ax.plot([base[0], g["xy"][0]], [base[1], g["xy"][1]], color=TERRA, linewidth=0.6,
                linestyle=(0, (1.2, 1.4)), zorder=12)
        ax.add_patch(Circle(g["xy"], 0.8 * upm, facecolor=CARTA, edgecolor=TERRA,
                            linewidth=0.8, zorder=13))
    for p in puntini:
        ax.add_patch(Circle(p["xy"], 1.2 * upm, facecolor=TERRA, edgecolor=CARTA,
                            linewidth=0.7, zorder=14))

    et = _Etichette(fig, ax, upm)
    for p in puntini:
        et.occupa_punto(p["xy"], 1.5)
    for g in gite:
        et.occupa_punto(g["xy"], 1.0)
    # Anche il percorso e i tratteggi delle gite: un nome tagliato da una linea
    # non si legge.
    for punto in tracciati:
        for i in range(1, 40):
            et.occupa_punto(punto(i / 40), 0.45, peso=1)
    for g in gite:
        a, b = fermate[g["base"]]["puntino"]["xy"], g["xy"]
        for i in range(1, 12):
            et.occupa_punto((a[0] + (b[0] - a[0]) * i / 12, a[1] + (b[1] - a[1]) * i / 12), 0.35, peso=1)
    nome = {"fontfamily": "EB Garamond", "fontsize": 8, "color": INCH}
    for p in sorted(puntini, key=lambda p: -len(p["capitoli"])):
        parti = [(p["nome"], nome)]
        for i, (romano, fam) in enumerate(p["capitoli"]):
            if i:
                parti.append(("·", {"fontfamily": "Archivo", "fontsize": 5.5, "color": GRIGIO}))
            parti.append((romano, {"fontfamily": "Archivo", "fontweight": "semibold", "fontsize": 6,
                                   "color": ARDESIA if fam == "collegamento" else TERRA}))
        et.metti(p["xy"], parti, 2.1)
    for g in gite:
        et.metti(g["xy"], [(g["nome"], {"fontfamily": "EB Garamond", "fontstyle": "italic",
                                        "fontsize": 7, "color": INCH2})], 1.5)

    # Scala e nord, in basso nell'angolo che copre meno.
    km = _scala_km((x1 - x0) * 111.32)
    lung = km / 111.32
    def coperti(sx):
        area = box(sx, y0, sx + lung + 10 * upm, y0 + 14 * upm)
        return sum(area.intersects(o) for o, _ in et.occupati)
    sx = min((x0 + 4 * upm, x1 - 4 * upm - lung - 8 * upm), key=coperti)
    sy = y0 + 5 * upm
    ax.plot([sx, sx + lung], [sy, sy], color=INCH2, linewidth=0.8, zorder=20)
    for xx in (sx, sx + lung):
        ax.plot([xx, xx], [sy - 0.8 * upm, sy + 0.8 * upm], color=INCH2, linewidth=0.6, zorder=20)
    ax.text(sx + lung / 2, sy + 1.6 * upm, f"{km} km", ha="center", va="bottom",
            fontfamily="Archivo", fontsize=5, color=INCH2, zorder=20)
    nx = sx + lung + 6 * upm
    ax.annotate("", xy=(nx, sy + 4 * upm), xytext=(nx, sy - 1 * upm),
                arrowprops={"arrowstyle": "-|>", "color": INCH2, "linewidth": 0.6}, zorder=20)
    ax.text(nx, sy + 5 * upm, "N", ha="center", va="bottom", fontfamily="Archivo",
            fontweight="semibold", fontsize=5.5, color=INCH2, zorder=20)
    ax.text(x1 - 3 * upm, y0 + 2.5 * upm, "Natural Earth · OpenStreetMap", ha="right", va="bottom",
            fontfamily="Archivo", fontsize=3.8, color=GRIGIO, zorder=20)

    destinazione.parent.mkdir(parents=True, exist_ok=True)
    fig.savefig(destinazione, facecolor=ACQUA, dpi=400)
    plt.close(fig)
    from PIL import Image

    Image.open(destinazione).convert("RGB").save(destinazione, optimize=True)
