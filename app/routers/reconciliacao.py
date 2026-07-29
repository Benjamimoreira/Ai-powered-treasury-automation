from datetime import date
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AuditoriaResponse, MovimentoHistoricoOut, MovimentoStatusOut, ReconciliarResponse, ResumoDiarioOut
from app.services.reconciliador import (
    auditoria_dia,
    listar_empresas,
    listar_movimentos_da_empresa,
    listar_movimentos_do_dia,
    reconciliar_dia,
    resumo_diario,
)

router = APIRouter()


@router.post("/reconciliar/{dia}", response_model=ReconciliarResponse)
def reconciliar(dia: date, db: Session = Depends(get_db)):
    resultado = reconciliar_dia(db, dia)
    return ReconciliarResponse(dia=dia, **resultado)


@router.get("/auditoria/{dia}", response_model=AuditoriaResponse)
def auditoria(dia: date, db: Session = Depends(get_db)):
    resultado = auditoria_dia(db, dia)
    return AuditoriaResponse(dia=dia, **resultado)


@router.get("/movimentos/resumo-diario", response_model=List[ResumoDiarioOut])
def movimentos_resumo_diario(db: Session = Depends(get_db)):
    """Totais de recebimentos/pagamentos por dia, somados por todas as
    empresas - para o gráfico de fluxo mensal da Visão Geral. Registada
    antes de /movimentos/{dia} de propósito: sendo os dois de um único
    segmento, a rota registada primeiro é que ganha (senão "resumo-diario"
    seria interpretado como uma data e falhava)."""
    return resumo_diario(db)


@router.get("/movimentos/{dia}", response_model=List[MovimentoStatusOut])
def movimentos_do_dia(dia: date, db: Session = Depends(get_db)):
    """Só leitura - mostra o estado atual de cada movimento do dia. Podes
    chamar isto quantas vezes quiseres, mesmo depois de já teres corrido
    /reconciliar, sem duplicar nem reprocessar nada."""
    return listar_movimentos_do_dia(db, dia)


@router.get("/empresas", response_model=List[str])
def empresas(db: Session = Depends(get_db)):
    """Lista as empresas com movimentos importados - para preencher
    seletores (ex. no dashboard) sem adivinhar o nome exato."""
    return listar_empresas(db)


@router.get("/movimentos/empresa/{empresa}", response_model=List[MovimentoHistoricoOut])
def movimentos_da_empresa(empresa: str, db: Session = Depends(get_db)):
    """Histórico de movimentos de uma empresa (todos os dias importados),
    ordenado por dia - para analisar o fluxo de uma conta ao longo do
    tempo."""
    return listar_movimentos_da_empresa(db, empresa)
