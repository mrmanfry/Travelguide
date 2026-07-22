"""CLI per generare un capitolo (e opzionalmente la critica).

Uso, dalla cartella engine/:

    python -m src.run_chapter fixtures/lisbona.json [--critic]

Il file JSON deve contenere le chiavi "brief" e "assignment".
"""

import argparse
import json
import re
import sys
from pathlib import Path

from schema.brief import Brief, ChapterAssignment
from src import config
from src.chapter_runner import (
    ENGINE_ROOT,
    build_system_blocks,
    chapter_paths,
    generate_chapter,
    make_client,
    run_verification_call,
)
from src.costs import scrivi_costi
from src.fix_chapter import apply_corrections


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


def run_critic(
    brief: Brief,
    assignment: ChapterAssignment,
    capitolo: str,
    suffix: str = "critic",
) -> tuple[Path, dict]:
    """Passata di critica del capitolo, con la stessa struttura system a due blocchi.

    `suffix` distingue il file di output: "critic" per la prima passata,
    "critic2" per la ri-critica dopo la correzione. Ritorna (percorso, risultato):
    il risultato include il verdetto parsato (o None se la risposta non è JSON).
    """
    client = make_client()
    system = build_system_blocks(brief, system_file="critic_system.md")
    tools = [
        {
            "type": config.WEB_SEARCH_TOOL_TYPE,
            "name": "web_search",
            "max_uses": config.MAX_SEARCHES_CRITIC,
        }
    ]

    response, usage_log, info = run_verification_call(
        client,
        system,
        tools,
        capitolo,
        model=config.MODEL_CRITIC,
        max_tokens=config.MAX_TOKENS_CRITIC,
    )

    critica = "".join(block.text for block in response.content if block.type == "text")
    cap_path, _ = chapter_paths(brief, assignment)
    critic_path = cap_path.with_name(f"cap_{assignment.numero:02d}.{suffix}.json")

    risultato = {
        "capitolo": assignment.numero,
        "model": response.model,
        "stop_reason": info["stop_reason"],
        "truncated": info["truncated"],
        "retried": info["retried"],
        "chiamate": usage_log,
    }
    verdetto = extract_verdict(critica)
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

    critic_path.write_text(
        json.dumps(risultato, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return critic_path, risultato


def verifica_fallita(risultato: dict) -> str | None:
    """Motivo per cui una passata di verifica non è affidabile, o None se è ok.

    Un verdetto non parsabile o una chiamata troncata (max_tokens) rendono la
    verifica non completata: il capitolo non è consegnabile.
    """
    if risultato.get("truncated"):
        return "chiamata troncata (max_tokens) anche dopo il ritentativo col tetto raddoppiato"
    if risultato.get("verdetto") is None:
        return "verdetto non parsabile come JSON"
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

    non_bloccanti = [a for a in alerts if a.get("gravita") != "bloccante"]
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

    cap_path, gen_warnings, gen_info = generate_chapter(brief, assignment)
    print(f"Capitolo: {cap_path}")

    if not args.critic:
        # Senza critica non c'è cancello: valgono solo i warning di generazione.
        if gen_warnings:
            sys.exit(1)
        return

    # Problemi di verifica: verdetti persi, chiamate troncate, claim non chiusi.
    # Se non è vuoto, il capitolo non è consegnabile.
    verifica_problemi: list[str] = []

    # Genera → critica.
    critic_path, risultato = run_critic(
        brief, assignment, cap_path.read_text(encoding="utf-8")
    )
    print(f"Critica: {critic_path}")
    verdetto, bloccanti, alerts = leggi_verdetto(risultato)
    motivo = verifica_fallita(risultato)
    if motivo:
        verifica_problemi.append(f"Critico: {motivo}.")

    # → se il critico è affidabile e ci sono bloccanti (o verdetto "da_rifare"),
    # applica le correzioni una volta sola → ri-critica → ri-valuta. Massimo un
    # giro, per non entrare in cicli costosi. Se il verdetto è perso non si
    # corregge: non si sa cosa correggere.
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
        if fix_info.get("truncated"):
            verifica_problemi.append(
                "Fixer: correzione troncata (max_tokens) anche dopo il ritentativo."
            )

        critic2_path, risultato = run_critic(
            brief,
            assignment,
            cap_path.read_text(encoding="utf-8"),
            suffix="critic2",
        )
        print(f"Ri-critica: {critic2_path}")
        verdetto, bloccanti, alerts = leggi_verdetto(risultato)
        motivo2 = verifica_fallita(risultato)
        if motivo2:
            verifica_problemi.append(f"Secondo critico: {motivo2}.")

    # Gate sulle ricerche (condizione corretta): la presenza di claim_da_verificare
    # è normale, NON un difetto. Il capitolo è problematico solo se le ricerche di
    # generazione sono esaurite E il critico segnala claim di tipo 'fatto' rimasti
    # aperti (non è riuscito a verificarli). verifica_incompleta: true è già
    # gestito come warning di generazione.
    fatti_aperti = [a for a in alerts if a.get("tipo") == "fatto"]
    if gen_info.get("ricerche_esaurite") and fatti_aperti:
        verifica_problemi.append(
            f"Ricerche di generazione esaurite "
            f"({gen_info.get('ricerche')}/{config.MAX_SEARCHES_PER_CHAPTER}) e il critico "
            f"segnala {len(fatti_aperti)} claim di tipo 'fatto' non risolti."
        )

    # Misura dei costi: aggrega gli usage di tutte le chiamate (generazione,
    # critico, fixer, secondo critico) e scrive output/{brief_id}/costi.json.
    costi_path, costi = scrivi_costi(brief, assignment)
    tot = costi["totale"].get("costo_usd")
    tot_str = f"${tot:.4f}" if tot is not None else "non disponibile"
    print(f"Costi: {costi_path} (totale capitolo: {tot_str})")

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
    if not consegnabile:
        sys.exit(1)


if __name__ == "__main__":
    main()
