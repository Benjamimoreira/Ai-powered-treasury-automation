import os
from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import FileResponse
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import FaturaRecebidaOut, RegistarFaturasRequest, RegistarFaturasResponse
from app.services.faturas import caminho_pdf, importar_e_reportar, listar_faturas, obter_fatura, registar_faturas

router = APIRouter(prefix="/faturas", tags=["faturas"])

FORNECEDORES_RAIZ = os.environ.get("FORNECEDORES_RAIZ")


@router.post("/recebidas", response_model=RegistarFaturasResponse)
def registar(pedido: RegistarFaturasRequest, db: Session = Depends(get_db)):
    return registar_faturas(db, pedido.linhas)


@router.post("/recebidas/importar")
def importar(ano: Optional[int] = None, mes: Optional[int] = None, reportar_sem_novas: bool = True,
             db: Session = Depends(get_db)):
    """Vai buscar as faturas ao Excel mensal do SharePoint (por omissão o mês
    corrente) e regista a corrida na Monitorização como
    importar_faturas_recebidas. O sincronizador chama-o com
    reportar_sem_novas=false, para não encher o histórico de corridas vazias."""
    if (ano is None) != (mes is None):
        raise HTTPException(status_code=422, detail="Indicar ano e mes juntos (ou nenhum).")
    return importar_e_reportar(db, [(ano, mes)] if ano else None, reportar_sem_novas=reportar_sem_novas)


@router.get("/recebidas", response_model=List[FaturaRecebidaOut])
def listar(
    dia: Optional[date] = None,
    desde: Optional[date] = None,
    ate: Optional[date] = None,
    pesquisa: Optional[str] = None,
    limit: int = 200,
    db: Session = Depends(get_db),
):
    from app.db.models import FaturaRecebida
    from app.services.faturas import normalizar_fornecedores
    from app.services.reconciliador import listar_empresas

    faturas = listar_faturas(db, dia, desde, ate, pesquisa, limit)
    nomes = normalizar_fornecedores(faturas, db.query(FaturaRecebida).all(), listar_empresas(db))
    return [
        {**FaturaRecebidaOut.model_validate(f).model_dump(), "fornecedor_normalizado": nome, "fonte_fornecedor": fonte}
        for f, (nome, fonte) in zip(faturas, nomes)
    ]


@router.get("/recebidas/{fatura_id}/pdf")
def pdf(fatura_id: int, db: Session = Depends(get_db)):
    """Serve o PDF por HTTP (em vez de um link file://, que o Chrome
    bloqueia quando navegado a partir de uma página http://)."""
    fatura = obter_fatura(db, fatura_id)
    if fatura is None or not fatura.pdf_relativo:
        raise HTTPException(status_code=404, detail="Fatura sem PDF associado.")

    caminho = caminho_pdf(fatura, FORNECEDORES_RAIZ)
    if caminho is None:
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
