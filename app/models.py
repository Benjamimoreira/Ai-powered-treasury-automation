from datetime import date
from typing import List, Optional

from pydantic import BaseModel


class ReconciliarResponse(BaseModel):
    dia: date
    casados: int
    novos: int
    ambiguos: int


class AuditoriaResponse(BaseModel):
    dia: date
    sem_match_fwd: int
    sem_match_rev: int


class CasoAmbiguoOut(BaseModel):
    id: int
    dia: date
    empresa: str
    valor: float
    candidatos: Optional[List[int]] = None
    resolvido_por: Optional[str] = None
    resolucao: Optional[str] = None

    model_config = {"from_attributes": True}


class ResolverAmbiguoRequest(BaseModel):
    linha_id: Optional[int] = None
    resolvido_por: str


class SaldoOut(BaseModel):
    dia: date
    entidade: str
    saldo_contabilistico: Optional[float] = None
    saldo_disponivel: Optional[float] = None

    model_config = {"from_attributes": True}


class AtualizarSaldosRequest(BaseModel):
    pasta_extratos: str
