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


def _system_prompt(modo: str = "intake") -> str:
    """Il mandato del turno. Due mandati diversi, due prompt diversi.

    'intake' è l'intervista di conoscenza prima di scrivere. 'correzione' è
    tutt'altro: la scheda esiste già, il libro pure, e si sta solo sistemando
    quello che non tornava. Usare il prompt dell'intake per una correzione fa
    ripartire l'intervista da capo sopra la risposta appena data — l'utente vede
    che non lo si è ascoltato.
    """
    nome = "correzione_system.md" if modo == "correzione" else "intervista_system.md"
    return (PROMPTS_DIR / nome).read_text(encoding="utf-8")


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


def _blocco_avvisi(avvisi: list[dict] | None) -> str:
    """I punti aperti sulla scheda, formattati per il turno di correzione."""
    voci = [a for a in (avvisi or []) if isinstance(a, dict)]
    if not voci:
        return ""
    righe = []
    for a in voci:
        campo = (a.get("campo") or "").strip()
        problema = (a.get("problema") or a.get("domanda") or "").strip()
        if problema:
            righe.append(f"- {campo + ': ' if campo else ''}{problema}")
    if not righe:
        return ""
    return "\n\nCOSA NON TORNAVA:\n\n" + "\n".join(righe)


def _costruisci_messaggi(
    brief_dict: dict,
    messaggi: list[dict],
    modo: str = "intake",
    avvisi: list[dict] | None = None,
) -> list[dict]:
    """Trasforma brief + storico in messaggi per l'API.

    Il primo turno è un messaggio user col brief; poi si riporta la
    conversazione (domande dell'AI come 'assistant', risposte come 'user').
    """
    if modo == "correzione":
        apertura = (
            "SCHEDA ATTUALE DEL VIAGGIO:\n\n"
            + stable_json(brief_dict)
            + _blocco_avvisi(avvisi)
            + "\n\nSistema la scheda con quello che i viaggiatori ti scrivono. "
            "Chiudi appena è utilizzabile."
        )
    else:
        apertura = (
            "BRIEF (dal form):\n\n"
            + stable_json(brief_dict)
            + "\n\nConduci l'intervista secondo le istruzioni: se serve, "
            "fai la prossima domanda; se hai un quadro sufficiente, chiudi."
        )
    out = [{"role": "user", "content": apertura}]
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


def _chiama_modello(
    brief_dict: dict,
    messaggi: list[dict],
    modo: str = "intake",
    avvisi: list[dict] | None = None,
) -> dict | None:
    client = make_client()
    response = client.messages.create(
        model=config.MODEL_INTERVISTA,
        max_tokens=config.MAX_TOKENS_INTERVISTA,
        output_config={"effort": config.EFFORT_LEGGERO},
        system=_system_prompt(modo),
        messages=_costruisci_messaggi(brief_dict, messaggi, modo, avvisi),
    )
    raw = "".join(b.text for b in response.content if b.type == "text")
    obj = _estrai_json(raw)
    if isinstance(obj, dict) and obj.get("azione") in ("domanda", "fine"):
        return obj
    return None


def _normalizza(
    obj: dict, brief_dict: dict, completa_con_originale: bool = False
) -> dict:
    """Ripulisce l'oggetto del modello nel contratto {azione, messaggio, opzioni, brief}."""
    messaggio = (obj.get("messaggio") or "").strip()
    if obj["azione"] == "fine":
        brief = obj.get("brief")
        if not isinstance(brief, dict) or not brief:
            brief = brief_dict
        elif completa_con_originale:
            # Rete sulla scheda parziale. Se il modello riemette solo i campi che
            # ha toccato, i mancanti tornerebbero ai valori di default e il
            # viaggio si impoverirebbe SENZA nessun errore: nessuno se ne
            # accorge finché non legge il libro. I campi assenti si riprendono
            # dall'originale.
            mancanti = [k for k in brief_dict if k not in brief]
            if mancanti:
                brief = {**brief_dict, **brief}
                print(
                    "Correzione: la scheda riemessa non aveva "
                    f"{', '.join(mancanti)} — ripresi dall'originale."
                )
        return {"azione": "fine", "messaggio": messaggio, "opzioni": [], "brief": brief}
    opzioni = obj.get("opzioni")
    if isinstance(opzioni, list):
        opzioni = [str(o).strip() for o in opzioni if str(o).strip()][:5]
    else:
        opzioni = []
    return {"azione": "domanda", "messaggio": messaggio, "opzioni": opzioni, "brief": None}


def passo_intervista(
    brief_dict: dict,
    messaggi: list[dict],
    modo: str = "intake",
    avvisi: list[dict] | None = None,
) -> dict:
    """Un turno di conversazione. Ritorna {azione, messaggio, opzioni, brief}.

    Due modi, con regole diverse:

    * 'intake' — l'intervista di conoscenza prima di scrivere. Non chiude prima
      di MIN_RISPOSTE risposte: due domande sono il minimo per tarare un libro.
    * 'correzione' — la scheda esiste già e si sta sistemando quello che non
      tornava. Qui chiudere subito è un pregio: se la risposta bastava, insistere
      con un'altra domanda significa non aver letto quello che hanno scritto.
      Nessun pavimento sul numero di turni.

    Robustezza: se il modello sbaglia il formato, ritenta una volta. Se sbaglia
    ancora, ripiega IN VOCE — mai frasi di sistema, mai il brief perso.
    """
    obj = _chiama_modello(brief_dict, messaggi, modo, avvisi) or _chiama_modello(
        brief_dict, messaggi, modo, avvisi
    )
    n = _conta_risposte(messaggi)

    if modo == "correzione":
        if obj is None:
            # Nel dubbio si tiene la scheda com'è: meglio un libro sulla scheda
            # vecchia che una scheda inventata da un ripiego.
            return {
                "azione": "fine",
                "messaggio": (
                    "Ho segnato quello che ci avete scritto. Se qualcosa non "
                    "dovesse tornare nel libro, ditecelo e lo sistemiamo."
                ),
                "opzioni": [],
                "brief": brief_dict,
            }
        return _normalizza(obj, brief_dict, completa_con_originale=True)

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
