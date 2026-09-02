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


def passo_intervista(brief_dict: dict, messaggi: list[dict]) -> dict:
    """Un turno di intervista. Ritorna {azione, messaggio, brief}.

    Non solleva sull'output malformato: in quel caso chiude in modo sicuro
    (azione='fine') restituendo il brief così com'era, così il flusso non si
    blocca e si può comunque generare.
    """
    client = make_client()
    response = client.messages.create(
        model=config.MODEL_INTERVISTA,
        max_tokens=config.MAX_TOKENS_INTERVISTA,
        system=_system_prompt(),
        messages=_costruisci_messaggi(brief_dict, messaggi),
    )
    raw = "".join(b.text for b in response.content if b.type == "text")
    obj = _estrai_json(raw)

    if not isinstance(obj, dict) or obj.get("azione") not in ("domanda", "fine"):
        # Output non conforme: chiudi senza perdere il brief del form.
        return {
            "azione": "fine",
            "messaggio": "Ho quanto mi serve: procediamo con la tua guida.",
            "brief": brief_dict,
        }

    azione = obj["azione"]
    messaggio = (obj.get("messaggio") or "").strip()
    if azione == "fine":
        brief = obj.get("brief")
        # Se il modello non ha rimandato un brief valido, tieni quello del form.
        if not isinstance(brief, dict) or not brief:
            brief = brief_dict
        return {"azione": "fine", "messaggio": messaggio, "brief": brief}

    return {"azione": "domanda", "messaggio": messaggio, "brief": None}
