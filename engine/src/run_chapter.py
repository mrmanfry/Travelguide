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


def run_critic(brief: Brief, assignment: ChapterAssignment, capitolo: str) -> Path:
    """Seconda passata: critica del capitolo, con la stessa struttura system a due blocchi."""
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
    critic_path = cap_path.with_name(f"cap_{assignment.numero:02d}.critic.json")

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
    return critic_path


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

    cap_path, warnings = generate_chapter(brief, assignment)
    print(f"Capitolo: {cap_path}")

    if args.critic:
        critic_path = run_critic(brief, assignment, cap_path.read_text(encoding="utf-8"))
        print(f"Critica: {critic_path}")

    if warnings:
        # Il capitolo è stato salvato ma non è valido: il file cap_NN.WARNING.txt
        # accanto spiega il problema. Uscita non-zero per fermare la pipeline.
        sys.exit(1)


if __name__ == "__main__":
    main()
