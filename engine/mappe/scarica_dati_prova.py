#!/usr/bin/env python3
"""Scarica i dati per la mappa di prova: capitolo III del libro di Elena (New York).

Da lanciare dove OpenStreetMap è raggiungibile (Cloud Shell): qui si fanno solo
le due cose che hanno bisogno della rete, e il disegno si fa altrove.

1. Geocodifica i luoghi del capitolo con Nominatim. Le coordinate vengono dal
   geocoder, MAI da un modello: dove si trova un luogo è la prima delle cose che
   il libro non può sbagliare. Si cerca l'indirizzo quando il capitolo lo dà, e
   sempre dentro i confini di New York — «Ferrara» da solo finirebbe in Emilia.
2. Scarica da Overpass strade, parchi, acqua, linea di costa e nomi di quartiere
   nel rettangolo che contiene i luoghi trovati.

Scrive mappa_dati.json nella cartella corrente. Solo libreria standard: in
Cloud Shell non c'è niente da installare.

L'elenco dei luoghi è quello che in produzione estrarrà Haiku dal capitolo già
scritto; qui è stato estratto a mano, leggendo il PDF.
"""

import json
import time
import urllib.parse
import urllib.request
from pathlib import Path

UA = "StradaLeggendo-prototipo-mappe/0.1"
# Il rettangolo di New York in cui il geocoder è obbligato a cercare
# (ovest, nord, est, sud). Con bounded=1 un nome ambiguo non può uscirne.
VIEWBOX = "-74.10,40.88,-73.85,40.66"

# (sezione, etichetta sulla mappa, ricerche in ordine di preferenza, tipo)
# tipo: "luogo" | "albergo" — l'albergo è la base del viaggio, si disegna a sé.
LUOGHI = [
    ("I", "Times Square", ["Times Square, Manhattan"], "luogo"),
    ("I", "TKTS, i gradini rossi", ["Father Duffy Square, Manhattan"], "luogo"),
    ("I", "MoMA", ["11 West 53rd Street, Manhattan"], "luogo"),
    ("I", "The Met", ["1000 5th Avenue, Manhattan"], "luogo"),
    ("I", "Moxy Times Square", ["485 7th Avenue, Manhattan"], "albergo"),
    ("II", "Grand Central", ["89 East 42nd Street, Manhattan", "Grand Central Terminal"], "luogo"),
    ("II", "Flatiron", ["175 5th Avenue, Manhattan", "Flatiron Building"], "luogo"),
    ("III", "Strand", ["828 Broadway, Manhattan"], "luogo"),
    ("III", "Veselka", ["144 2nd Avenue, Manhattan"], "luogo"),
    ("IV", "Brooklyn Bridge", ["Brooklyn Bridge"], "luogo"),
    ("IV", "Dumbo, la vista sul ponte",
     ["Washington Street & Water Street, Brooklyn", "Washington Street, Dumbo, Brooklyn"], "luogo"),
    ("IV", "Juliana's", ["19 Old Fulton Street, Brooklyn"], "luogo"),
    ("V", "First Corinthian Baptist Church",
     ["1912 Adam Clayton Powell Jr Boulevard, Manhattan", "First Corinthian Baptist Church"], "luogo"),
    ("V", "Sylvia's", ["328 Malcolm X Boulevard, Manhattan", "328 Lenox Avenue, Manhattan"], "luogo"),
    ("VI", "9/11 Memorial", ["180 Greenwich Street, Manhattan", "National September 11 Memorial"], "luogo"),
    ("VI", "Federal Hall", ["26 Wall Street, Manhattan"], "luogo"),
    ("VI", "Trinity Church", ["Trinity Church, 75 Broadway, Manhattan", "Trinity Church, Manhattan"], "luogo"),
    ("VI", "Imbarco per Liberty ed Ellis", ["Castle Clinton, Manhattan"], "luogo"),
    ("VI", "Statua della Libertà", ["Statue of Liberty, Liberty Island"], "luogo"),
    ("VI", "Ellis Island", ["Ellis Island Immigration Museum", "Ellis Island"], "luogo"),
    ("VII", "SoHo, la ghisa", ["Greene Street & Broome Street, Manhattan", "Greene Street, Manhattan"], "luogo"),
    ("VII", "Di Palo's · Ferrara", ["200 Grand Street, Manhattan"], "luogo"),
    ("VII", "Doyers Street · Nom Wah", ["13 Doyers Street, Manhattan", "Doyers Street, Manhattan"], "luogo"),
    ("VII", "Summit One Vanderbilt", ["1 Vanderbilt Avenue, Manhattan", "One Vanderbilt"], "luogo"),
    ("VII", "Empire State Building", ["350 5th Avenue, Manhattan", "Empire State Building"], "luogo"),
    ("VII", "Rockefeller Center · Top of the Rock", ["30 Rockefeller Plaza, Manhattan", "Rockefeller Center"], "luogo"),
    ("VII", "The Edge", ["30 Hudson Yards, Manhattan"], "luogo"),
]


def _get(url: str, timeout: int = 60) -> bytes:
    req = urllib.request.Request(url, headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=timeout) as r:
        return r.read()


def geocodifica(ricerche: list[str]) -> dict | None:
    for q in ricerche:
        params = urllib.parse.urlencode(
            {"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "us",
             "viewbox": VIEWBOX, "bounded": 1}
        )
        risposta = json.loads(_get(f"https://nominatim.openstreetmap.org/search?{params}"))
        time.sleep(1.1)  # regola d'uso di Nominatim: al massimo una richiesta al secondo
        if risposta:
            r = risposta[0]
            return {"lon": float(r["lon"]), "lat": float(r["lat"]),
                    "trovato_con": q, "nome_osm": r.get("display_name", "")[:120]}
    return None


def _p(nodo: dict) -> list[float]:
    return [round(nodo["lon"], 5), round(nodo["lat"], 5)]


def compatta(el: dict) -> dict | None:
    """Tiene solo quello che serve a disegnare: un ventesimo del peso."""
    tags = el.get("tags", {})
    utili = {k: tags[k] for k in ("highway", "leisure", "natural", "name", "place", "bridge") if k in tags}
    if el["type"] == "node":
        return {"t": "n", "tags": utili, "p": [round(el["lon"], 5), round(el["lat"], 5)]}
    if el["type"] == "way" and el.get("geometry"):
        return {"t": "w", "tags": utili, "g": [_p(n) for n in el["geometry"]]}
    if el["type"] == "relation":
        membri = [
            {"ruolo": m.get("role", ""), "g": [_p(n) for n in m["geometry"]]}
            for m in el.get("members", [])
            if m.get("type") == "way" and m.get("geometry")
        ]
        return {"t": "r", "tags": utili, "m": membri} if membri else None
    return None


def main() -> None:
    print("1/2 Geocodifica dei luoghi (circa mezzo minuto)…")
    luoghi = []
    for sezione, etichetta, ricerche, tipo in LUOGHI:
        g = geocodifica(ricerche)
        stato = f"{g['lat']:.5f}, {g['lon']:.5f}  ← {g['nome_osm']}" if g else "NON TROVATO"
        print(f"  [{sezione:>3}] {etichetta:<38} {stato}")
        luoghi.append({"sezione": sezione, "etichetta": etichetta, "tipo": tipo, **(g or {})})

    trovati = [l for l in luoghi if "lat" in l]
    margine = 0.012
    sud = min(l["lat"] for l in trovati) - margine
    nord = max(l["lat"] for l in trovati) + margine
    ovest = min(l["lon"] for l in trovati) - margine
    est = max(l["lon"] for l in trovati) + margine
    bbox = f"{sud:.4f},{ovest:.4f},{nord:.4f},{est:.4f}"

    print(f"2/2 Strade, parchi e acqua da Overpass nel rettangolo {bbox} (fino a due minuti)…")
    query = f"""[out:json][timeout:240];
(
  way["highway"~"^(motorway|trunk|primary|secondary|tertiary|residential|unclassified|pedestrian)$"]({bbox});
  way["leisure"="park"]({bbox});
  relation["leisure"="park"]({bbox});
  way["natural"="water"]({bbox});
  relation["natural"="water"]({bbox});
  way["natural"="coastline"]({bbox});
  node["place"~"^(suburb|neighbourhood|quarter)$"]({bbox});
);
out geom;"""
    req = urllib.request.Request(
        "https://overpass-api.de/api/interpreter",
        data=urllib.parse.urlencode({"data": query}).encode(),
        headers={"User-Agent": UA},
    )
    with urllib.request.urlopen(req, timeout=300) as r:
        grezzo = json.loads(r.read())
    elementi = [c for c in (compatta(e) for e in grezzo.get("elements", [])) if c]

    dati = {"bbox": [sud, ovest, nord, est], "luoghi": luoghi, "osm": elementi,
            "attribuzione": "© OpenStreetMap contributors"}
    out = Path("mappa_dati.json")
    out.write_text(json.dumps(dati, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
    mancanti = [l["etichetta"] for l in luoghi if "lat" not in l]
    print(f"\nScritto {out.resolve()} — {out.stat().st_size / 1e6:.1f} MB, "
          f"{len(elementi)} elementi, {len(trovati)}/{len(luoghi)} luoghi trovati.")
    if mancanti:
        print("Non trovati (restano fuori dalla mappa, non si indovinano):", ", ".join(mancanti))


if __name__ == "__main__":
    main()
