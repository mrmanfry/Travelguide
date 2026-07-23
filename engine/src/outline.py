"""Generatore dell'outline della guida.

Da un Brief produce l'agenda della guida: una lista di ChapterAssignment
completi (titolo provvisorio, tipo, tappa associata, budget parole, confine
tematico). L'outline è un contratto generato una volta sola, salvato in
output/{brief_id}/outline.json e congelato: i capitoli non lo rinegoziano.
"""

import json
import re
from pathlib import Path

from schema.brief import Brief, ChapterAssignment
from src import config
from src.chapter_runner import (
    PROMPTS_DIR,
    build_calendar_block,
    build_mezzo_block,
    make_client,
    stable_json,
)
from src.costs import costo_chiamata

ENGINE_ROOT = Path(__file__).resolve().parents[1]

_ARRAY_RE = re.compile(r"```(?:json)?\s*(\[.*?\])\s*```", re.DOTALL)


def outline_path(brief: Brief) -> Path:
    return ENGINE_ROOT / "output" / brief.brief_id / "outline.json"


def _build_outline_system(brief: Brief) -> list[dict]:
    """System a due blocchi per l'outline: prompt architetto + brief/calendario.

    Non include la style guide (non serve alla struttura): l'outline è una sola
    chiamata piccola, la teniamo leggera.
    """
    prompt = (PROMPTS_DIR / "outline_system.md").read_text(encoding="utf-8")
    prompt = prompt.replace("{{LINGUA}}", brief.lingua_guida)
    return [
        {"type": "text", "text": prompt, "cache_control": {"type": "ephemeral"}},
        {
            "type": "text",
            "text": (
                "# BRIEF DEL VIAGGIO\n\n"
                + stable_json(brief.model_dump(mode="json"))
                + "\n\n"
                + build_calendar_block(brief)
                + "\n\n"
                + build_mezzo_block(brief)
            ),
            "cache_control": {"type": "ephemeral"},
        },
    ]


def _extract_array(testo: str) -> list | None:
    """Estrae l'array JSON dei capitoli dalla risposta del modello."""
    candidati = [testo.strip()]
    candidati.extend(m.group(1) for m in _ARRAY_RE.finditer(testo))
    inizio, fine = testo.find("["), testo.rfind("]")
    if inizio != -1 and fine > inizio:
        candidati.append(testo[inizio : fine + 1])
    for c in candidati:
        try:
            obj = json.loads(c)
        except json.JSONDecodeError:
            continue
        if isinstance(obj, list):
            return obj
    return None


def _assignment_da_voce(brief: Brief, numero: int, voce: dict) -> ChapterAssignment:
    """Costruisce un ChapterAssignment da una voce dell'outline del modello.

    La tappa NON viene copiata dal modello ma ripresa dal brief tramite
    `tappa_ordine`, così hotel/notti/logistica restano esatti.
    """
    tappa = None
    ordine = voce.get("tappa_ordine")
    if isinstance(ordine, int):
        tappa = next((t for t in brief.tappe if t.ordine == ordine), None)
    return ChapterAssignment(
        numero=numero,
        titolo_provvisorio=voce.get("titolo_provvisorio", f"Capitolo {numero}"),
        tappa=tappa,
        tipo=voce.get("tipo", "tappa"),
        budget_parole=int(voce.get("budget_parole", 2500)),
        confine_tematico=voce.get("confine_tematico"),
    )


def genera_outline(brief: Brief) -> tuple[list[ChapterAssignment], float | None]:
    """Genera l'outline via una singola chiamata (senza ricerca).

    Ritorna (lista di ChapterAssignment, costo_usd della chiamata). Ogni
    assignment porta già `outline_guida` (la lista completa dei titoli);
    `riassunti_precedenti` resta vuoto (lo riempie l'orchestratore a runtime).
    """
    client = make_client()
    system = _build_outline_system(brief)
    response = client.messages.create(
        model=config.MODEL_OUTLINE,
        max_tokens=config.MAX_TOKENS_OUTLINE,
        system=system,
        messages=[
            {
                "role": "user",
                "content": (
                    "Produci l'outline della guida per questo brief, "
                    "seguendo le regole della struttura. Solo l'array JSON."
                ),
            }
        ],
    )
    testo = "".join(b.text for b in response.content if b.type == "text")
    voci = _extract_array(testo)
    if not voci:
        raise RuntimeError(
            "Outline non parsabile come array JSON. Risposta grezza:\n" + testo[:2000]
        )

    assignments = [
        _assignment_da_voce(brief, numero, voce)
        for numero, voce in enumerate(voci, start=1)
        if isinstance(voce, dict)
    ]
    # Ogni capitolo riceve l'outline completo (lista dei titoli numerati).
    titoli = [f"{a.numero}. {a.titolo_provvisorio}" for a in assignments]
    for a in assignments:
        a.outline_guida = titoli

    costo = costo_chiamata(config.MODEL_OUTLINE, response.usage.model_dump()).get("costo_usd")
    return assignments, costo


def salva_outline(brief: Brief, assignments: list[ChapterAssignment]) -> Path:
    """Congela l'outline in output/{brief_id}/outline.json."""
    path = outline_path(brief)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(
            [a.model_dump(mode="json") for a in assignments],
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return path


def carica_outline(brief: Brief) -> list[ChapterAssignment] | None:
    """Carica l'outline congelato, o None se non esiste."""
    path = outline_path(brief)
    if not path.exists():
        return None
    voci = json.loads(path.read_text(encoding="utf-8"))
    return [ChapterAssignment.model_validate(v) for v in voci]


def carica_o_genera_outline(
    brief: Brief,
) -> tuple[list[ChapterAssignment], bool, float | None]:
    """Carica l'outline se esiste (congelato), altrimenti lo genera e lo salva.

    Ritorna (assignments, generato, costo_usd). Se caricato da file, generato è
    False e costo_usd è 0.0 (nessuna chiamata).
    """
    esistente = carica_outline(brief)
    if esistente is not None:
        return esistente, False, 0.0
    assignments, costo = genera_outline(brief)
    salva_outline(brief, assignments)
    return assignments, True, costo


def main() -> None:
    import argparse

    parser = argparse.ArgumentParser(description="Genera (o mostra) l'outline della guida.")
    parser.add_argument("input", help="File JSON con la chiave 'brief'")
    parser.add_argument(
        "--force", action="store_true", help="Rigenera anche se l'outline esiste già"
    )
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute() and not input_path.exists():
        input_path = ENGINE_ROOT / args.input
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    brief = Brief.model_validate(payload["brief"])

    if args.force and outline_path(brief).exists():
        outline_path(brief).unlink()

    assignments, generato, costo = carica_o_genera_outline(brief)
    path = salva_outline(brief, assignments)
    origine = "generato" if generato else "caricato (congelato)"
    costo_str = f"${costo:.4f}" if costo is not None else "n/d"
    print(f"Outline {origine}: {path}  (costo: {costo_str}, {len(assignments)} capitoli)")
    print(path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    main()
