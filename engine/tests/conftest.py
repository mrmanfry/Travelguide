"""Le prove del motore: niente rete, niente chiamate vere ai modelli, niente dati di clienti.

Si lanciano dalla cartella engine:

    python -m pytest tests -q

Tre regole che valgono per tutte:
- ogni prova scrive in una cartella temporanea sua (fixture `uscita`), mai in
  engine/output né sulla Volume;
- i client finti dei modelli controllano i parametri contro la firma VERA
  dell'SDK installato (`controlla_messages` / `controlla_stream_beta`): un
  parametro che l'SDK rifiuterebbe (come `temperature` quando è stato tolto)
  fa fallire la prova invece di passare in silenzio;
- i dati sono inventati: il viaggio finto a Lisbona delle fixture e un libro
  finto costruito qui.
"""

import inspect
import json
import sys
import types
from pathlib import Path

import pytest

ENGINE = Path(__file__).resolve().parent.parent
RADICE = ENGINE.parent
for p in (str(ENGINE), str(RADICE)):
    if p not in sys.path:
        sys.path.insert(0, p)

from anthropic.resources.beta.messages import Messages as MessagesBeta  # noqa: E402
from anthropic.resources.messages import Messages  # noqa: E402

_FIRMA = inspect.signature(Messages.create)
_FIRMA_STREAM_BETA = inspect.signature(MessagesBeta.stream)


def controlla_messages(kw: dict) -> None:
    """TypeError, come l'SDK vero, su un parametro di messages.create che non esiste."""
    _FIRMA.bind(None, **kw)


def controlla_stream_beta(kw: dict) -> None:
    _FIRMA_STREAM_BETA.bind(None, **kw)


def risposta(testo: str, stop_reason: str = "end_turn", model: str = "claude-opus-5-5"):
    """Una risposta dell'API come la vede il motore: blocchi, usage, stop_reason."""
    return types.SimpleNamespace(
        stop_reason=stop_reason, model=model,
        content=[types.SimpleNamespace(type="thinking", thinking=""),
                 types.SimpleNamespace(type="text", text=testo)],
        usage=types.SimpleNamespace(model_dump=lambda: {"input_tokens": 1000, "output_tokens": 200}))


@pytest.fixture
def uscita(tmp_path, monkeypatch):
    """La radice degli artefatti, in una cartella temporanea."""
    from src import config

    radice = tmp_path / "output"
    radice.mkdir()
    monkeypatch.setenv("GUIDE_OUTPUT_ROOT", str(radice))
    monkeypatch.setattr(config, "output_root", lambda: radice)
    return radice


def brief_dict(brief_id: str = "prova-lisbona", **cambi) -> dict:
    dati = json.loads((ENGINE / "fixtures" / "lisbona.json").read_text(encoding="utf-8"))["brief"]
    dati["brief_id"] = brief_id
    dati["viaggiatori"]["nomi"] = ["Anna", "Marco"]
    dati.update(cambi)
    return dati


@pytest.fixture
def brief():
    from schema.brief import Brief

    return Brief.model_validate(brief_dict())


CAPITOLO_TAPPA = """# Lisbona (Lisboa)

## La città che si guarda dall'alto e si capisce dal basso

## I. L'Alfama prima delle nove

{prosa}

> **STORIA PROFONDA — Il terremoto del 1755**
>
> Il primo novembre del 1755 la città crollò, bruciò e fu travolta dal mare. {breve}

## II. Il mercato e il fado

{prosa}

> **DA FARE — Lisbona**
>
> *Da vedere e da fare*
>
> - **Miradouro de Santa Luzia**: la luce del mattino sui tetti dell'Alfama.
> - **Mercado de Campo de Ourique**: il mercato vero, quello del quartiere.
>
> *Da mangiare e bere*
>
> - **Taberna da Rua das Flores**: la cena dell'anniversario.
>
> *Se avanza tempo*
>
> - **Museu Nacional do Azulejo**: se piove.

> **GLI IMMOBILI — Lisbona, 18–21 settembre**
>
> - **Lunedì 21 settembre**: molti musei sono chiusi.
"""


@pytest.fixture
def libro():
    """Un libro finto di tre capitoli: introduzione, una tappa con tutti i box, apparati."""
    prosa = " ".join(["Una frase di prova, scritta per riempire la pagina come farebbe un capitolo."] * 40)
    breve = " ".join(["Una frase breve."] * 6)
    return {
        "brief_id": "prova-lisbona",
        "capitoli_totali": 3,
        "capitoli": [
            {"numero": 1, "tipo": "introduzione", "titolo": "Lisbona, per voi due", "sezioni": ["I. Il viaggio"],
             "corpo_md": "## I. Il viaggio\n\n" + prosa},
            {"numero": 2, "tipo": "tappa", "titolo": "Lisbona (Lisboa)",
             "sezioni": ["I. L'Alfama prima delle nove", "II. Il mercato e il fado"],
             "corpo_md": CAPITOLO_TAPPA.split("\n", 1)[1].format(prosa=prosa, breve=breve)},
            {"numero": 3, "tipo": "apparati", "titolo": "Apparati", "sezioni": ["I. Pratica"],
             "corpo_md": "## I. Pratica\n\n" + prosa},
        ],
    }
