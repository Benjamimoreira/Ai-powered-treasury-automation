from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import CasoAmbiguo
from app.db.session import get_db
from app.models import CasoAmbiguoOut, ResolverAmbiguoRequest
from app.services.llm_resolver import sugerir_resolucao
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


@router.post("/ambiguos/{caso_id}/sugerir", response_model=CasoAmbiguoOut)
def sugerir(caso_id: int, db: Session = Depends(get_db)):
    """Pede ao LLM (com RAG sobre casos parecidos já resolvidos) uma
    proposta de resolução. Só grava a sugestão - nunca aplica nada; a
    decisão final continua a precisar de POST /ambiguos/{id}/resolver."""
    try:
        return sugerir_resolucao(db, caso_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
