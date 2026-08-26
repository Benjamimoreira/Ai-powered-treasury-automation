from datetime import date, datetime
from typing import Dict, List, Optional

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


class CandidatoLinhaOut(BaseModel):
    id: int
    linha: int
    tipo: str
    empresa: str
    previsto: Optional[float] = None
    imputacao: Optional[str] = None

    model_config = {"from_attributes": True}


class CasoAmbiguoOut(BaseModel):
    id: int
    dia: date
    empresa: str
    valor: float
    candidatos: Optional[List[int]] = None
    candidatos_detalhe: Optional[List[CandidatoLinhaOut]] = None
    resolvido_por: Optional[str] = None
    resolucao: Optional[str] = None
    resolucao_sugerida: Optional[str] = None
    justificacao_sugerida: Optional[str] = None

    model_config = {"from_attributes": True}


class MovimentoStatusOut(BaseModel):
    id: int
    empresa: str
    descricao: str
    valor: float
    tipo_match: Optional[str] = None
    linha_id: Optional[int] = None
    linha_imputacao: Optional[str] = None


class MovimentoHistoricoOut(BaseModel):
    id: int
    dia: date
    descricao: str
    valor: float


class ResumoDiarioOut(BaseModel):
    dia: date
    recebimentos: float
    pagamentos: float


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


class SaldoTotalOut(BaseModel):
    entidades: int
    saldo_contabilistico_total: float
    saldo_disponivel_total: float


class AtualizarDadosResponse(BaseModel):
    dias_verificados: int
    dias_com_movimentos_novos: List[str]
    dias_com_saldos_novos: List[str]
    dias_com_mapa_novo: List[str]
    erros: List[str]


class AnomaliaOut(BaseModel):
    id: int
    dia: date
    empresa: str
    descricao: str
    valor: float


class PontoSerieOut(BaseModel):
    dia: str
    valor: float


class PrevisaoSaldoOut(BaseModel):
    historico: List[PontoSerieOut]
    previsao: Dict[str, List[PontoSerieOut]]


class PontoCashflowOut(BaseModel):
    dia: str
    recebimentos: float
    pagamentos: float
    liquido: float


class PrevisaoCashflowOut(BaseModel):
    historico: List[PontoCashflowOut]
    previsao: Dict[str, List[PontoSerieOut]]
    importancia_features: Optional[Dict[str, float]] = None


class AvaliacaoModelosOut(BaseModel):
    dias_teste: int
    rmse_por_modelo: Dict[str, float]
    melhor_modelo: Optional[str] = None
    falhas: Dict[str, str]


class FaturaRecebidaIn(BaseModel):
    outlook_id: str
    data_recebido: datetime
    remetente: Optional[str] = None
    assunto: Optional[str] = None
    motivo: Optional[str] = None
    empresa: Optional[str] = None
    fornecedor: Optional[str] = None
    nif_fornecedor: Optional[str] = None
    n_anexos_pdf: Optional[int] = None
    debito: Optional[str] = None
    credito: Optional[str] = None
    saldo: Optional[str] = None
    valor_fatura: Optional[str] = None
    pdf_relativo: Optional[str] = None


class RegistarFaturasRequest(BaseModel):
    linhas: List[FaturaRecebidaIn]


class RegistarFaturasResponse(BaseModel):
    novas: int
    duplicadas: int


class FaturaRecebidaOut(BaseModel):
    id: int
    outlook_id: str
    dia: date
    hora: Optional[str] = None
    remetente: Optional[str] = None
    assunto: Optional[str] = None
    motivo: Optional[str] = None
    empresa: Optional[str] = None
    fornecedor: Optional[str] = None
    nif_fornecedor: Optional[str] = None
    n_anexos_pdf: Optional[int] = None
    debito: Optional[str] = None
    credito: Optional[str] = None
    saldo: Optional[str] = None
    valor_fatura: Optional[str] = None
    pdf_relativo: Optional[str] = None

    model_config = {"from_attributes": True}


class ChatRequest(BaseModel):
    pergunta: str


class ChatResponse(BaseModel):
    resposta: str
    ferramentas_usadas: List[str]
