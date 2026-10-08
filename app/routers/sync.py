from datetime import date, datetime, timedelta
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AtualizarDadosResponse
from app.services.monitorizacao import FUSO_LOCAL, pedir_corrida
from app.services.onedrive_sync import atualizar_dados_do_dia, atualizar_dados_recentes, importar_historico

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


@router.post("/atualizar-historico")
def atualizar_historico(desde: Optional[date] = None, db: Session = Depends(get_db)):
    """Importa todo o histórico disponível na pasta de extratos (extratos
    mensais + todas as pastas diárias desde `desde`, por omissão 1 de
    janeiro) - para a previsão treinar com o histórico completo e não só
    com os últimos dias sincronizados. Seguro repetir."""
    try:
        return importar_historico(db, desde)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))


@router.post("/extratos/prontos")
def extratos_prontos(dia: Optional[date] = None, db: Session = Depends(get_db)):
    """Chamado pelo script que extrai os extratos da CGD, no fim de uma
    extração bem sucedida - dispara o resto da cadeia, em vez de esperar
    pelas horas fixas das tarefas agendadas:
      1. pede o preencher_mapa (que já corre o atualizar_mapa_saldos por
         dentro) - o agente do Windows lança-o, como o botão "Correr";
      2. importa para a BD o dia e o anterior (a versão final de ontem
         chega com a extração da manhã), para o dashboard ficar atualizado.
    O envio do Mapa (enviar_mapa_smtp) não entra: fica à hora fixa, senão
    saía um email por cada extração. `dia` por omissão = hoje (Lisboa)."""
    dia = dia or datetime.now(FUSO_LOCAL).date()
    resultado = {"dia": dia.isoformat()}
    try:
        resultado["preencher_mapa"] = pedir_corrida(db, "preencher_mapa")
    except RuntimeError as e:
        resultado["preencher_mapa"] = {"erro": str(e)}

    sincronizacao = {"dias_com_movimentos_novos": [], "dias_com_saldos_novos": [], "dias_com_mapa_novo": [], "erros": []}
    for d in (dia - timedelta(days=1), dia):
        try:
            r = atualizar_dados_do_dia(db, d)
        except RuntimeError as e:
            sincronizacao["erros"].append(f"{d.isoformat()}: {e}")
            continue
        for chave in sincronizacao:
            sincronizacao[chave] += r[chave]
    resultado["sincronizacao"] = sincronizacao
    return resultado
