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
    SaldoOut,
    SaldoTotalOut,
)
from app.services.previsao import avaliar_cashflow, avaliar_modelos, prever_cashflow, prever_saldo
from app.services.saldos import consultar_saldo as consultar_saldo_servico
from app.services.saldos import listar_saldos_atuais, registar_saldos_do_dia, saldo_total_geral

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


@router.get("/saldos/{empresa}", response_model=List[SaldoOut])
def consultar_saldo(empresa: str, dia: Optional[date] = None, db: Session = Depends(get_db)):
    return consultar_saldo_servico(db, empresa, dia)


@router.post("/saldos/atualizar/{dia}")
def atualizar_saldos(dia: date, pedido: AtualizarSaldosRequest, db: Session = Depends(get_db)):
    entidades_registadas = registar_saldos_do_dia(db, dia, pedido.pasta_extratos)
    return {"dia": dia, "entidades_registadas": entidades_registadas}
