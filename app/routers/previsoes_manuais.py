from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services import previsoes_manuais
from app.services.monitorizacao import pedir_corrida

router = APIRouter(prefix="/previsoes-manuais", tags=["previsoes-manuais"])


class PrevisaoManualRequest(BaseModel):
    dia: int = Field(..., ge=1, le=31)
    mes: int = Field(..., ge=1, le=12)
    ano: int = Field(..., ge=2000, le=2100)
    tipo: str = Field(..., pattern="^(recebimento|pagamento)$")
    descricao: str
    valor: float = Field(..., description="Valor previsto, positivo (o tipo diz se entra ou sai)")
    imputacao: str


class PrevisaoManualEstadoRequest(BaseModel):
    estado: str = Field(..., pattern="^(aplicada|erro)$")
    erro: Optional[str] = None


def _pedir_aplicacao(db: Session) -> str:
    """Melhor esforço, como na previsão mensal: a linha já ficou guardada."""
    try:
        pedir_corrida(db, previsoes_manuais.SCRIPT)
        return "pedido"
    except Exception as e:
        return f"não pedido: {e}"


@router.post("")
def criar_previsao(payload: PrevisaoManualRequest, db: Session = Depends(get_db)):
    """Guarda a linha e pede ao agente do Windows que a escreva na folha do
    dia do Mapa de Pagamentos e Recebimentos."""
    try:
        dia = date(payload.ano, payload.mes, payload.dia)
    except ValueError:
        raise HTTPException(status_code=422, detail=f"Data inválida: {payload.dia:02d}/{payload.mes:02d}/{payload.ano}")
    try:
        previsao = previsoes_manuais.criar(db, dia, payload.tipo, payload.descricao, payload.valor, payload.imputacao)
    except ValueError as e:
        raise HTTPException(status_code=422, detail=str(e))
    return {**previsao, "mapa": _pedir_aplicacao(db)}


@router.get("")
def listar_previsoes(por_aplicar: bool = False, limit: int = 50, db: Session = Depends(get_db)):
    """Mais recentes primeiro; por_aplicar=true: as pendentes e com erro (o
    que o aplicar_previsoes_manuais.py vai buscar)."""
    return {"previsoes": previsoes_manuais.listar(db, por_aplicar=por_aplicar, limit=limit)}


@router.get("/imputacoes")
def listar_imputacoes(tipo: Optional[str] = None, db: Session = Depends(get_db)):
    return {"imputacoes": previsoes_manuais.imputacoes(db, tipo)}


@router.post("/aplicar")
def aplicar(db: Session = Depends(get_db)):
    """Volta a pedir a escrita no Mapa (ex.: depois de um erro por o Mapa
    estar aberto no Excel)."""
    return {"mapa": _pedir_aplicacao(db)}


@router.post("/{previsao_id}/estado")
def marcar_estado(previsao_id: int, payload: PrevisaoManualEstadoRequest, db: Session = Depends(get_db)):
    previsao = previsoes_manuais.marcar(db, previsao_id, payload.estado, payload.erro)
    if previsao is None:
        raise HTTPException(status_code=409, detail="Previsão inexistente ou já aplicada")
    return previsao


@router.delete("/{previsao_id}")
def apagar_previsao(previsao_id: int, db: Session = Depends(get_db)):
    if not previsoes_manuais.apagar(db, previsao_id):
        raise HTTPException(status_code=409, detail="Previsão inexistente ou já aplicada no Mapa")
    return {"apagada": previsao_id}
