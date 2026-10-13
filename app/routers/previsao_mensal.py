import base64
import binascii

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.monitorizacao import pedir_corrida
from app.services.previsao_mensal import importar, resumo

router = APIRouter(prefix="/previsao-mensal", tags=["previsao-mensal"])


class ImportarPrevisaoRequest(BaseModel):
    ficheiro: str = Field(..., description="Nome do ficheiro (ex. \"Previsão Tesouraria_OUT2026.xlsx\")")
    conteudo_base64: str = Field(..., description="O .xlsx em base64")


@router.post("/importar")
def importar_previsao(payload: ImportarPrevisaoRequest, db: Session = Depends(get_db)):
    """Carrega o ficheiro de previsão de tesouraria (substitui os meses que
    ele tem) - ver app/services/previsao_mensal.py."""
    try:
        conteudo = base64.b64decode(payload.conteudo_base64, validate=True)
    except (binascii.Error, ValueError):
        raise HTTPException(status_code=422, detail="conteudo_base64 inválido")
    try:
        resultado = importar(db, conteudo, payload.ficheiro)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    except Exception as e:  # ficheiro que não é .xlsx
        raise HTTPException(status_code=422, detail=f"Não consegui ler o ficheiro: {e}")
    # a folha "Previsão" de cada Mapa mensal: o agente do Windows corre o
    # aplicar_previsao_mensal.py (PT2) - melhor esforço, a importação já ficou
    try:
        pedir_corrida(db, "aplicar_previsao_mensal")
        resultado["mapas"] = "pedido"
    except Exception as e:
        resultado["mapas"] = f"não pedido: {e}"
    return resultado


@router.get("")
def obter_previsao(db: Session = Depends(get_db)):
    """Previsão por mês, com o realizado do Mapa ao lado."""
    return resumo(db)
