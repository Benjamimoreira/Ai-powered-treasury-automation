from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.monitorizacao import listar_logs, listar_scripts, registar_execucao

router = APIRouter(prefix="/monitorizacao", tags=["monitorizacao"])


class ExecucaoScriptRequest(BaseModel):
    status: str = Field(..., description="ok|erro|warning")
    erro: Optional[str] = None
    log: Optional[List[str]] = None
    duracao_segundos: Optional[float] = None


@router.get("/scripts")
def listar_status_scripts(db: Session = Depends(get_db)):
    return {"scripts": listar_scripts(db)}


@router.post("/scripts/{script}/executar")
def executar_script(script: str, payload: ExecucaoScriptRequest, db: Session = Depends(get_db)):
    try:
        return registar_execucao(db, script, payload.status, erro=payload.erro, log=payload.log, duracao_segundos=payload.duracao_segundos)
    except Exception as exc:  # pragma: no cover - não deve acontecer nesta camada
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/logs")
def listar_monitorizacao_logs(limit: int = 50, db: Session = Depends(get_db)):
    return {"logs": listar_logs(db, limit=limit)}
