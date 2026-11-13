"""Primo gruppo di soggetti per la libreria: bozze da rivedere."""
import json, sys
from pathlib import Path
sys.path.insert(0, "/home/user/Travelguide/engine")
S = Path(__file__).resolve().parent / "uscita_copertine"
from schema.brief import Brief
from src import scelta_copertina as sc
from src.copertina_illustrata import scrivi_copertina, valida_progetto
import pypdfium2 as p
from PIL import Image

def specchio(meta_sinistra, asse=500):
    """Un profilo simmetrico: si disegna la metà sinistra (dall'alto in basso), la destra è lo specchio."""
    destra = [(2 * asse - x, y) for x, y in reversed(meta_sinistra)]
    punti = meta_sinistra + destra
    return "M" + " L".join(f"{x:g},{y:g}" for x, y in punti) + " Z"

# Empire State: zoccolo, fusto altissimo, i gradoni in cima, l'albero d'ormeggio, l'antenna.
empire_cima_sx = [(498, 0), (498, 24), (496, 24), (495, 40), (492, 40), (491, 54), (487, 54), (486, 68),
                  (481, 68), (481, 80), (475, 80), (475, 92), (469, 92), (469, 106)]
empire_sx = empire_cima_sx + [(462, 106), (462, 318), (452, 318), (452, 352), (438, 352), (438, 400)]
empire = specchio(empire_sx)
empire_cima = specchio(empire_cima_sx + [(469, 112)])

# Statua della Libertà: forte a stella, piedistallo a gradini, la veste, il braccio alzato con la fiaccola.
liberta = ("M330,400 L360,372 L640,372 L670,400 Z "
           "M440,372 L446,330 L454,330 L458,262 L470,256 L530,256 L542,262 L546,330 L554,330 L560,372 Z "
           "M474,256 C474,228 478,196 482,170 C484,156 486,146 490,138 L488,132 C486,124 490,114 498,112 "
           "L492,100 L497,104 L496,92 L501,103 L505,90 L506,104 L512,94 L509,108 C514,114 514,124 510,132 "
           "L512,140 C516,146 520,152 522,160 L534,152 L540,186 L524,192 C526,214 528,236 528,256 Z "
           "M486,142 C480,124 474,104 470,84 L466,62 L462,62 L460,44 C460,36 466,30 470,36 C474,30 480,36 478,44 "
           "L476,62 L472,62 C476,86 482,108 492,132 Z")

# Torre di Belém: la torre alta e snella, le garitte tonde a cupola sugli angoli (il suo tratto), il bastione basso.
def garitta(x, y, w=22):
    """Una garitta: il cilindro e la cupola a spicchi, con la punta."""
    m = x + w / 2
    return (f"M{x},{y} L{x},{y - 22} C{x},{y - 34} {x + 4},{y - 40} {m - 1},{y - 42} L{m},{y - 50} "
            f"L{m + 1},{y - 42} C{x + w - 4},{y - 40} {x + w},{y - 34} {x + w},{y - 22} L{x + w},{y} Z ")
merli_b = "".join(f"L{x},318 L{x},306 L{x + 10},306 L{x + 10},318 " for x in range(386, 606, 20))
merli_t = "".join(f"L{x},92 L{x},80 L{x + 8},80 L{x + 8},92 " for x in range(462, 532, 14))
belem = ("M366,400 L366,318 " + merli_b + "L634,318 L634,400 Z "
         "M456,318 L456,92 " + merli_t + "L544,92 L544,318 Z "
         "M476,80 L476,52 L524,52 L524,80 Z "
         + garitta(440, 96) + garitta(538, 96) + garitta(350, 322) + garitta(612, 322)
         + garitta(420, 322) + garitta(558, 322))

# Ha Long: faraglioni alti e ripidi; davanti una giunca con le vele a ventaglio color ruggine (il suo tratto).
halong_lontani = ("M60,400 C64,330 76,268 92,250 C100,242 108,252 114,246 C126,236 136,262 140,310 C144,356 146,384 148,400 Z "
                  "M770,400 C772,320 782,240 798,222 C806,214 814,226 820,218 C834,206 846,236 850,290 C854,346 856,380 858,400 Z "
                  "M880,400 C882,356 894,320 908,316 C920,312 930,336 934,366 L938,400 Z")
halong_vicini = ("M170,400 C170,300 180,180 202,132 C210,116 220,124 226,112 C238,92 254,104 260,136 "
                 "C266,170 268,214 272,232 C282,208 296,196 310,200 C330,206 338,262 342,320 C344,360 346,386 348,400 Z "
                 "M650,400 C652,316 662,214 682,180 C690,166 700,176 706,166 C720,148 738,170 744,214 C750,264 754,340 756,400 Z")
giunca_scafo = "M400,396 L594,396 L612,372 L384,372 Z M436,372 L436,232 L440,232 L440,372 Z M500,372 L500,196 L504,196 L504,372 Z M556,372 L556,248 L560,248 L560,372 Z"
giunca_vele = ("M440,360 L440,236 C468,244 492,272 500,300 C488,320 470,344 440,360 Z "
               "M504,360 L504,200 C538,212 566,250 574,290 C560,318 536,344 504,360 Z "
               "M560,360 L560,252 C584,260 602,284 608,308 C598,326 582,346 560,360 Z")

# Assisi: la basilica di San Francesco con il suo campanile, sul muraglione ad archi del Sacro Convento;
# dietro, il colle che sale verso la Rocca. Gli archi sono un secondo tono, come la neve sul Fuji.
assisi_colle = "M0,400 C200,396 360,380 460,350 C560,320 660,280 760,250 C840,226 920,214 1000,214 L1000,400 Z"
assisi = ("M180,400 L180,286 L420,286 L420,256 L436,244 L436,286 L560,286 L560,250 L620,214 L680,250 L680,286 "
          "L720,286 L720,400 Z "
          "M436,244 L436,156 L442,148 L442,138 L456,124 L470,138 L470,148 L476,156 L476,244 Z")
archi = " ".join(f"M{x},400 L{x},346 C{x},328 {x + 30},328 {x + 30},346 L{x + 30},400 Z" for x in range(196, 700, 44))

CANDIDATI = {
  "empire_state": {"nome": "Empire State Building", "luoghi": ["New York", "Manhattan", "Stati Uniti"],
                   "tratti": "il fusto altissimo e sottile, i gradoni in cima, l'albero d'ormeggio e l'antenna; la cima illuminata",
                   "atmosfera": "notte",
                   "sagome": [{"d": empire, "colore": "#191a26"}, {"d": empire_cima, "colore": "#d8a85c"}],
                   "astro": {"x": 0.68, "y": 0.42, "r": 15}, "uccelli": []},
  "liberta": {"nome": "Statua della Libertà", "luoghi": ["New York", "Stati Uniti"],
              "tratti": "il braccio alzato con la fiaccola, la corona a raggi, la tavola sul fianco, il piedistallo",
              "atmosfera": "tramonto",
              "sagome": [{"d": liberta, "colore": "#1a1a22"}],
              "astro": {"x": 0.64, "y": 0.40, "r": 15}, "uccelli": [[0.24, 0.42, 2.2], [0.29, 0.39, 1.7]]},
  "belem": {"nome": "Torre di Belém", "luoghi": ["Lisbona", "Portogallo"],
            "tratti": "la torre squadrata coi merli, le garitte tonde agli angoli, il bastione basso sull'acqua",
            "atmosfera": "tramonto",
            "sagome": [{"d": belem, "colore": "#1c1a22"}],
            "astro": {"x": 0.70, "y": 0.43, "r": 15}, "uccelli": [[0.22, 0.40, 2.2], [0.27, 0.37, 1.7]]},
  "halong": {"nome": "Baia di Ha Long", "luoghi": ["Vietnam", "Ha Long", "Baia di Halong", "Hanoi"],
             "tratti": "i faraglioni alti e tondeggianti che escono dall'acqua, una giunca con le vele a ventaglio",
             "atmosfera": "alba",
             "sagome": [{"d": halong_lontani, "colore": "#3a3456"}, {"d": halong_vicini, "colore": "#211c33"},
                        {"d": giunca_scafo, "colore": "#17141f"}, {"d": giunca_vele, "colore": "#b4522f"}],
             "astro": {"x": 0.50, "y": 0.38, "r": 16}, "uccelli": [[0.72, 0.30, 2.0], [0.77, 0.28, 1.6]]},
  "assisi": {"nome": "Assisi", "luoghi": ["Assisi", "Umbria", "Spoleto", "Perugia"],
             "tratti": "il colle lungo, la basilica con gli archi e il campanile, la città sul crinale, la Rocca in cima",
             "atmosfera": "sera",
             "sagome": [{"d": assisi_colle, "colore": "#2a2737"}, {"d": assisi, "colore": "#1c1a24"}, {"d": archi, "colore": "#3a3445"}],
             "astro": {"x": 0.70, "y": 0.40, "r": 15}, "uccelli": [[0.22, 0.42, 2.0], [0.27, 0.39, 1.6]]},
}
TITOLI = {"empire_state": "New York", "liberta": "New York", "belem": "Portogallo", "halong": "Vietnam", "assisi": "Umbria"}
ATMOS = {"empire_state": ["notte", "sera"], "liberta": ["tramonto", "sera"], "belem": ["tramonto", "sera"],
         "halong": ["alba", "tramonto"], "assisi": ["sera", "alba"]}
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
