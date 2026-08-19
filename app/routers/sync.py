from datetime import date

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AtualizarDadosResponse
from app.services.onedrive_sync import atualizar_dados_do_dia, atualizar_dados_recentes

router = APIRouter()


@router.post("/atualizar-dados", response_model=AtualizarDadosResponse)
def atualizar_dados(dias_atras: int = 7, db: Session = Depends(get_db)):
    """Importa, a partir do OneDrive (só leitura), os dias recentes que
    ainda não existem localmente: movimentos, linhas do mapa e saldos.
    Seguro chamar repetidamente - dias já importados são ignorados."""
    try:
        return atualizar_dados_recentes(db, dias_atras)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/atualizar-dados/{dia}")
def atualizar_dados_dia(dia: date, db: Session = Depends(get_db)):
    """Importa um dia específico (qualquer mês/ano), a partir do OneDrive
    (só leitura) - usado pelo dashboard quando o utilizador escolhe uma
    data fora da janela dos últimos 7 dias que /atualizar-dados cobre.
    Seguro chamar repetidamente - dados já importados são ignorados."""
    try:
        return atualizar_dados_do_dia(db, dia)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
