from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.rendas import detalhe_rendas

router = APIRouter(prefix="/rendas", tags=["rendas"])


@router.get("")
def rendas(empresa: Optional[str] = None, ate: Optional[date] = None, db: Session = Depends(get_db)):
    """Contratos do Mapa de Rendas com o estado de cada mês do ano de `ate`
    (pago no extrato, por registar no Mapa, em falta...), os recebimentos
    identificados pelo nome do arrendatário e pelo valor, e um resumo - ver
    app/services/rendas.py."""
    return detalhe_rendas(db, empresa, ate)
