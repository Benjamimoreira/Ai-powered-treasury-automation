from datetime import date
from typing import List

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AnomaliaOut
from app.services.anomalias import detetar_anomalias_do_dia

router = APIRouter()


@router.get("/anomalias/{dia}", response_model=List[AnomaliaOut])
def anomalias_do_dia(dia: date, contaminacao: float = 0.1, db: Session = Depends(get_db)):
    """Deteção de anomalias (Isolation Forest, por empresa) - assinala
    movimentos do dia com valor fora do padrão habitual da própria
    empresa. Só leitura, não altera nada. Empresas com histórico
    insuficiente são ignoradas."""
    return detetar_anomalias_do_dia(db, dia, contaminacao)
