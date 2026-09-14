from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import (
    AtualizarSaldosRequest,
    AvaliacaoModelosOut,
    PrevisaoCashflowOut,
    PrevisaoSaldoOut,
    RiscoRankingItemOut,
    RiscoSaldoOut,
    SaldoMapaOut,
    SaldoOut,
    SaldoTotalOut,
)
from app.services.previsao import (
    avaliar_cashflow,
    avaliar_modelos,
    avaliar_zona_risco,
    listar_ranking_risco,
    prever_cashflow,
    prever_saldo,
)
from app.services.saldos import consultar_saldo as consultar_saldo_servico
from app.services.saldos import (
    listar_saldos_atuais,
    mapa_saldos,
    registar_saldos_do_dia,
    saldo_total_geral,
    serie_saldo_total,
)

router = APIRouter()


@router.get("/previsao/saldo/{empresa}", response_model=PrevisaoSaldoOut)
def previsao_saldo(empresa: str, dias: int = 7, db: Session = Depends(get_db)):
    """Previsão do saldo contabilístico dos próximos dias, com vários
    modelos (regressão linear, média móvel, suavização exponencial,
    ARIMA, Markov-switching) para comparação lado a lado. Só leitura, não
    guarda nada."""
    try:
        return prever_saldo(db, empresa, dias)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/previsao/saldo-total", response_model=PrevisaoSaldoOut)
def previsao_saldo_total(dias: int = 7, db: Session = Depends(get_db)):
    """Previsão do saldo total (riqueza da empresa como um todo - soma do
    saldo de todas as contas), com os mesmos modelos de `/previsao/saldo/
    {empresa}` (incluindo ARIMA). Path próprio (em vez de um `empresa`
    especial) para não colidir com `/previsao/saldo/{empresa}`."""
    try:
        return prever_saldo(db, None, dias)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/previsao/saldo-total-avaliacao", response_model=AvaliacaoModelosOut)
def previsao_saldo_total_avaliacao(dias_teste: int = 5, db: Session = Depends(get_db)):
    """Avaliação treino/teste (RMSE) do saldo total, equivalente a
    `/previsao/avaliacao/{empresa}` para uma entidade só."""
    try:
        return avaliar_modelos(db, None, dias_teste)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/previsao/cashflow", response_model=PrevisaoCashflowOut)
def previsao_cashflow_agregada(dias: int = 7, db: Session = Depends(get_db)):
    """Previsão do cash-flow líquido diário (recebimentos - pagamentos)
    agregado de todas as entidades, com os mesmos modelos da previsão de
    saldo. Ao contrário do saldo (plano na maioria dos dias), varia todos
    os dias - previsão mais percetível para a tesouraria como um todo."""
    try:
        return prever_cashflow(db, None, dias)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/previsao/cashflow/{empresa}", response_model=PrevisaoCashflowOut)
def previsao_cashflow_empresa(empresa: str, dias: int = 7, db: Session = Depends(get_db)):
    """Mesma previsão de cash-flow, restrita a uma entidade."""
    try:
        return prever_cashflow(db, empresa, dias)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/previsao/cashflow-avaliacao", response_model=AvaliacaoModelosOut)
def previsao_cashflow_avaliacao(
    empresa: Optional[str] = None, dias_teste: int = 5, db: Session = Depends(get_db)
):
    """Avaliação treino/teste (RMSE) para o cash-flow, equivalente a
    `/previsao/avaliacao/{empresa}` para o saldo. `empresa` omitida =
    carteira agregada. Path próprio (em vez de `/previsao/cashflow/avaliacao`)
    para não colidir com `/previsao/cashflow/{empresa}`."""
    try:
        return avaliar_cashflow(db, empresa, dias_teste)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/previsao/avaliacao/{empresa}", response_model=AvaliacaoModelosOut)
def previsao_avaliacao(empresa: str, dias_teste: int = 5, db: Session = Depends(get_db)):
    """Avaliação treino/teste: retira os últimos `dias_teste` dias,
    treina cada modelo só com o resto, e compara com o valor real
    (RMSE) - responde a "qual modelo acerta mais" com dados retidos, não
    só com a previsão visual."""
    try:
        return avaliar_modelos(db, empresa, dias_teste)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/previsao/risco-ranking", response_model=List[RiscoRankingItemOut])
def previsao_risco_ranking(dias: int = 30, db: Session = Depends(get_db)):
    """Ranking de todas as empresas por zona de risco do saldo atual
    (crítico primeiro), com a previsão a `dias` dias já aplicada por
    empresa (zona_prevista/saldo_previsto_fim) - para a tabela da aba
    Análise de Contas."""
    return listar_ranking_risco(db, dias)


@router.get("/previsao/risco/{empresa}", response_model=RiscoSaldoOut)
def previsao_risco(empresa: str, dias: int = 7, db: Session = Depends(get_db)):
    """Classifica a saúde do saldo de `empresa` em zonas (ok/alerta/crítico)
    com base na despesa mensal média, e avisa com antecedência se a
    previsão de saldo (ensemble) vai entrar numa zona pior nos próximos
    `dias` dias - para o aviso de "zona de risco" no dashboard."""
    try:
        return avaliar_zona_risco(db, empresa, dias)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))


@router.get("/saldo-total", response_model=SaldoTotalOut)
def saldo_total(dia: Optional[date] = None, db: Session = Depends(get_db)):
    """Soma o último saldo conhecido de cada entidade até `dia` (ou o mais
    recente de sempre, sem `dia`) - nem todos os dias têm leitura de todas
    as contas, por isso não é simplesmente a soma das leituras desse dia."""
    return saldo_total_geral(db, dia)


@router.get("/saldos", response_model=List[SaldoOut])
def saldos_atuais(dia: Optional[date] = None, db: Session = Depends(get_db)):
    """Último saldo conhecido de cada entidade até `dia` (ou o mais recente
    de sempre, sem `dia`) - para rankings/gráficos."""
    return listar_saldos_atuais(db, dia)


@router.get("/saldos/mapa", response_model=List[SaldoMapaOut])
def saldos_mapa(dia: Optional[date] = None, db: Session = Depends(get_db)):
    """Mapa de saldos de todas as entidades num dia, lado a lado com a
    leitura anterior de cada conta (valor e variação %) - para ver de
    relance quem teve entrada/saída de dinheiro. Rota definida antes de
    `/saldos/{empresa}` para não ser apanhada por esse path param."""
    return mapa_saldos(db, dia)


@router.get("/saldos/serie-total")
def saldos_serie_total(db: Session = Depends(get_db)):
    """Evolução do saldo total (todas as entidades juntas) ao longo do
    tempo - para o gráfico da Visão Geral. Rota definida antes de
    `/saldos/{empresa}` para não ser apanhada por esse path param."""
    return {"serie": serie_saldo_total(db)}


@router.get("/saldos/{empresa}", response_model=List[SaldoOut])
def consultar_saldo(empresa: str, dia: Optional[date] = None, db: Session = Depends(get_db)):
    return consultar_saldo_servico(db, empresa, dia)


@router.post("/saldos/atualizar/{dia}")
def atualizar_saldos(dia: date, pedido: AtualizarSaldosRequest, db: Session = Depends(get_db)):
    entidades_registadas = registar_saldos_do_dia(db, dia, pedido.pasta_extratos)
    return {"dia": dia, "entidades_registadas": entidades_registadas}
