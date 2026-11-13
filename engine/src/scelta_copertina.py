"""Quale copertina illustrata ha un libro: il soggetto, l'atmosfera, il titolo.

I soggetti stanno nella libreria (stampa/sagome/libreria.json): sagome curate
a mano e approvate, non disegnate al momento. Qui si sceglie soltanto:

- il soggetto: il luogo più riconoscibile del viaggio tra quelli in libreria;
- l'atmosfera: una delle quattro curate qui sotto (colori, sole o luna, stelle),
  scelta per la stagione e il tono del viaggio;
- il titolo: il luogo come lo dice un lettore italiano («Giappone»,
  «Monaco di Baviera»).

Lo sceglie Sonnet, in una chiamata breve; se la chiamata non va, lo sceglie il
motore da solo (tappe e stagione). Se nessun soggetto della libreria è del
viaggio, il libro tiene la copertina con la mappa: meglio quella che una
sagoma sbagliata. La scelta si salva in copertina.json, così la copertina è la
stessa sul PDF e sul libro stampato.
"""

import json
import re
import sys
from pathlib import Path

from src import config
from src.costs import costo_chiamata

LIBRERIA = config.ENGINE_ROOT / "stampa" / "sagome" / "libreria.json"
MODELLO = "claude-sonnet-5-5"

# Le quattro atmosfere. Il cielo va sempre dallo scuro (in alto, dove sta il
# titolo chiaro) al chiaro (all'orizzonte): così il titolo si legge sempre.
ATMOSFERE = {
    "alba": {"cielo": ["#1d2246", "#46376a", "#b9637a", "#f0b088"], "astro": "sole",
             "astro_colore": "#e0573f", "primo_piano": "#141029", "riflesso": "#272043",
             "testo": "#f5e8de", "accento": "#f0b088"},
    "sera": {"cielo": ["#14213a", "#34476a", "#b0705a", "#eab273"], "astro": "luna",
             "astro_colore": "#f3dcb4", "primo_piano": "#14131b", "riflesso": "#2a2633",
             "testo": "#f4e7d4", "accento": "#eab273"},
    "tramonto": {"cielo": ["#2b1730", "#6e2a3a", "#c4512e", "#f0a24a"], "astro": "stelle",
                 "primo_piano": "#1d1218", "riflesso": "#33202a", "testo": "#f6e3cc",
                 "accento": "#f0a24a"},
    "notte": {"cielo": ["#0a1024", "#16244a", "#2c4470", "#5d7aa6"], "astro": "luna_piccola",
              "astro_colore": "#e9e4d4", "primo_piano": "#0b0f1c", "riflesso": "#18203a",
              "testo": "#eef0f2", "accento": "#c9d6e8"},
}
# Le stelle stanno in alto a destra, lontane dal titolo.
STELLE = [[0.74, 0.07, 0.45], [0.80, 0.11, 0.35], [0.86, 0.06, 0.4], [0.90, 0.13, 0.3],
          [0.70, 0.13, 0.25], [0.12, 0.09, 0.3], [0.18, 0.14, 0.25]]


def carica_libreria() -> dict:
    try:
        return json.loads(LIBRERIA.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def libreria_completa() -> dict:
    """La libreria approvata più le sagome disegnate al volo per libri passati.
    A parità di chiave vince la libreria: è quella rivista a mano."""
    from src.disegno_copertina import carica_disegnate

    return {**carica_disegnate(), **carica_libreria()}


def file_copertina(brief_id: str) -> Path:
    return config.output_root() / brief_id / "copertina.json"


def progetto_copertina(brief_id: str) -> dict | None:
    """Il progetto della copertina illustrata del libro, se ne ha una."""
    try:
        dati = json.loads(file_copertina(brief_id).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return dati.get("progetto")


def componi(voce: dict, atmosfera: str, titolo: str) -> dict:
    """Soggetto della libreria + atmosfera = il progetto da disegnare.

    Ogni soggetto si usa solo nelle atmosfere in cui è stato visto e approvato
    (voce["atmosfere"]): fuori da quelle, vale la sua.
    """
    adatte = voce.get("atmosfere") or list(ATMOSFERE)
    if atmosfera not in adatte:
        atmosfera = voce.get("atmosfera") if voce.get("atmosfera") in adatte else adatte[0]
    a = ATMOSFERE[atmosfera]
    progetto = {"titolo": titolo, "sopra": "una guida di viaggio", "cielo": list(a["cielo"]),
                "sagome": voce["sagome"], "primo_piano": a["primo_piano"], "riflesso": a["riflesso"],
                "testo": a["testo"], "accento": a["accento"], "uccelli": voce.get("uccelli") or []}
    posto = voce.get("astro") or {"x": 0.66, "y": 0.44, "r": 15}
    if a["astro"] == "sole" or a["astro"] == "luna":
        progetto["sole"] = {**posto, "colore": a["astro_colore"]}
    if a["astro"] == "luna":
        progetto["stelle"] = STELLE[:4]
    elif a["astro"] == "luna_piccola":
        progetto["sole"] = {"x": 0.80, "y": 0.10, "r": 6, "colore": a["astro_colore"]}
        progetto["stelle"] = STELLE[1:]
        progetto["uccelli"] = []  # di notte non volano
    elif a["astro"] == "stelle":
        progetto["stelle"] = STELLE
    return progetto


def _luoghi_del_viaggio(brief) -> list[str]:
    return [re.sub(r"\s*\(.*?\)", "", t.luogo).strip() for t in sorted(brief.tappe, key=lambda t: t.ordine)]


def scelta_del_motore(brief, libreria: dict) -> dict | None:
    """Senza modello: il primo soggetto della libreria che appartiene a una tappa."""
    luoghi = [l.lower() for l in _luoghi_del_viaggio(brief)]
    for chiave, voce in libreria.items():
        if any(any(lv.lower() in l or l in lv.lower() for l in luoghi) for lv in voce.get("luoghi", [])):
            mese = brief.date.inizio.month if brief.date.inizio else 0
            atmosfera = voce.get("atmosfera") or (
                "notte" if mese in (12, 1, 2) else "alba" if mese in (3, 4, 5)
                else "tramonto" if mese in (6, 7, 8) else "sera")
            unici = list(dict.fromkeys(_luoghi_del_viaggio(brief)))
            titolo = unici[0] if len(unici) == 1 else voce["luoghi"][0]
            return {"soggetto": chiave, "atmosfera": atmosfera, "titolo": titolo}
    return None


def _istruzioni(brief, libro: dict, libreria: dict) -> str:
    from src.copertina import _attacco

    voci = "\n".join(f'- "{k}": {v["nome"]} (luoghi: {", ".join(v["luoghi"])}; tratti: {v["tratti"]}; '
                     f'atmosfere: {", ".join(v.get("atmosfere") or ATMOSFERE)})'
                     for k, v in libreria.items())
    quando = (f"dal {brief.date.inizio} al {brief.date.fine}" if brief.date.inizio else "date non note")
    return f"""Scegli la copertina di una guida di viaggio scritta per un viaggio preciso.

Il viaggio: tappe {", ".join(_luoghi_del_viaggio(brief))}; {quando}.
L'attacco del libro: «{_attacco(libro, parole=80)}»

I soggetti disponibili (sagome già disegnate):
{voci}

Le atmosfere: "alba" (indaco, rosa, pesca, sole rosso), "sera" (blu notte, rame, ambra, luna chiara), "tramonto" (prugna, rosso, arancio, stelle), "notte" (blu profondo, luna piccola, stelle).

Scegli:
- "soggetto": la chiave del soggetto che appartiene davvero a questo viaggio, il più riconoscibile; null se nessuno è di questo viaggio. Non scegliere mai un soggetto di un altro paese. Se più soggetti sono dello stesso luogo, scegli quello che si accorda meglio al tono del libro.
- "atmosfera": quella che si accorda alla stagione e al tono del viaggio.
- "titolo": il luogo del viaggio come lo dice un lettore italiano, al massimo tre parole (il paese per un viaggio in più città, la città per un viaggio in una città sola).

Rispondi SOLO con JSON: {{"soggetto": "...", "atmosfera": "...", "titolo": "..."}}"""


def scelta_del_modello(client, brief, libro: dict, libreria: dict) -> tuple[dict | None, float]:
    response = client.messages.create(model=MODELLO, max_tokens=4000,
                                      output_config={"effort": "low"},
                                      messages=[{"role": "user",
                                                 "content": _istruzioni(brief, libro, libreria)}])
    costo = costo_chiamata(MODELLO, response.usage.model_dump()).get("costo_usd") or 0.0
    testo = "".join(b.text for b in response.content if b.type == "text")
    inizio, fine = testo.find("{"), testo.rfind("}")
    if inizio < 0 or fine < inizio:
        return None, costo
    s = json.loads(testo[inizio:fine + 1])
    soggetto, atmosfera = s.get("soggetto"), s.get("atmosfera")
    titolo = str(s.get("titolo") or "").strip()
    if soggetto is None:
        return {"soggetto": None}, costo
    if soggetto not in libreria or atmosfera not in ATMOSFERE or not (1 <= len(titolo) <= 28):
        return None, costo
    return {"soggetto": soggetto, "atmosfera": atmosfera, "titolo": titolo}, costo


def prepara_copertina(brief, libro: dict, client=None, rifai: bool = False) -> dict | None:
    """Sceglie la copertina e la salva in copertina.json. Ritorna il progetto, o None."""
    from src.copertina_illustrata import valida_progetto

    js = file_copertina(brief.brief_id)
    if js.exists() and not rifai:
        return progetto_copertina(brief.brief_id)
    libreria = libreria_completa()
    if not libreria:
        return None
    scelta, costo, da, disegno = None, 0.0, "motore", None
    if client is not None:
        try:
            scelta, costo = scelta_del_modello(client, brief, libro, libreria)
            da = MODELLO
        except Exception as exc:
            print(f"Copertina: la scelta del modello non è riuscita ({exc!r}), scelgo io.", file=sys.stderr)
    if scelta is None:
        scelta, da = scelta_del_motore(brief, libreria), "motore"
    if not (scelta and scelta.get("soggetto")) and client is not None and config.COPERTINA_AL_VOLO:
        # Nessun soggetto per questo viaggio: si disegna, e se il disegno non
        # passa il giudice resta la mappa.
        from src.disegno_copertina import _registra, chiave_libera, disegna_soggetto

        try:
            esito = disegna_soggetto(client, brief, libro, carica_libreria())
            costo += esito["costo_usd"]
            disegno = {"giri": esito["giri"], "costo_usd": esito["costo_usd"]}
            if esito["voce"]:
                chiave = chiave_libera(esito["voce"]["nome"], libreria)
                _registra(chiave, esito["voce"], esito["png"])
                libreria[chiave] = esito["voce"]
                scelta = {"soggetto": chiave, "atmosfera": esito["voce"]["atmosfera"], "titolo": esito["titolo"]}
                da = config.MODEL_COPERTINA
                disegno["soggetto"] = chiave
        except Exception as exc:
            print(f"Copertina: il disegno al volo non è riuscito ({exc!r}), resta la mappa.", file=sys.stderr)
    progetto = None
    if scelta and scelta.get("soggetto"):
        progetto = componi(libreria[scelta["soggetto"]], scelta["atmosfera"], scelta["titolo"])
        errori = valida_progetto(progetto)
        if errori:
            print(f"Copertina: progetto non valido ({errori}), resta la mappa.", file=sys.stderr)
            progetto = None
    js.parent.mkdir(parents=True, exist_ok=True)
    js.write_text(json.dumps({"scelta": scelta, "scelto_da": da, "costo_usd": round(costo, 4),
                              "disegno": disegno, "progetto": progetto}, ensure_ascii=False, indent=1),
                  encoding="utf-8")
    if progetto:
        print(f"Copertina: {scelta['soggetto']}, atmosfera {scelta['atmosfera']}, «{scelta['titolo']}» (scelta: {da}).")
    else:
        print("Copertina: nessun soggetto della libreria per questo viaggio, resta la mappa.")
    return progetto
