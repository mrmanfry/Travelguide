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


_ANCORA = """LA SCHEDA QUI SOPRA È L'UNICA VERITÀ SU QUESTO VIAGGIO.

Se nello storico della conversazione compaiono destinazioni, luoghi o dettagli
che non c'entrano niente con la scheda, sono il residuo di un'altra intervista
rimasta aperta: ignorali, non nominarli, non farci domande sopra. Parla solo del
viaggio che sta nella scheda.

Questo non vale per quello che i viaggiatori ti scrivono adesso: se sono loro a
correggere la scheda, la correzione è buona e la tieni."""


def _blocco_scheda(
    brief_dict: dict, modo: str = "intake", avvisi: list[dict] | None = None
) -> str:
    """La scheda come blocco di sistema, non come primo turno di conversazione.

    Stava nel primo messaggio user, cioè in fondo alla pila: con una
    conversazione sopra, il modello seguiva la conversazione. È esattamente come
    un'intervista sull'Umbria finiva a parlare di Tokyo — bastava che il sito si
    dimenticasse di svuotare i messaggi. Nel sistema la scheda non viene
    sopravanzata da niente.
    """
    intestazione = (
        "SCHEDA ATTUALE DEL VIAGGIO" if modo == "correzione" else "SCHEDA DEL VIAGGIO (dal form)"
    )
    return (
        f"{intestazione}:\n\n"
        + stable_json(brief_dict)
        + _blocco_avvisi(avvisi)
        + "\n\n"
        + _ANCORA
    )


def _costruisci_messaggi(messaggi: list[dict], modo: str = "intake") -> list[dict]:
    """Lo storico della conversazione, e nient'altro.

    Le domande dell'AI come 'assistant', le risposte come 'user'. La scheda non
    passa più di qui: sta nel blocco di sistema.
    """
    apertura = (
        "Sistema la scheda con quello che i viaggiatori ti scrivono. "
        "Chiudi appena è utilizzabile."
        if modo == "correzione"
        else "Conduci l'intervista secondo le istruzioni: se serve, fai la prossima "
        "domanda; se hai un quadro sufficiente, chiudi."
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


def impronta_viaggio(brief_dict: dict) -> str:
    """Le otto cifre che dicono DI QUALE viaggio si sta parlando.

    Calcolata sulle sole destinazioni, normalizzate e ordinate: cambiare le
    notti, aggiungere una passione o correggere l'hotel NON cambia l'impronta,
    e la conversazione in corso sopravvive. Cambiare le destinazioni sì — e a
    quel punto è un altro viaggio, su cui le domande già fatte non valgono più.

    Serve al sito per dimostrare che la conversazione che ci manda è nata su
    questa scheda e non su quella di prima.
    """
    import hashlib

    tappe = brief_dict.get("tappe") or []
    luoghi = sorted(
        {
            re.sub(r"\s+", " ", str(t.get("luogo") or "")).strip().lower()
            for t in tappe
            if isinstance(t, dict) and str(t.get("luogo") or "").strip()
        }
    )
    return hashlib.sha256("|".join(luoghi).encode("utf-8")).hexdigest()[:8]


# Quante risposte servono prima di poter chiudere. NON è una costante: una
# quota fissa garantisce che, quando non c'è più niente da imparare, si chieda
# lo stesso — ed è esattamente la macchina che produce le domande a cui si
# risponde solo perché sono state fatte. Il pavimento dipende da quanto il form
# ha già dato.
PAVIMENTO_SCHEDA_RICCA = 1
PAVIMENTO_SCHEDA_SCARNA = 2
# Rete di sicurezza, non un progetto: se il modello continuasse a fare domande
# oltre questo punto lo si chiude comunque. Deve essere abbastanza alto da non
# entrare mai in gioco in un'intervista normale.
MAX_RISPOSTE = 5


def _pavimento(brief_dict: dict) -> int:
    """Il numero minimo di risposte, in funzione di cosa sappiamo già.

    Si contano i campi in cui il viaggiatore ha scritto qualcosa DI SUA
    INIZIATIVA nel form: sono l'unico materiale ad alta confidenza che abbiamo,
    perché nessuno glielo ha suggerito. Se ce n'è abbastanza, una domanda basta
    e si chiude; se la scheda è scarna, se ne fanno due.
    """
    oro = brief_dict.get("oro") or {}
    ricchezza = 0
    passioni = brief_dict.get("passioni") or []
    if any(
        isinstance(p, dict) and str(p.get("dettaglio") or "").strip() for p in passioni
    ):
        ricchezza += 1
    for campo in ("da_non_perdere", "da_evitare"):
        valore = oro.get(campo)
        if isinstance(valore, list) and any(str(x).strip() for x in valore):
            ricchezza += 1
    for campo in ("contesto_emotivo", "note_libere"):
        if str(oro.get(campo) or "").strip():
            ricchezza += 1
    if str(brief_dict.get("note_mezzo") or "").strip():
        ricchezza += 1
    return PAVIMENTO_SCHEDA_RICCA if ricchezza >= 3 else PAVIMENTO_SCHEDA_SCARNA


def _avvisi_dalla_scheda(brief_dict: dict) -> list[dict]:
    """Le incoerenze della scheda, cercate ADESSO e non dopo.

    Finora un campo rimasto pieno dal viaggio precedente si scopriva a libro
    cominciato, e la domanda arrivava quando il viaggiatore aveva già aspettato.
    Qui il controllo gira al primo turno dell'intervista — quando è già davanti
    allo schermo e sta parlando con noi — e i punti aperti diventano la prima
    domanda invece di un avviso su una pagina.

    Costa millesimi (Haiku, nessuna ricerca) e non solleva mai: se il controllo
    fallisce, l'intervista prosegue come se la scheda fosse a posto.
    """
    try:
        from schema.brief import Brief
        from src.coerenza import verifica_coerenza

        scheda = dict(brief_dict)
        scheda.setdefault("brief_id", "intervista")
        return verifica_coerenza(Brief.model_validate(scheda)).get("avvisi") or []
    except Exception as exc:
        print(f"Intervista: controllo di coerenza non eseguito ({exc}).")
        return []

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
        system=[
            {"type": "text", "text": _system_prompt(modo)},
            {"type": "text", "text": _blocco_scheda(brief_dict, modo, avvisi)},
        ],
        messages=_costruisci_messaggi(messaggi, modo),
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
    impronta: str | None = None,
) -> dict:
    """Un turno di conversazione. Ritorna {azione, messaggio, opzioni, brief, impronta}.

    Due modi, con regole diverse:

    * 'intake' — l'intervista di conoscenza prima di scrivere. Non chiude prima
      di MIN_RISPOSTE risposte: due domande sono il minimo per tarare un libro.
    * 'correzione' — la scheda esiste già e si sta sistemando quello che non
      tornava. Qui chiudere subito è un pregio: se la risposta bastava, insistere
      con un'altra domanda significa non aver letto quello che hanno scritto.
      Nessun pavimento sul numero di turni.

    `impronta` è quella restituita al turno precedente. Se non corrisponde al
    viaggio che c'è nella scheda adesso, la conversazione appartiene a un altro
    viaggio e viene buttata: è la garanzia deterministica contro il caso —
    successo davvero — di una scheda dell'Umbria con sopra l'intervista del
    Giappone. Chi non manda l'impronta si comporta esattamente come prima.

    Robustezza: se il modello sbaglia il formato, ritenta una volta. Se sbaglia
    ancora, ripiega IN VOCE — mai frasi di sistema, mai il brief perso.
    """
    adesso = impronta_viaggio(brief_dict)
    if impronta and impronta != adesso and messaggi:
        print(
            f"Intervista: la conversazione ricevuta è nata su un altro viaggio "
            f"(impronta {impronta}, la scheda dice {adesso}): "
            f"{len(messaggi)} messaggi scartati, si riparte pulito."
        )
        messaggi = []

    # Al primo turno dell'intake si guarda se la scheda si contraddice, così i
    # punti aperti diventano la prima domanda. Se il sito ce li manda già (ha
    # chiamato /coerenza per conto suo) non si rifà il lavoro.
    if modo == "intake" and not messaggi and avvisi is None:
        avvisi = _avvisi_dalla_scheda(brief_dict)
        if avvisi:
            print(f"Intervista: {len(avvisi)} punto/i da chiarire nella scheda.")

    esito = _turno(brief_dict, messaggi, modo, avvisi)
    return {**esito, "impronta": adesso}


def _turno(
    brief_dict: dict,
    messaggi: list[dict],
    modo: str,
    avvisi: list[dict] | None,
) -> dict:
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

    pavimento = _pavimento(brief_dict)

    if obj is None:
        # Due tentativi falliti: ripiego in voce, calibrato su quanto siamo avanti.
        if n < pavimento:
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
    if obj["azione"] == "fine" and n < pavimento:
        return dict(_RIPIEGO_DOMANDA)

    # Rete di sicurezza: se continua a fare domande oltre il tetto, si chiude.
    # Non dovrebbe succedere mai — se succede, è un difetto del prompt, non una
    # cosa da far pagare al viaggiatore in domande.
    if obj["azione"] == "domanda" and n >= MAX_RISPOSTE:
        print(f"Intervista: {n} risposte e ancora domande — chiusa d'ufficio.")
        return {
            "azione": "fine",
            "messaggio": (
                "Ho abbastanza per iniziare: comincio a scrivervi qualcosa che vi somigli."
            ),
            "opzioni": [],
            "brief": brief_dict,
        }

    return _normalizza(obj, brief_dict)
