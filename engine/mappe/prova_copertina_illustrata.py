import json, sys
from pathlib import Path
sys.path.insert(0, "/home/user/Travelguide/engine")
S = Path(__file__).resolve().parent / "uscita_copertine"
UP = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(".")
from schema.brief import Brief, Tappa
from src.copertina_illustrata import scrivi_copertina
import pypdfium2 as p

GIAPPONE = {
  "titolo": "Giappone", "sopra": "una guida di viaggio",
  "cielo": ["#1d2246", "#46376a", "#b9637a", "#f0b088"],
  "sole": {"x": 0.68, "y": 0.43, "r": 15, "colore": "#e0573f"},
  "uccelli": [[0.22, 0.40, 2.6], [0.27, 0.37, 2.0], [0.31, 0.415, 1.6]],
  "sagome": [
    {"d": "M0,400 C150,394 300,330 410,192 L472,118 C488,104 512,104 528,118 L590,192 C700,330 850,394 1000,400 Z", "colore": "#2c2443"},
    {"d": "M410,192 L472,118 C488,104 512,104 528,118 L590,192 L571,200 L552,184 L534,204 L512,180 L492,206 L470,184 L449,202 L430,188 Z", "colore": "#ead9e2"},
    {"d": "M0,400 C60,384 140,372 240,376 C340,380 420,364 520,374 C640,386 720,366 820,372 C900,377 960,390 1000,400 Z", "colore": "#1b1631"},
  ],
  "primo_piano": "#141029", "riflesso": "#272043", "testo": "#f5e8de", "accento": "#f0b088",
}
MONACO = {
  "titolo": "Monaco di Baviera", "sopra": "una guida di viaggio",
  "cielo": ["#111f33", "#28405a", "#a8634a", "#e2a462"],
  "stelle": [[0.74, 0.08, 0.45], [0.81, 0.12, 0.3], [0.86, 0.07, 0.35], [0.69, 0.14, 0.25], [0.9, 0.16, 0.3]],
  "sagome": [
    {"d": "M0,400 L0,372 L30,356 L60,370 L60,346 L100,324 L140,346 L140,340 L170,322 L200,340 L230,316 L260,334 L260,326 L300,306 L330,326 L330,400 Z M830,400 L830,320 L860,304 L890,320 L920,300 L950,320 L980,350 L1000,372 L1000,400 Z", "colore": "#24212c"},
    {"d": "M330,400 L330,262 L504,214 L678,262 L678,400 Z", "colore": "#1d1b25"},
    {"d": "M412,400 L412,150 C412,118 428,96 446,90 L446,74 L450,64 L454,74 L454,90 C472,96 488,118 488,150 L488,400 Z", "colore": "#1d1b25"},
    {"d": "M520,400 L520,150 C520,118 536,96 554,90 L554,74 L558,64 L562,74 L562,90 C580,96 596,118 596,150 L596,400 Z", "colore": "#1d1b25"},
    {"d": "M748,400 L748,190 L752,180 L752,150 L758,140 L758,112 L762,98 L766,112 L766,140 L772,150 L772,180 L776,190 L776,400 Z M690,400 L690,300 L720,286 L748,300 L776,300 L804,286 L830,300 L830,400 Z", "colore": "#24212c"},
  ],
  "primo_piano": "#13121a", "riflesso": "#1f1d28", "testo": "#f2e5d2", "accento": "#e2a462",
}

b_g = Brief.model_validate(json.loads((UP / "1eff72bc-giappone_brief.json").read_text()))
b_g.viaggiatori.nomi = ["Alessandra", "Barbara", "Stefano", "Monica"]
l_g = json.loads((UP / "23e42672-giappone_libro.json").read_text())
b_m = Brief.model_validate({"brief_id": "0a0c2b91cb73", "date": {"inizio": "2026-11-13", "fine": "2026-11-16"},
    "tappe": [{"ordine": 1, "luogo": "Monaco di Baviera", "notti": 3}], "viaggiatori": {"nomi": ["Bernardo", "Giulia"]}})
l_m = {"capitoli": [{"numero": 1, "tipo": "introduzione", "titolo": "Monaco, a passo lento",
       "corpo_md": "Un weekend breve e già ben incorniciato: il castello domenica, le cene prenotate, e in mezzo il tempo che volete tenere per voi. Monaco si presta: è una città che si gira a piedi, che mette la birreria e il palazzo reale a pochi passi l'uno dall'altro, e che la sera di novembre si accende presto."}]}
for nome, b, l, prog in (("giappone", b_g, l_g, GIAPPONE), ("monaco", b_m, l_m, MONACO)):
    pdf = scrivi_copertina(b, l, 7.0, prog, S / f"{nome}.pdf")
    p.PdfDocument(str(pdf))[0].render(scale=2).to_pil().save(S / f"{nome}.png")
    print(nome, "ok")
