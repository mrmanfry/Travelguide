"""Report di QA deterministico sulla guida assemblata.

Analisi in Python puro — nessuna chiamata a modelli — dei capitoli su disco.
Il testo dei capitoli viene letto ed elaborato dal programma, ma NON compare mai
nell'output se non come frammenti brevi (le sequenze ripetute trovate): il report
è fatto di numeri e liste corte, non del testo del libro.

Uso, dalla cartella engine/:

    python -m src.qa_report fixtures/grecia.json

Legge output/{brief_id}/outline.json (budget e tipo per capitolo) e i file
output/{brief_id}/cap_NN.md. Il blocco META finale è escluso da ogni conteggio.
"""

import argparse
import json
import re
import sys
from collections import Counter, defaultdict
from difflib import SequenceMatcher
from itertools import combinations
from pathlib import Path

from src import config

ENGINE_ROOT = Path(__file__).resolve().parents[1]

META_MARK = "<!--META"
# Token di parola: lettere (anche accentate) e cifre.
WORD_RE = re.compile(r"[0-9A-Za-zÀ-ÖØ-öø-ÿ]+")
# Titolo di box in grassetto: **TIPO — ...** oppure **GLI IMMOBILI**.
# Il TIPO è la parte in maiuscolo prima dell'em-dash (o l'intero grassetto).
BOLD_RE = re.compile(r"\*\*([^*\n]+?)\*\*")
DASH_RE = re.compile(r"\s*[—–]\s*")
# Riga di titolo/sezione da escludere dai conteggi di prosa.
HEADING_RE = re.compile(r"^\s{0,3}#{1,6}\s")
# Fine frase.
SENT_SPLIT_RE = re.compile(r"(?<=[.!?…])\s+")

# Avverbi/formule: consideriamo avverbi in -mente e alcune formule di apertura.
ADVERB_RE = re.compile(r"\b\w+mente\b", re.IGNORECASE)

# Parole funzione italiane: se capitano maiuscole a metà frase (dopo due punti,
# em-dash, virgolette) non sono nomi propri. Filtro anti-rumore.
STOPWORDS_CAP = {
    "il", "lo", "la", "i", "gli", "le", "un", "uno", "una", "l",
    "di", "a", "da", "in", "con", "su", "per", "tra", "fra",
    "del", "dello", "della", "dei", "degli", "delle", "dal", "dalla", "al",
    "alla", "allo", "ai", "agli", "alle", "nel", "nella", "nei", "negli",
    "nelle", "sul", "sulla", "sui", "col",
    "e", "ed", "o", "od", "ma", "né", "anzi", "però", "perché", "poi",
    "che", "chi", "cui", "questo", "questa", "quello", "quella", "questi",
    "queste", "quelli", "quelle", "non", "qui", "qua", "lì", "là", "ecco",
    "ora", "sempre", "mai", "più", "meno", "come", "quando", "dove", "se",
    "ci", "vi", "ne", "si", "lei", "lui", "loro", "voi", "noi", "io", "tu",
    "è", "sono", "era", "c", "d", "un'", "quel", "quella",
}


# ------------------------------------------------------------------ caricamento


def _guide_dir(brief_id: str) -> Path:
    return config.output_root() / brief_id


def _brief_id_da_input(path_arg: str) -> str:
    p = Path(path_arg)
    if not p.is_absolute() and not p.exists():
        p = ENGINE_ROOT / path_arg
    payload = json.loads(p.read_text(encoding="utf-8"))
    return payload["brief"]["brief_id"]


def _corpo(testo: str) -> str:
    """Il capitolo senza il blocco META finale."""
    return testo.split(META_MARK, 1)[0]


def _tokens(testo: str) -> list[str]:
    return [m.group(0).lower() for m in WORD_RE.finditer(testo)]


def _righe_prosa(corpo: str) -> list[str]:
    """Righe non vuote che non sono heading Markdown."""
    return [r for r in corpo.splitlines() if r.strip() and not HEADING_RE.match(r)]


def _paragrafi(corpo: str) -> list[str]:
    """Paragrafi: blocchi separati da riga vuota, esclusi heading."""
    parti = re.split(r"\n\s*\n", corpo)
    out = []
    for blocco in parti:
        righe = [r for r in blocco.splitlines() if not HEADING_RE.match(r)]
        testo = " ".join(r.strip() for r in righe).strip()
        testo = testo.lstrip("> ").strip()
        if testo:
            out.append(testo)
    return out


def _frasi(corpo: str) -> list[str]:
    testo = " ".join(
        r.strip().lstrip("> ").strip()
        for r in corpo.splitlines()
        if r.strip() and not HEADING_RE.match(r)
    )
    frasi = [f.strip() for f in SENT_SPLIT_RE.split(testo) if len(f.split()) >= 4]
    return frasi


# ------------------------------------------------------------------ box


def _box_del_capitolo(corpo: str) -> list[str]:
    """Tipi di box trovati: parte in MAIUSCOLO del titolo in grassetto.

    Un titolo di box ha la forma `**TIPO — titolo**` (o `**GLI IMMOBILI**`): il
    TIPO è tutto in maiuscolo. Il grassetto inline nella prosa (es. **Itaca**)
    non è tutto maiuscolo e viene ignorato.
    """
    tipi = []
    for m in BOLD_RE.finditer(corpo):
        contenuto = m.group(1).strip()
        tipo = DASH_RE.split(contenuto, 1)[0].strip()
        lettere = [c for c in tipo if c.isalpha()]
        if len(lettere) >= 3 and all(c.isupper() for c in lettere):
            tipi.append(tipo.upper())
    return tipi


def _ha_immobili(tipi: list[str]) -> bool:
    return any("IMMOBIL" in t for t in tipi)


# ------------------------------------------------------------------ entità


def _nomi_propri(corpo: str) -> Counter:
    """Nomi propri: parole con iniziale maiuscola non a inizio frase/riga.

    Escluse: parole tutte-maiuscole (titoli di box), la prima parola di ogni
    frase e di ogni riga, i token dopo i due punti d'inizio elenco.
    """
    conteggio: Counter = Counter()
    for riga in corpo.splitlines():
        if HEADING_RE.match(riga):
            continue
        riga = riga.strip().lstrip(">*-• ").strip()
        # Spezza in frasi dentro la riga.
        for frase in SENT_SPLIT_RE.split(riga):
            parole = frase.split()
            for i, p in enumerate(parole):
                nucleo = p.strip("«»\"'“”‘’(),.;:—–…!?")
                if i == 0:
                    continue  # inizio frase
                if len(nucleo) < 2:
                    continue
                if not nucleo[0].isupper():
                    continue
                # Deve avere almeno una minuscola: esclude sigle e TIPI di box.
                if nucleo.isupper():
                    continue
                if not nucleo[1:].islower() and not any(c.islower() for c in nucleo[1:]):
                    continue
                if not nucleo[0].isalpha():
                    continue
                if nucleo.lower() in STOPWORDS_CAP:
                    continue
                conteggio[nucleo] += 1
    return conteggio


# ------------------------------------------------------------------ n-grammi


def _ngrams(tokens: list[str], n: int) -> list[tuple]:
    return [tuple(tokens[i : i + n]) for i in range(len(tokens) - n + 1)]


def _perc_condivisi(a_tokens: list[str], b_tokens: list[str], n: int) -> tuple[int, float]:
    sa, sb = set(_ngrams(a_tokens, n)), set(_ngrams(b_tokens, n))
    if not sa or not sb:
        return 0, 0.0
    cond = sa & sb
    perc = 100.0 * len(cond) / min(len(sa), len(sb))
    return len(cond), perc


def _sequenze_comuni(a_tokens: list[str], b_tokens: list[str], soglia: int) -> list[str]:
    """Le sequenze contigue di token condivise più lunghe (≥ soglia parole)."""
    sm = SequenceMatcher(None, a_tokens, b_tokens, autojunk=False)
    blocchi = [b for b in sm.get_matching_blocks() if b.size >= soglia]
    blocchi.sort(key=lambda b: b.size, reverse=True)
    return [" ".join(a_tokens[b.a : b.a + b.size]) for b in blocchi[:3]]


# ------------------------------------------------------------------ report


def costruisci_report(brief_id: str) -> str:
    out_dir = _guide_dir(brief_id)
    outline_path = out_dir / "outline.json"
    if not outline_path.exists():
        return f"[qa_report] outline.json assente in {out_dir}"
    outline = json.loads(outline_path.read_text(encoding="utf-8"))

    # Carica i capitoli presenti su disco.
    cap = {}  # numero -> dict con corpo, tokens, ...
    for voce in outline:
        n = voce["numero"]
        cap_path = out_dir / f"cap_{n:02d}.md"
        if not cap_path.exists():
            continue
        corpo = _corpo(cap_path.read_text(encoding="utf-8"))
        cap[n] = {
            "voce": voce,
            "corpo": corpo,
            "tokens": _tokens(corpo),
            "box": _box_del_capitolo(corpo),
        }

    R: list[str] = []
    R.append(f"=== QA REPORT — {brief_id} ===")
    R.append(f"Capitoli presenti su disco: {len(cap)} / {len(outline)} previsti dall'outline.")
    numeri = sorted(cap)

    # 1. Parole vs budget.
    R.append("\n--- 1. PAROLE vs BUDGET OUTLINE ---")
    R.append(f"{'cap':>3} {'tipo':<13} {'parole':>7} {'budget':>7} {'scarto':>8}")
    for n in numeri:
        v = cap[n]["voce"]
        # Stesso conteggio del motore: parole = token separati da spazi del corpo
        # senza META (chapter_word_count usa str.split()).
        parole = len(cap[n]["corpo"].split())
        budget = v.get("budget_parole") or 0
        scarto = (100.0 * (parole - budget) / budget) if budget else 0.0
        R.append(f"{n:>3} {v.get('tipo',''):<13} {parole:>7} {budget:>7} {scarto:>+7.1f}%")

    # 2. Sovrapposizione tra coppie di capitoli.
    R.append("\n--- 2. SOVRAPPOSIZIONE TRA CAPITOLI (n-grammi condivisi) ---")
    R.append("Coppie con maggiore condivisione di 4-grammi (soglia riportata: top per %).")
    R.append(f"{'coppia':<9} {'4g #':>6} {'4g %':>7} {'5g #':>6} {'5g %':>7}")
    righe_coppie = []
    for i, j in combinations(numeri, 2):
        c4, p4 = _perc_condivisi(cap[i]["tokens"], cap[j]["tokens"], 4)
        c5, p5 = _perc_condivisi(cap[i]["tokens"], cap[j]["tokens"], 5)
        righe_coppie.append((p4, i, j, c4, p4, c5, p5))
    righe_coppie.sort(reverse=True)
    for p4, i, j, c4, _p4, c5, p5 in righe_coppie[:12]:
        R.append(f"{i:>2}-{j:<6} {c4:>6} {p4:>6.2f}% {c5:>6} {p5:>6.2f}%")

    R.append("\nSequenze ripetute più lunghe (≥6 parole) nelle coppie principali:")
    trovate = 0
    for p4, i, j, c4, _p4, c5, p5 in righe_coppie:
        seqs = _sequenze_comuni(cap[i]["tokens"], cap[j]["tokens"], 6)
        if seqs:
            for s in seqs:
                R.append(f"  {i}-{j} ({len(s.split())}p): {s}")
            trovate += 1
        if trovate >= 8:
            break
    if trovate == 0:
        R.append("  nessuna sequenza contigua ≥6 parole condivisa tra capitoli.")

    # 3. Entità ripetute (nomi propri in più capitoli).
    R.append("\n--- 3. ENTITÀ RIPETUTE (nomi propri in >1 capitolo) ---")
    per_cap = {n: _nomi_propri(cap[n]["corpo"]) for n in numeri}
    entita_cap: dict[str, dict[int, int]] = defaultdict(dict)
    for n in numeri:
        for nome, k in per_cap[n].items():
            entita_cap[nome][n] = k
    multi = {e: d for e, d in entita_cap.items() if len(d) > 1}
    # Ordina per numero di capitoli in cui compare, poi per totale occorrenze.
    ordinati = sorted(multi.items(), key=lambda kv: (len(kv[1]), sum(kv[1].values())), reverse=True)
    R.append(f"Nomi propri distinti presenti in >1 capitolo: {len(multi)}. Top 25:")
    for nome, d in ordinati[:25]:
        dettaglio = " ".join(f"c{n}:{k}" for n, k in sorted(d.items()))
        R.append(f"  {nome:<22} in {len(d)} cap — {dettaglio}")

    # 4. Ripetizioni stilistiche.
    R.append("\n--- 4. RIPETIZIONI STILISTICHE ---")

    # 4a. Aperture di paragrafo (prime 3 parole) ricorrenti.
    aperture = Counter()
    aperture_dove: dict[str, list[int]] = defaultdict(list)
    for n in numeri:
        for par in _paragrafi(cap[n]["corpo"]):
            parole = par.split()
            if len(parole) >= 3:
                chiave = " ".join(w.strip(".,;:—–").lower() for w in parole[:3])
                aperture[chiave] += 1
                aperture_dove[chiave].append(n)
    ricorrenti = [(k, c) for k, c in aperture.items() if c >= 2]
    ricorrenti.sort(key=lambda kv: kv[1], reverse=True)
    R.append(f"4a. Aperture di paragrafo (prime 3 parole) ripetute ≥2 volte: {len(ricorrenti)}. Top 15:")
    for k, c in ricorrenti[:15]:
        caps = ",".join(f"c{n}" for n in sorted(set(aperture_dove[k])))
        R.append(f"  {c}× [{caps}]  \"{k}…\"")

    # 4b. Avverbi in -mente più frequenti nell'intera guida.
    avverbi = Counter()
    for n in numeri:
        for m in ADVERB_RE.finditer(cap[n]["corpo"]):
            avverbi[m.group(0).lower()] += 1
    R.append(f"\n4b. Avverbi in -mente (totale occorrenze {sum(avverbi.values())}). Top 15:")
    R.append("  " + ", ".join(f"{a}:{c}" for a, c in avverbi.most_common(15)))

    # 4c. Trigrammi ricorrenti (formule), esclusi quelli con nomi propri.
    trigrammi = Counter()
    for n in numeri:
        toks = cap[n]["tokens"]
        for g in _ngrams(toks, 3):
            trigrammi[g] += 1
    formule = [(g, c) for g, c in trigrammi.items() if c >= 4]
    formule.sort(key=lambda kv: kv[1], reverse=True)
    R.append(f"\n4c. Trigrammi ricorrenti (≥4 occorrenze) nell'intera guida: {len(formule)}. Top 15:")
    for g, c in formule[:15]:
        R.append(f"  {c}× \"{' '.join(g)}\"")

    # 4d. Frasi identiche o quasi tra capitoli diversi.
    R.append("\n4d. Frasi (quasi) identiche tra capitoli diversi (ratio ≥0.90):")
    frasi_per_cap = {n: _frasi(cap[n]["corpo"]) for n in numeri}
    viste = 0
    coppie_frasi = set()
    for i, j in combinations(numeri, 2):
        fi, fj = frasi_per_cap[i], frasi_per_cap[j]
        for a in fi:
            for b in fj:
                if abs(len(a) - len(b)) > 40:
                    continue
                r = SequenceMatcher(None, a, b, autojunk=False).ratio()
                if r >= 0.90:
                    chiave = (a[:60], b[:60])
                    if chiave in coppie_frasi:
                        continue
                    coppie_frasi.add(chiave)
                    frag = a if len(a) <= 90 else a[:90] + "…"
                    R.append(f"  c{i}~c{j} ({r:.2f}): {frag}")
                    viste += 1
        if viste >= 12:
            break
    if viste == 0:
        R.append("  nessuna frase con similarità ≥0.90 tra capitoli distinti.")

    # 5. Box per tipo e per capitolo.
    R.append("\n--- 5. BOX PER TIPO E PER CAPITOLO ---")
    tutti_tipi = sorted({t for n in numeri for t in cap[n]["box"]})
    R.append(f"Tipi di box rilevati ({len(tutti_tipi)}): {', '.join(tutti_tipi) or '—'}")
    R.append("Conteggio per capitolo:")
    for n in numeri:
        c = Counter(cap[n]["box"])
        tot = sum(c.values())
        dett = ", ".join(f"{t}×{k}" for t, k in sorted(c.items()))
        R.append(f"  cap {n:>2} ({cap[n]['voce'].get('tipo',''):<12}) totale {tot}: {dett or '—'}")
    R.append("Distribuzione per tipo (totale sull'intera guida):")
    totali_tipo = Counter(t for n in numeri for t in cap[n]["box"])
    for t, k in totali_tipo.most_common():
        R.append(f"  {t:<22} {k}")

    # 6. GLI IMMOBILI nei capitoli di tappa.
    R.append("\n--- 6. BOX 'GLI IMMOBILI' NEI CAPITOLI DI TAPPA ---")
    tappe = [n for n in numeri if cap[n]["voce"].get("tipo") == "tappa"]
    if not tappe:
        R.append("  nessun capitolo di tipo 'tappa' presente su disco.")
    for n in tappe:
        presente = _ha_immobili(cap[n]["box"])
        R.append(f"  cap {n:>2} {cap[n]['voce'].get('titolo_provvisorio','')[:40]:<40} "
                 f"GLI IMMOBILI: {'SÌ' if presente else 'NO'}")
    mancanti = [n for n in tappe if not _ha_immobili(cap[n]["box"])]
    R.append(f"  → tappe senza box GLI IMMOBILI: {mancanti or 'nessuna'}")

    return "\n".join(R)


def main() -> None:
    parser = argparse.ArgumentParser(description="Report di QA deterministico sulla guida.")
    parser.add_argument("input", help="File JSON con la chiave 'brief' (per il brief_id)")
    args = parser.parse_args()
    brief_id = _brief_id_da_input(args.input)
    print(costruisci_report(brief_id))


if __name__ == "__main__":
    main()
