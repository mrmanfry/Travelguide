"""Terzo gruppo di soggetti per la libreria: bozze da rivedere."""
import sys
from pathlib import Path
sys.path.insert(0, "/home/user/Travelguide/engine")
S = Path(__file__).resolve().parent / "uscita_copertine"
S.mkdir(exist_ok=True)
from schema.brief import Brief
from src import scelta_copertina as sc
from src.copertina_illustrata import scrivi_copertina, valida_progetto
import pypdfium2 as p
from PIL import Image


def poligono(punti):
    return "M" + " L".join(f"{x:g},{y:g}" for x, y in punti) + " Z "


def specchio(meta_sinistra, asse=500):
    """Un profilo simmetrico: si disegna la metà sinistra (dall'alto in basso), la destra è lo specchio."""
    destra = [(2 * asse - x, y) for x, y in reversed(meta_sinistra)]
    return poligono(meta_sinistra + destra)


def cerchio(cx, cy, r):
    k = 0.5523 * r
    return (f"M{cx - r:g},{cy:g} C{cx - r:g},{cy - k:g} {cx - k:g},{cy - r:g} {cx:g},{cy - r:g} "
            f"C{cx + k:g},{cy - r:g} {cx + r:g},{cy - k:g} {cx + r:g},{cy:g} "
            f"C{cx + r:g},{cy + k:g} {cx + k:g},{cy + r:g} {cx:g},{cy + r:g} "
            f"C{cx - k:g},{cy + r:g} {cx - r:g},{cy + k:g} {cx - r:g},{cy:g} Z ")


# Islanda, Hallgrímskirkja: la torre centrale con la punta e la cella, e ai lati le colonne
# di basalto che scendono a gradini, come le colonne d'organo.
def _hallgrim():
    # Il profilo è appena concavo: vicino alla torre le colonne calano di più e sono strette,
    # verso l'esterno calano meno e si allargano.
    meta = [(500, 4), (497, 28), (486, 44), (486, 92)]
    x = 486
    for i, w in enumerate([10, 11, 12, 13, 14, 15, 17, 19, 22], start=1):
        y = 92 + 294 * (1 - (1 - i / 9) ** 1.4)
        meta += [(x, y), (x - w, y)]
        x -= w
    meta += [(x, 400)]
    return specchio(meta)


hallgrimskirkja = _hallgrim()
cella = "M493,56 L507,56 L507,74 L493,74 Z"

# Marocco, la Koutoubia: il minareto quadrato coi merli, la lanterna, le tre sfere dorate;
# ai lati due palme, sotto le mura di terra coi merli.
merli_minareto = "".join(f"L{x},112 L{x},102 L{x + 8},102 L{x + 8},112 " for x in range(472, 528, 14))
koutoubia = ("M470,400 L470,112 " + merli_minareto + "L530,112 L530,400 Z "
             "M487,104 L487,66 L491,66 L491,58 L497,58 L497,66 L503,66 L503,58 L509,58 L509,66 L513,66 L513,104 Z "
             "M490,58 C490,48 494,42 500,40 C506,42 510,48 510,58 Z M499,40 L499,12 L501,12 L501,40 Z")
sfere = cerchio(500, 32, 4.5) + cerchio(500, 22, 3.5) + cerchio(500, 14, 2.6)
mura = "M220,400 L220,362 " + "".join(f"L{x},362 L{x},352 L{x + 10},352 L{x + 10},362 " for x in range(230, 770, 22)) + "L780,362 L780,400 Z"


def palma(x, y, verso=1):
    """Una palma: il fusto incurvato e cinque foglie. `verso` -1 la specchia."""
    def X(dx):
        return x + verso * dx
    fusto = (f"M{X(0)},400 C{X(4)},350 {X(14)},300 {X(30)},{y} L{X(36)},{y + 2} "
             f"C{X(22)},{y + 44} {X(12)},350 {X(10)},400 Z ")
    cx, cy = X(33), y
    foglie = ""
    for dx, dy, curva in [(-62, 22, -18), (-44, -18, -26), (8, -38, -20), (52, -10, -22), (64, 30, -8)]:
        ex, ey = cx + verso * dx, cy + dy
        mx, my = cx + verso * dx * 0.5, cy + dy * 0.5 + curva
        foglie += (f"M{cx:g},{cy:g} Q{mx:g},{my - 6:g} {ex:g},{ey:g} Q{mx:g},{my + 4:g} {cx:g},{cy + 3:g} Z ")
    return fusto + foglie


# Egitto, le piramidi di Giza: Chefren dietro (la più alta, sul terreno più alto), Cheope davanti,
# Micerino piccola a destra. Ognuna con la faccia in luce e quella in ombra.
def piramide(cima_x, cima_y, sinistra, destra, spigolo):
    luce = poligono([(cima_x, cima_y), (sinistra, 400), (spigolo, 400)])
    ombra = poligono([(cima_x, cima_y), (spigolo, 400), (destra, 400)])
    return luce, ombra


chefren = piramide(612, 150, 430, 800, 660)
cheope = piramide(410, 172, 200, 624, 452)
micerino = piramide(806, 284, 714, 900, 824)

# Thailandia, Wat Arun: il prang centrale a gradoni (la pannocchia), sulle terrazze;
# ai lati due prang piccoli.
def prang(c, scala, base=400):
    def Y(v):
        return base - (400 - v) * scala
    def W(v):
        return v * scala
    meta = [(c - W(1.5), Y(26)), (c - W(2.5), Y(60)), (c - W(6), Y(80)), (c - W(6), Y(96))]
    y = 100
    while y < 316:
        t = (y - 100) / 216
        w = 10 + 46 * t ** 0.85
        t2 = min(1.0, (y + 18 - 100) / 216)
        w2 = 10 + 46 * t2 ** 0.85
        meta += [(c - W(w + 4), Y(y)), (c - W(w2), Y(y + 18))]
        y += 18
    meta += [(c - W(62), Y(318)), (c - W(62), Y(334)), (c - W(92), Y(338)), (c - W(92), Y(360)),
             (c - W(124), Y(364)), (c - W(124), Y(400))]
    meta[0] = (c, Y(6))
    meta.insert(1, (c - W(1.5), Y(26)))
    return specchio(meta, asse=c)


wat_arun = prang(500, 1.0) + prang(250, 0.48) + prang(750, 0.48)

# USA ovest, Monument Valley: le due Mitten, il blocco a picco sul ghiaione e il «pollice»;
# dietro, più chiara, la Merrick Butte.
def mitten(x, scala=1.0, cima=170, pollice=212):
    def X(v):
        return x + v * scala
    return poligono([(X(0), 400), (X(30), 380), (X(56), 346), (X(74), 312), (X(80), 300),
                     (X(84), cima + 8), (X(100), cima), (X(150), cima - 2), (X(186), cima + 2), (X(198), cima + 10),
                     (X(203), 292), (X(212), 294), (X(216), pollice + 8), (X(222), pollice), (X(230), pollice + 4),
                     (X(236), 294), (X(244), 300), (X(268), 330), (X(296), 364), (X(340), 400)])


monument_vicine = mitten(90) + mitten(560, 1.02, 162, 206)
monument_lontane = ("M396,400 L420,352 L426,330 L438,324 L588,322 L598,330 L604,352 L626,400 Z "
                    "M880,400 L898,360 L904,342 L1000,340 L1000,400 Z")

# San Francisco, il Golden Gate: la torre vicina coi portali, i cavi che scendono e risalgono
# alla torre lontana, l'impalcato che esce dalla collina di Marin.
def torre(c, cima, larghezza, gamba):
    l, r = c - larghezza / 2, c + larghezza / 2
    s = poligono([(l, cima), (l + gamba, cima), (l + gamba, 400), (l, 400)])
    s += poligono([(r - gamba, cima), (r, cima), (r, 400), (r - gamba, 400)])
    h = 322 - cima
    for f in (0.0, 0.3, 0.56, 0.8):
        y = cima + h * f + 2
        s += poligono([(l, y), (r, y), (r, y + gamba * 0.7), (l, y + gamba * 0.7)])
    return s


def cavo(x0, y0, cx, cy, x1, y1, spessore=2.2):
    return (f"M{x0:g},{y0:g} Q{cx:g},{cy:g} {x1:g},{y1:g} L{x1:g},{y1 + spessore:g} "
            f"Q{cx:g},{cy + spessore:g} {x0:g},{y0 + spessore:g} Z ")


golden_gate = (torre(640, 40, 50, 11) + torre(262, 206, 20, 4.5)
               + "M150,322 L1000,322 L1000,331 L150,331 Z "
               + cavo(252, 207, 446, 420, 620, 42) + cavo(660, 42, 820, 300, 1000, 300)
               + "".join(f"M{x},{y} L{x + 1.4},{y} L{x + 1.4},322 L{x},322 Z "
                         for x, y in [(340, 274), (400, 302), (470, 307), (540, 270), (590, 182),
                                      (720, 164), (800, 248), (880, 286)]))
marin = "M0,400 C60,384 110,346 160,326 C200,312 236,318 262,334 C290,352 310,380 330,400 Z"

# Australia, Uluru: il monolito lungo e basso, il capo sinistro più ripido, la schiena tonda;
# i solchi verticali in un secondo tono.
uluru = ("M110,400 C124,370 140,320 166,280 C184,252 204,234 236,226 C276,218 320,222 360,226 "
         "C420,232 470,228 530,232 C610,238 690,250 750,266 C800,280 840,300 866,328 C884,350 898,376 910,400 Z")
solchi = "".join(f"M{x},{y} C{x + 3},{y + 20} {x + 2},{y + 40} {x + 5},{y + 64} L{x + 8},{y + 64} C{x + 5},{y + 40} {x + 6},{y + 20} {x + 4},{y} Z "
                 for x, y in [(186, 254), (222, 232), (268, 222), (330, 224), (420, 230), (520, 232), (620, 242), (716, 258)])

# Sydney, l'Opera: le vele bianche una dietro l'altra, a punta verso sinistra, sul podio.
def vela(tx, ty, bx, base=332):
    return (f"M{tx},{ty} C{tx + (bx - tx) * 0.55:g},{ty + 4} {bx - 6},{ty + 50} {bx},{base} "
            f"L{tx + 10},{base} C{tx + 12},{base - 40} {tx + 6},{ty + 40} {tx},{ty} Z ")


opera_dietro = vela(410, 204, 500) + vela(476, 186, 566) + vela(650, 222, 724)
opera_davanti = (vela(352, 238, 432) + vela(530, 214, 596) + vela(606, 248, 668) + vela(694, 250, 744)
                 + "M318,400 L318,346 L330,332 L770,332 L782,346 L782,400 Z")

CANDIDATI = {
  "hallgrimskirkja": {"nome": "Hallgrímskirkja", "luoghi": ["Reykjavík", "Islanda"],
                      "tratti": "la torre centrale con la punta, e ai lati le colonne di basalto che scendono a gradini",
                      "sagome": [{"d": hallgrimskirkja, "colore": "#1c1d29"}, {"d": cella, "colore": "#e8d6a8"}],
                      "astro": {"x": 0.70, "y": 0.40, "r": 13}, "uccelli": [[0.22, 0.40, 2.0], [0.27, 0.37, 1.6]]},
  "koutoubia": {"nome": "Koutoubia", "luoghi": ["Marrakech", "Marocco"],
                "tratti": "il minareto quadrato coi merli e la lanterna, le tre sfere dorate, due palme, le mura di terra",
                "sagome": [{"d": mura + " " + koutoubia + " " + palma(300, 250) + palma(700, 266, -1), "colore": "#1d1820"},
                           {"d": sfere, "colore": "#e0b060"}],
                "astro": {"x": 0.70, "y": 0.40, "r": 14}, "uccelli": [[0.22, 0.40, 2.2], [0.27, 0.37, 1.7]]},
  "piramidi": {"nome": "Piramidi di Giza", "luoghi": ["Egitto", "Il Cairo", "Giza"],
               "tratti": "le tre piramidi, Chefren dietro la più alta, ognuna con la faccia in luce e quella in ombra",
               "sagome": [{"d": chefren[0], "colore": "#4a3138"}, {"d": chefren[1], "colore": "#241a22"},
                          {"d": cheope[0], "colore": "#553840"}, {"d": cheope[1], "colore": "#2a1e26"},
                          {"d": micerino[0], "colore": "#553840"}, {"d": micerino[1], "colore": "#2a1e26"}],
               "astro": {"x": 0.30, "y": 0.36, "r": 15}, "uccelli": [[0.72, 0.36, 2.2], [0.77, 0.33, 1.7]]},
  "wat_arun": {"nome": "Wat Arun", "luoghi": ["Bangkok", "Thailandia"],
               "tratti": "il prang centrale a gradoni come una pannocchia, le terrazze, due prang piccoli ai lati",
               "sagome": [{"d": wat_arun, "colore": "#1c1722"}],
               "astro": {"x": 0.72, "y": 0.38, "r": 15}, "uccelli": [[0.22, 0.38, 2.2], [0.27, 0.35, 1.7]]},
  "monument_valley": {"nome": "Monument Valley", "luoghi": ["Monument Valley", "Arizona", "Utah", "Page", "Kayenta"],
                      "tratti": "le due Mitten: il blocco a picco sul ghiaione e il pollice staccato; dietro la Merrick Butte",
                      "sagome": [{"d": monument_lontane, "colore": "#3b2a35"}, {"d": monument_vicine, "colore": "#211821"}],
                      "astro": {"x": 0.50, "y": 0.43, "r": 13}, "uccelli": [[0.20, 0.34, 2.0], [0.25, 0.31, 1.6]]},
  "golden_gate": {"nome": "Golden Gate", "luoghi": ["San Francisco", "California"],
                  "tratti": "la torre vicina coi portali, i cavi che scendono e risalgono alla torre lontana, l'impalcato dalla collina",
                  "sagome": [{"d": marin, "colore": "#262230"}, {"d": golden_gate, "colore": "#1a1822"}],
                  "astro": {"x": 0.32, "y": 0.34, "r": 14}, "uccelli": [[0.80, 0.30, 2.0], [0.85, 0.27, 1.6]]},
  "opera_sydney": {"nome": "Opera di Sydney", "luoghi": ["Sydney", "Australia", "Nuovo Galles del Sud"],
                   "tratti": "le vele una dietro l'altra, a punta, sul podio; due toni per la profondità",
                   "sagome": [{"d": opera_dietro, "colore": "#2e2734"}, {"d": opera_davanti, "colore": "#1b1720"}],
                   "astro": {"x": 0.70, "y": 0.38, "r": 15}, "uccelli": [[0.22, 0.38, 2.2], [0.27, 0.35, 1.7]]},
  "uluru": {"nome": "Uluru", "luoghi": ["Uluru", "Ayers Rock", "Territorio del Nord", "Alice Springs", "Australia"],
            "tratti": "il monolito lungo e basso, il capo sinistro più ripido, la schiena tonda, i solchi verticali",
            "sagome": [{"d": uluru, "colore": "#2c1d24"}, {"d": solchi, "colore": "#3c2830"}],
            "astro": {"x": 0.66, "y": 0.36, "r": 15}, "uccelli": [[0.22, 0.38, 2.2], [0.27, 0.35, 1.7]]},
}
TITOLI = {"hallgrimskirkja": "Islanda", "koutoubia": "Marocco", "piramidi": "Egitto", "wat_arun": "Thailandia",
          "monument_valley": "Stati Uniti", "golden_gate": "San Francisco", "uluru": "Australia", "opera_sydney": "Sydney"}
ATMOS = {"hallgrimskirkja": ["notte", "alba"], "koutoubia": ["tramonto", "sera"], "piramidi": ["tramonto", "alba"],
         "wat_arun": ["tramonto", "alba"], "monument_valley": ["tramonto", "sera"],
         "golden_gate": ["sera", "alba"], "uluru": ["tramonto", "alba"], "opera_sydney": ["sera", "alba"]}
for k, v in CANDIDATI.items():
    v["atmosfere"] = ATMOS[k]
    v["atmosfera"] = ATMOS[k][0]

if __name__ == "__main__":
    b = Brief.model_validate({"brief_id": "x", "date": {"inizio": "2026-11-13", "fine": "2026-11-16"},
        "tappe": [{"ordine": 1, "luogo": "X", "notti": 3}], "viaggiatori": {"nomi": ["Ludovica", "Filippo"]}})
    solo = sys.argv[1:] or list(CANDIDATI)
    immagini = []
    for k in solo:
        for a in ATMOS[k]:
            prog = sc.componi(CANDIDATI[k], a, TITOLI[k])
            err = valida_progetto(prog)
            if err:
                print(k, a, err); continue
            f = scrivi_copertina(b, {"capitoli": []}, 0, prog, S / f"{k}_{a}.pdf", solo_fronte=True)
            immagini.append(p.PdfDocument(str(f))[0].render(scale=1).to_pil())
    w, h = immagini[0].size
    col = 4
    righe = (len(immagini) + col - 1) // col
    c = Image.new("RGB", (col * w + (col - 1) * 10, righe * h + (righe - 1) * 10), "#ddd")
    for i, im in enumerate(immagini):
        c.paste(im, ((i % col) * (w + 10), (i // col) * (h + 10)))
    c.save(S / ("griglia_" + "_".join(solo) + ".png" if sys.argv[1:] else "griglia.png")); print("ok", len(immagini))
