"""La sagoma di un luogo che la libreria non ha, disegnata al momento.

La libreria (stampa/sagome/libreria.json) resta la prima scelta: sono sagome
riviste a mano. Quando il viaggio non ha un soggetto in libreria, qui si
disegna, in tre passi che si ripetono al massimo config.COPERTINA_GIRI volte:

1. il disegnatore (Opus) sceglie il monumento più riconoscibile del viaggio e
   lo traccia nel riquadro 1000×400, con le stesse regole della libreria e
   le sagome della libreria come esempi;
2. il motore lo valida (valida_progetto) e lo rende in PNG, esattamente come
   verrà stampato;
3. un lettore guarda l'immagine col titolo coperto e dice che luogo è, senza
   sapere cosa doveva essere; poi un critico, che sa cosa doveva essere e ha
   sentito il lettore, dà il voto e i difetti. Tutti e due in conversazioni a
   parte, che non sanno niente del disegno. Se non basta, il disegnatore vede
   l'immagine e i difetti, e ridisegna.

Se nessun giro passa, il libro tiene la copertina con la mappa: un monumento
sbagliato in copertina è peggio di nessun monumento. Le sagome riuscite si
salvano sul volume (_copertine/disegnate.json, con il PNG accanto): il libro
dopo, nello stesso posto, le riusa senza ridisegnare, e quelle buone si possono
portare in libreria.
"""

import base64
import io
import json
import os
import re
import sys
import tempfile
import unicodedata
from datetime import datetime, timezone
from pathlib import Path

from src import config
from src.costs import costo_chiamata

FALLBACK_BETA = "server-side-fallback-2026-07-01"
ATMOSFERE = ["alba", "sera", "tramonto", "notte"]
ESEMPI = ["frauenkirche", "fuji", "belem", "assisi"]


def cartella() -> Path:
    return config.output_root() / "_copertine"


def file_disegnate() -> Path:
    return cartella() / "disegnate.json"


def carica_disegnate() -> dict:
    try:
        return json.loads(file_disegnate().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _registra(chiave: str, voce: dict, png: bytes | None) -> None:
    """Aggiunge la sagoma alle disegnate. Si rilegge il file subito prima di
    scrivere: due libri possono finire insieme, e nessuno deve cancellare l'altro."""
    d = cartella()
    d.mkdir(parents=True, exist_ok=True)
    tutte = carica_disegnate()
    tutte[chiave] = voce
    tmp = file_disegnate().with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(tutte, ensure_ascii=False, indent=1), encoding="utf-8")
    os.replace(tmp, file_disegnate())
    if png:
        (d / f"{chiave}.png").write_bytes(png)


def chiave_libera(nome: str, occupate) -> str:
    base = unicodedata.normalize("NFKD", nome).encode("ascii", "ignore").decode()
    base = re.sub(r"[^a-z0-9]+", "_", base.lower()).strip("_")[:40] or "soggetto"
    chiave, n = base, 2
    while chiave in occupate:
        chiave, n = f"{base}_{n}", n + 1
    return chiave


SCHEMA_DISEGNO = {
    "type": "object",
    "additionalProperties": False,
    "required": ["nessuno", "nome", "luoghi", "tratti", "titolo", "sagome", "astro", "uccelli", "atmosfere"],
    "properties": {
        "nessuno": {"type": "boolean"},
        "nome": {"type": "string"},
        "luoghi": {"type": "array", "items": {"type": "string"}},
        "tratti": {"type": "string"},
        "titolo": {"type": "string"},
        "sagome": {"type": "array", "items": {
            "type": "object", "additionalProperties": False, "required": ["d", "colore"],
            "properties": {"d": {"type": "string"}, "colore": {"type": "string"}}}},
        "astro": {"type": "object", "additionalProperties": False, "required": ["x", "y", "r"],
                  "properties": {"x": {"type": "number"}, "y": {"type": "number"}, "r": {"type": "number"}}},
        "uccelli": {"type": "array", "items": {"type": "array", "items": {"type": "number"}}},
        "atmosfere": {"type": "array", "items": {"type": "string", "enum": ATMOSFERE}},
    },
}

SCHEMA_CIECO = {
    "type": "object",
    "additionalProperties": False,
    "required": ["luogo", "sicurezza"],
    "properties": {"luogo": {"type": "string"}, "sicurezza": {"type": "integer"}},
}

SCHEMA_GIUDIZIO = {
    "type": "object",
    "additionalProperties": False,
    "required": ["sembra", "riconoscibile", "voto", "problemi"],
    "properties": {
        "sembra": {"type": "string"},
        "riconoscibile": {"type": "boolean"},
        "voto": {"type": "integer"},
        "problemi": {"type": "array", "items": {"type": "string"}},
    },
}

REGOLE = """Le regole del disegno (le stesse delle sagome della libreria):
- Il riquadro è 1000 unità di larghezza per 400 di altezza; y cresce verso il basso; y=400 è l'orizzonte, la base su cui poggia tutto.
- Ogni tracciato usa SOLO comandi assoluti M, L, C, Q, S, Z, con i numeri sempre a coppie x,y. Niente minuscole, niente H, V, A.
- Al bordo sinistro (x vicino a 0) una forma può toccare solo la base y=400: lì c'è il dorso del libro e una forma più alta si spezzerebbe a scalino. Il bordo destro (x=1000) si può superare: è il taglio del libro.
- Nessun numero sotto -5 o sopra 1005.
- Sul libro il riquadro è largo 15 cm: un'unità è 0,15 mm. Un dettaglio sotto le 8 unità sparisce; un elemento sottile (un'antenna, un cavo) deve essere spesso almeno 3 unità.
- La fascia in basso, da y=350 a y=400, è in parte coperta da un ovale scuro di riflesso tra x=140 e x=860: archi, vuoti e dettagli che contano stanno sopra y=345.
- Un soggetto solo, quello che un lettore riconosce a colpo d'occhio, alto tra 250 e 400 unità. Elementi in più solo se sono parte della sua identità (le palme della Koutoubia, la collina da cui esce il Golden Gate). Mai un collage di monumenti.
- Le proporzioni sono quelle vere, appena stilizzate come nei manifesti: se una forma a questa scala diventa un ago, si allarga un poco la base, non si inventa.
- Le parti che nel vero si toccano, nel disegno si sovrappongono: un campanile parte dal tetto, non sta sospeso sopra.
- Da 1 a 4 sagome, disegnate in ordine (la prima è la più lontana). Colori scuri, da silhouette controluce (#14xxxx–#3cxxxx); due toni vicini per dare profondità. Al massimo un accento chiaro piccolo che è un tratto del soggetto (la neve sulla cima, il quadrante dell'orologio, la cima illuminata). Mai colori saturi: una vela ruggine rovina la scena.
- "astro": il sole o la luna, in frazioni della pagina (x da 0 a 1, y da 0 a 1; l'orizzonte è a y=0,61, la cima del riquadro a y≈0,32) e raggio in mm (12–16). Mettilo dove non copre il soggetto né il titolo (y tra 0,36 e 0,48), di lato al soggetto.
- "uccelli": 0–3 voli, [x, y, misura] in frazioni della pagina, misura 1,5–2,6, nel cielo libero lontano dall'astro.
- "atmosfere": due delle quattro (alba: indaco, rosa, sole rosso; sera: blu notte, rame, luna chiara; tramonto: prugna, arancio, stelle; notte: blu profondo, luna piccola), quelle in cui il soggetto si legge meglio."""


def _esempi(libreria: dict) -> str:
    scelti = [k for k in ESEMPI if k in libreria] or list(libreria)[:3]
    righe = []
    for k in scelti[:3]:
        v = libreria[k]
        righe.append(json.dumps({"nome": v["nome"], "tratti": v["tratti"], "sagome": v["sagome"],
                                 "astro": v.get("astro"), "uccelli": v.get("uccelli") or [],
                                 "atmosfere": v.get("atmosfere")}, ensure_ascii=False))
    return "\n".join(righe)


def _istruzioni(brief, libro: dict, libreria: dict) -> str:
    from src.copertina import _attacco

    tappe = [re.sub(r"\s*\(.*?\)", "", t.luogo).strip() for t in sorted(brief.tappe, key=lambda t: t.ordine)]
    quando = f"dal {brief.date.inizio} al {brief.date.fine}" if brief.date.inizio else "date non note"
    return f"""Disegni la copertina di una guida di viaggio scritta per un viaggio preciso. Lo stile della collana è fisso: un cielo in sfumatura, il sole o la luna, e sull'orizzonte la sagoma piatta, in controluce, del luogo più riconoscibile del viaggio. Il tuo lavoro è la sagoma.

Il viaggio: tappe {", ".join(tappe)}; {quando}.
L'attacco del libro: «{_attacco(libro, parole=80)}»

{REGOLE}

Tre sagome della libreria, approvate, come esempio di misura, densità e stile:
{_esempi(libreria)}

Scegli il soggetto: il monumento o il paesaggio che un lettore italiano associa subito a questo viaggio e che si riconosce anche in silhouette. Se il viaggio non ne ha uno (una campagna, un giro di paesi senza un'icona), metti "nessuno": true e lascia vuoti gli altri campi.

Ragiona prima sulla forma vera: le proporzioni, le parti, cosa la distingue da un edificio qualsiasi. Poi traccia le coordinate.

Campi: "nome" (il nome del soggetto in italiano), "luoghi" (le tappe e i paesi a cui appartiene, come li scrive un viaggiatore italiano), "tratti" (una frase: cosa lo rende riconoscibile), "titolo" (il luogo del viaggio come lo dice un lettore italiano, al massimo tre parole: il paese per un viaggio in più città, la città per un viaggio in una città sola), "sagome", "astro", "uccelli", "atmosfere"."""


CIECO = """Questa è la copertina di una guida di viaggio; il titolo è coperto. Sull'orizzonte c'è una sagoma in controluce: quale monumento o luogo è? Rispondi con il nome ("luogo") e quanto ne sei sicuro da 1 a 10 ("sicurezza"). Se non lo riconosci, scrivi cosa sembra."""


def _giudizio_istruzioni(nome: str, luogo: str, cieco: dict) -> str:
    return f"""Questa è la copertina di una guida di viaggio per {luogo}. Al centro, sull'orizzonte, c'è una sagoma in controluce che dovrebbe essere: {nome}.

Un lettore l'ha guardata con il titolo coperto, senza sapere cosa doveva essere, e ha detto: «{cieco.get("luogo")}» (sicurezza {cieco.get("sicurezza")}/10).

Il cielo in sfumatura, il sole o la luna, la fascia scura sotto l'orizzonte e l'ovale di riflesso sotto la sagoma sono lo stile fisso della collana, uguale per tutti i libri: non sono difetti e non si giudicano. Si giudica solo la sagoma.

Il compito della copertina è uno: chi la guarda deve pensare subito a {luogo}. Non serve che distingua {nome} da un monumento simile dello stesso luogo: una moschea ottomana su una guida di Istanbul fa il suo lavoro. Conta che non sembri un altro paese, un'altra epoca o un edificio qualsiasi.
- "sembra": cosa sembra a prima vista, in poche parole.
- "riconoscibile": true se il lettore ha nominato {nome} o comunque {luogo} (la stessa città o lo stesso paese), con sicurezza almeno 6; oppure se chiunque conosca {luogo} ci penserebbe subito.
- "voto": da 1 a 10. 9–10: un manifesto di viaggio fatto bene; 8: fa pensare subito a {luogo}, piccoli difetti; 6–7: fa pensare a {luogo} solo sapendolo; sotto 6: sembra altro.
- "problemi": i difetti concreti che si possono correggere nel disegno, ognuno con il punto dove sta e cosa fare (proporzioni sbagliate, parti staccate che nel vero si toccano, forme che sembrano altro, dettagli troppo piccoli o nascosti dal riflesso scuro in basso, colori che stonano, il sole che copre il soggetto). Non elencare come problema che esistono monumenti simili. Vuoto se non ce ne sono."""


def _testo(risposta) -> str:
    return "".join(b.text for b in risposta.content if getattr(b, "type", "") == "text")


def _chiama(client, messaggi: list, schema: dict, max_tokens: int, effort: str | None = None):
    """Una chiamata al modello delle copertine, in streaming (il ragionamento può
    essere lungo), con il ripiego lato server se una richiesta viene rifiutata."""
    with client.beta.messages.stream(
        model=config.MODEL_COPERTINA, max_tokens=max_tokens,
        betas=[FALLBACK_BETA], fallbacks="default",
        output_config={"effort": effort or config.EFFORT_COPERTINA,
                       "format": {"type": "json_schema", "schema": schema}},
        messages=messaggi,
    ) as flusso:
        risposta = flusso.get_final_message()
    costo = costo_chiamata(risposta.model or config.MODEL_COPERTINA,
                           risposta.usage.model_dump()).get("costo_usd") or 0.0
    if risposta.stop_reason == "refusal":
        raise RuntimeError("il modello ha rifiutato la richiesta")
    if risposta.stop_reason == "max_tokens":
        raise RuntimeError("risposta troncata (max_tokens)")
    return risposta, json.loads(_testo(risposta)), costo


def _voce(dati: dict) -> dict:
    atmosfere = [a for a in dict.fromkeys(dati.get("atmosfere") or []) if a in ATMOSFERE] or ["sera"]
    return {"nome": dati["nome"].strip(), "luoghi": [l for l in dati.get("luoghi") or [] if l.strip()],
            "tratti": dati.get("tratti", "").strip(), "sagome": dati["sagome"],
            "astro": dati.get("astro") or {"x": 0.68, "y": 0.42, "r": 15},
            "uccelli": [u[:3] for u in dati.get("uccelli") or [] if len(u) >= 3][:3],
            "atmosfere": atmosfere, "atmosfera": atmosfere[0]}


def _png(brief, libro: dict, progetto: dict, coperto: bool = False) -> bytes:
    """La copertina resa come si vedrà: il fronte del PDF, in PNG. Con `coperto`,
    senza titolo: per chi deve riconoscere il luogo dalla sola sagoma."""
    import pypdfium2 as pdfium

    from src.copertina_illustrata import scrivi_copertina

    if coperto:  # niente titolo e niente itinerario: le tappe direbbero il luogo
        progetto = {**progetto, "titolo": "·", "sopra": " "}
        brief = brief.model_copy(update={"tappe": brief.tappe[:1]})
    with tempfile.TemporaryDirectory() as d:
        pdf = scrivi_copertina(brief, libro, 0, progetto, Path(d) / "prova.pdf", solo_fronte=True)
        documento = pdfium.PdfDocument(str(pdf))
        try:
            immagine = documento[0].render(scale=1.6).to_pil().convert("RGB")
        finally:
            documento.close()
    buf = io.BytesIO()
    immagine.save(buf, "PNG")
    return buf.getvalue()


def _immagine(png: bytes) -> dict:
    return {"type": "image", "source": {"type": "base64", "media_type": "image/png",
                                        "data": base64.standard_b64encode(png).decode()}}


def disegna_soggetto(client, brief, libro: dict, libreria: dict, cartella_giri: Path | None = None) -> dict:
    """Disegna, giudica, ridisegna. Ritorna {voce, titolo, png, giri, costo_usd};
    voce è None se il viaggio non ha un'icona o se nessun disegno ha passato il giudice.
    In `cartella_giri`, se c'è, il PNG di ogni giro, anche di quelli bocciati:
    per vedere cosa ha disegnato e cosa ha detto il giudice."""
    from src import scelta_copertina as sc
    from src.copertina_illustrata import valida_progetto

    messaggi = [{"role": "user", "content": _istruzioni(brief, libro, libreria)}]
    costo, giri, migliore = 0.0, [], None
    for giro in range(1, config.COPERTINA_GIRI + 1):
        risposta, dati, c = _chiama(client, messaggi, SCHEMA_DISEGNO, 32000)
        costo += c
        messaggi.append({"role": "assistant", "content": risposta.content})
        if dati.get("nessuno"):
            print(f"Copertina al volo: il viaggio non ha un'icona da disegnare ({dati.get('tratti') or ''}).")
            return {"voce": None, "titolo": None, "png": None, "giri": giri, "costo_usd": round(costo, 4)}
        voce = _voce(dati)
        titolo = (dati.get("titolo") or "").strip()[:28] or voce["nome"][:28]
        progetto = sc.componi(voce, voce["atmosfera"], titolo)
        errori = valida_progetto(progetto)
        if errori:
            giri.append({"giro": giro, "errori": errori})
            messaggi.append({"role": "user", "content": "Il disegno non passa il controllo: " + "; ".join(errori)
                             + ". Correggi e rimanda il disegno intero, con le stesse regole."})
            continue
        png = _png(brief, libro, progetto, coperto=True)
        _, cieco, c = _chiama(client, [{"role": "user", "content": [
            _immagine(png), {"type": "text", "text": CIECO}]}], SCHEMA_CIECO, 4000, effort="low")
        costo += c
        png = _png(brief, libro, progetto)
        _, giudizio, c = _chiama(client, [{"role": "user", "content": [
            _immagine(png), {"type": "text", "text": _giudizio_istruzioni(voce["nome"], titolo, cieco)}]}],
            SCHEMA_GIUDIZIO, 8000)
        costo += c
        if cartella_giri is not None:
            cartella_giri.mkdir(parents=True, exist_ok=True)
            (cartella_giri / f"giro_{giro}.png").write_bytes(png)
        voto = int(giudizio.get("voto") or 0)
        passa = bool(giudizio.get("riconoscibile")) and voto >= config.COPERTINA_VOTO_MIN
        giri.append({"giro": giro, "voto": voto, "alla_cieca": cieco, "sembra": giudizio.get("sembra"),
                     "problemi": giudizio.get("problemi") or []})
        print(f"Copertina al volo, giro {giro}: {voce['nome']}; alla cieca «{cieco.get('luogo')}» "
              f"({cieco.get('sicurezza')}/10); voto {voto}{' (passa)' if passa else ''}; "
              f"difetti: {'; '.join(giudizio.get('problemi') or []) or 'nessuno'}.")
        if passa and (migliore is None or voto > migliore["voto"]):
            migliore = {"voce": voce, "titolo": titolo, "png": png, "voto": voto}
        if passa:
            break
        problemi = "\n".join(f"- {p}" for p in giudizio.get("problemi") or []) or "- (nessuno elencato)"
        messaggi.append({"role": "user", "content": [
            _immagine(png),
            {"type": "text", "text": f"Ecco la copertina come viene stampata. Un lettore che la guarda dice che sembra: «{giudizio.get('sembra')}» (voto {voto}/10). I problemi:\n{problemi}\n\nRidisegna la sagoma intera correggendoli. Stesso soggetto, salvo che la forma non si possa rendere riconoscibile: allora scegline un altro del viaggio."}]})
    esito = {"voce": None, "titolo": None, "png": None, "giri": giri, "costo_usd": round(costo, 4)}
    if migliore:
        migliore["voce"].update({"disegnata": True, "voto": migliore["voto"],
                                 "quando": datetime.now(timezone.utc).isoformat()})
        esito.update(voce=migliore["voce"], titolo=migliore["titolo"], png=migliore["png"])
    else:
        print("Copertina al volo: nessun disegno ha passato il giudice, resta la mappa.", file=sys.stderr)
    return esito
