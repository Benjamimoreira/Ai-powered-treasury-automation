from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import LinhaMapa, MovimentoBancario
from app.db.session import get_db
from app.models import AuditoriaResponse, MovimentoHistoricoOut, MovimentoStatusOut, ReconciliarResponse, ResumoDiarioOut
from app.services.reconciliador import (
    analise_imputacoes,
    auditoria_dia,
    listar_empresas,
    listar_historico_auditorias,
    listar_linhas_imputacao,
    listar_movimentos_da_empresa,
    listar_movimentos_do_dia,
    reconciliar_dia,
    registar_auditoria_dia,
    resumo_diario,
)

router = APIRouter()


@router.post("/reconciliar/{dia}", response_model=ReconciliarResponse)
def reconciliar(dia: date, db: Session = Depends(get_db)):
    resultado = reconciliar_dia(db, dia)
    return ReconciliarResponse(dia=dia, **resultado)


# Os dois endpoints seguintes ("geral" e "historico") têm de ficar
# registados ANTES de "/auditoria/{dia}" (mesmo motivo do
# "resumo-diario" acima de "/movimentos/{dia}"): senão o Starlette tenta
# casar "geral"/"historico" com o parâmetro {dia} (tipo date) e falha com
# 422 antes de chegar aqui.
@router.post("/auditoria/geral")
def auditoria_geral(dias_atras: int = 31, db: Session = Depends(get_db)):
    """'Fazer tudo de novo': sincroniza os últimos `dias_atras` dias a
    partir do OneDrive e depois corre + regista (auditorias_dia) a
    auditoria de TODOS os dias que já têm movimentos ou linhas do mapa
    importados - não precisas de ir dia a dia no dashboard."""
    from app.services.onedrive_sync import atualizar_dados_recentes  # import tardio: onedrive_sync já importa deste módulo

    atualizar_dados_recentes(db, dias_atras)
    dias = sorted(
        {d for (d,) in db.query(MovimentoBancario.dia).distinct().all()}
        | {d for (d,) in db.query(LinhaMapa.dia).distinct().all()}
    )
    resultados = {}
    for dia in dias:
        try:
            r = registar_auditoria_dia(db, dia)
            resultados[dia.isoformat()] = {
                "sem_match_fwd": r["sem_match_fwd"],
                "sem_match_rev": r["sem_match_rev"],
                "diferenca_extrato_mapa": r["diferenca_extrato_mapa"],
            }
        except Exception as e:
            db.rollback()
            resultados[dia.isoformat()] = {"erro": str(e)}
    return {"dias_auditados": len(resultados), "resultados": resultados}


@router.get("/auditoria/historico")
def auditoria_historico(limit: int = 100, db: Session = Depends(get_db)):
    return {"historico": listar_historico_auditorias(db, limit=limit)}


@router.post("/auditoria/{dia}/registar", response_model=AuditoriaResponse)
def auditoria_registar(dia: date, db: Session = Depends(get_db)):
    """Como GET /auditoria/{dia}, mas primeiro sincroniza esse dia a
    partir do OneDrive (para não auditar dados velhos) e depois grava o
    resultado em auditorias_dia (histórico persistente) - chamada apenas
    pelo botão "Auditar este dia" do dashboard; não há atualmente nenhuma
    chamada automática a partir de scripts externos."""
    from app.services.onedrive_sync import atualizar_dados_do_dia  # import tardio: onedrive_sync já importa deste módulo

    atualizar_dados_do_dia(db, dia)
    resultado = registar_auditoria_dia(db, dia)
    return AuditoriaResponse(dia=dia, **resultado)


@router.get("/auditoria/{dia}", response_model=AuditoriaResponse)
def auditoria(dia: date, db: Session = Depends(get_db)):
    resultado = auditoria_dia(db, dia)
    return AuditoriaResponse(dia=dia, **resultado)


@router.get("/movimentos/resumo-diario", response_model=List[ResumoDiarioOut])
def movimentos_resumo_diario(db: Session = Depends(get_db)):
    """Totais de recebimentos/pagamentos por dia, somados por todas as
    empresas - para o gráfico de fluxo mensal da Visão Geral. Registada
    antes de /movimentos/{dia} de propósito: sendo os dois de um único
    segmento, a rota registada primeiro é que ganha (senão "resumo-diario"
    seria interpretado como uma data e falhava)."""
    return resumo_diario(db)


@router.get("/movimentos/{dia}", response_model=List[MovimentoStatusOut])
def movimentos_do_dia(dia: date, db: Session = Depends(get_db)):
    """Só leitura - mostra o estado atual de cada movimento do dia. Podes
    chamar isto quantas vezes quiseres, mesmo depois de já teres corrido
    /reconciliar, sem duplicar nem reprocessar nada."""
    return listar_movimentos_do_dia(db, dia)


@router.get("/empresas", response_model=List[str])
def empresas(db: Session = Depends(get_db)):
    """Lista as empresas com movimentos importados - para preencher
    seletores (ex. no dashboard) sem adivinhar o nome exato."""
    return listar_empresas(db)


@router.get("/movimentos/empresa/{empresa}", response_model=List[MovimentoHistoricoOut])
def movimentos_da_empresa(empresa: str, db: Session = Depends(get_db)):
    """Histórico de movimentos de uma empresa (todos os dias importados),
    ordenado por dia - para analisar o fluxo de uma conta ao longo do
    tempo."""
    return listar_movimentos_da_empresa(db, empresa)


@router.get("/analise/imputacoes")
def analise_imputacoes_endpoint(
    empresa: Optional[str] = None,
    dia_inicio: Optional[date] = None,
    dia_fim: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Soma o valor recebido/pago por imputação (categoria do Mapa) no
    período - opcionalmente filtrado por empresa - para os gráficos
    circulares da aba "Análise de Extratos" do dashboard."""
    return analise_imputacoes(db, empresa=empresa, dia_inicio=dia_inicio, dia_fim=dia_fim)


@router.get("/analise/imputacoes/linhas")
def analise_imputacoes_linhas_endpoint(
    empresa: Optional[str] = None,
    dia_inicio: Optional[date] = None,
    dia_fim: Optional[date] = None,
    db: Session = Depends(get_db),
):
    """Linhas do Mapa em detalhe (mesmo filtro de /analise/imputacoes) -
    para a tabela de extratos por baixo do gráfico circular, colorida
    conforme a fatia/imputação a que cada linha pertence."""
    return {"linhas": listar_linhas_imputacao(db, empresa=empresa, dia_inicio=dia_inicio, dia_fim=dia_fim)}
