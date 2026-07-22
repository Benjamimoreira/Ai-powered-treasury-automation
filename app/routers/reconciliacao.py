from datetime import date
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AuditoriaResponse, MovimentoStatusOut, ReconciliarResponse
from app.services.reconciliador import auditoria_dia, listar_movimentos_do_dia, reconciliar_dia

router = APIRouter()


@router.post("/reconciliar/{dia}", response_model=ReconciliarResponse)
def reconciliar(dia: date, db: Session = Depends(get_db)):
    resultado = reconciliar_dia(db, dia)
    return ReconciliarResponse(dia=dia, **resultado)


@router.get("/auditoria/{dia}", response_model=AuditoriaResponse)
def auditoria(dia: date, db: Session = Depends(get_db)):
    resultado = auditoria_dia(db, dia)
    return AuditoriaResponse(dia=dia, **resultado)


@router.get("/movimentos/{dia}", response_model=List[MovimentoStatusOut])
def movimentos_do_dia(dia: date, db: Session = Depends(get_db)):
    """Só leitura - mostra o estado atual de cada movimento do dia. Podes
    chamar isto quantas vezes quiseres, mesmo depois de já teres corrido
    /reconciliar, sem duplicar nem reprocessar nada."""
    return listar_movimentos_do_dia(db, dia)
