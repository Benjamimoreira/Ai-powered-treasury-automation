from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AtualizarDadosResponse
from app.services.onedrive_sync import atualizar_dados_recentes

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
