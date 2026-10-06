from datetime import date
from typing import Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db

from app.services.comercial import listar_cpcv_escrituras, mapa_espaco_fracao_por_ref
from app.services.vendas import detalhe_negocios

router = APIRouter(prefix="/comercial", tags=["comercial"])


@router.get("/cpcv")
def cpcv_escrituras(dia_inicio: Optional[date] = None, dia_fim: Optional[date] = None):
    """CPCVs/Escrituras do índice comercial (SharePoint, ver
    app/services/comercial.py) assinados no período - para a tabela do
    início da aba "Análise de Extratos" do dashboard."""
    return {"linhas": listar_cpcv_escrituras(dia_inicio, dia_fim)}


@router.get("/espaco-fracao-por-ref")
def espaco_fracao_por_ref():
    """Todo o índice comercial reduzido a {REF: espaço físico/fração}, sem
    filtro de data - para a tabela "CPCVs/Escrituras do período" (Mapa de
    Pagamentos e Recebimentos) enriquecer cada linha com o espaço físico e
    a fração a que a referência (ex. "00.PO.23.035") corresponde."""
    return {"por_ref": mapa_espaco_fracao_por_ref()}


@router.get("/negocios")
def negocios(empresa: Optional[str] = None, dia_inicio: Optional[date] = None, dia_fim: Optional[date] = None,
             db: Session = Depends(get_db)):
    """Negócios do índice comercial com a agenda de pagamentos (sinal,
    reforços, escritura), o que já entrou segundo o Mapa (pela REF), o que
    falta e o próximo pagamento, mais um resumo e as vendas por mês - ver
    app/services/vendas.py. Um negócio entra se o CPCV ou algum pagamento
    cair no período."""
    return detalhe_negocios(db, empresa, dia_inicio, dia_fim)
