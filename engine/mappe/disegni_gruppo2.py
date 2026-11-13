"""Secondo gruppo di soggetti per la libreria: bozze da rivedere."""
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


def specchio(meta_sinistra, asse=500):
    """Un profilo simmetrico: si disegna la metà sinistra (dall'alto in basso), la destra è lo specchio."""
    destra = [(2 * asse - x, y) for x, y in reversed(meta_sinistra)]
    return "M" + " L".join(f"{x:g},{y:g}" for x, y in meta_sinistra + destra) + " Z"


def cerchio(cx, cy, r):
    k = 0.5523 * r
    return (f"M{cx - r:g},{cy:g} C{cx - r:g},{cy - k:g} {cx - k:g},{cy - r:g} {cx:g},{cy - r:g} "
            f"C{cx + k:g},{cy - r:g} {cx + r:g},{cy - k:g} {cx + r:g},{cy:g} "
            f"C{cx + r:g},{cy + k:g} {cx + k:g},{cy + r:g} {cx:g},{cy + r:g} "
            f"C{cx - k:g},{cy + r:g} {cx - r:g},{cy + k:g} {cx - r:g},{cy:g} Z ")


# Tour Eiffel, stilizzata come nei manifesti: la base più larga del vero, così a questa scala si
# leggono le gambe incurvate, l'arco (alto, sopra il riflesso) sotto la prima piattaforma, le due piattaforme e la punta.
eiffel = specchio([
    (499, 0), (499, 34), (495, 34), (495, 50), (491, 54), (491, 62),
    (488, 96), (484, 134), (479, 172), (474, 204), (470, 222),
    (461, 222), (461, 233), (467, 233),
    (462, 256), (454, 278), (446, 294),
    (426, 294), (426, 308), (440, 308),
    (432, 326), (420, 350), (406, 376), (386, 400),
    (422, 400), (428, 382), (438, 364), (452, 348), (468, 338), (484, 333), (500, 332)])

# Big Ben: la torre quadrata e sottile, il quadrante chiaro, la cella, la guglia coi pinnacoli;
# a destra, basso, il fianco gotico di Westminster coi suoi pinnacoli.
big_ben = specchio([
    (475, 0), (473, 22), (470, 40), (466, 42), (466, 56), (462, 58),
    (454, 88), (449, 88), (446, 70), (443, 88), (443, 96), (447, 100), (447, 120),
    (442, 122), (442, 174), (449, 178), (449, 400)], asse=475)
pinnacoli = "".join(f"L{x},330 L{x + 3},314 L{x + 6},330 " for x in range(512, 772, 26))
westminster = ("M501,400 L501,330 " + pinnacoli + "L790,330 L790,400 Z "
               "M640,330 L640,300 L646,292 L652,300 L652,330 Z")
quadrante = cerchio(475, 148, 17)


# Sagrada Família: le torri a fuso, una accanto all'altra, la torre di Gesù più alta con la croce.
def fuso(c, larghezza, cima, base=400):
    h = base - cima
    m = larghezza / 2
    return (f"M{c - m:g},{base} L{c - m:g},{cima + h * 0.5:g} C{c - m:g},{cima + h * 0.22:g} {c - 3:g},{cima + 10:g} "
            f"{c:g},{cima} C{c + 3:g},{cima + 10:g} {c + m:g},{cima + h * 0.22:g} {c + m:g},{cima + h * 0.5:g} "
            f"L{c + m:g},{base} Z ") + cerchio(c, cima - 4, 4)


sagrada_vicine = ("M318,400 L318,336 L346,318 L374,336 L626,336 L654,318 L682,336 L682,400 Z "
                  + fuso(340, 22, 128) + fuso(368, 24, 100) + fuso(398, 24, 100) + fuso(426, 22, 128))
sagrada_centro = (fuso(500, 44, 52) + fuso(456, 30, 94) + fuso(544, 30, 94)
                  + fuso(574, 22, 136) + fuso(602, 24, 110) + fuso(632, 24, 110) + fuso(660, 22, 136)
                  + "M497,48 L497,24 L489,24 L489,18 L497,18 L497,8 L503,8 L503,18 L511,18 L511,24 L503,24 L503,48 Z")

# Partenone: il tempio con il frontone e le colonne (il cielo tra l'una e l'altra), sulla rupe dell'Acropoli.
acropoli = ("M90,400 L118,388 L146,370 L170,354 L180,342 L196,337 L208,324 L232,315 L290,309 L360,303 "
            "L700,300 L758,304 L796,311 L818,322 L830,338 L846,346 L860,364 L886,384 L912,400 Z")
colonne = "".join(f"M{x},290 L{x},246 L{x + 13},246 L{x + 13},290 Z " for x in range(392, 690, 25))
partenone = ("M372,300 L372,290 L702,290 L702,300 Z "
             "M378,246 L378,226 L696,226 L696,246 Z "
             "M378,226 L537,198 L696,226 Z " + colonne)

CANDIDATI = {
  "eiffel": {"nome": "Tour Eiffel", "luoghi": ["Parigi", "Francia", "Île-de-France"],
             "tratti": "le quattro gambe che si allargano incurvate, l'arco sotto la prima piattaforma, le due piattaforme, la punta con l'antenna",
             "atmosfera": "sera", "sagome": [{"d": eiffel, "colore": "#1a1922"}],
             "astro": {"x": 0.68, "y": 0.42, "r": 15}, "uccelli": [[0.24, 0.42, 2.2], [0.29, 0.39, 1.7]]},
  "big_ben": {"nome": "Big Ben", "luoghi": ["Londra", "Inghilterra", "Regno Unito"],
              "tratti": "la torre quadrata e sottile col quadrante chiaro, la guglia coi pinnacoli, il fianco gotico di Westminster",
              "atmosfera": "sera", "sagome": [{"d": big_ben + " " + westminster, "colore": "#1b1a23"},
                                              {"d": quadrante, "colore": "#efd9a8"}],
              "astro": {"x": 0.30, "y": 0.42, "r": 13}, "uccelli": [[0.70, 0.40, 2.0], [0.75, 0.37, 1.6]]},
  "sagrada": {"nome": "Sagrada Família", "luoghi": ["Barcellona", "Catalogna", "Spagna"],
              "tratti": "le torri a fuso una accanto all'altra, la torre centrale più alta con la croce",
              "atmosfera": "tramonto", "sagome": [{"d": sagrada_centro, "colore": "#2e2433"},
                                                  {"d": sagrada_vicine, "colore": "#1b1520"}],
              "astro": {"x": 0.27, "y": 0.42, "r": 14}, "uccelli": [[0.72, 0.36, 2.2], [0.77, 0.33, 1.7]]},
  "partenone": {"nome": "Partenone", "luoghi": ["Atene", "Grecia", "Acropoli"],
                "tratti": "il tempio col frontone basso e la fila di colonne, alto sulla rupe dell'Acropoli",
                "atmosfera": "tramonto", "sagome": [{"d": acropoli, "colore": "#2a2029"},
                                                    {"d": partenone, "colore": "#1a1519"}],
                "astro": {"x": 0.72, "y": 0.38, "r": 15}, "uccelli": [[0.22, 0.38, 2.2], [0.27, 0.35, 1.7]]},
}
TITOLI = {"eiffel": "Parigi", "big_ben": "Londra", "sagrada": "Barcellona", "partenone": "Grecia"}
ATMOS = {"eiffel": ["sera", "alba"], "big_ben": ["sera", "notte"], "sagrada": ["tramonto", "alba"],
         "partenone": ["tramonto", "alba"]}

if __name__ == "__main__":
    b = Brief.model_validate({"brief_id": "x", "date": {"inizio": "2026-11-13", "fine": "2026-11-16"},
        "tappe": [{"ordine": 1, "luogo": "X", "notti": 3}], "viaggiatori": {"nomi": ["Ludovica", "Filippo"]}})
    solo = sys.argv[1:] or list(CANDIDATI)
    immagini = []
    for k in solo:
        for a in ATMOS[k]:
            prog = sc.componi({**CANDIDATI[k], "atmosfere": ATMOS[k]}, a, TITOLI[k])
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
