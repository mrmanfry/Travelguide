"""Contratto dati del sistema: modelli Pydantic v2 per brief e assignment."""

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field, field_validator, model_validator


class Tappa(BaseModel):
    ordine: int
    luogo: str
    place_id: Optional[str] = None
    notti: int
    hotel: Optional[str] = None
    note_logistica: Optional[str] = None


class DateViaggio(BaseModel):
    inizio: Optional[date] = None
    fine: Optional[date] = None
    flessibili: bool = False
    mese: Optional[str] = None


class Viaggiatori(BaseModel):
    adulti: int = 2
    bambini_eta: list[int] = Field(default_factory=list)
    # I nomi di chi viaggia. Il sito li chiede promettendo che «il libro parlerà
    # di voi, non di due generici viaggiatori»; senza questo campo Pydantic li
    # scartava in silenzio e chi scriveva non li vedeva mai.
    nomi: list[str] = Field(default_factory=list)
    occasione: Literal[
        "luna_di_miele",
        "coppia",
        "anniversario",
        "famiglia",
        "amici",
        "solo",
        "altro",
    ] = "altro"


class Stile(BaseModel):
    avventura_relax: int = Field(3, ge=1, le=5)
    ritmo: int = Field(3, ge=1, le=5)
    budget_fascia: Literal["contenuto", "medio", "medio_alto", "alto"] = "medio"


class Passione(BaseModel):
    tema: str
    dettaglio: Optional[str] = None

    @model_validator(mode="before")
    @classmethod
    def _da_testo(cls, dati):
        # Il sito a volte manda le passioni come parole semplici
        # («birra e birrerie storiche») invece che {tema, dettaglio}. È la
        # stessa informazione: rifiutarla fermava il libro prima di cominciare.
        if isinstance(dati, str):
            return {"tema": dati}
        return dati


class Oro(BaseModel):
    da_non_perdere: list[str] = Field(default_factory=list)
    da_evitare: list[str] = Field(default_factory=list)
    contesto_emotivo: Optional[str] = None
    note_libere: Optional[str] = None

    @field_validator("da_non_perdere", "da_evitare", mode="before")
    @classmethod
    def _lista(cls, v):
        # Un solo testo invece di un elenco: è un elenco di uno.
        if v is None:
            return []
        if isinstance(v, str):
            return [v] if v.strip() else []
        return v


class GiornoProgramma(BaseModel):
    numero: Optional[int] = None
    titolo: Optional[str] = None
    racconto: Optional[str] = None


class Programma(BaseModel):
    """Il documento che i viaggiatori hanno già in mano, letto dal sito.

    Programma dell'agenzia, conferme di prenotazione, il foglio che si sono
    preparati. Non è una preferenza: è la fonte più precisa che abbiamo sulla
    loro logistica. Per questo sta fuori da `oro` — che l'intervista affina e
    l'utente riscrive — e nessuno lo tocca dopo la lettura.

    Il file originale non arriva mai qui: il sito lo legge, ne ricava questo
    estratto senza codici di prenotazione, documenti, telefoni e prezzi, e lo
    butta. Al motore serve cosa c'è scritto, non la carta.
    """

    titolo: Optional[str] = None
    giorni: list[GiornoProgramma] = Field(default_factory=list)
    # Tutto quello che non sta nei giorni: alberghi e quartieri, orari di
    # arrivo e partenza, inclusi ed esclusi, note pratiche.
    testo: Optional[str] = None
    # I nomi dei file letti: servono a noi per capire da dove viene cosa.
    fonti: list[str] = Field(default_factory=list)


class Brief(BaseModel):
    brief_id: str
    lingua_guida: str = "it"
    tappe: list[Tappa] = Field(default_factory=list)
    date: DateViaggio = Field(default_factory=DateViaggio)
    viaggiatori: Viaggiatori = Field(default_factory=Viaggiatori)
    stile: Stile = Field(default_factory=Stile)
    passioni: list[Passione] = Field(default_factory=list)
    oro: Oro = Field(default_factory=Oro)
    # Il mezzo di trasporto è un vincolo strutturale (bagaglio, spostamenti,
    # parcheggi, traghetti, tipo di strada), non un dettaglio.
    mezzo: Literal[
        "auto",
        "moto",
        "treno",
        "aereo_e_locale",
        "camper",
        "misto",
    ] = "misto"
    note_mezzo: Optional[str] = None
    programma: Optional[Programma] = None

    @property
    def giorni(self) -> int:
        """Durata del viaggio in giorni: dalle date se presenti, altrimenti notti + 1."""
        if self.date.inizio and self.date.fine:
            return (self.date.fine - self.date.inizio).days + 1
        return sum(t.notti for t in self.tappe) + 1


class ChapterAssignment(BaseModel):
    numero: int
    titolo_provvisorio: str
    tappa: Optional[Tappa] = None
    tipo: Literal[
        "introduzione",
        "contesto",
        "tappa",
        "collegamento",
        "congedo",
        "apparati",
    ] = "tappa"
    budget_parole: int
    # Confine tematico: cosa il capitolo tratta e cosa lascia agli altri, deciso
    # una volta nell'outline per evitare sovrapposizioni tra capitoli.
    confine_tematico: Optional[str] = None
    outline_guida: list[str] = Field(default_factory=list)
    riassunti_precedenti: list[str] = Field(default_factory=list)
