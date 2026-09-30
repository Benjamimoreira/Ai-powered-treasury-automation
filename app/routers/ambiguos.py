from typing import List

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.models import CasoAmbiguo, LinhaMapa
from app.db.session import get_db
from app.models import CasoAmbiguoOut, ResolverAmbiguoRequest
from app.services.llm_resolver import sugerir_resolucao
from app.services.reconciliador import resolver_ambiguo

router = APIRouter()


@router.get("/ambiguos", response_model=List[CasoAmbiguoOut])
def listar_ambiguos(db: Session = Depends(get_db)):
    casos = db.query(CasoAmbiguo).filter(CasoAmbiguo.resolvido_por.is_(None)).all()
    resultado = []
    for caso in casos:
        candidatos_detalhe = db.query(LinhaMapa).filter(LinhaMapa.id.in_(caso.candidatos or [])).all()
        resultado.append(CasoAmbiguoOut(
            id=caso.id, dia=caso.dia, empresa=caso.empresa, valor=caso.valor,
            candidatos=caso.candidatos, candidatos_detalhe=candidatos_detalhe,
            resolvido_por=caso.resolvido_por, resolucao=caso.resolucao,
            resolucao_sugerida=caso.resolucao_sugerida, justificacao_sugerida=caso.justificacao_sugerida,
        ))
    return resultado


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


@router.post("/ambiguos/{caso_id}/investigar")
def investigar(caso_id: int, db: Session = Depends(get_db)):
    """Põe o agente de investigação (app/services/agente_ambiguos.py) a
    preparar um dossier para o caso: histórico da empresa, movimentos com
    descritivo parecido, faturas com o mesmo valor, recomendação e
    alertas. Grava o dossier e devolve-o - não resolve o caso (a decisão
    continua a ser POST /ambiguos/{id}/resolver, feita por uma pessoa)."""
    from app.services.agente_ambiguos import investigar_caso

    try:
        registo = investigar_caso(db, caso_id)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return {"id": registo.id, "caso_id": registo.caso_id, "criado_em": registo.criado_em.isoformat(),
            "fornecedor": registo.fornecedor, "modelo": registo.modelo, **registo.dossier}


@router.get("/ambiguos/{caso_id}/dossiers")
def listar_dossiers(caso_id: int, db: Session = Depends(get_db)):
    """Dossiers já preparados pelo agente para um caso, mais recente primeiro -
    o registo de que modelo recomendou o quê e quando."""
    from app.db.models import DossierAmbiguo

    registos = (
        db.query(DossierAmbiguo).filter(DossierAmbiguo.caso_id == caso_id)
        .order_by(DossierAmbiguo.criado_em.desc()).all()
    )
    return [
        {"id": r.id, "criado_em": r.criado_em.isoformat(), "fornecedor": r.fornecedor, "modelo": r.modelo,
         "linha_id_recomendada": r.linha_id_recomendada, "confianca": r.confianca, **r.dossier}
        for r in registos
    ]
