#!/usr/bin/env python3
"""Coordinate delle tappe e delle gite per la prova di mappa del paese (Giappone).

Da lanciare dove Nominatim è raggiungibile (Cloud Shell). Solo libreria standard.
Le coordinate vengono dal geocoder di OpenStreetMap, mai da un modello. In
produzione lo stesso lavoro lo fa src/mappe.py dentro il motore.

Stampa una riga JSON da incollare in chat, e la scrive in tappe_dati.json.
"""

import json
import time
import urllib.parse
import urllib.request

UA = "StradaLeggendo-prototipo-mappe/0.1"

# (nome sulla mappa, ricerche in ordine di preferenza, tipo)
LUOGHI = [
    ("Tokyo", ["Tokyo Station", "Tokyo"], "tappa"),
    ("Hakone", ["Hakone-Yumoto Station", "Hakone"], "tappa"),
    ("Kyoto", ["Kyoto Station", "Kyoto"], "tappa"),
    ("Osaka", ["Osaka Station", "Osaka"], "tappa"),
    ("Hiroshima", ["Hiroshima Station", "Hiroshima"], "tappa"),
    ("Kanazawa", ["Kanazawa Station", "Kanazawa"], "tappa"),
    ("Nikko", ["Nikko Toshogu", "Nikko"], "gita"),
    ("Nara", ["Todai-ji", "Nara"], "gita"),
    ("Akame", ["Akame 48 Waterfalls", "Akameguchi Station", "Akame"], "gita"),
    ("Miyajima", ["Itsukushima Shrine", "Miyajima"], "gita"),
    ("Shirakawa-go", ["Ogimachi, Shirakawa", "Shirakawa-go"], "gita"),
]


def cerca(q: str):
    params = urllib.parse.urlencode({"q": q, "format": "jsonv2", "limit": 1, "countrycodes": "jp"})
    req = urllib.request.Request(f"https://nominatim.openstreetmap.org/search?{params}",
                                 headers={"User-Agent": UA})
    with urllib.request.urlopen(req, timeout=60) as r:
        risposta = json.loads(r.read())
    time.sleep(1.1)  # regola d'uso di Nominatim: una richiesta al secondo
    return risposta[0] if risposta else None


def main() -> None:
    fuori = []
    for nome, ricerche, tipo in LUOGHI:
        trovato = None
        for q in ricerche:
            r = cerca(q)
            if r:
                trovato = {"lat": round(float(r["lat"]), 5), "lon": round(float(r["lon"]), 5), "con": q}
                break
        print(f"  {nome:<14} {trovato or 'NON TROVATO'}")
        fuori.append({"nome": nome, "tipo": tipo, **(trovato or {})})
    riga = json.dumps(fuori, ensure_ascii=False, separators=(",", ":"))
    with open("tappe_dati.json", "w", encoding="utf-8") as f:
        f.write(riga)
    print("\nDa incollare in chat:\n" + riga)


if __name__ == "__main__":
    main()
