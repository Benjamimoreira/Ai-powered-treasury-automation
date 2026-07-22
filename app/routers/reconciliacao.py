from datetime import date

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AuditoriaResponse, ReconciliarResponse
from app.services.reconciliador import auditoria_dia, reconciliar_dia

router = APIRouter()


@router.post("/reconciliar/{dia}", response_model=ReconciliarResponse)
def reconciliar(dia: date, db: Session = Depends(get_db)):
    resultado = reconciliar_dia(db, dia)
    return ReconciliarResponse(dia=dia, **resultado)


@router.get("/auditoria/{dia}", response_model=AuditoriaResponse)
def auditoria(dia: date, db: Session = Depends(get_db)):
    resultado = auditoria_dia(db, dia)
    return AuditoriaResponse(dia=dia, **resultado)
