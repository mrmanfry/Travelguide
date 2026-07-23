"""Loop di correzione mirata di un capitolo già criticato — a patch.

Il fixer non riscrive più il capitolo: restituisce un oggetto JSON di patch
testuali (sostituzioni `cerca`→`sostituisci`) e un elenco di `alert_respinti`.
È il codice ad applicare le patch al capitolo, in modo deterministico e sicuro:
ogni `cerca` deve comparire esattamente una volta, altrimenti la patch è scartata
e l'originale non viene toccato. Così un output malformato del modello non può
più corrompere il file (il difetto che aveva ridotto un capitolo a 234 parole).
Riusa il prefisso system in cache (style guide + brief + calendario) del
generatore, così il correttore lavora con lo stesso contesto di chi ha scritto.
"""

import json
import re
import sys
from pathlib import Path

from schema.brief import Brief, ChapterAssignment
from src import config
from src.assets import capture_assets, delete_chapter_assets
from src.chapter_runner import (
    META_RE,
    TITLE_RE,
    build_system_blocks,
    chapter_word_count,
    controlli_struttura,
    make_client,
    parse_meta,
    run_verification_call,
    strip_cite_tags,
)

MAX_FIX_ATTEMPTS = 2  # 1 tentativo + 1 solo ritentativo con le patch fallite


def format_alerts(alerts: list[dict]) -> str:
    """Rende la lista degli alert (numerata 1-based) per il correttore.

    L'indice mostrato qui è quello che il fixer deve riportare nel campo `alert`
    di ogni patch.
    """
    parti = []
    for i, a in enumerate(alerts, 1):
        if not isinstance(a, dict):
            continue
        parti.append(
            f"Alert {i} — gravità: {a.get('gravita', '?')}, tipo: {a.get('tipo', '?')}\n"
            f"  posizione: {a.get('posizione', '')}\n"
            f"  problema: {a.get('problema', '')}\n"
            f"  evidenza: {a.get('evidenza', '')}\n"
            f"  correzione proposta: {a.get('correzione_proposta', '')}"
        )
    return "\n\n".join(parti)


def build_fixer_user(chapter_text: str, alerts: list[dict], nota: str = "") -> str:
    """Messaggio user del correttore: capitolo integrale + alert numerati.

    Il capitolo serve al fixer per copiare alla lettera i frammenti `cerca`; gli
    alert sono numerati perché ogni patch ne riporti l'indice.
    """
    testo = (
        "CAPITOLO DI RIFERIMENTO (da cui copiare alla lettera i frammenti 'cerca', "
        "NON da riscrivere):\n\n"
        f"{chapter_text}\n\n"
        "---\n\n"
        "ALERT EMESSI DALL'EDITOR DI VERIFICA (numerati; riverificali prima di "
        "correggere):\n\n"
        f"{format_alerts(alerts)}"
    )
    if nota:
        testo += "\n\n---\n\n" + nota
    return testo


def _extract_json_object(raw: str) -> dict | None:
    """Estrae l'oggetto JSON di patch dalla risposta del modello.

    Tollera eventuali code-fence o testo attorno: prova il testo intero, poi il
    contenuto di un blocco ```json, poi la porzione tra la prima graffa aperta e
    l'ultima chiusa.
    """
    candidati = [raw.strip()]
    m = re.search(r"```(?:json)?\s*(\{.*?\})\s*```", raw, re.DOTALL)
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


def _applica_patch(testo: str, patches: list) -> tuple[str, list[str], list[dict]]:
    """Applica le patch valide al testo, in modo deterministico.

    Una patch è valida se il suo `cerca` compare ESATTAMENTE una volta nel testo
    corrente (aggiornato man mano). Se compare zero o più volte, la patch è
    scartata e registrata tra le fallite. Ritorna (testo_nuovo, motivi_applicati,
    patch_fallite).
    """
    nuovo = testo
    applicate: list[str] = []
    fallite: list[dict] = []
    for p in patches:
        if not isinstance(p, dict):
            fallite.append({"alert": None, "cerca": "", "motivo_fallimento": "patch non è un oggetto"})
            continue
        cerca = p.get("cerca")
        sostituisci = p.get("sostituisci", "")
        motivo = (p.get("motivo") or "").strip()
        if not isinstance(cerca, str) or not cerca:
            fallite.append({"alert": p.get("alert"), "cerca": "", "motivo_fallimento": "campo 'cerca' vuoto o assente"})
            continue
        if not isinstance(sostituisci, str):
            fallite.append({"alert": p.get("alert"), "cerca": cerca[:120], "motivo_fallimento": "campo 'sostituisci' non è testo"})
            continue
        n = nuovo.count(cerca)
        if n != 1:
            fallite.append(
                {
                    "alert": p.get("alert"),
                    "cerca": cerca[:120],
                    "motivo_fallimento": f"'cerca' trovato {n} volte (atteso esattamente 1)",
                }
            )
            continue
        nuovo = nuovo.replace(cerca, sostituisci, 1)
        applicate.append(motivo or f"alert {p.get('alert')}")
    return nuovo, applicate, fallite


def _aggiorna_meta(testo: str, correzioni: list[str], respinti: list) -> tuple[str, bool]:
    """Aggiorna il blocco META: aggiunge correzioni_applicate e alert_respinti,
    lasciando invariati tutti gli altri campi. Ritorna (testo_nuovo, ok)."""
    m = META_RE.search(testo)
    if not m:
        return testo, False
    try:
        meta = json.loads(m.group(1).strip())
    except json.JSONDecodeError:
        return testo, False
    if not isinstance(meta, dict):
        return testo, False
    meta["correzioni_applicate"] = correzioni
    meta["alert_respinti"] = respinti if isinstance(respinti, list) else []
    blocco = "<!--META\n" + json.dumps(meta, ensure_ascii=False, indent=2) + "\nMETA-->"
    return testo[: m.start()] + blocco + testo[m.end() :], True


def _nota_patch_fallite(fallite: list[dict]) -> str:
    """Nota per il ritentativo: elenca le patch fallite perché il fixer ricopi
    il frammento esatto."""
    righe = [
        "PATCH FALLITE nel tentativo precedente — il campo 'cerca' non compariva "
        "esattamente una volta nel capitolo. Ricopia il testo ESATTO dal capitolo "
        "(una sola occorrenza, allungandolo col contesto se serve) e riprova SOLO "
        "su questi punti:"
    ]
    for f in fallite:
        righe.append(
            f"- alert {f.get('alert')}: cerca era «{f.get('cerca')}» → {f.get('motivo_fallimento')}"
        )
    return "\n".join(righe)


def _problemi_candidato(
    assignment: ChapterAssignment, testo: str, truncated: bool
) -> list[str]:
    """Valida il capitolo dopo l'applicazione delle patch, con gli stessi
    controlli della generazione: titolo, META parsabile, banda di lunghezza, box
    GLI IMMOBILI, campo `correzioni_applicate` non vuoto. Ritorna la lista dei
    problemi (vuota = valido)."""
    problemi: list[str] = []
    if truncated:
        problemi.append("output del fixer troncato (max_tokens)")
    if not TITLE_RE.search(testo):
        problemi.append("nessuna riga di titolo '# ' nel capitolo patchato")
        return problemi
    c = controlli_struttura(assignment, testo)
    lo, hi = c["banda"]
    if not c["meta_ok"]:
        problemi.append("blocco META assente o non parsabile dopo le patch")
    if not c["lunghezza_ok"]:
        problemi.append(
            f"lunghezza fuori banda dopo le patch: {c['parole']} parole (ammesse {lo}-{hi})"
        )
    if not c["immobili_ok"]:
        if assignment.tipo == "tappa":
            problemi.append("box GLI IMMOBILI assente in un capitolo di tappa")
        else:
            problemi.append(
                f"box GLI IMMOBILI presente in un capitolo di tipo '{assignment.tipo}'"
            )
    meta = c["meta"] or {}
    corr = meta.get("correzioni_applicate")
    if not (isinstance(corr, list) and corr):
        problemi.append("nessuna correzione applicata (correzioni_applicate vuoto)")
    return problemi


def apply_corrections(
    brief: Brief,
    assignment: ChapterAssignment,
    chapter_path: Path,
    verdict: dict | None,
) -> tuple[Path, int, dict]:
    """Applica le correzioni del critico come patch e promuove solo se valido.

    Il fixer restituisce un JSON di patch; il codice le applica al capitolo
    (ogni `cerca` deve comparire esattamente una volta), aggiorna il blocco META
    con `correzioni_applicate` e `alert_respinti`, e valida il risultato con i
    controlli della generazione. Solo se almeno una patch è applicata e la
    validazione passa, il capitolo patchato è promosso a `cap_NN.md`. Se nessuna
    patch è applicabile si concede un solo ritentativo passando al modello le
    patch fallite; poi il capitolo va in stato fallito con l'originale INTATTO
    (mai sovrascritto). La versione pre-correzione resta in `cap_NN.v1.md`.
    Ritorna (percorso, n_parole, info): `info` include `promoted` (bool) e
    `fix_problemi` (list[str]).
    """
    numero = assignment.numero
    original_text = chapter_path.read_text(encoding="utf-8")

    # Versione pre-correzione, per record e confronto.
    v1_path = chapter_path.with_name(f"cap_{numero:02d}.v1.md")
    v1_path.write_text(original_text, encoding="utf-8")

    alerts = [a for a in ((verdict or {}).get("alerts") or []) if isinstance(a, dict)]

    client = make_client()
    system = build_system_blocks(brief, system_file="fixer_system.md")
    tools = [
        {
            "type": config.WEB_SEARCH_TOOL_TYPE,
            "name": "web_search",
            "max_uses": config.MAX_SEARCHES_CRITIC,
        }
    ]
    candidate_path = chapter_path.with_name(f"cap_{numero:02d}.fix_candidate.md")
    patch_path = chapter_path.with_name(f"cap_{numero:02d}.fix.patch.json")

    usage_totale: list[dict] = []
    ultimo_response = None
    ultimo_info: dict = {"stop_reason": None, "truncated": False, "retried": False}
    testo_valido: str | None = None
    problemi_finali: list[str] = []
    fallite_prec: list[dict] = []
    ultimo_patch_obj: dict | None = None

    for tentativo in range(1, MAX_FIX_ATTEMPTS + 1):
        nota = _nota_patch_fallite(fallite_prec) if (tentativo > 1 and fallite_prec) else ""
        user_content = build_fixer_user(original_text, alerts, nota)
        response, usage_log, info = run_verification_call(
            client,
            system,
            tools,
            user_content,
            model=config.MODEL_FIXER,
            max_tokens=config.MAX_TOKENS_FIXER,
        )
        usage_totale.extend(usage_log)
        ultimo_response = response
        ultimo_info = info

        raw = "".join(block.text for block in response.content if block.type == "text")
        obj = _extract_json_object(raw)
        if obj is None:
            problemi_finali = ["output del fixer non è un oggetto JSON parsabile"]
            fallite_prec = []
            candidate_path.write_text(raw, encoding="utf-8")
            print(
                f"ATTENZIONE: correzione cap {numero:02d}, tentativo {tentativo}/"
                f"{MAX_FIX_ATTEMPTS}: JSON di patch non parsabile.",
                file=sys.stderr,
            )
            continue

        ultimo_patch_obj = obj
        patches = obj.get("patch") or []
        respinti = obj.get("alert_respinti") or []
        patch_path.write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")

        patchato, applicate, fallite = _applica_patch(original_text, patches)
        patchato = strip_cite_tags(patchato)

        if not applicate:
            problemi_finali = ["nessuna patch applicabile"] + [
                f"alert {f.get('alert')}: {f.get('motivo_fallimento')}" for f in fallite
            ]
            fallite_prec = fallite
            candidate_path.write_text(patchato, encoding="utf-8")
            print(
                f"ATTENZIONE: correzione cap {numero:02d}, tentativo {tentativo}/"
                f"{MAX_FIX_ATTEMPTS}: nessuna patch applicabile "
                f"({len(fallite)} fallite).",
                file=sys.stderr,
            )
            continue

        patchato, meta_ok = _aggiorna_meta(patchato, applicate, respinti)
        candidate_path.write_text(patchato, encoding="utf-8")
        if not meta_ok:
            problemi_finali = ["blocco META originale assente o non aggiornabile"]
            fallite_prec = fallite
            continue

        problemi = _problemi_candidato(assignment, patchato, info["truncated"])
        if not problemi:
            testo_valido = patchato
            problemi_finali = []
            if fallite:
                print(
                    f"Cap {numero:02d}: {len(applicate)} patch applicate, "
                    f"{len(fallite)} scartate (cerca non univoco).",
                    file=sys.stderr,
                )
            break

        problemi_finali = problemi
        fallite_prec = fallite
        print(
            f"ATTENZIONE: correzione cap {numero:02d}, tentativo {tentativo}/"
            f"{MAX_FIX_ATTEMPTS} non valido dopo le patch: " + "; ".join(problemi),
            file=sys.stderr,
        )

    # Scrivi l'usage del fixer (accumulato su tutti i tentativi).
    usage_path = chapter_path.with_name(f"cap_{numero:02d}.fix.usage.json")
    usage_path.write_text(
        json.dumps(
            {
                "model": ultimo_response.model if ultimo_response else config.MODEL_FIXER,
                "stop_reason": ultimo_info.get("stop_reason"),
                "truncated": ultimo_info.get("truncated"),
                "retried": ultimo_info.get("retried"),
                "promoted": testo_valido is not None,
                "patch_applicate": len((ultimo_patch_obj or {}).get("patch") or [])
                if testo_valido is not None
                else 0,
                "chiamate": usage_totale,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )

    info_ritorno = dict(ultimo_info)
    info_ritorno["promoted"] = testo_valido is not None
    info_ritorno["fix_problemi"] = [] if testo_valido is not None else problemi_finali

    if testo_valido is None:
        # Nessun candidato valido: NON sovrascrivo l'originale.
        warning_path = chapter_path.with_name(f"cap_{numero:02d}.WARNING.txt")
        warning_path.write_text(
            f"CORREZIONE NON APPLICATA dopo {MAX_FIX_ATTEMPTS} tentativi — "
            "originale mantenuto:\n\n"
            + "\n".join(f"- {p}" for p in problemi_finali)
            + f"\n\nLe patch proposte sono in {patch_path.name} (non applicate).\n",
            encoding="utf-8",
        )
        print(
            f"ATTENZIONE: capitolo {numero:02d}: nessuna correzione valida dopo "
            f"{MAX_FIX_ATTEMPTS} tentativi — tengo l'originale, capitolo destinato "
            "allo stato fallito.",
            file=sys.stderr,
        )
        return chapter_path, chapter_word_count(original_text), info_ritorno

    # Candidato valido → promozione a cap_NN.md.
    chapter_path.write_text(testo_valido, encoding="utf-8")

    # Il META aggiornato è autoritativo: dedup della libreria asset SOLO se porta
    # una lista `assets` non vuota (altrimenti niente riga degenere).
    meta_match = META_RE.search(testo_valido)
    meta = parse_meta(testo_valido)
    assets = meta.get("assets") if isinstance(meta, dict) else None
    if meta_match and isinstance(assets, list) and assets:
        delete_chapter_assets(brief.brief_id, numero)
        capture_assets(brief, assignment, meta_match.group(1).strip())
    else:
        print(
            f"ATTENZIONE: capitolo corretto {numero:02d}: blocco META senza chiave "
            "'assets' valorizzata — asset della v1 mantenuti, nessuna cattura né "
            "deduplica.",
            file=sys.stderr,
        )

    return chapter_path, chapter_word_count(testo_valido), info_ritorno
