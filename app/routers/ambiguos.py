from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import CasoAmbiguo
from app.db.session import get_db
from app.models import CasoAmbiguoOut, ResolverAmbiguoRequest
from app.services.reconciliador import resolver_ambiguo

router = APIRouter()


@router.get("/ambiguos", response_model=List[CasoAmbiguoOut])
def listar_ambiguos(db: Session = Depends(get_db)):
    return (
        db.query(CasoAmbiguo)
        .filter(CasoAmbiguo.resolvido_por.is_(None))
        .all()
    )


@router.post("/ambiguos/{caso_id}/resolver", response_model=CasoAmbiguoOut)
def resolver(caso_id: int, pedido: ResolverAmbiguoRequest, db: Session = Depends(get_db)):
    try:
        return resolver_ambiguo(db, caso_id, pedido.linha_id, pedido.resolvido_por)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
