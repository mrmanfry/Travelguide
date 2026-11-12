"""Le mappe dei capitoli sulle tappe: dove stanno i luoghi di cui il libro parla.

La catena, e chi fa cosa:

1. Haiku legge il capitolo GIÀ SCRITTO e ne elenca i luoghi: un nome breve per
   la legenda, la sezione in cui se ne parla e le parole con cui cercarli.
   Mai le coordinate. Dove si trova un luogo è la prima cosa che il libro non
   può sbagliare, e un modello che "si ricorda" una latitudine sbaglia di
   isolati senza accorgersene.
2. Nominatim (OpenStreetMap) trova i luoghi, sempre dentro il rettangolo della
   città: «Ferrara» cercato da solo finirebbe in Emilia, non a Little Italy.
   Quello che non si trova resta fuori dalla mappa e viene annotato: non si
   indovina.
3. Overpass (OpenStreetMap) dà strade, parchi, acqua e quartieri, solo per i
   rettangoli che la mappa mostrerà.
4. mappa_disegno disegna.

Tutto quello che passa dalla rete finisce in una cache sulla Volume, condivisa
tra i libri: la seconda volta che un libro passa da Times Square, Times Square
non si cerca più. Ed è anche una cortesia dovuta: Nominatim e Overpass sono
servizi gratuiti, con regole d'uso precise (una richiesta al secondo, un nome
che dice chi siamo).

Niente qui dentro può fermare un libro: una mappa che non viene è una pagina in
meno, mai un libro in meno.
"""

import hashlib
import json
import math
import os
import re
import sys
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from src import config
from src.costs import costo_chiamata

SEZIONE_ROMANA_RE = re.compile(r"^##\s+([IVXLC]+)[.)]\s*(.+?)\s*$", re.MULTILINE)
VERSIONE_CACHE = "v1"


# ------------------------------------------------------------------- percorsi


def cartella_mappe(brief_id: str) -> Path:
    return config.output_root() / brief_id / "mappe"


def file_mappa(brief_id: str, numero: int) -> tuple[Path, Path]:
    d = cartella_mappe(brief_id)
    return d / f"cap_{numero:02d}.png", d / f"cap_{numero:02d}.json"


def mappe_del_libro(brief_id: str) -> dict[int, dict]:
    """Le mappe pronte di un libro, per numero di capitolo: quelle col disegno."""
    pronte = {}
    d = cartella_mappe(brief_id)
    if not d.is_dir():
        return pronte
    for j in sorted(d.glob("cap_*.json")):
        try:
            dati = json.loads(j.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        png = j.with_suffix(".png")
        if dati.get("luoghi") and png.exists():
            from src.mappa_disegno import nome_tavola

            dati["png"] = str(png)
            dati["tavole_png"] = [str(nome_tavola(png, i)) for i in range(int(dati.get("tavole") or 1))
                                  if nome_tavola(png, i).exists()]
            pronte[int(dati["numero"])] = dati
    return pronte


def file_paese(brief_id: str) -> tuple[Path, Path]:
    d = cartella_mappe(brief_id)
    return d / "paese.png", d / "paese.json"


def mappa_del_paese(brief_id: str) -> dict | None:
    """La mappa del viaggio pronta, con il suo itinerario; None se non c'è."""
    png, js = file_paese(brief_id)
    try:
        dati = json.loads(js.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not png.exists() or dati.get("saltata"):
        return None
    dati["png"] = str(png)
    return dati


def _provvisorio(png: Path) -> Path:
    """Dove si disegna la mappa nuova, finché non è sicuro che sia venuta."""
    return png.with_name(png.stem + ".nuovo.png")


def _buona(png: Path, js: Path) -> bool:
    try:
        dati = json.loads(js.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False
    return png.exists() and not dati.get("saltata") and bool(dati.get("luoghi") or dati.get("fermate"))


def _consegna(png: Path, js: Path, esito: dict) -> dict:
    """Mette al suo posto la mappa nuova e il suo json, insieme.

    Rifare una mappa non deve mai costare quella che c'era: se il nuovo giro
    non ne produce una (il geocoder che quel giorno non risponde, un modello
    che elenca meno luoghi), resta la vecchia. Il disegno nuovo sta in un file
    provvisorio e prende il posto del vecchio solo a lavoro finito.
    """
    from src.mappa_disegno import nome_tavola

    nuovo = _provvisorio(png)
    if esito.get("saltata") and _buona(png, js):
        _butta_provvisori(png)
        esito["tenuta_la_precedente"] = True
        return esito
    js.parent.mkdir(parents=True, exist_ok=True)
    tmp = js.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(esito, ensure_ascii=False, indent=2), encoding="utf-8")
    disegnata = nuovo.exists()
    for i in range(8):
        provvisoria, finale = nome_tavola(nuovo, i), nome_tavola(png, i)
        if provvisoria.exists():
            os.replace(provvisoria, finale)
        elif disegnata or esito.get("saltata"):
            finale.unlink(missing_ok=True)  # una tavola che il nuovo disegno non ha più
    os.replace(tmp, js)
    return esito


def _butta_provvisori(png: Path) -> None:
    from src.mappa_disegno import nome_tavola

    for i in range(8):
        nome_tavola(_provvisorio(png), i).unlink(missing_ok=True)


# ---------------------------------------------------------------------- cache


def _cache(tipo: str, chiave: str) -> Path:
    h = hashlib.sha256(f"{VERSIONE_CACHE}|{chiave}".encode()).hexdigest()[:32]
    return config.output_root() / "_cache_mappe" / tipo / f"{h}.json"


def _leggi_cache(tipo: str, chiave: str):
    p = _cache(tipo, chiave)
    try:
        return True, json.loads(p.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return False, None


def _scrivi_cache(tipo: str, chiave: str, valore) -> None:
    p = _cache(tipo, chiave)
    try:
        p.parent.mkdir(parents=True, exist_ok=True)
        tmp = p.with_suffix(f".{os.getpid()}.tmp")
        tmp.write_text(json.dumps(valore, ensure_ascii=False, separators=(",", ":")),
                       encoding="utf-8")
        os.replace(tmp, p)  # chi legge vede il file intero o niente
    except OSError as exc:
        print(f"Mappe: cache non scritta ({exc}).", file=sys.stderr)


# ---------------------------------------------------------------------- rete

_ultima_nominatim = [0.0]


def _scarica(url: str, dati: bytes | None = None, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, data=dati, headers={"User-Agent": config.MAPPE_UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def _con_ritentativi(fai, pause: tuple[int, ...]):
    """Ritenta solo quando il server dice «più tardi» (429, 5xx) o la rete cade."""
    for i in range(len(pause) + 1):
        try:
            return fai()
        except urllib.error.HTTPError as exc:
            if exc.code not in (429, 500, 502, 503, 504) or i == len(pause):
                raise
        except (urllib.error.URLError, TimeoutError):
            if i == len(pause):
                raise
        time.sleep(pause[i])


def _nominatim(params: dict) -> list:
    chiave = json.dumps(params, sort_keys=True)
    trovato, valore = _leggi_cache("nominatim", chiave)
    if trovato:
        return valore

    def fai():
        # Regola d'uso di Nominatim: al massimo una richiesta al secondo.
        attesa = _ultima_nominatim[0] + 1.1 - time.monotonic()
        if attesa > 0:
            time.sleep(attesa)
        try:
            return json.loads(_scarica(f"{config.NOMINATIM_URL}?{urllib.parse.urlencode(params)}"))
        finally:
            _ultima_nominatim[0] = time.monotonic()

    risposta = _con_ritentativi(fai, (5, 20))
    _scrivi_cache("nominatim", chiave, risposta)
    return risposta


def _distanza_km(a: tuple[float, float], b: tuple[float, float]) -> float:
    """(lat, lon) → km, abbastanza preciso alla scala di una città."""
    k = math.cos(math.radians((a[0] + b[0]) / 2))
    return math.hypot(a[0] - b[0], (a[1] - b[1]) * k) * 111.32


def riquadro_citta(luogo: str) -> dict | None:
    """Il rettangolo in cui cercare i luoghi di una tappa, e il suo paese.

    «Kyoto (con gita a Nara)» si cerca come «Kyoto». Il rettangolo di una città
    amministrativa può essere enorme — quello di Tokyo arriva alle isole
    Ogasawara, mille chilometri a sud — e allora vale il centro più un margine.
    """
    nome = re.sub(r"\s*\(.*?\)", "", luogo).strip() or luogo
    r = _nominatim({"q": nome, "format": "jsonv2", "limit": 1, "addressdetails": 1})
    if not r:
        return None
    r = r[0]
    lat, lon = float(r["lat"]), float(r["lon"])
    s, n, w, e = (float(x) for x in r["boundingbox"])
    if n - s > 1.0 or e - w > 1.4:
        s, n, w, e = lat - 0.35, lat + 0.35, lon - 0.45, lon + 0.45
    else:
        # Un paese piccolo è un punto: gli si dà almeno il raggio di una gita a piedi.
        cl, cg = (s + n) / 2, (w + e) / 2
        ml, mg = max((n - s) / 2 * 1.2, 0.12), max((e - w) / 2 * 1.2, 0.16)
        s, n, w, e = cl - ml, cl + ml, cg - mg, cg + mg
    return {"sud": s, "nord": n, "ovest": w, "est": e, "centro": (lat, lon),
            "paese": (r.get("address") or {}).get("country_code")}


def _ricerche_con_ripiego(ricerche: list[str]) -> list[str]:
    """Le ricerche date, più ciascuna senza la parte dopo la prima virgola.

    «Statue of Liberty, Liberty Island» non lo trova nessuno; «Statue of
    Liberty» sì. Il ripiego è sicuro perché la ricerca resta chiusa nel
    rettangolo della città.
    """
    fuori = []
    for q in ricerche:
        for v in (q, q.split(",")[0].strip()):
            if v and v.lower() not in (f.lower() for f in fuori):
                fuori.append(v)
    return fuori


def geocodifica(luoghi: list[dict], riquadro: dict) -> tuple[list[dict], list[dict]]:
    """Trova i luoghi nel rettangolo della città. Ritorna (trovati, non trovati)."""
    viewbox = f"{riquadro['ovest']},{riquadro['nord']},{riquadro['est']},{riquadro['sud']}"
    trovati, mancanti = [], []
    for l in luoghi:
        esito = None
        for q in _ricerche_con_ripiego(l["ricerche"]):
            params = {"q": q, "format": "jsonv2", "limit": 1, "viewbox": viewbox, "bounded": 1}
            if riquadro.get("paese"):
                params["countrycodes"] = riquadro["paese"]
            r = _nominatim(params)
            if r:
                esito = {"lat": float(r[0]["lat"]), "lon": float(r[0]["lon"]),
                         "trovato_con": q, "nome_osm": (r[0].get("display_name") or "")[:120]}
                break
        if esito:
            trovati.append({**l, **esito})
        else:
            mancanti.append({**l, "motivo": "non trovato dal geocoder"})

    # Un omonimo dall'altra parte della città: un luogo a più di 25 km dalla
    # mediana degli altri è quasi sempre il posto sbagliato con il nome giusto.
    if len(trovati) >= 4:
        lat_m = sorted(t["lat"] for t in trovati)[len(trovati) // 2]
        lon_m = sorted(t["lon"] for t in trovati)[len(trovati) // 2]
        tenuti = []
        for t in trovati:
            km = _distanza_km((t["lat"], t["lon"]), (lat_m, lon_m))
            if km > 25:
                mancanti.append({**t, "motivo": f"a {km:.0f} km dagli altri: probabile omonimo"})
            else:
                tenuti.append(t)
        trovati = tenuti
    return trovati, mancanti


def _compatta(el: dict) -> dict | None:
    """Tiene solo quello che serve a disegnare: un ventesimo del peso."""
    def p(n):
        return [round(n["lon"], 5), round(n["lat"], 5)]

    tags = el.get("tags", {})
    utili = {k: tags[k] for k in ("highway", "leisure", "natural", "name", "name:it", "name:en",
                                  "place", "bridge") if k in tags}
    if el["type"] == "node":
        return {"t": "n", "tags": utili, "p": p(el)}
    if el["type"] == "way" and el.get("geometry"):
        return {"t": "w", "tags": utili, "g": [p(n) for n in el["geometry"]]}
    if el["type"] == "relation":
        membri = [{"ruolo": m.get("role", ""), "g": [p(n) for n in m["geometry"]]}
                  for m in el.get("members", []) if m.get("type") == "way" and m.get("geometry")]
        return {"t": "r", "tags": utili, "m": membri} if membri else None
    return None


def dati_osm(rettangoli: list[tuple[float, float, float, float]], sommario: bool = False) -> list[dict]:
    """Strade, parchi, acqua, costa e quartieri nei rettangoli (sud, ovest, nord, est).

    `sommario`: solo costa, acque e grandi strade, per la mappina d'orientamento
    di tutta la città — pochi elementi su un'area grande.
    """
    elementi = []
    for s, w, n, e in rettangoli:
        ms, mw = (n - s) * 0.05, (e - w) * 0.05
        s, w, n, e = round(s - ms, 3), round(w - mw, 3), round(n + ms, 3), round(e + mw, 3)
        larghezza_km = (e - w) * 111.32 * math.cos(math.radians((s + n) / 2))
        # Sopra i sei chilometri le vie di quartiere sono un grigio fitto che
        # copre i puntini: si chiedono solo le strade che si leggono.
        classi = ("motorway|trunk|primary|secondary|tertiary|residential|unclassified|pedestrian"
                  if larghezza_km <= 6 else "motorway|trunk|primary|secondary|tertiary|pedestrian")
        bbox = f"{s},{w},{n},{e}"
        if sommario:
            query = f"""[out:json][timeout:240];
(
  way["highway"~"^(motorway|trunk|primary)$"]({bbox});
  way["natural"="water"]({bbox});
  relation["natural"="water"]({bbox});
  way["natural"="coastline"]({bbox});
);
out geom;"""
        else:
            query = f"""[out:json][timeout:240];
(
  way["highway"~"^({classi})$"]({bbox});
  way["leisure"="park"]({bbox});
  relation["leisure"="park"]({bbox});
  way["natural"="water"]({bbox});
  relation["natural"="water"]({bbox});
  way["natural"="coastline"]({bbox});
  node["place"~"^(suburb|neighbourhood|quarter)$"]({bbox});
);
out geom;"""
        trovato, valore = _leggi_cache("overpass", query)
        if not trovato:
            def fai():
                dati = urllib.parse.urlencode({"data": query}).encode()
                return json.loads(_scarica(config.OVERPASS_URL, dati, timeout=300))

            grezzo = _con_ritentativi(fai, (30, 90))
            valore = [c for c in (_compatta(x) for x in grezzo.get("elements", [])) if c]
            _scrivi_cache("overpass", query, valore)
        elementi.extend(valore)
    return elementi


# ------------------------------------------------------------------- modello


def sezioni_del_capitolo(corpo_md: str) -> list[tuple[str, str]]:
    """Le sezioni numerate del capitolo: [("I", "Il primo giorno"), ...]."""
    return [(m.group(1), m.group(2)) for m in SEZIONE_ROMANA_RE.finditer(corpo_md)]


def _istruzioni(luogo: str, sezioni: list[tuple[str, str]], albergo: str | None) -> str:
    elenco = "\n".join(f"{r}. {t}" for r, t in sezioni) or "(il capitolo non ha sezioni numerate)"
    nota_albergo = f" L'albergo di questa tappa è: {albergo}." if albergo else ""
    return f"""Sei il cartografo di una guida di viaggio. Il capitolo qui sotto parla di {luogo}. Il tuo compito è elencare i luoghi del capitolo da segnare su una mappa della città, con un numero e una legenda.

Cosa va sulla mappa: i luoghi PUNTUALI di cui il capitolo parla e che il lettore andrà a cercare — monumenti, musei, chiese e templi, mercati, ristoranti, bar e negozi nominati, punti panoramici, il molo o la stazione da cui parte una gita descritta, l'albergo.
Cosa NON va: quartieri interi, vie o fiumi lunghi, la città stessa, luoghi in un'altra città, luoghi citati solo per confronto o per sconsigliarli.

Per ogni luogo:
- "etichetta": il nome breve come lo leggerà il lettore nella legenda, al massimo 40 caratteri, come lo chiama il capitolo.
- "sezione": il numero romano della sezione in cui il capitolo ne parla di più, scelto da questo elenco:
{elenco}
- "ricerche": da 1 a 4 testi per il geocoder di OpenStreetMap, dal più preciso al più generico. Prima l'indirizzo, se il capitolo lo dà (via, numero civico, quartiere); poi il nome ufficiale com'è scritto sulle mappe locali o in inglese (per esempio "Senso-ji" o "Metropolitan Museum of Art"). Aggiungi sempre la città in fondo, dopo una virgola. Dove si usa un'altra scrittura (giapponese, cinese, coreano, thai, cirillico, arabo, greco…), una delle ricerche è il nome nella scrittura locale, che è quello principale su OpenStreetMap (per esempio "金沢21世紀美術館, 金沢市").
- "tipo": "albergo" per dove si dorme; "gita" per un luogo che sta in un'altra località, raggiunta in treno o in bus in una gita fuori città (i templi di Nikko in un capitolo su Tokyo); "luogo" per tutto il resto.{nota_albergo}

Due luoghi allo stesso indirizzo (un caffè dentro un museo) diventano una voce sola: "MoMA · Terrace 5". Le ricerche di una voce così cercano il luogo che contiene l'altro, o il primo dei due: mai i due nomi insieme.
Al massimo {config.MAPPE_MAX_LUOGHI} luoghi: se ce ne sono di più, tieni quelli a cui il capitolo dà più spazio. Elencali nell'ordine in cui il lettore li incontra.
NON scrivere coordinate: le trova il geocoder.

Rispondi SOLO con un array JSON, senza altro testo:
[{{"etichetta": "...", "sezione": "I", "ricerche": ["...", "..."], "tipo": "luogo"}}]"""


def _leggi_elenco(testo: str, romani: set[str]) -> list[dict]:
    """L'array JSON della risposta, pulito: niente doppioni, un solo albergo."""
    inizio, fine = testo.find("["), testo.rfind("]")
    if inizio < 0 or fine <= inizio:
        raise ValueError("la risposta non contiene un elenco JSON")
    grezzo = json.loads(testo[inizio:fine + 1])
    fuori, visti, albergo = [], set(), False
    for v in grezzo:
        if not isinstance(v, dict):
            continue
        etichetta = str(v.get("etichetta") or "").strip()[:60]
        ricerche = [str(q).strip() for q in (v.get("ricerche") or []) if str(q).strip()][:4]
        if not etichetta or not ricerche or etichetta.lower() in visti:
            continue
        visti.add(etichetta.lower())
        sezione = str(v.get("sezione") or "").strip().rstrip(".").upper()
        tipo = ("albergo" if v.get("tipo") == "albergo" and not albergo
                else "gita" if v.get("tipo") == "gita" else "luogo")
        albergo = albergo or tipo == "albergo"
        fuori.append({"etichetta": etichetta, "sezione": sezione if sezione in romani else "",
                      "ricerche": ricerche, "tipo": tipo})
    return fuori[: config.MAPPE_MAX_LUOGHI + 1]  # l'albergo non conta tra i trenta


def luoghi_dal_capitolo(client, corpo_md: str, luogo: str, sezioni: list[tuple[str, str]],
                        albergo: str | None) -> tuple[list[dict], dict]:
    """I luoghi del capitolo secondo Haiku. Ritorna (luoghi, voce di costo)."""
    response = client.messages.create(
        model=config.MODEL_MAPPE,
        max_tokens=config.MAX_TOKENS_MAPPE,
        messages=[{"role": "user", "content": _istruzioni(luogo, sezioni, albergo)
                   + "\n\n---\n\n" + corpo_md}],
    )
    voce = costo_chiamata(config.MODEL_MAPPE, response.usage.model_dump())
    if response.stop_reason == "max_tokens":
        raise ValueError("elenco dei luoghi troncato")
    testo = "".join(b.text for b in response.content if b.type == "text")
    return _leggi_elenco(testo, {r for r, _ in sezioni}), voce


# -------------------------------------------------------------------- insieme


def prepara_mappa_capitolo(client, brief_id: str, numero: int, corpo_md: str, luogo: str,
                           albergo: str | None) -> dict:
    """Mappa di un capitolo: scrive cap_NN.png e cap_NN.json. Ritorna il json."""
    from src.mappa_disegno import disegna, rettangoli_geografici, rettangolo_insieme

    png, js = file_mappa(brief_id, numero)
    sezioni = sezioni_del_capitolo(corpo_md)
    esito = {"numero": numero, "luogo": luogo, "sezioni": [list(s) for s in sezioni],
             "luoghi": [], "non_trovati": [], "costo_usd": 0.0}

    def chiudi(motivo: str | None = None) -> dict:
        if motivo:
            esito["saltata"] = motivo
        return _consegna(png, js, esito)

    riquadro = riquadro_citta(luogo)
    if riquadro is None:
        return chiudi(f"la tappa «{luogo}» non si trova sulla mappa")
    luoghi, voce = luoghi_dal_capitolo(client, corpo_md, luogo, sezioni, albergo)
    esito["costo_usd"] = voce.get("costo_usd") or 0.0
    # I luoghi delle gite stanno fuori dal rettangolo della città: cercarli è
    # tempo perso, e la gita c'è già sulla mappa del viaggio.
    esito["in_gita"] = [l["etichetta"] for l in luoghi if l["tipo"] == "gita"]
    luoghi = [l for l in luoghi if l["tipo"] != "gita"]
    # L'albergo si cerca anche col nome scritto nella scheda, così com'è: il
    # modello lo «corregge» («Liberté» per Liberte) e il geocoder non lo trova più.
    if albergo:
        for l in luoghi:
            if l["tipo"] == "albergo":
                nome = re.sub(r"\s+", " ", albergo).strip()
                l["ricerche"] = l["ricerche"] + [f"{nome}, {titolo_breve(luogo)}", nome]
    trovati, mancanti = geocodifica(luoghi, riquadro)
    esito["non_trovati"] = [{"etichetta": m["etichetta"], "motivo": m["motivo"],
                             "ricerche": m["ricerche"]} for m in mancanti]
    if sum(1 for t in trovati if t["tipo"] == "luogo") < config.MAPPE_MIN_LUOGHI:
        return chiudi(f"solo {len(trovati)} luoghi trovati: troppo pochi per una pagina")

    osm = dati_osm(rettangoli_geografici(trovati))
    insieme = rettangolo_insieme(trovati)
    osm_insieme = dati_osm([insieme], sommario=True) if insieme else None
    testo = corpo_md + "\n" + "\n".join(t for _, t in sezioni)
    numerati, scritti = disegna(osm, trovati, [r for r, _ in sezioni], testo, _provvisorio(png),
                                osm_insieme)
    esito["tavole"] = len(scritti)
    esito["luoghi"] = [{k: l[k] for k in ("numero", "etichetta", "sezione", "tipo", "lat", "lon",
                                          "trovato_con") if k in l} for l in numerati]
    return chiudi()


def prepara_mappe(brief, assignments, libro: dict, rifai: bool = False) -> list[dict]:
    """Le mappe di tutti i capitoli sulle tappe. Non solleva mai.

    Un capitolo che ha già la sua mappa (o la nota del perché non ce l'ha) non
    si rifà, a meno di `rifai`: la ripresa di un libro non ripaga niente.
    """
    if not config.MAPPE_ATTIVE:
        return []
    per_numero = {a.numero: a for a in assignments}
    client, esiti = None, []

    # Prima il paese: è una pagina sola, ma sta in testa al libro.
    if rifai or not file_paese(brief.brief_id)[1].exists():
        try:
            from src.chapter_runner import make_client

            client = make_client()
            esito = prepara_mappa_paese(client, brief, assignments, libro)
            if esito.get("tenuta_la_precedente"):
                print(f"Mappa del viaggio: nuovo giro senza mappa ({esito['saltata']}): resta quella di prima.")
            elif esito.get("saltata"):
                print(f"Mappa del viaggio: saltata — {esito['saltata']}.")
            else:
                mancanti = ", ".join(m["nome"] for m in esito["non_trovati"])
                print(f"Mappa del viaggio: {len(esito['fermate'])} tappe, gite: "
                      f"{', '.join(g['nome'] for g in esito['gite']) or 'nessuna'}, "
                      f"${esito['costo_usd']:.3f}" + (f"; fuori mappa: {mancanti}" if mancanti else "") + ".")
        except Exception as exc:  # una pagina in meno, mai un libro in meno
            print(f"Mappa del viaggio non riuscita: {exc!r}", file=sys.stderr)
            _butta_provvisori(file_paese(brief.brief_id)[0])
    for c in libro.get("capitoli") or []:
        a = per_numero.get(c["numero"])
        if c.get("tipo") != "tappa" or a is None or a.tappa is None:
            continue
        png, js = file_mappa(brief.brief_id, c["numero"])
        if js.exists() and not rifai:
            continue
        try:
            if client is None:
                from src.chapter_runner import make_client

                client = make_client()
            t0 = time.monotonic()
            esito = prepara_mappa_capitolo(client, brief.brief_id, c["numero"], c.get("corpo_md") or "",
                                           a.tappa.luogo, a.tappa.hotel)
            if esito.get("tenuta_la_precedente"):
                print(f"Mappa capitolo {c['numero']:02d}: nuovo giro senza mappa "
                      f"({esito['saltata']}): resta quella di prima.")
            elif esito.get("saltata"):
                print(f"Mappa capitolo {c['numero']:02d}: saltata — {esito['saltata']}.")
            else:
                mancanti = ", ".join(m["etichetta"] for m in esito["non_trovati"])
                in_gita = ", ".join(esito.get("in_gita") or [])
                print(f"Mappa capitolo {c['numero']:02d}: {len(esito['luoghi'])} luoghi in "
                      f"{time.monotonic() - t0:.0f}s, ${esito['costo_usd']:.3f}"
                      + (f"; non trovati: {mancanti}" if mancanti else "")
                      + (f"; in gita, sulla mappa del viaggio: {in_gita}" if in_gita else "") + ".")
            esiti.append(esito)
        except Exception as exc:  # una mappa in meno, mai un libro in meno
            print(f"Mappa capitolo {c['numero']:02d} non riuscita: {exc!r}", file=sys.stderr)
            try:
                _butta_provvisori(png)
            except OSError:
                pass
    return esiti


# ------------------------------------------------------------- il paese intero


def titolo_breve(titolo: str) -> str:
    """«Osaka (大阪) — La città che vi guarda mangiare» → «Osaka»."""
    t = re.split(r"\s+—\s+|:\s+", titolo, maxsplit=1)[0]
    return re.sub(r"\s*\([^)]*\)\s*$", "", t).strip() or titolo


def righe_itinerario(brief, assignments, libro: dict, gite: list[dict]) -> list[dict]:
    """L'itinerario sotto la mappa del paese: un rigo per ogni capitolo in viaggio.

    Una tappa dice quante notti e quali gite; un collegamento dice da dove a dove
    (e le notti, se la sosta non ha un capitolo suo, come Hakone).
    `gite`: {nome, capitolo}.
    """
    from src.pdf import romano

    titoli = {c["numero"]: c["titolo"] for c in libro.get("capitoli") or []}
    tappe = sorted(brief.tappe, key=lambda t: t.ordine)
    con_capitolo_tappa = {a.tappa.ordine for a in assignments if a.tappa and a.tipo == "tappa"}
    righe = []
    for a in assignments:
        if a.tappa is None or a.tipo not in ("tappa", "collegamento") or a.numero not in titoli:
            continue
        notti = f"{a.tappa.notti} {'notte' if a.tappa.notti == 1 else 'notti'}"
        mie = [g["nome"] for g in gite if g.get("capitolo") == a.numero]
        if a.tipo == "tappa":
            dettaglio = [notti]
        else:
            prima = next((t for t in reversed(tappe) if t.ordine < a.tappa.ordine), None)
            dettaglio = [f"{titolo_breve(prima.luogo)} → {titolo_breve(a.tappa.luogo)}" if prima
                         else f"verso {titolo_breve(a.tappa.luogo)}"]
            if a.tappa.ordine not in con_capitolo_tappa:
                dettaglio.append(notti)
        if mie:
            dettaglio.append(("gita a " if len(mie) == 1 else "gite a ") + _elenco(mie))
        righe.append({"romano": romano(a.numero), "famiglia": a.tipo,
                      "titolo": titolo_breve(titoli[a.numero]), "dettaglio": " · ".join(dettaglio)})
    return righe


def _elenco(nomi: list[str]) -> str:
    return nomi[0] if len(nomi) == 1 else ", ".join(nomi[:-1]) + " e " + nomi[-1]


def cartella_natural_earth() -> Path:
    """I file di Natural Earth: dall'immagine, o scaricati una volta nella cache."""
    if all((config.NATURAL_EARTH_DIR / f"{n}.geojson").exists() for n in config.NATURAL_EARTH_FILE):
        return config.NATURAL_EARTH_DIR
    d = config.output_root() / "_cache_mappe" / "naturalearth"
    d.mkdir(parents=True, exist_ok=True)
    for n in config.NATURAL_EARTH_FILE:
        f = d / f"{n}.geojson"
        if not f.exists():
            dati = _con_ritentativi(lambda: _scarica(f"{config.NATURAL_EARTH_URL}/{n}.geojson",
                                                     timeout=300), (10, 30))
            tmp = f.with_suffix(f".{os.getpid()}.tmp")
            tmp.write_bytes(dati)
            os.replace(tmp, f)
    return d


def _istruzioni_gite(tappe: list[str]) -> str:
    return f"""Sei il cartografo di una guida di viaggio. Ti do i capitoli del libro che parlano delle tappe del viaggio; ogni capitolo dice qual è la sua città-base.

Elenca le GITE: i luoghi FUORI dalla città-base dove il capitolo manda il lettore in giornata, o in mezza giornata, tornando a dormire alla base.
Non sono gite: i luoghi dentro la città-base o nei suoi quartieri; le tappe del viaggio, cioè le città dove si dorme ({", ".join(tappe)}); le alternative scartate o solo nominate.

Leggi ogni capitolo sezione per sezione: una sezione dedicata a un'altra località è quasi sempre una gita. Valgono anche i capitoli brevi sulle soste di una notte: un'isola o un santuario raggiunti in traghetto o in treno da lì sono gite di quella sosta. Se più capitoli parlano della stessa località, mettila una volta sola, nel capitolo in cui la si visita davvero, non in quello che la nomina soltanto.

Per ogni gita:
- "nome": il nome breve, come si scrive su una mappa ("Nikko", "Nara").
- "ricerca": il testo per il geocoder di OpenStreetMap: il luogo principale della gita col nome com'è sulle mappe locali o in inglese ("Nikko Toshogu"). Mai coordinate.
- "capitolo": il numero del capitolo in cui se ne parla, in cifre arabe.

Al massimo 8 gite. Se non ce ne sono, rispondi [].
Rispondi SOLO con un array JSON: [{{"nome": "...", "ricerca": "...", "capitolo": 4}}]"""


def _leggi_gite(testo: str, capitoli_validi: set[int]) -> list[dict]:
    inizio, fine = testo.find("["), testo.rfind("]")
    if inizio < 0 or fine < inizio:
        raise ValueError("la risposta non contiene un elenco JSON")
    fuori, visti = [], set()
    for v in json.loads(testo[inizio:fine + 1]):
        if not isinstance(v, dict):
            continue
        nome = str(v.get("nome") or "").strip()[:30]
        ricerca = str(v.get("ricerca") or "").strip()
        try:
            capitolo = int(v.get("capitolo"))
        except (TypeError, ValueError):
            continue
        if nome and ricerca and capitolo in capitoli_validi and nome.lower() not in visti:
            visti.add(nome.lower())
            fuori.append({"nome": nome, "ricerche": [ricerca, nome], "capitolo": capitolo})
    return fuori[:8]


def prepara_mappa_paese(client, brief, assignments, libro: dict) -> dict:
    """La mappa del viaggio intero: scrive mappe/paese.png e paese.json."""
    from src.mappa_paese import disegna_paese, proporzioni
    from src.pdf import romano

    png, js = file_paese(brief.brief_id)
    esito = {"fermate": [], "gite": [], "non_trovati": [], "righe": [], "costo_usd": 0.0}

    def chiudi(motivo: str | None = None) -> dict:
        if motivo:
            esito["saltata"] = motivo
        return _consegna(png, js, esito)

    scritti = {c["numero"]: c for c in libro.get("capitoli") or []}
    in_viaggio = [a for a in assignments if a.tappa and a.tipo in ("tappa", "collegamento")
                  and a.numero in scritti]
    tappe = sorted(brief.tappe, key=lambda t: t.ordine)
    if len(tappe) < 2 or not in_viaggio:
        return chiudi("una tappa sola: la mappa della città basta")

    # Le tappe, dal geocoder. Una tappa che non si trova falsa il percorso:
    # meglio nessuna mappa del viaggio che una con un pezzo mancante.
    fermate, paesi = [], set()
    for t in tappe:
        r = riquadro_citta(t.luogo)
        if r is None:
            esito["non_trovati"].append({"nome": t.luogo, "motivo": "tappa non trovata"})
            return chiudi(f"la tappa «{t.luogo}» non si trova sulla mappa")
        if r.get("paese"):
            paesi.add(r["paese"])
        capitoli = [(romano(a.numero), a.tipo) for a in in_viaggio if a.tappa.ordine == t.ordine]
        fermate.append({"nome": titolo_breve(t.luogo), "lat": r["centro"][0], "lon": r["centro"][1],
                        "ordine": t.ordine, "capitoli": capitoli})
    estensione = max(_distanza_km((a["lat"], a["lon"]), (b["lat"], b["lon"]))
                     for a in fermate for b in fermate)
    if estensione < 40:
        return chiudi(f"le tappe stanno in {estensione:.0f} km: la mappa della città basta")

    # Le gite: Haiku le legge dai capitoli, il geocoder le cerca intorno alla base.
    testi = "\n\n".join(
        f"=== Capitolo {a.numero} — base: {titolo_breve(a.tappa.luogo)} ===\n{scritti[a.numero].get('corpo_md') or ''}"
        for a in in_viaggio)
    response = client.messages.create(
        model=config.MODEL_MAPPE, max_tokens=2000,
        messages=[{"role": "user", "content": _istruzioni_gite([f["nome"] for f in fermate])
                   + "\n\n---\n\n" + testi}])
    esito["costo_usd"] = costo_chiamata(config.MODEL_MAPPE, response.usage.model_dump()).get("costo_usd") or 0.0
    testo = "".join(b.text for b in response.content if b.type == "text")
    per_numero = {a.numero: a for a in in_viaggio}
    gite = []
    for g in _leggi_gite(testo, set(per_numero)):
        ordine = per_numero[g["capitolo"]].tappa.ordine
        base = next(i for i, f in enumerate(fermate) if f["ordine"] == ordine)
        b = fermate[base]
        riq = {"sud": b["lat"] - 2.5, "nord": b["lat"] + 2.5, "ovest": b["lon"] - 3.0,
               "est": b["lon"] + 3.0, "paese": next(iter(paesi)) if len(paesi) == 1 else None}
        trovati, _ = geocodifica([{"etichetta": g["nome"], "ricerche": g["ricerche"], "tipo": "luogo"}], riq)
        if not trovati:
            esito["non_trovati"].append({"nome": g["nome"], "motivo": "gita non trovata"})
            continue
        km = _distanza_km((trovati[0]["lat"], trovati[0]["lon"]), (b["lat"], b["lon"]))
        if km < 10:
            continue  # è la città stessa: sta già nella mappa della città
        if any(_distanza_km((trovati[0]["lat"], trovati[0]["lon"]), (f["lat"], f["lon"])) < 8 for f in fermate):
            continue  # è un'altra tappa del viaggio, non una gita
        gite.append({"nome": g["nome"], "lat": trovati[0]["lat"], "lon": trovati[0]["lon"],
                     "base": base, "capitolo": g["capitolo"]})

    righe = righe_itinerario(brief, assignments, libro, gite)
    altezza = max(80.0, min(117 / proporzioni(fermate, gite), 177 - 16 - 8 * len(righe)))
    disegna_paese([dict(f) for f in fermate], [dict(g) for g in gite], paesi or {"XX"},
                  cartella_natural_earth(), _provvisorio(png), altezza)
    esito.update({"fermate": fermate, "gite": gite, "righe": righe, "altezza_mm": round(altezza, 1),
                  "paesi": sorted(paesi)})
    return chiudi()
