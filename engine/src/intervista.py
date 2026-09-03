"""Intervista di intake: una conversazione breve che rifinisce il brief.

Dopo che l'utente ha compilato il form, l'AI riceve il brief e conduce una
mini-intervista (poche domande, una alla volta) per capire meglio il tipo di
viaggiatore e le preferenze. A chiusura restituisce il brief arricchito nei
soli campi di preferenza. Nessuna ricerca web: è solo conversazione.

Contratto (usato dall'endpoint /intervista di modal_app.py):

    passo_intervista(brief_dict, messaggi) -> dict con:
      - azione: "domanda" | "fine"
      - messaggio: testo dell'AI (prossima domanda, o chiusura)
      - brief: None mentre intervista; il brief arricchito quando azione="fine"

`messaggi` è la conversazione finora, lista di {"ruolo": "assistant"|"user",
"testo": "..."} — le domande dell'AI e le risposte dell'utente, in ordine.
"""

import json
import re

from src import config
from src.chapter_runner import PROMPTS_DIR, make_client, stable_json

_JSON_FENCE_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def _system_prompt() -> str:
    return (PROMPTS_DIR / "intervista_system.md").read_text(encoding="utf-8")


def _estrai_json(raw: str) -> dict | None:
    """Estrae l'oggetto JSON dalla risposta del modello, tollerando fence/preamboli."""
    candidati = [raw.strip()]
    m = _JSON_FENCE_RE.search(raw)
    if m:
        candidati.append(m.group(1))
    i, j = raw.find("{"), raw.rfind("}")
    if i != -1 and j > i:
        candidati.append(raw[i : j + 1])
    for c in candidati:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def _costruisci_messaggi(brief_dict: dict, messaggi: list[dict]) -> list[dict]:
    """Trasforma brief + storico in messaggi per l'API.

    Il primo turno è un messaggio user col brief del form; poi si riporta la
    conversazione (domande dell'AI come 'assistant', risposte come 'user').
    """
    out = [
        {
            "role": "user",
            "content": (
                "BRIEF (dal form):\n\n"
                + stable_json(brief_dict)
                + "\n\nConduci l'intervista secondo le istruzioni: se serve, "
                "fai la prossima domanda; se hai un quadro sufficiente, chiudi."
            ),
        }
    ]
    for m in messaggi or []:
        if not isinstance(m, dict):
            continue
        ruolo = "assistant" if m.get("ruolo") == "assistant" else "user"
        testo = (m.get("testo") or "").strip()
        if testo:
            out.append({"role": ruolo, "content": testo})
    return out


MIN_RISPOSTE = 2  # non chiudere prima di almeno due risposte dell'utente

# Ripieghi IN VOCE (mai frasi di sistema), usati solo se il modello sbaglia il
# formato due volte di fila: una domanda morbida se siamo ancora presto, un
# piccolo ritratto-chiusura se abbiamo già abbastanza.
_RIPIEGO_DOMANDA = {
    "azione": "domanda",
    "messaggio": (
        "Prima di mettermi a scrivere, ditemi una cosa: quando un viaggio vi resta "
        "dentro, di solito è per un posto, per una persona incontrata o per un momento "
        "in cui vi siete sentiti liberi?"
    ),
    "opzioni": ["Un posto", "Un incontro", "Un momento di libertà", "Un po' tutto"],
    "brief": None,
}


def _conta_risposte(messaggi: list[dict]) -> int:
    return sum(1 for m in (messaggi or []) if isinstance(m, dict) and m.get("ruolo") == "user")


def _chiama_modello(brief_dict: dict, messaggi: list[dict]) -> dict | None:
    client = make_client()
    response = client.messages.create(
        model=config.MODEL_INTERVISTA,
        max_tokens=config.MAX_TOKENS_INTERVISTA,
        system=_system_prompt(),
        messages=_costruisci_messaggi(brief_dict, messaggi),
    )
    raw = "".join(b.text for b in response.content if b.type == "text")
    obj = _estrai_json(raw)
    if isinstance(obj, dict) and obj.get("azione") in ("domanda", "fine"):
        return obj
    return None


def _normalizza(obj: dict, brief_dict: dict) -> dict:
    """Ripulisce l'oggetto del modello nel contratto {azione, messaggio, opzioni, brief}."""
    messaggio = (obj.get("messaggio") or "").strip()
    if obj["azione"] == "fine":
        brief = obj.get("brief")
        if not isinstance(brief, dict) or not brief:
            brief = brief_dict
        return {"azione": "fine", "messaggio": messaggio, "opzioni": [], "brief": brief}
    opzioni = obj.get("opzioni")
    if isinstance(opzioni, list):
        opzioni = [str(o).strip() for o in opzioni if str(o).strip()][:5]
    else:
        opzioni = []
    return {"azione": "domanda", "messaggio": messaggio, "opzioni": opzioni, "brief": None}


def passo_intervista(brief_dict: dict, messaggi: list[dict]) -> dict:
    """Un turno di intervista. Ritorna {azione, messaggio, opzioni, brief}.

    Robustezza: se il modello sbaglia il formato, ritenta una volta. Se sbaglia
    ancora, ripiega IN VOCE — una domanda morbida se l'intervista è appena
    iniziata, altrimenti una chiusura garbata — senza mai esporre frasi di
    sistema e senza perdere il brief. Non chiude prima di MIN_RISPOSTE risposte:
    se il modello prova a chiudere troppo presto, lo si riporta a una domanda.
    """
    obj = _chiama_modello(brief_dict, messaggi) or _chiama_modello(brief_dict, messaggi)
    n = _conta_risposte(messaggi)

    if obj is None:
        # Due tentativi falliti: ripiego in voce, calibrato su quanto siamo avanti.
        if n < MIN_RISPOSTE:
            return dict(_RIPIEGO_DOMANDA)
        return {
            "azione": "fine",
            "messaggio": (
                "Ho abbastanza per iniziare: comincio a scrivervi qualcosa che vi somigli."
            ),
            "opzioni": [],
            "brief": brief_dict,
        }

    # Il modello vuole chiudere troppo presto: riportalo a una domanda in voce.
    if obj["azione"] == "fine" and n < MIN_RISPOSTE:
        return dict(_RIPIEGO_DOMANDA)

    return _normalizza(obj, brief_dict)
