"""CLI per generare un capitolo (e opzionalmente la critica).

Uso, dalla cartella engine/:

    python -m src.run_chapter fixtures/lisbona.json [--critic]

Il file JSON deve contenere le chiavi "brief" e "assignment".
"""

import argparse
import json
import re
import sys
import time
from pathlib import Path

from schema.brief import Brief, ChapterAssignment
from src import config
from src.chapter_runner import (
    ENGINE_ROOT,
    build_system_blocks,
    chapter_paths,
    generate_chapter,
    make_client,
    parse_meta,
    run_verification_call,
)
from src.costs import costruisci_costi, scrivi_costi
from src.fix_chapter import apply_corrections, format_alerts

# Pausa (secondi) prima dell'unico ritentativo del critico quando la ricerca web
# risulta indisponibile: dà tempo al limite dello strumento di rientrare.
PAUSA_RITENTATIVO_RICERCA_S = 60

_MARKER_RICERCA_KO = (
    "ricerca non disponibile",
    "ricerca web indisponibile",
    "strumento di ricerca",
    "limite superato",
    "non è stato disponibile",
    "non è stata disponibile",
    "web_search",
)


FENCED_JSON_RE = re.compile(r"```(?:json)?\s*(\{.*?\})\s*```", re.DOTALL)


def extract_verdict(critica: str) -> dict | None:
    """Estrae l'oggetto JSON del verdetto dalla risposta del critico.

    Il prompt chiede solo l'oggetto JSON, ma il modello può comunque
    aggiungere preamboli, fence markdown o riepiloghi: si prova nell'ordine
    il testo intero, i blocchi ```json``` e la fetta tra la prima "{" e
    l'ultima "}".
    """
    candidati = [critica.strip()]
    candidati.extend(m.group(1) for m in FENCED_JSON_RE.finditer(critica))
    inizio, fine = critica.find("{"), critica.rfind("}")
    if inizio != -1 and fine > inizio:
        candidati.append(critica[inizio : fine + 1])

    for candidato in candidati:
        try:
            obj = json.loads(candidato)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, dict):
            return obj
    return None


def build_second_pass_user(
    capitolo: str, correzioni_applicate: list[str], alert_primo_giro: list[dict]
) -> str:
    """Messaggio user della seconda passata: capitolo corretto + correzioni + alert del 1° giro."""
    parti = [
        "CAPITOLO CORRETTO (versione finale, dopo il giro di correzione):",
        "",
        capitolo,
        "",
        "---",
        "",
        "CORREZIONI APPLICATE DAL FIXER (campo 'correzioni_applicate' del META):",
    ]
    if correzioni_applicate:
        parti.extend(f"- {c}" for c in correzioni_applicate)
    else:
        parti.append("(nessuna correzione dichiarata nel META)")
    parti.extend(
        [
            "",
            "---",
            "",
            "ALERT DEL PRIMO GIRO DI CRITICA (verifica solo che siano stati risolti):",
            format_alerts(alert_primo_giro) or "(nessun alert nel primo giro)",
        ]
    )
    return "\n".join(parti)


def run_critic(
    brief: Brief,
    assignment: ChapterAssignment,
    capitolo: str,
    suffix: str = "critic",
    seconda_passata: bool = False,
    correzioni_applicate: list[str] | None = None,
    alert_primo_giro: list[dict] | None = None,
) -> tuple[Path, dict]:
    """Passata di critica del capitolo, con la stessa struttura system a due blocchi.

    `suffix` distingue il file di output: "critic" per la prima passata,
    "critic2" per la ri-critica dopo la correzione. Con `seconda_passata=True` il
    critico gira a mandato ristretto (prompt critic2_system.md, budget di ricerche
    MAX_SEARCHES_CRITIC_2): verifica solo le correzioni del fixer, i punti già
    segnalati e la coerenza interna, e riceve nel messaggio user le
    `correzioni_applicate` e gli `alert_primo_giro`. Ritorna (percorso, risultato):
    il risultato include il verdetto parsato (o None se la risposta non è JSON).
    """
    client = make_client()
    if seconda_passata:
        system = build_system_blocks(brief, system_file="critic2_system.md")
        max_searches = config.MAX_SEARCHES_CRITIC_2
        user_content = build_second_pass_user(
            capitolo, correzioni_applicate or [], alert_primo_giro or []
        )
    else:
        system = build_system_blocks(brief, system_file="critic_system.md")
        max_searches = config.MAX_SEARCHES_CRITIC
        user_content = capitolo

    tools = [
        {
            "type": config.WEB_SEARCH_TOOL_TYPE,
            "name": "web_search",
            "max_uses": max_searches,
        }
    ]

    # Claim fattuali del capitolo (dal META): servono a capire se un critico che
    # non conferma nulla è un problema (c'erano fatti da verificare) o no.
    meta_cap = parse_meta(capitolo) or {}
    ci_sono_claim = bool(meta_cap.get("claims_da_verificare"))

    cap_path, _ = chapter_paths(brief, assignment)
    critic_path = cap_path.with_name(f"cap_{assignment.numero:02d}.{suffix}.json")

    usage_totale: list[dict] = []
    ritentato_ricerca = False
    verdetto = None
    critica = ""
    info = {"truncated": False, "retried": False, "stop_reason": None}

    # Al più due tentativi: se la ricerca web è indisponibile, una pausa di 60s
    # e un solo ritentativo prima di arrendersi.
    for tentativo in (1, 2):
        response, usage_log, info = run_verification_call(
            client,
            system,
            tools,
            user_content,
            model=config.MODEL_CRITIC,
            max_tokens=config.MAX_TOKENS_CRITIC,
        )
        usage_totale.extend(usage_log)
        critica = "".join(b.text for b in response.content if b.type == "text")
        verdetto = extract_verdict(critica)
        motivo_ricerca = motivo_verifica_ricerca(verdetto, ci_sono_claim)
        if tentativo == 1 and motivo_ricerca and not info["truncated"]:
            ritentato_ricerca = True
            print(
                f"ATTENZIONE: critico ({suffix}): {motivo_ricerca}. Pausa "
                f"{PAUSA_RITENTATIVO_RICERCA_S}s e un solo ritentativo…",
                file=sys.stderr,
            )
            time.sleep(PAUSA_RITENTATIVO_RICERCA_S)
            continue
        break

    motivo_ricerca_finale = motivo_verifica_ricerca(verdetto, ci_sono_claim)

    risultato = {
        "capitolo": assignment.numero,
        "model": response.model,
        "stop_reason": info["stop_reason"],
        "truncated": info["truncated"],
        "retried": bool(info["retried"]) or ritentato_ricerca,
        "ricerca_ritentata": ritentato_ricerca,
        "motivo_ricerca": motivo_ricerca_finale,
        "chiamate": usage_totale,
    }
    risultato["verdetto"] = verdetto
    if verdetto is None:
        risultato["critica_raw"] = critica
        print(
            f"ATTENZIONE: risposta del critico non parsabile come JSON, "
            f"salvato il testo grezzo in 'critica_raw' ({critic_path.name})",
            file=sys.stderr,
        )
    if info["truncated"]:
        print(
            f"ATTENZIONE: critico ({suffix}) troncato (max_tokens) anche dopo il "
            f"ritentativo col tetto raddoppiato: verdetto non affidabile.",
            file=sys.stderr,
        )
    if motivo_ricerca_finale:
        print(
            f"ATTENZIONE: critico ({suffix}): {motivo_ricerca_finale} "
            "(anche dopo il ritentativo).",
            file=sys.stderr,
        )

    critic_path.write_text(
        json.dumps(risultato, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return critic_path, risultato


def _ricerca_indisponibile(verdict: dict) -> bool:
    """True se il verdetto indica che la ricerca web non ha funzionato.

    Segnale primario deterministico: il campo `ricerca_disponibile: false`
    (emesso dal critico). Fallback euristico: un alert la cui prosa nomina
    esplicitamente il fallimento dello strumento di ricerca.
    """
    if verdict.get("ricerca_disponibile") is False:
        return True
    for a in verdict.get("alerts") or []:
        if not isinstance(a, dict):
            continue
        testo = f"{a.get('problema', '')} {a.get('evidenza', '')}".lower()
        if any(k in testo for k in _MARKER_RICERCA_KO):
            return True
    return False


def motivo_verifica_ricerca(verdict: dict | None, ci_sono_claim: bool) -> str | None:
    """Motivo per cui la verifica fattuale non è avvenuta, o None se è a posto.

    Due casi: la ricerca è dichiarata indisponibile; oppure il critico non ha
    confermato NESSUN fatto pur essendoci claim fattuali da verificare (segno che
    la verifica non è avvenuta davvero).
    """
    if not isinstance(verdict, dict):
        return None
    if _ricerca_indisponibile(verdict):
        return "lo strumento di ricerca web è risultato indisponibile: verifica fattuale non eseguita"
    fatti = verdict.get("fatti_confermati") or []
    if ci_sono_claim and not fatti:
        return (
            "il critico non ha confermato alcun fatto pur essendoci claim fattuali "
            "da verificare: verifica fattuale non avvenuta"
        )
    return None


def verifica_fallita(risultato: dict) -> str | None:
    """Motivo per cui una passata di verifica non è affidabile, o None se è ok.

    Rendono la verifica non completata (capitolo non consegnabile): un verdetto
    non parsabile, una chiamata troncata (max_tokens), o una verifica fattuale
    non avvenuta (ricerca indisponibile / nessun fatto confermato con claim
    aperti) — rilevata in run_critic e riportata in `motivo_ricerca`.
    """
    if risultato.get("truncated"):
        return "chiamata troncata (max_tokens) anche dopo il ritentativo col tetto raddoppiato"
    if risultato.get("verdetto") is None:
        return "verdetto non parsabile come JSON"
    if risultato.get("motivo_ricerca"):
        return risultato["motivo_ricerca"]
    return None


def leggi_verdetto(risultato: dict) -> tuple[str | None, list[dict], list[dict]]:
    """Estrae dal risultato del critico: (verdetto, alert_bloccanti, tutti_gli_alert)."""
    verdict_obj = risultato.get("verdetto")
    if not isinstance(verdict_obj, dict):
        return None, [], []
    verdetto = verdict_obj.get("verdetto")
    alerts = [a for a in (verdict_obj.get("alerts") or []) if isinstance(a, dict)]
    bloccanti = [a for a in alerts if a.get("gravita") == "bloccante"]
    return verdetto, bloccanti, alerts


def _sintesi_alert(a: dict) -> str:
    """Riga sintetica di un alert per il file WARNING."""
    posizione = a.get("posizione", "")
    problema = a.get("problema", "")
    return f"[{a.get('tipo', '?')}] {posizione}: {problema}".strip()


def scrivi_gate(
    brief: Brief,
    assignment: ChapterAssignment,
    verdetto: str | None,
    bloccanti: list[dict],
    alerts: list[dict],
    corretto: bool,
    gen_warnings: list[str],
    verifica_problemi: list[str],
) -> bool:
    """Applica il cancello sul verdetto del critico. Ritorna True se il capitolo è consegnabile.

    Non consegnabile (scrive cap_NN.WARNING.txt, stampa su stderr, il chiamante
    esce non-zero) se: la verifica non è stata completata (`verifica_problemi`,
    es. verdetto perso o chiamata troncata), restano problemi di generazione, il
    verdetto è "da_rifare", o resta almeno un alert bloccante. Se il verdetto è
    "correzioni_minori" senza bloccanti, il capitolo è consegnabile ma si segnala
    a video quanti alert restano aperti.
    """
    cap_path, _ = chapter_paths(brief, assignment)
    fatale = (
        bool(verifica_problemi)
        or bool(gen_warnings)
        or bool(bloccanti)
        or verdetto == "da_rifare"
    )

    if not fatale:
        aperti = len(alerts)
        if verdetto == "correzioni_minori" and aperti:
            print(
                f"Capitolo {assignment.numero:02d} consegnabile (verdetto "
                f"'correzioni_minori'): restano {aperti} alert non bloccanti aperti.",
                file=sys.stderr,
            )
        else:
            stato = verdetto if verdetto else "verdetto non disponibile"
            print(f"Capitolo {assignment.numero:02d} consegnabile (verdetto '{stato}').")
        return True

    # Non consegnabile: compongo il WARNING.
    if corretto:
        riga_loop = (
            "Il capitolo È STATO sottoposto al loop di correzione (un giro), ma "
            "restano problemi bloccanti dopo la ri-critica."
        )
    else:
        riga_loop = "Il capitolo NON è stato sottoposto al loop di correzione."

    sezioni = [
        "CAPITOLO NON CONSEGNABILE — il cancello di verifica ha bloccato la consegna.",
        "",
        riga_loop,
        "",
        f"Verdetto del critico: {verdetto if verdetto else 'non disponibile'}.",
    ]

    if verifica_problemi:
        sezioni.append("")
        sezioni.append(
            "LA VERIFICA NON È STATA COMPLETATA — il capitolo non può dirsi verificato:"
        )
        sezioni.extend(f"  - {p}" for p in verifica_problemi)

    if gen_warnings:
        sezioni.append("")
        sezioni.append("Problemi di generazione ancora aperti:")
        sezioni.extend(f"  - {w}" for w in gen_warnings)

    if bloccanti:
        sezioni.append("")
        sezioni.append(f"Alert bloccanti ancora aperti ({len(bloccanti)}):")
        sezioni.extend(f"  - {_sintesi_alert(a)}" for a in bloccanti)

    # "Non bloccanti" = tutti gli alert che non sono nella lista dei bloccanti
    # effettivi. Include gli alert declassati (es. i 'non_verificabile' della
    # seconda passata, che restano aperti ma non bloccano la consegna).
    bloccanti_ids = {id(a) for a in bloccanti}
    non_bloccanti = [a for a in alerts if id(a) not in bloccanti_ids]
    if non_bloccanti:
        sezioni.append("")
        sezioni.append(f"Altri alert aperti, non bloccanti ({len(non_bloccanti)}):")
        sezioni.extend(f"  - {_sintesi_alert(a)}" for a in non_bloccanti)

    warning_path = cap_path.with_name(f"cap_{assignment.numero:02d}.WARNING.txt")
    warning_path.write_text("\n".join(sezioni) + "\n", encoding="utf-8")

    motivo_sintetico = (
        "verifica non completata"
        if verifica_problemi
        else f"verdetto '{verdetto}', {len(bloccanti)} alert bloccanti"
    )
    print(
        f"ATTENZIONE: capitolo {assignment.numero:02d} NON consegnabile "
        f"({motivo_sintetico}) — vedi {warning_path.name}",
        file=sys.stderr,
    )
    for p in verifica_problemi:
        print(f"  - {p}", file=sys.stderr)
    for a in bloccanti:
        print(f"  - {_sintesi_alert(a)}", file=sys.stderr)
    return False


def esegui_capitolo(brief: Brief, assignment: ChapterAssignment) -> dict:
    """Pipeline completa di un capitolo: genera → critica → eventuale fixer →
    seconda critica (mandato ristretto) → gate. Ritorna un dict con l'esito,
    SENZA uscire dal processo — così è riusabile sia dalla CLI del singolo
    capitolo sia dall'orchestratore dell'intera guida.

    Chiavi del dict: consegnabile (bool), numero, cap_path, verdetto, riassunto
    (dal META finale), corretto (bool), costo (dict di costruisci_costi),
    costo_usd (float|None), problemi (list[str]).
    """
    cap_path, gen_warnings, gen_info = generate_chapter(brief, assignment)
    print(f"Capitolo: {cap_path}")

    # Il critico non deve MAI vedere un capitolo strutturalmente rotto (niente
    # titolo, niente META, fuori banda, box sbagliato): se la generazione non ha
    # prodotto un capitolo valido dopo i tentativi, si fallisce subito, senza
    # chiamare (e pagare) il critico su un file degenere.
    if not gen_info.get("valido", True):
        costo = costruisci_costi(brief, assignment)
        problemi = list(gen_warnings) or [
            "capitolo strutturalmente non valido dopo i tentativi di generazione"
        ]
        print(
            f"ATTENZIONE: capitolo {assignment.numero:02d} strutturalmente non valido "
            "— critico NON chiamato, capitolo non consegnabile.",
            file=sys.stderr,
        )
        meta_finale = parse_meta(cap_path.read_text(encoding="utf-8")) or {}
        return {
            "numero": assignment.numero,
            "consegnabile": False,
            "cap_path": str(cap_path),
            "verdetto": None,
            "riassunto": meta_finale.get("riassunto"),
            "corretto": False,
            "costo": costo,
            "costo_usd": costo["totale"].get("costo_usd"),
            "problemi": problemi,
        }

    verifica_problemi: list[str] = []

    # Genera → critica (prima passata, mandato pieno).
    critic_path, risultato = run_critic(
        brief, assignment, cap_path.read_text(encoding="utf-8")
    )
    print(f"Critica: {critic_path}")
    verdetto, bloccanti, alerts = leggi_verdetto(risultato)
    alert_primo_giro = alerts  # da passare alla seconda passata
    motivo = verifica_fallita(risultato)
    if motivo:
        verifica_problemi.append(f"Critico: {motivo}.")

    # → se il critico è affidabile e ci sono bloccanti (o verdetto "da_rifare"),
    # applica le correzioni una volta sola → ri-critica (mandato ristretto) →
    # ri-valuta. Massimo un giro. Se il verdetto è perso non si corregge: non si
    # sa cosa correggere.
    corretto = False
    if not motivo and (bloccanti or verdetto == "da_rifare"):
        print(
            f"Verdetto '{verdetto}' con {len(bloccanti)} alert bloccanti: "
            f"avvio il loop di correzione (un giro).",
            file=sys.stderr,
        )
        _, parole, fix_info = apply_corrections(
            brief, assignment, cap_path, risultato.get("verdetto")
        )
        corretto = True
        print(f"Capitolo corretto: {cap_path} ({parole} parole)")

        if not fix_info.get("promoted"):
            # Il fixer non ha prodotto una correzione valida: l'originale è stato
            # mantenuto (mai sovrascritto). Niente seconda critica — non c'è una
            # correzione valida da riverificare — e i problemi di validazione
            # rendono il capitolo non consegnabile (→ stato fallito a monte).
            for p in fix_info.get("fix_problemi", []):
                verifica_problemi.append(f"Fixer: {p}.")
            print(
                f"Fixer: nessuna correzione valida promossa per il capitolo "
                f"{assignment.numero:02d} — originale mantenuto, seconda critica saltata.",
                file=sys.stderr,
            )
        else:
            if fix_info.get("truncated"):
                verifica_problemi.append(
                    "Fixer: correzione troncata (max_tokens) anche dopo il ritentativo."
                )

            capitolo_corretto = cap_path.read_text(encoding="utf-8")
            meta_corretto = parse_meta(capitolo_corretto) or {}
            correzioni_applicate = meta_corretto.get("correzioni_applicate") or []
            critic2_path, risultato = run_critic(
                brief,
                assignment,
                capitolo_corretto,
                suffix="critic2",
                seconda_passata=True,
                correzioni_applicate=correzioni_applicate,
                alert_primo_giro=alert_primo_giro,
            )
            print(f"Ri-critica (mandato ristretto): {critic2_path}")
            verdetto, bloccanti, alerts = leggi_verdetto(risultato)
            motivo2 = verifica_fallita(risultato)
            if motivo2:
                verifica_problemi.append(f"Secondo critico: {motivo2}.")

            # I bloccanti 'non_verificabile' della seconda passata NON bloccano la
            # consegna: sono lacune di verifica su punti che il primo critico aveva
            # già in carico, non errori accertati.
            bloccanti_non_verif = [a for a in bloccanti if a.get("non_verificabile")]
            if bloccanti_non_verif:
                bloccanti = [a for a in bloccanti if not a.get("non_verificabile")]
                print(
                    f"Seconda passata: {len(bloccanti_non_verif)} alert bloccanti "
                    f"'non verificabili' declassati (non bloccano la consegna).",
                    file=sys.stderr,
                )

    # Gate sulle ricerche di generazione: problema solo se le ricerche sono
    # esaurite E restano claim di tipo 'fatto' non risolti (la presenza di
    # claims_da_verificare è normale, non un difetto).
    fatti_aperti = [
        a for a in alerts if a.get("tipo") == "fatto" and not a.get("non_verificabile")
    ]
    if gen_info.get("ricerche_esaurite") and fatti_aperti:
        tetto_gen = gen_info.get("tetto_ricerche") or config.MAX_SEARCHES_PER_CHAPTER
        verifica_problemi.append(
            f"Ricerche di generazione esaurite "
            f"({gen_info.get('ricerche')}/{tetto_gen}) e il critico "
            f"segnala {len(fatti_aperti)} claim di tipo 'fatto' non risolti."
        )

    costo = costruisci_costi(brief, assignment)

    consegnabile = scrivi_gate(
        brief,
        assignment,
        verdetto,
        bloccanti,
        alerts,
        corretto,
        gen_warnings,
        verifica_problemi,
    )

    meta_finale = parse_meta(cap_path.read_text(encoding="utf-8")) or {}
    problemi = (
        list(verifica_problemi)
        + list(gen_warnings)
        + [_sintesi_alert(a) for a in bloccanti]
    )
    return {
        "numero": assignment.numero,
        "consegnabile": consegnabile,
        "cap_path": str(cap_path),
        "verdetto": verdetto,
        "riassunto": meta_finale.get("riassunto"),
        "corretto": corretto,
        "costo": costo,
        "costo_usd": costo["totale"].get("costo_usd"),
        "problemi": problemi,
    }


def main() -> None:
    parser = argparse.ArgumentParser(description="Genera un capitolo della guida di viaggio.")
    parser.add_argument("input", help="File JSON con le chiavi 'brief' e 'assignment'")
    parser.add_argument("--critic", action="store_true", help="Esegue anche la passata di critica")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute() and not input_path.exists():
        input_path = ENGINE_ROOT / args.input

    payload = json.loads(input_path.read_text(encoding="utf-8"))
    brief = Brief.model_validate(payload["brief"])
    assignment = ChapterAssignment.model_validate(payload["assignment"])

    if not args.critic:
        # Senza critica non c'è cancello: valgono solo i warning di generazione.
        cap_path, gen_warnings, _ = generate_chapter(brief, assignment)
        print(f"Capitolo: {cap_path}")
        if gen_warnings:
            sys.exit(1)
        return

    res = esegui_capitolo(brief, assignment)
    costi_path, costi = scrivi_costi(brief, assignment)
    tot = costi["totale"].get("costo_usd")
    tot_str = f"${tot:.4f}" if tot is not None else "non disponibile"
    print(f"Costi: {costi_path} (totale capitolo: {tot_str})")
    if not res["consegnabile"]:
        sys.exit(1)


if __name__ == "__main__":
    main()
