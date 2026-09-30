from datetime import date
from typing import Optional

from fastapi import APIRouter

from app.services.comercial import listar_cpcv_escrituras, mapa_espaco_fracao_por_ref

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
