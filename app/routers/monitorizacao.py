from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.monitorizacao import correr_script, listar_eventos, listar_logs, listar_scripts, registar_evento, registar_execucao
from app.services.monitorizacao_ia import metricas_ia

router = APIRouter(prefix="/monitorizacao", tags=["monitorizacao"])


class ExecucaoScriptRequest(BaseModel):
    status: str = Field(..., description="ok|erro|warning")
    erro: Optional[str] = None
    log: Optional[List[str]] = None
    duracao_segundos: Optional[float] = None


class EventoScriptRequest(BaseModel):
    nivel: str = Field(..., description="erro|aviso|info")
    mensagem: str


@router.get("/scripts")
def listar_status_scripts(db: Session = Depends(get_db)):
    return {"scripts": listar_scripts(db)}


@router.post("/scripts/{script}/executar")
def executar_script(script: str, payload: ExecucaoScriptRequest, db: Session = Depends(get_db)):
    try:
        return registar_execucao(db, script, payload.status, erro=payload.erro, log=payload.log, duracao_segundos=payload.duracao_segundos)
    except Exception as exc:  # pragma: no cover - não deve acontecer nesta camada
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/scripts/{script}/correr")
def correr_script_endpoint(script: str):
    """Dispara a execução do script na máquina onde a API corre nativamente
    (fora do Docker) - usado pelo botão "Correr" do dashboard para scripts
    em falha/atrasados. O resultado da corrida chega depois pelo caminho
    normal, POST /scripts/{script}/executar, enviado pelo próprio script."""
    try:
        return correr_script(script)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/logs")
def listar_monitorizacao_logs(limit: int = 50, dia: Optional[date] = None, db: Session = Depends(get_db)):
    return {"logs": listar_logs(db, limit=limit, dia=dia)}


@router.post("/scripts/{script}/eventos")
def registar_evento_script(script: str, payload: EventoScriptRequest, db: Session = Depends(get_db)):
    """Recebe um evento de log em tempo real (um [ERRO]/[AVISO] apanhado
    a meio de uma corrida ainda a decorrer) - ver _HandlerEventoDashboard
    em monitorizacao_client.py. Separado de /scripts/{script}/executar,
    que só reporta o resultado final da corrida toda."""
    try:
        return registar_evento(db, script, payload.nivel, payload.mensagem)
    except Exception as exc:  # pragma: no cover - não deve acontecer nesta camada
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/eventos")
def listar_monitorizacao_eventos(limit: int = 50, script: Optional[str] = None, db: Session = Depends(get_db)):
    return {"eventos": listar_eventos(db, limit=limit, script=script)}


@router.get("/ia")
def metricas_qualidade_ia(dias: int = 30, db: Session = Depends(get_db)):
    """Qualidade da IA em produção nos últimos `dias`: Assistente (perguntas,
    erros, 👍/👎, tempos) e sugestões para casos ambíguos (aceites vs
    rejeitadas, decididas por regras vs LLM, resolvidas sem sugestão) - ver
    app/services/monitorizacao_ia.py."""
    return metricas_ia(db, dias=dias)
