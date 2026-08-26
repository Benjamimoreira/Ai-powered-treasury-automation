import os
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import FaturaRecebidaOut, RegistarFaturasRequest, RegistarFaturasResponse
from app.services.faturas import listar_faturas, obter_fatura, registar_faturas

router = APIRouter(prefix="/faturas", tags=["faturas"])

FORNECEDORES_RAIZ = os.environ.get("FORNECEDORES_RAIZ")


@router.post("/recebidas", response_model=RegistarFaturasResponse)
def registar(pedido: RegistarFaturasRequest, db: Session = Depends(get_db)):
    return registar_faturas(db, pedido.linhas)


@router.get("/recebidas", response_model=List[FaturaRecebidaOut])
def listar(dia: Optional[date] = None, pesquisa: Optional[str] = None, limit: int = 200, db: Session = Depends(get_db)):
    return listar_faturas(db, dia, pesquisa, limit)


@router.get("/recebidas/{fatura_id}/pdf")
def pdf(fatura_id: int, db: Session = Depends(get_db)):
    """Serve o PDF por HTTP (em vez de um link file://, que o Chrome
    bloqueia quando navegado a partir de uma página http://)."""
    if not FORNECEDORES_RAIZ:
        raise HTTPException(status_code=503, detail="FORNECEDORES_RAIZ não configurado neste ambiente.")

    fatura = obter_fatura(db, fatura_id)
    if fatura is None or not fatura.pdf_relativo:
        raise HTTPException(status_code=404, detail="Fatura sem PDF associado.")

    raiz = os.path.realpath(FORNECEDORES_RAIZ)
    # pdf_relativo é gravado com barras invertidas (Windows, de onde vem o
    # script) - num container Linux "\" não é separador de caminho, por
    # isso sem isto o ficheiro nunca era encontrado
    relativo_posix = fatura.pdf_relativo.replace("\\", "/")
    caminho = os.path.realpath(os.path.join(raiz, relativo_posix))
    # nunca sair da pasta montada (defesa contra pdf_relativo malformado)
    if os.path.commonpath([raiz, caminho]) != raiz or not os.path.isfile(caminho):
        raise HTTPException(status_code=404, detail="Ficheiro PDF não encontrado.")

    # content_disposition_type="inline" - com filename mas sem isto, o
    # FileResponse manda "attachment" por omissão e o browser descarrega o
    # PDF em silêncio em vez de o abrir/mostrar (bug reportado: "não abre")
    return FileResponse(
        caminho,
        media_type="application/pdf",
        filename=os.path.basename(caminho),
        content_disposition_type="inline",
    )
