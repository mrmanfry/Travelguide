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

    response = client.messages.create(
        model=config.MODEL_CRITIC,
        max_tokens=config.MAX_TOKENS_CRITIC,
        system=system,
        tools=tools,
        messages=[{"role": "user", "content": capitolo}],
    )

    critica = "".join(block.text for block in response.content if block.type == "text")
    cap_path, _ = chapter_paths(brief, assignment)
    critic_path = cap_path.with_name(f"cap_{assignment.numero:02d}.{suffix}.json")

    risultato = {
        "capitolo": assignment.numero,
        "model": response.model,
        "stop_reason": response.stop_reason,
        "usage": response.usage.model_dump(),
    }
    verdetto = extract_verdict(critica)
    if verdetto is not None:
        risultato["verdetto"] = verdetto
    else:
        risultato["verdetto"] = None
        risultato["critica_raw"] = critica
        print(
            f"ATTENZIONE: risposta del critico non parsabile come JSON, "
            f"salvato il testo grezzo in 'critica_raw' ({critic_path.name})",
            file=sys.stderr,
        )

    critic_path.write_text(
        json.dumps(risultato, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return critic_path, risultato


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
) -> bool:
    """Applica il cancello sul verdetto del critico. Ritorna True se il capitolo è consegnabile.

    Non consegnabile (scrive cap_NN.WARNING.txt, stampa su stderr, il chiamante
    esce non-zero) se restano problemi di generazione, se il verdetto è
    "da_rifare", o se resta almeno un alert bloccante. Se il verdetto è
    "correzioni_minori" senza bloccanti, il capitolo è consegnabile ma si segnala
    a video quanti alert restano aperti.
    """
    cap_path, _ = chapter_paths(brief, assignment)
    fatale = bool(gen_warnings) or bool(bloccanti) or verdetto == "da_rifare"

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

    print(
        f"ATTENZIONE: capitolo {assignment.numero:02d} NON consegnabile "
        f"(verdetto '{verdetto}', {len(bloccanti)} alert bloccanti) — "
        f"vedi {warning_path.name}",
        file=sys.stderr,
    )
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

    cap_path, gen_warnings = generate_chapter(brief, assignment)
    print(f"Capitolo: {cap_path}")

    if not args.critic:
        # Senza critica non c'è cancello: valgono solo i warning di generazione.
        if gen_warnings:
            sys.exit(1)
        return

    # Genera → critica.
    critic_path, risultato = run_critic(
        brief, assignment, cap_path.read_text(encoding="utf-8")
    )
    print(f"Critica: {critic_path}")
    verdetto, bloccanti, alerts = leggi_verdetto(risultato)

    # → se ci sono bloccanti (o verdetto "da_rifare"), applica le correzioni una
    # volta sola → ri-critica → ri-valuta. Massimo un giro, per non entrare in
    # cicli costosi.
    corretto = False
    if bloccanti or verdetto == "da_rifare":
        print(
            f"Verdetto '{verdetto}' con {len(bloccanti)} alert bloccanti: "
            f"avvio il loop di correzione (un giro).",
            file=sys.stderr,
        )
        _, parole = apply_corrections(brief, assignment, cap_path, risultato.get("verdetto"))
        corretto = True
        print(f"Capitolo corretto: {cap_path} ({parole} parole)")

        critic2_path, risultato = run_critic(
            brief,
            assignment,
            cap_path.read_text(encoding="utf-8"),
            suffix="critic2",
        )
        print(f"Ri-critica: {critic2_path}")
        verdetto, bloccanti, alerts = leggi_verdetto(risultato)

    # Misura dei costi: aggrega gli usage di tutte le chiamate (generazione,
    # critico, fixer, secondo critico) e scrive output/{brief_id}/costi.json.
    costi_path, costi = scrivi_costi(brief, assignment)
    tot = costi["totale"].get("costo_usd")
    tot_str = f"${tot:.4f}" if tot is not None else "non disponibile"
    print(f"Costi: {costi_path} (totale capitolo: {tot_str})")

    consegnabile = scrivi_gate(
        brief, assignment, verdetto, bloccanti, alerts, corretto, gen_warnings
    )
    if not consegnabile:
        sys.exit(1)


if __name__ == "__main__":
    main()
