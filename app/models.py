from datetime import date, datetime
from typing import Dict, List, Optional

from pydantic import BaseModel


class ReconciliarResponse(BaseModel):
    dia: date
    casados: int
    novos: int
    ambiguos: int


class MovimentoSemMatchOut(BaseModel):
    empresa: str
    descricao: str
    valor: float
    ficheiro_origem: str


class LinhaSemMatchOut(BaseModel):
    linha: int
    empresa: str
    previsto: float
    imputacao: Optional[str] = None


class DiferencaEmpresaOut(BaseModel):
    empresa: str
    soma_extrato: float
    soma_mapa: float
    diferenca: float


class AuditoriaResponse(BaseModel):
    dia: date
    sem_match_fwd: int
    sem_match_rev: int
    movimentos_sem_match: List[MovimentoSemMatchOut] = []
    linhas_sem_match: List[LinhaSemMatchOut] = []
    movimentos_dia: List[MovimentoSemMatchOut] = []
    soma_extrato: float = 0.0
    soma_mapa: float = 0.0
    diferenca_extrato_mapa: float = 0.0
    diferencas_por_empresa: List[DiferencaEmpresaOut] = []


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
    # sem transferências entre empresas do grupo
    recebimentos_externos: float = 0.0
    pagamentos_externos: float = 0.0


class ResolverAmbiguoRequest(BaseModel):
    linha_id: Optional[int] = None
    resolvido_por: str


class SaldoOut(BaseModel):
    dia: date
    entidade: str
    saldo_contabilistico: Optional[float] = None
    saldo_disponivel: Optional[float] = None

    model_config = {"from_attributes": True}


class SaldoMapaOut(BaseModel):
    entidade: str
    dia: date
    saldo_contabilistico: Optional[float] = None
    saldo_disponivel: Optional[float] = None
    dia_anterior: Optional[date] = None
    saldo_contabilistico_anterior: Optional[float] = None
    saldo_disponivel_anterior: Optional[float] = None
    variacao_contabilistico: Optional[float] = None
    variacao_disponivel: Optional[float] = None
    variacao_pct_contabilistico: Optional[float] = None
    variacao_pct_disponivel: Optional[float] = None


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


class BandaIncertezaOut(BaseModel):
    baixa: List[PontoSerieOut]
    alta: List[PontoSerieOut]


class CashflowSemanalOut(BaseModel):
    semana: str
    dias: int
    recebimentos: float
    pagamentos: float
    liquido: float
    liquido_baixo: float
    liquido_alto: float
    recebimentos_conhecidos: float = 0.0
    pagamentos_conhecidos: float = 0.0


class FluxoConhecidoOut(BaseModel):
    dia: str
    valor: float
    fonte: str  # "renda" | "recorrente"
    empresa: str
    descricao: str


class PrevisaoSaldoOut(BaseModel):
    historico: List[PontoSerieOut]
    previsao: Dict[str, List[PontoSerieOut]]
    banda_incerteza: Optional[BandaIncertezaOut] = None
    # Só em /previsao/saldo-total-cashflow: algumas trajetórias simuladas
    # (ver previsao.py::_simular_saldo) para mostrar a volatilidade real.
    trajetorias_exemplo: Optional[List[List[PontoSerieOut]]] = None
    cashflow_semanal_previsto: Optional[List[CashflowSemanalOut]] = None
    fluxos_conhecidos_previstos: Optional[List[FluxoConhecidoOut]] = None


class PontoCashflowOut(BaseModel):
    dia: str
    recebimentos: float
    pagamentos: float
    liquido: float


class PrevisaoCashflowOut(BaseModel):
    historico: List[PontoCashflowOut]
    previsao: Dict[str, List[PontoSerieOut]]
    importancia_features: Optional[Dict[str, float]] = None
    banda_incerteza: Optional[BandaIncertezaOut] = None


class AvaliacaoModelosOut(BaseModel):
    dias_teste: int
    rmse_por_modelo: Dict[str, float]
    melhor_modelo: Optional[str] = None
    falhas: Dict[str, str]


class RiscoSaldoOut(BaseModel):
    saldo_atual: float
    despesa_media_mensal: float
    taxa_diaria_liquida: float
    dias_autonomia_tendencia: Optional[float] = None
    dias_autonomia_despesa: Optional[float] = None
    zona_atual: str
    zona_prevista: str
    dia_risco: Optional[str] = None


class RiscoRankingItemOut(BaseModel):
    empresa: str
    saldo_atual: float
    despesa_media_mensal: float
    taxa_diaria_liquida: float
    dias_autonomia_tendencia: Optional[float] = None
    dias_autonomia_despesa: Optional[float] = None
    zona_atual: str
    zona_prevista: str
    dia_risco: Optional[str] = None
    saldo_previsto_fim: Optional[float] = None


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
    # o melhor nome de fornecedor (ver services/faturas.normalizar_fornecedores)
    # e de onde veio: extraido | nif | remetente; `fornecedor` fica o original
    fornecedor_normalizado: Optional[str] = None
    fonte_fornecedor: Optional[str] = None

    model_config = {"from_attributes": True}


class ChatRequest(BaseModel):
    pergunta: str


class ChatResponse(BaseModel):
    resposta: str
    ferramentas_usadas: List[str]
    id: Optional[int] = None  # interacoes_assistente.id, para o 👍/👎
    # guardrail de números: valores da resposta que não vieram de nenhuma ferramenta
    numeros_nao_verificados: List[str] = []
