from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AtualizarSaldosRequest, AvaliacaoModelosOut, PrevisaoSaldoOut, SaldoOut, SaldoTotalOut
from app.services.previsao import avaliar_modelos, prever_saldo
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
def saldo_total(db: Session = Depends(get_db)):
    """Soma o último saldo conhecido de cada entidade - visão geral, não
    de um único dia (nem todos os dias têm leitura de todas as contas)."""
    return saldo_total_geral(db)


@router.get("/saldos", response_model=List[SaldoOut])
def saldos_atuais(db: Session = Depends(get_db)):
    """Último saldo conhecido de cada entidade - para rankings/gráficos."""
    return listar_saldos_atuais(db)


@router.get("/saldos/{empresa}", response_model=List[SaldoOut])
def consultar_saldo(empresa: str, dia: Optional[date] = None, db: Session = Depends(get_db)):
    return consultar_saldo_servico(db, empresa, dia)


@router.post("/saldos/atualizar/{dia}")
def atualizar_saldos(dia: date, pedido: AtualizarSaldosRequest, db: Session = Depends(get_db)):
    entidades_registadas = registar_saldos_do_dia(db, dia, pedido.pasta_extratos)
    return {"dia": dia, "entidades_registadas": entidades_registadas}
