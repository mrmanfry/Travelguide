"""Il client della Print API di Lulu: preventivo, ordine, stato, firma dei webhook.

Solo libreria standard. Le chiavi stanno nel secret Modal `lulu-secret`
(LULU_CLIENT_KEY, LULU_CLIENT_SECRET) e non escono mai dal motore: il sito
chiede al motore, il motore chiede a Lulu. LULU_AMBIENTE sceglie il server:
"sandbox" (il default: ordini finti, carte di prova) o "produzione".
"""

import base64
import hashlib
import hmac
import json
import os
import time
import urllib.error
import urllib.parse
import urllib.request

SERVER = {"sandbox": "https://api.sandbox.lulu.com", "produzione": "https://api.lulu.com"}

# A5 (148×210), a colori, qualità standard, brossura, patinata 80#, copertina
# opaca. Il colore serve: mappe, bolli, linguette, titoli in terracotta.
PRODOTTO = os.environ.get("GUIDE_LULU_PRODOTTO", "0583X0827.FC.STD.PB.080CW444.MXX")
PAGINE_MIN = 32  # sotto, la brossura non si fa
# Davanti alle API di Lulu c'è Cloudflare, che respinge (errore 1010) le
# richieste con lo User-Agent di serie di Python: diciamo chi siamo.
USER_AGENT = "StradaLeggendo-Motore/1.0 (+print-api)"


class ErroreLulu(RuntimeError):
    def __init__(self, stato: int, corpo: str):
        super().__init__(f"Lulu {stato}: {corpo[:500]}")
        self.stato, self.corpo = stato, corpo


def ambiente() -> str:
    a = os.environ.get("LULU_AMBIENTE", "sandbox").strip().lower()
    return a if a in SERVER else "sandbox"


def _base() -> str:
    return SERVER[ambiente()]


def _chiavi() -> tuple[str, str]:
    k, s = os.environ.get("LULU_CLIENT_KEY", "").strip(), os.environ.get("LULU_CLIENT_SECRET", "").strip()
    if not k or not s:
        raise RuntimeError("Chiavi Lulu mancanti: LULU_CLIENT_KEY e LULU_CLIENT_SECRET nel secret lulu-secret.")
    return k, s


_gettone = {"valore": None, "scade": 0.0}


def _token() -> str:
    if _gettone["valore"] and time.time() < _gettone["scade"] - 60:
        return _gettone["valore"]
    k, s = _chiavi()
    req = urllib.request.Request(
        f"{_base()}/auth/realms/glasstree/protocol/openid-connect/token",
        data=b"grant_type=client_credentials",
        headers={"Content-Type": "application/x-www-form-urlencoded", "User-Agent": USER_AGENT,
                 "Authorization": "Basic " + base64.b64encode(f"{k}:{s}".encode()).decode()})
    with urllib.request.urlopen(req, timeout=30) as r:
        dati = json.loads(r.read())
    _gettone.update(valore=dati["access_token"], scade=time.time() + float(dati.get("expires_in", 300)))
    return _gettone["valore"]


def _chiama(metodo: str, percorso: str, corpo: dict | None = None, autenticata: bool = True):
    intestazioni = {"Content-Type": "application/json", "Accept": "application/json",
                    "User-Agent": USER_AGENT}
    if autenticata:
        intestazioni["Authorization"] = f"Bearer {_token()}"
    req = urllib.request.Request(f"{_base()}{percorso}", method=metodo, headers=intestazioni,
                                 data=json.dumps(corpo).encode() if corpo is not None else None)
    try:
        with urllib.request.urlopen(req, timeout=60) as r:
            testo = r.read().decode() or "null"
    except urllib.error.HTTPError as exc:
        raise ErroreLulu(exc.code, exc.read().decode(errors="replace")) from None
    return json.loads(testo)


# ------------------------------------------------------------------ indirizzo

CAMPI_INDIRIZZO = ("name", "street1", "street2", "city", "postcode", "state_code", "country_code",
                   "phone_number")


def indirizzo_pulito(ind: dict) -> dict:
    """Solo i campi che Lulu conosce, e quelli obbligatori presenti."""
    pulito = {k: str(ind[k]).strip() for k in CAMPI_INDIRIZZO if ind.get(k) not in (None, "")}
    mancanti = [k for k in ("name", "street1", "city", "postcode", "country_code", "phone_number")
                if not pulito.get(k)]
    if mancanti:
        raise ValueError("Indirizzo incompleto: manca " + ", ".join(mancanti))
    pulito["country_code"] = pulito["country_code"].upper()
    return pulito


# ----------------------------------------------------------------- chiamate

def dorso_mm(pagine: int) -> float:
    """Lo spessore del dorso, da Lulu: larghezza della copertina meno due pagine e l'abbondanza."""
    d = _chiama("POST", "/cover-dimensions/",
                {"pod_package_id": PRODOTTO, "interior_page_count": pagine, "unit": "mm"})
    return round(float(d["width"]) - 2 * 148.0 - 2 * 3.175, 3)


def spedizioni(indirizzo: dict, pagine: int, valuta: str = "EUR") -> list[dict]:
    """Le opzioni di spedizione con le date di consegna stimate."""
    ind = indirizzo_pulito(indirizzo)
    # Questo endpoint chiama i campi diversamente dagli altri: country e state.
    corpo = {"currency": valuta,
             "line_items": [{"page_count": pagine, "pod_package_id": PRODOTTO, "quantity": 1}],
             "shipping_address": {"country": ind["country_code"], "city": ind["city"],
                                  "postcode": ind["postcode"], "street1": ind["street1"],
                                  **({"state": ind["state_code"]} if ind.get("state_code") else {})}}
    return _chiama("POST", "/shipping-options/", corpo)


def costo(indirizzo: dict, pagine: int, livello: str, valuta: str = "EUR") -> dict:
    """Stampa, spedizione, servizio e IVA per un livello di spedizione."""
    return _chiama("POST", "/print-job-cost-calculations/", {
        "currency": valuta,
        "line_items": [{"page_count": pagine, "pod_package_id": PRODOTTO, "quantity": 1}],
        "shipping_address": indirizzo_pulito(indirizzo),
        "shipping_option": livello,
    })


def crea_ordine(titolo: str, url_interno: str, url_copertina: str, indirizzo: dict, livello: str,
                email_contatto: str, riferimento: str, ritardo_minuti: int = 120) -> dict:
    """L'ordine di stampa. `riferimento` torna indietro nei webhook (external_id)."""
    return _chiama("POST", "/print-jobs/", {
        "contact_email": email_contatto,
        "external_id": riferimento,
        "production_delay": max(60, min(2880, int(ritardo_minuti))),
        "shipping_level": livello,
        "shipping_address": indirizzo_pulito(indirizzo),
        "line_items": [{
            "external_id": riferimento,
            "title": titolo[:255],
            "quantity": 1,
            "printable_normalization": {
                "pod_package_id": PRODOTTO,
                "interior": {"source_url": url_interno},
                "cover": {"source_url": url_copertina},
            },
        }],
    })


def ordine(id_lulu: int) -> dict:
    return _chiama("GET", f"/print-jobs/{int(id_lulu)}/")


def firma_valida(corpo_grezzo: bytes, firma: str) -> bool:
    """La firma dei webhook: HMAC-SHA256 del corpo così com'è, col segreto del cliente."""
    _, segreto = _chiavi()
    atteso = hmac.new(segreto.encode(), corpo_grezzo, hashlib.sha256).hexdigest()
    return bool(firma) and hmac.compare_digest(atteso, firma.strip().lower())


def webhook(url: str) -> dict:
    """Iscrive `url` agli stati degli ordini, se non lo è già. Ritorna il webhook."""
    elenco = _chiama("GET", "/webhooks/")
    risultati = elenco.get("results", elenco) if isinstance(elenco, dict) else elenco
    for w in risultati or []:
        if w.get("url") == url:
            if not w.get("is_active", True):
                return _chiama("PATCH", f"/webhooks/{w['id']}/", {"is_active": True})
            return w
    return _chiama("POST", "/webhooks/", {"topics": ["PRINT_JOB_STATUS_CHANGED"], "url": url})


def prova_chiavi() -> str:
    """Chiede un gettone e dice in chiaro se le chiavi valgono per questo ambiente."""
    k, s = _chiavi()
    try:
        _gettone["valore"] = None
        _token()
    except urllib.error.HTTPError as exc:
        altro = "produzione" if ambiente() == "sandbox" else "sandbox"
        return (f"NO: Lulu {ambiente()} rifiuta le chiavi ({exc.code}). Controlla che vengano da "
                f"{'developers.sandbox.lulu.com' if ambiente() == 'sandbox' else 'developers.lulu.com'} "
                f"e non da {altro}, senza spazi, e che key e secret non siano invertiti "
                f"(key di {len(k)} caratteri, secret di {len(s)}).")
    return f"OK: chiavi valide per Lulu {ambiente()}."
