"""Orchestratore dell'intera guida.

Uso, dalla cartella engine/:

    python -m src.guide_runner fixtures/portogallo.json

Flusso: carica o genera l'outline (congelato) → per ogni capitolo, in sequenza,
chiama la pipeline del singolo capitolo (genera → critica → eventuale fixer →
seconda critica → gate) → raccoglie il riassunto e passa al successivo. Ogni
capitolo riceve l'outline completo e i riassunti dei capitoli precedenti (mai i
capitoli interi); il prefisso in cache resta identico byte per byte per tutta la
guida. Stato e ripresa in output/{brief_id}/stato.json; fermate fail-fast; a
guida completa, assemblaggio di guida.md e costi_guida.json.
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
    META_RE,
    chapter_paths,
    rimuovi_note_di_lavoro,
)
from src.coerenza import coerenza_path, scrivi_coerenza, verifica_coerenza
from src.costs import costruisci_costi
from src.outline import carica_o_genera_outline
from src.run_chapter import esegui_capitolo

TITLE_RE = re.compile(r"^#\s+(.+)$", re.MULTILINE)


def _guide_dir(brief: Brief) -> Path:
    return config.output_root() / brief.brief_id


# ---------------------------------------------------------------- stato / ripresa


BASE_STORICO_USD = 15.0  # base dichiarata quando lo storico non è ricostruibile


def _seed_storico(brief: Brief, assignments: list[ChapterAssignment]) -> float:
    """Base dello speso storico per un brief.

    Lo storico deve sommare TUTTI i run sullo stesso brief_id, anche i tentativi
    falliti, ma gli artefatti conservano solo l'ultimo tentativo per capitolo:
    ciò che è stato speso in tentativi poi sovrascritti non è più recuperabile.
    Si ricostruisce quindi il minimo verificabile dagli artefatti presenti e, se
    resta sotto la base dichiarata di $15 (quasi certamente più bassa dello speso
    reale), si parte da quella base.
    """
    ricostruito = 0.0
    for a in assignments:
        cap_path, _ = chapter_paths(brief, a)
        if cap_path.exists():
            try:
                ricostruito += costruisci_costi(brief, a)["totale"].get("costo_usd") or 0.0
            except Exception:
                pass
    return round(max(ricostruito, BASE_STORICO_USD), 6)


def carica_stato(brief: Brief, assignments: list[ChapterAssignment]) -> dict:
    """Carica lo stato della guida, o lo inizializza (tutti i capitoli 'da_fare').

    Garantisce sempre il campo `speso_storico_usd`: accumulatore monotòno dello
    speso su tutti i run del brief, seminato dalla base ricostruita/dichiarata.
    """
    path = _guide_dir(brief) / "stato.json"
    if path.exists():
        stato = json.loads(path.read_text(encoding="utf-8"))
        if "speso_storico_usd" not in stato:
            stato["speso_storico_usd"] = _seed_storico(brief, assignments)
        return stato
    return {
        "brief_id": brief.brief_id,
        "costo_outline": 0.0,
        "speso_storico_usd": _seed_storico(brief, assignments),
        "capitoli": {
            str(a.numero): {
                "stato": "da_fare",
                "path": None,
                "riassunto": None,
                "costo": None,
                "verdetto": None,
                "problemi_revisione": [],
            }
            for a in assignments
        },
    }


def salva_stato(brief: Brief, stato: dict) -> None:
    path = _guide_dir(brief) / "stato.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(stato, ensure_ascii=False, indent=2), encoding="utf-8")


def scrivi_arresto(
    brief: Brief, titolo: str, dettaglio: str, costo_cumulato: float, riprende_da: int
) -> Path:
    """Scrive il rapporto d'arresto: cosa è successo, quanto speso, da dove riprendere."""
    path = _guide_dir(brief) / "ARRESTO.txt"
    testo = (
        f"GUIDA INTERROTTA — {titolo}\n\n"
        f"{dettaglio}\n\n"
        f"Speso finora (incluso outline): ${costo_cumulato:.2f}\n"
        f"Per riprendere: rilancia lo stesso comando; la guida riparte dal "
        f"capitolo {riprende_da} (i capitoli 'approvato' non si rigenerano).\n"
    )
    path.write_text(testo, encoding="utf-8")
    print(testo, file=sys.stderr)
    return path


# --------------------------------------------------------------- validazioni


def valida_riassunto(riassunto: str | None) -> str | None:
    """Valida il riassunto del META: presente, non vuoto, ≤150 parole. Motivo o None."""
    if not riassunto or not riassunto.strip():
        return "riassunto del META assente o vuoto (o blocco META non parsabile)"
    n = len(riassunto.split())
    if n > 150:
        return f"riassunto del META troppo lungo ({n} parole > 150)"
    return None


# --------------------------------------------------------------- assemblaggio


def _titolo_capitolo(testo: str, fallback: str) -> str:
    m = TITLE_RE.search(testo)
    return m.group(1).strip() if m else fallback


def _corpo_senza_meta(testo: str) -> str:
    """Il capitolo senza il blocco META finale, per la guida stampata.

    Ripassa anche le note di lavoro: i capitoli scritti prima che il filtro
    esistesse le portano dentro, e nessuno li rigenera. La pulizia va fatta
    anche qui, in lettura, o quelle note restano nei libri già pagati.
    """
    testo = rimuovi_note_di_lavoro(testo)
    m = META_RE.search(testo)
    corpo = testo[: m.start()] if m else testo
    return corpo.rstrip()


SEZIONE_RE = re.compile(r"^##\s+(.+)$", re.MULTILINE)


def _senza_titolo(corpo: str) -> str:
    """Il corpo del capitolo senza la riga di titolo iniziale."""
    righe = corpo.lstrip().split("\n")
    if righe and righe[0].startswith("# "):
        righe = righe[1:]
    return "\n".join(righe).strip()


def costruisci_libro(
    brief: Brief, assignments: list[ChapterAssignment], stato: dict
) -> dict:
    """Il libro con la sua struttura, ricavato dai capitoli sul disco.

    Sta separato dalla scrittura del file perché è ricostruibile in qualunque
    momento dai capitoli: i libri finiti prima che questo formato esistesse non
    devono restare illeggibili per sempre solo perché il file non c'era.
    """
    capitoli_stato = stato.get("capitoli", {})
    voci = []
    for a in assignments:
        e = capitoli_stato.get(str(a.numero), {})
        if e.get("stato") not in ("approvato", "da_rivedere"):
            continue
        cap_path, _ = chapter_paths(brief, a)
        if not cap_path.exists():
            continue
        corpo = _corpo_senza_meta(cap_path.read_text(encoding="utf-8"))
        voci.append(
            {
                "numero": a.numero,
                "titolo": _titolo_capitolo(corpo, a.titolo_provvisorio),
                "tipo": a.tipo,
                "sezioni": [s.strip() for s in SEZIONE_RE.findall(corpo)],
                "corpo_md": _senza_titolo(corpo),
            }
        )
    return {
        "brief_id": brief.brief_id,
        "capitoli_totali": len(assignments),
        "capitoli": voci,
    }


def scrivi_libro_json(
    brief: Brief, assignments: list[ChapterAssignment], stato: dict
) -> Path:
    """Scrive libro.json: il libro con la sua struttura, non come testo piatto.

    guida.md è un file di prosa: chi lo riceve deve indovinare, dalle righe che
    cominciano con un cancelletto, cosa sia un capitolo e cosa una sezione — e
    sbaglia, perché i due livelli si somigliano. Il risultato visto sul primo
    libro finito è stato un indice di ottantacinque voci in cui i capitoli e le
    loro sezioni stavano mescolati sullo stesso piano.

    Questo file toglie di mezzo l'indovinello: ogni capitolo ha numero, titolo,
    tipo, l'elenco delle sue sezioni e il corpo in markdown senza la riga del
    titolo (che chi legge renderà a modo suo). I capitoli non ancora scritti non
    compaiono: c'è quello che c'è.
    """
    path = _guide_dir(brief) / "libro.json"
    path.write_text(
        json.dumps(
            costruisci_libro(brief, assignments, stato), ensure_ascii=False, indent=2
        ),
        encoding="utf-8",
    )
    return path


def assembla_guida(brief: Brief, assignments: list[ChapterAssignment], stato: dict) -> tuple[Path, Path]:
    """A guida completa: scrive guida.md (indice + capitoli) e costi_guida.json."""
    out_dir = _guide_dir(brief)
    titoli: list[str] = []
    corpi: list[str] = []
    dettaglio_costi: dict[str, dict] = {}
    totale = float(stato.get("costo_outline") or 0.0)

    for a in assignments:
        cap_path, _ = chapter_paths(brief, a)
        testo = cap_path.read_text(encoding="utf-8")
        titolo = _titolo_capitolo(testo, a.titolo_provvisorio)
        titoli.append(titolo)
        corpi.append(_corpo_senza_meta(testo))
        costo = costruisci_costi(brief, a)
        c = costo["totale"].get("costo_usd") or 0.0
        totale += c
        dettaglio_costi[str(a.numero)] = {
            "titolo": titolo,
            "costo_usd": c,
            "dettaglio": costo,
        }

    # guida.md: indice dai titoli reali + capitoli in ordine.
    indice = ["# Indice", ""]
    indice += [f"{i}. {t}" for i, t in enumerate(titoli, start=1)]
    guida_md = "\n".join(indice) + "\n\n---\n\n" + "\n\n---\n\n".join(corpi) + "\n"
    guida_path = out_dir / "guida.md"
    guida_path.write_text(guida_md, encoding="utf-8")

    costi_path = out_dir / "costi_guida.json"
    costi_path.write_text(
        json.dumps(
            {
                "brief_id": brief.brief_id,
                # In testa: il totale storico su TUTTI i run del brief (tentativi
                # falliti inclusi), non solo l'ultimo assemblaggio.
                "totale_storico_usd": round(float(stato.get("speso_storico_usd") or 0.0), 6),
                "costo_outline_usd": float(stato.get("costo_outline") or 0.0),
                "totale_ultimo_assemblaggio_usd": round(totale, 6),
                "capitoli": dettaglio_costi,
            },
            ensure_ascii=False,
            indent=2,
        ),
        encoding="utf-8",
    )
    return guida_path, costi_path


def _corpi_consegnati(
    brief: Brief, assignments: list[ChapterAssignment], stato: dict
) -> list[str]:
    """I corpi (senza META) dei capitoli già consegnati, in ordine."""
    capitoli = stato.get("capitoli", {})
    corpi: list[str] = []
    for a in assignments:
        e = capitoli.get(str(a.numero), {})
        if e.get("stato") not in ("approvato", "da_rivedere"):
            continue
        cap_path, _ = chapter_paths(brief, a)
        if not cap_path.exists():
            continue
        corpi.append(_corpo_senza_meta(cap_path.read_text(encoding="utf-8")))
    return corpi


def assembla_parziale(
    brief: Brief, assignments: list[ChapterAssignment], stato: dict
) -> Path | None:
    """Assembla parziale.md: i capitoli scritti prima che la guida si fermasse.

    Senza questo, un'interruzione lascia i capitoli scritti sul disco come file
    sciolti che nessuno può leggere: il lettore ha pagato, il testo esiste, e la
    pagina gli mostra un pulsante che porta a un 404. Non si chiama guida.md
    apposta — quel nome significa 'libro finito' a valle, e questo non lo è.
    """
    corpi = _corpi_consegnati(brief, assignments, stato)
    if not corpi:
        return None
    mancanti = max(len(assignments) - len(corpi), 0)
    coda = (
        "\n\n---\n\n*Il libro si ferma qui: la scrittura si è interrotta prima "
        f"della fine e mancano {mancanti} capitoli. Quello che avete letto è "
        "definitivo — quando la scrittura riprende, questi capitoli non vengono "
        "riscritti.*\n"
    )
    path = _guide_dir(brief) / "parziale.md"
    path.write_text("\n\n---\n\n".join(corpi) + coda, encoding="utf-8")
    return path


def assembla_anteprima(
    brief: Brief, assignments: list[ChapterAssignment], stato: dict
) -> Path:
    """Assembla l'assaggio: i capitoli già consegnati, con un congedo che
    annuncia il resto del libro.

    Serve a far leggere qualcosa di VERO prima del paywall: non l'indice, non un
    riassunto, ma i capitoli scritti sulle loro tappe. Scrive anteprima.md.
    """
    corpi = _corpi_consegnati(brief, assignments, stato)
    out_dir = _guide_dir(brief)

    restanti = max(len(assignments) - len(corpi), 0)
    coda = (
        "\n\n---\n\n*Qui si ferma l'assaggio. Il vostro libro prosegue per altri "
        f"{restanti} capitoli, scritti sulle vostre tappe e sulle vostre date.*\n"
    )
    path = out_dir / "anteprima.md"
    path.write_text("\n\n---\n\n".join(corpi) + coda, encoding="utf-8")
    return path


def scrivi_da_rivedere(
    brief: Brief, assignments: list[ChapterAssignment], stato: dict
) -> Path:
    """Scrive output/{brief_id}/da_rivedere.md: la lista di revisione da leggere
    accanto al libro. Per ogni capitolo marcato 'da_rivedere' riporta numero,
    titolo reale e l'elenco dei problemi aperti — uno per riga, con la fonte.
    Se non c'è nulla da rivedere, lo scrive esplicitamente."""
    out_dir = _guide_dir(brief)
    capitoli = stato.get("capitoli", {})

    righe = [
        f"# Da rivedere — {brief.brief_id}",
        "",
        "Capitoli consegnati con alert non risolti. Col fixer disattivato le "
        "correzioni non sono automatiche: questa è la lista da leggere accanto al "
        "libro per decidere caso per caso.",
        "",
    ]

    trovati = 0
    for a in assignments:
        e = capitoli.get(str(a.numero), {})
        if e.get("stato") != "da_rivedere":
            continue
        problemi = e.get("problemi_revisione") or []
        if not problemi:
            continue
        trovati += 1
        cap_path, _ = chapter_paths(brief, a)
        titolo = a.titolo_provvisorio
        if cap_path.exists():
            titolo = _titolo_capitolo(
                cap_path.read_text(encoding="utf-8"), a.titolo_provvisorio
            )
        righe.append(f"## Capitolo {a.numero}: {titolo}")
        for p in problemi:
            gravita = p.get("gravita", "?")
            posizione = p.get("posizione") or ""
            testo = (p.get("problema") or "").strip()
            fonte = (p.get("fonte") or "").strip() or "—"
            loc = f" ({posizione})" if posizione else ""
            righe.append(f"- [{gravita}]{loc} {testo} — fonte: {fonte}")
        righe.append("")

    if trovati == 0:
        righe.append(
            "Nessun capitolo da rivedere: tutti consegnati senza alert bloccanti "
            "né lacune di verifica."
        )
        righe.append("")

    path = out_dir / "da_rivedere.md"
    path.write_text("\n".join(righe), encoding="utf-8")
    return path


# --------------------------------------------------------------- orchestrazione


def orchestrazione(brief: Brief, on_progress=None, anteprima: bool = False) -> int:
    """Esegue la guida. Ritorna 0 (completa), 1 (interrotta) o 2 (assaggio pronto).

    Con `anteprima=True` non si scrive il libro intero: ci si ferma appena
    consegnato il primo capitolo di tappa (o il primo capitolo, secondo
    `config.ANTEPRIMA_FINO_A_TAPPA`) e si assembla anteprima.md. Non è un
    errore: è il punto in cui il lettore ha in mano qualcosa di vero, prima del
    paywall — e il costo grosso non è stato speso.

    `on_progress`, se fornito, è una callable invocata con lo stato corrente
    (dict) dopo ogni scrittura di stato durante il ciclo dei capitoli. Serve agli
    host esterni (es. una funzione Modal) per persistere/pubblicare l'avanzamento
    a ogni capitolo — senza che il motore conosca l'host. Le sue eccezioni sono
    ignorate: un problema di pubblicazione non deve far cadere la generazione.
    """
    # Prima riga di ogni run: con quale configurazione sta girando. Senza
    # questa, un confronto fra due configurazioni non è verificabile — si
    # crederebbe di aver misurato 'medium' avendo misurato 'high'.
    print(
        f"Configurazione: scrittura {config.MODEL_GENERATION} "
        f"(sforzo {config.effort_generazione()}) | "
        f"critico {config.MODEL_CRITIC} (sforzo {config.effort_critico()}) | "
        f"ricerche {config.MAX_SEARCHES_PER_CHAPTER}/"
        f"{config.MAX_SEARCHES_NON_TAPPA}/{config.MAX_SEARCHES_CRITIC}"
    )

    # Guardia sul brief, prima di spendere: solo alla prima passata (se
    # coerenza.json esiste già, questa guida è una ripresa o un completamento e
    # il controllo è stato fatto). Non blocca nulla: scrive gli avvisi e prosegue.
    costo_coerenza = 0.0
    if not coerenza_path(brief).exists():
        esito = verifica_coerenza(brief)
        scrivi_coerenza(brief, esito)
        costo_coerenza = float(esito.get("costo_usd") or 0.0)

    assignments, generato, costo_outline = carica_o_genera_outline(brief)
    print(
        f"Outline: {len(assignments)} capitoli "
        f"({'generato' if generato else 'caricato (congelato)'})."
    )

    stato = carica_stato(brief, assignments)
    if costo_coerenza:
        stato["costo_coerenza"] = costo_coerenza

    def _persist() -> None:
        salva_stato(brief, stato)
        if on_progress is not None:
            try:
                on_progress(stato)
            except Exception:
                pass

    if generato:
        stato["costo_outline"] = costo_outline or 0.0
        _persist()

    def _salva_il_salvabile() -> None:
        """Prima di ogni interruzione: rendi leggibile quello che è già scritto.

        Un'interruzione non deve lasciare il lettore con dei file sciolti sul
        disco e una pagina che non ha niente da mostrargli.
        """
        try:
            parziale = assembla_parziale(brief, assignments, stato)
            scrivi_libro_json(brief, assignments, stato)
            revisione = scrivi_da_rivedere(brief, assignments, stato)
            if parziale:
                print(f"Capitoli già scritti, leggibili: {parziale} | {revisione}")
        except Exception as exc:  # non deve mai mascherare l'arresto vero
            print(f"Assemblaggio del parziale non riuscito: {exc}")

    capitoli = stato["capitoli"]
    riassunti: list[str] = []
    costo_cumulato = float(stato.get("costo_outline") or 0.0) + float(
        stato.get("costo_coerenza") or 0.0
    )

    for a in assignments:
        n = str(a.numero)
        e = capitoli.setdefault(
            n,
            {
                "stato": "da_fare",
                "path": None,
                "riassunto": None,
                "costo": None,
                "verdetto": None,
                "problemi_revisione": [],
            },
        )

        # Ripresa: i capitoli già consegnati non si rigenerano mai — né gli
        # 'approvato' né i 'da_rivedere' (col fixer spento questi ultimi sono
        # comunque consegnati e validi, solo con alert aperti da leggere a parte).
        if e["stato"] in ("approvato", "da_rivedere"):
            riassunti.append(e.get("riassunto") or "")
            costo_cumulato += e.get("costo") or 0.0
            etichetta = "già approvato" if e["stato"] == "approvato" else "già consegnato (da rivedere)"
            print(f"Capitolo {a.numero} {etichetta}: salto.")
            continue

        # Ogni capitolo riceve outline completo (già in a.outline_guida) e i
        # riassunti dei precedenti (mai i capitoli interi).
        a.riassunti_precedenti = list(riassunti)
        e["stato"] = "in_corso"
        _persist()
        print(f"\n=== Capitolo {a.numero}: {a.titolo_provvisorio} ({a.tipo}) ===")

        res = esegui_capitolo(brief, a)

        costo_cap = res.get("costo_usd") or 0.0
        costo_cumulato += costo_cap
        # Accumulatore storico: ogni tentativo si somma, anche quelli falliti.
        stato["speso_storico_usd"] = round(
            (stato.get("speso_storico_usd") or 0.0) + costo_cap, 6
        )
        e.update(
            {
                "path": res["cap_path"],
                "riassunto": res["riassunto"],
                "costo": costo_cap,
                "verdetto": res["verdetto"],
            }
        )

        # FAIL-FAST 1 (checkpoint cap 1) + 2 (capitolo senza riassunto da
        # passare al successivo): interrompi, non generare i successivi. I
        # difetti di forma non arrivano più qui: sono consegnati e finiscono in
        # da_rivedere.md.
        if not res["consegnabile"]:
            e["stato"] = "fallito"
            _persist()
            prefisso = (
                "CHECKPOINT capitolo 1 non superato — "
                if a.numero == 1
                else f"Capitolo {a.numero} senza riassunto per il capitolo "
                f"successivo — "
            )
            scrivi_arresto(
                brief,
                prefisso + "guida interrotta",
                "Problemi: " + ("; ".join(res["problemi"]) or "vedi cap WARNING."),
                costo_cumulato,
                a.numero,
            )
            _salva_il_salvabile()
            return 1

        # FAIL-FAST 3: riassunto del META rotto → ferma prima del capitolo seguente.
        problema_riassunto = valida_riassunto(res["riassunto"])
        if problema_riassunto:
            e["stato"] = "fallito"
            _persist()
            scrivi_arresto(
                brief,
                f"Riassunto del capitolo {a.numero} non valido — guida interrotta",
                problema_riassunto
                + " — senza un riassunto valido i capitoli successivi non hanno il "
                "contesto necessario.",
                costo_cumulato,
                a.numero,
            )
            _salva_il_salvabile()
            return 1

        # Capitolo consegnato. Col fixer attivo è "approvato" senz'altro; col fixer
        # spento è "da_rivedere" se restano alert non risolti — consegnato lo
        # stesso, la guida non si ferma. In entrambi i casi il riassunto è valido.
        if res.get("da_rivedere"):
            e["stato"] = "da_rivedere"
            e["problemi_revisione"] = res.get("problemi_revisione") or []
        else:
            e["stato"] = "approvato"
            e["problemi_revisione"] = []
        _persist()
        riassunti.append(res["riassunto"])
        etichetta = "approvato" if e["stato"] == "approvato" else "consegnato (DA RIVEDERE)"
        print(
            f"Capitolo {a.numero} {etichetta} (costo ${costo_cap:.4f}, "
            f"cumulato ${costo_cumulato:.2f})."
        )

        # ASSAGGIO: fermata voluta, non un guasto. Il lettore ha in mano
        # l'introduzione e il primo capitolo sulle sue tappe; il resto si
        # sblocca col pagamento.
        if anteprima and (a.tipo == "tappa" or not config.ANTEPRIMA_FINO_A_TAPPA):
            percorso = assembla_anteprima(brief, assignments, stato)
            scrivi_libro_json(brief, assignments, stato)
            print(
                f"\nASSAGGIO PRONTO. {percorso} "
                f"({len(riassunti)} capitoli, costo ${costo_cumulato:.2f})."
            )
            return 2

        # FAIL-FAST 4: tetto di spesa cumulativo superato → ferma.
        if costo_cumulato > config.max_costo_guida_usd():
            prossimo = a.numero + 1
            scrivi_arresto(
                brief,
                "Tetto di spesa superato — guida interrotta",
                f"Spesa cumulativa ${costo_cumulato:.2f} oltre il tetto "
                f"MAX_COSTO_GUIDA_USD=${config.max_costo_guida_usd():.2f}.",
                costo_cumulato,
                prossimo,
            )
            _salva_il_salvabile()
            return 1

    # Tutti i capitoli consegnati → assemblaggio + lista di revisione.
    guida_path, costi_path = assembla_guida(brief, assignments, stato)
    libro = costruisci_libro(brief, assignments, stato)
    scrivi_libro_json(brief, assignments, stato)
    da_rivedere_path = scrivi_da_rivedere(brief, assignments, stato)

    # Il PDF impaginato: è la copia che il lettore si porta dietro. Se
    # l'impaginazione fallisce il libro resta comunque consegnato — un problema
    # di stampa non deve cancellare cinque ore di scrittura.
    try:
        from src.pdf import scrivi_pdf

        print(f"PDF: {scrivi_pdf(brief, libro)}")
    except Exception as exc:
        print(f"Impaginazione PDF non riuscita: {exc}", file=sys.stderr)
    n_rivedere = sum(
        1 for e in stato["capitoli"].values() if e.get("stato") == "da_rivedere"
    )
    print(
        f"\nGUIDA COMPLETA. {guida_path} | {costi_path} | {da_rivedere_path} "
        f"({n_rivedere} capitoli da rivedere; totale ${costo_cumulato:.2f})."
    )
    return 0


def main() -> None:
    parser = argparse.ArgumentParser(description="Orchestratore dell'intera guida di viaggio.")
    parser.add_argument("input", help="File JSON con la chiave 'brief'")
    args = parser.parse_args()

    input_path = Path(args.input)
    if not input_path.is_absolute() and not input_path.exists():
        input_path = ENGINE_ROOT / args.input
    payload = json.loads(input_path.read_text(encoding="utf-8"))
    brief = Brief.model_validate(payload["brief"])

    sys.exit(orchestrazione(brief))


if __name__ == "__main__":
    main()
