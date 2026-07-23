"""Contratto dati del sistema: modelli Pydantic v2 per brief e assignment."""

from datetime import date
from typing import Literal, Optional

from pydantic import BaseModel, Field


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


class Oro(BaseModel):
    da_non_perdere: list[str] = Field(default_factory=list)
    da_evitare: list[str] = Field(default_factory=list)
    contesto_emotivo: Optional[str] = None
    note_libere: Optional[str] = None


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
