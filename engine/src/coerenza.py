"""Controllo di coerenza del brief, prima di spendere.

Il motore si fida del brief: se il brief dice "in moto, massimo 200 km al
giorno" e la destinazione è il Giappone raggiunto in aereo, Opus scrive
tredici capitoli coerenti con una premessa sbagliata — e il libro è da
buttare. È successo davvero (il precaricamento del form ha lasciato dentro
i residui di un viaggio in Grecia).

Questa è la guardia: una sola chiamata al modello più economico, senza
ricerca, prima dell'outline. Non corregge e non blocca — non decide al posto
di nessuno. Scrive `coerenza.json` accanto agli altri artefatti, così il
sito può mostrare gli avvisi sulla pagina dell'assaggio, dove l'utente sta
già decidendo se pagare: è quello il momento giusto per dire "abbiamo
notato che...", non dopo.

Costo: qualche millesimo di dollaro contro i venti del libro.
"""

import json
import re

from schema.brief import Brief
from src import config
from src.chapter_runner import make_client, stable_json
from src.costs import costo_chiamata

_OGGETTO_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)

_SYSTEM = """Sei il lettore scettico che guarda la scheda di un viaggio prima che
qualcuno spenda dei soldi a scriverci sopra un libro.

Il tuo unico compito: trovare le contraddizioni INTERNE alla scheda e i residui
di un altro viaggio. Non giudichi il gusto di nessuno, non suggerisci itinerari,
non commenti se il viaggio ti sembra bello o brutto.

Cerchi esattamente questo:
- Luoghi citati che non appartengono alla destinazione (il caso tipico: campi
  riempiti da un viaggio precedente e mai svuotati).
- Il mezzo dichiarato contro i vincoli dichiarati (limiti di chilometri al
  giorno quando ci si muove in treno o in aereo; bagaglio da moto su un viaggio
  in auto; patenti e noleggi che non servono).
- Le date contro la durata, e le date contro le tappe (più tappe che giorni).
- Il numero e la composizione dei viaggiatori contro se stessi.
- Passioni o richieste che il mezzo o le date rendono impossibili.

NON segnalare: ambizioni faticose ma possibili, gusti insoliti, budget bassi,
campi vuoti, o cose che richiedono di sapere orari e prezzi reali (non hai
ricerca: non inventare fatti sul mondo).

Rispondi SOLO con un oggetto JSON, senza testo attorno:

{"avvisi": [{"campo": "<il campo del brief>", "gravita": "bloccante" | "attenzione",
  "problema": "<una frase: cosa non torna, citando i valori>",
  "domanda": "<una frase rivolta al viaggiatore, in italiano, dandogli del voi>"}]}

"bloccante" = il libro uscirebbe sbagliato (un luogo di un altro paese, un mezzo
che contraddice il viaggio). "attenzione" = è strano ma potrebbe essere voluto.

Se la scheda è coerente: {"avvisi": []}. Non riempire per forza.
"""


def coerenza_path(brief: Brief):
    return config.output_root() / brief.brief_id / "coerenza.json"


def _estrai(testo: str) -> dict | None:
    m = _OGGETTO_RE.search(testo)
    grezzo = m.group(1) if m else testo.strip()
    try:
        dato = json.loads(grezzo)
    except (json.JSONDecodeError, ValueError):
        return None
    return dato if isinstance(dato, dict) else None


def verifica_coerenza(brief: Brief) -> dict:
    """Controlla il brief e ritorna {"avvisi": [...], "costo_usd": float}.

    Non solleva mai: se il controllo fallisce (modello irraggiungibile, risposta
    non parsabile) si ritorna una lista vuota. Una guardia che rompe la
    generazione sarebbe peggio del problema che previene.
    """
    try:
        client = make_client()
        response = client.messages.create(
            model=config.MODEL_COERENZA,
            max_tokens=config.MAX_TOKENS_COERENZA,
            system=[{"type": "text", "text": _SYSTEM}],
            messages=[
                {
                    "role": "user",
                    "content": (
                        "# SCHEDA DEL VIAGGIO\n\n"
                        + stable_json(brief.model_dump(mode="json"))
                        + "\n\nCosa non torna? Solo l'oggetto JSON."
                    ),
                }
            ],
        )
    except Exception as exc:  # la guardia non deve mai fermare la guida
        print(f"Controllo di coerenza non eseguito: {exc}")
        return {"avvisi": [], "costo_usd": 0.0}

    testo = "".join(b.text for b in response.content if b.type == "text")
    dato = _estrai(testo) or {}
    avvisi = [a for a in (dato.get("avvisi") or []) if isinstance(a, dict)]
    costo = costo_chiamata(config.MODEL_COERENZA, response.usage.model_dump()).get(
        "costo_usd"
    )
    return {"avvisi": avvisi, "costo_usd": float(costo or 0.0)}


def scrivi_coerenza(brief: Brief, esito: dict):
    """Salva l'esito accanto agli altri artefatti e lo racconta nel log."""
    path = coerenza_path(brief)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(esito, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    avvisi = esito.get("avvisi") or []
    if not avvisi:
        print("Coerenza del brief: nessun avviso.")
        return path
    bloccanti = sum(1 for a in avvisi if a.get("gravita") == "bloccante")
    print(
        f"Coerenza del brief: {len(avvisi)} avviso/i ({bloccanti} da chiarire) "
        f"— vedi {path}"
    )
    for a in avvisi:
        print(f"  - [{a.get('gravita', '?')}] {a.get('campo', '?')}: {a.get('problema', '')}")
    return path
