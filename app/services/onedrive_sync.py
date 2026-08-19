"""Sincronização automática a partir do OneDrive: só lê os ficheiros
reais (extratos CGD + Mapa de Pagamentos e Recebimentos), nunca escreve
neles. Importa apenas os dias que ainda não existem na base de dados
local, para nunca duplicar movimentos/linhas já importados."""
import glob
import os
from datetime import date, timedelta

from sqlalchemy.orm import Session

from app.db.models import LinhaMapa, MovimentoBancario, SaldoDiario
from app.services.mapa_importer import importar_dia_do_mapa
from app.services.reconciliador import importar_extrato_para_bd, nome_empresa_do_ficheiro
from app.services.saldos import registar_saldos_do_dia

MESES_PASTA = {
    1: "01_Janeiro", 2: "02_Fevereiro", 3: "03_Março", 4: "04_Abril",
    5: "05_Maio", 6: "06_Junho", 7: "07_Julho", 8: "08_Agosto",
    9: "09_Setembro", 10: "10_Outubro", 11: "11_Novembro", 12: "12_Dezembro",
}
MESES_NOME = {
    1: "Janeiro", 2: "Fevereiro", 3: "Março", 4: "Abril",
    5: "Maio", 6: "Junho", 7: "Julho", 8: "Agosto",
    9: "Setembro", 10: "Outubro", 11: "Novembro", 12: "Dezembro",
}

def _onedrive_raiz() -> str:
    raiz = os.environ.get("ONEDRIVE_RAIZ")
    if not raiz:
        raise RuntimeError(
            "ONEDRIVE_RAIZ não definido - cria/edita o ficheiro .env com o caminho "
            "para a pasta '...Documentos' sincronizada do OneDrive."
        )
    return raiz


def pasta_extratos_do_dia(dia: date) -> str:
    return os.path.join(
        _onedrive_raiz(), "FINANCEIRO", "03 - Extratos Bancários", "Movimentos Diários",
        "CGD", MESES_PASTA[dia.month], dia.strftime("%d-%m-%Y"),
    )


def caminho_mapa(dia: date) -> str:
    return os.path.join(
        _onedrive_raiz(), "CONTABILIDADE", "13- Mapa de Pagamentos e Recebimentos", str(dia.year),
        f"{dia.month:02d} - Mapa de Pagamentos  e Recebimentos de {MESES_NOME[dia.month]}.xlsx",
    )


def _importar_dia(db: Session, dia: date, forcar_resync_saldos: bool) -> dict:
    """Importa um único dia (movimentos, saldos, mapa) se ainda não
    existir localmente. Partilhado entre `atualizar_dados_recentes`
    (varre os últimos N dias a partir de hoje) e `atualizar_dados_do_dia`
    (um dia arbitrário, ex. escolhido no date_input do dashboard - pode
    ser de um mês/ano completamente fora da janela dos últimos N dias)."""
    resultado = {"movimentos": None, "saldos": None, "mapa": None, "erro": None}
    pasta = pasta_extratos_do_dia(dia)
    if not os.path.isdir(pasta):
        return resultado

    se_ja_tem_movimentos = db.query(MovimentoBancario).filter(MovimentoBancario.dia == dia).first()
    if not se_ja_tem_movimentos:
        try:
            for caminho in sorted(glob.glob(os.path.join(pasta, "*.xlsx"))):
                empresa = nome_empresa_do_ficheiro(caminho)
                importar_extrato_para_bd(db, caminho, dia, empresa)
            resultado["movimentos"] = dia.isoformat()
        except Exception as e:
            resultado["erro"] = f"movimentos {dia.isoformat()}: {e}"

    if forcar_resync_saldos:
        db.query(SaldoDiario).filter(SaldoDiario.dia == dia).delete()

    se_ja_tem_saldos = db.query(SaldoDiario).filter(SaldoDiario.dia == dia).first()
    if not se_ja_tem_saldos:
        try:
            if registar_saldos_do_dia(db, dia, pasta) > 0:
                resultado["saldos"] = dia.isoformat()
        except Exception as e:
            resultado["erro"] = f"saldos {dia.isoformat()}: {e}"

    se_ja_tem_mapa = db.query(LinhaMapa).filter(LinhaMapa.dia == dia).first()
    if not se_ja_tem_mapa:
        caminho_mapa_ficheiro = caminho_mapa(dia)
        if os.path.isfile(caminho_mapa_ficheiro):
            try:
                importar_dia_do_mapa(db, caminho_mapa_ficheiro, dia)
                resultado["mapa"] = dia.isoformat()
            except KeyError:
                pass  # folha do dia ainda não existe no Mapa - normal para o dia de hoje
            except Exception as e:
                resultado["erro"] = f"mapa {dia.isoformat()}: {e}"

    return resultado


def atualizar_dados_recentes(db: Session, dias_atras: int = 7) -> dict:
    """Percorre os últimos `dias_atras` dias (incluindo hoje) e importa,
    para cada um, os dados que ainda não existem localmente: movimentos
    bancários, linhas do mapa e saldos. Dias já importados são ignorados -
    seguro chamar repetidamente (ex. a partir de um botão no dashboard).

    Exceção: os SALDOS de ontem são sempre re-sincronizados (apagados e
    reimportados), mesmo que já existam. Confirmado em 28/07/2026: o CGD
    publica os extratos em duas fases - às 14:00 uma versão PROVISÓRIA do
    dia a decorrer, e só na manhã seguinte (8:30) é que a pasta inteira é
    SUBSTITUÍDA pela versão final/fechada (ex.: saldo disponível da HCN
    passou de 57.089,68 € para 202.354,33 € nessa troca - um depósito que
    esteve em cobrança até compensar). Sem isto, um dia importado à tarde
    (provisório) nunca mais era corrigido, mesmo depois de a versão final
    chegar. Não se aplica a movimentos/mapa - esses têm reconciliações e
    resoluções manuais associadas que este refresh automático destruiria.

    Nota: isto só cobre os últimos `dias_atras` dias a contar de hoje. Um
    dia escolhido no dashboard fora dessa janela (mês anterior, etc.)
    nunca é importado por esta função - ver `atualizar_dados_do_dia`."""
    _onedrive_raiz()  # falha cedo e com mensagem clara se não estiver configurado

    hoje = date.today()
    ontem = hoje - timedelta(days=1)
    dias_com_movimentos_novos = []
    dias_com_saldos_novos = []
    dias_com_mapa_novo = []
    erros = []

    for i in range(dias_atras, -1, -1):
        dia = hoje - timedelta(days=i)
        r = _importar_dia(db, dia, forcar_resync_saldos=(dia == ontem))
        if r["movimentos"]:
            dias_com_movimentos_novos.append(r["movimentos"])
        if r["saldos"]:
            dias_com_saldos_novos.append(r["saldos"])
        if r["mapa"]:
            dias_com_mapa_novo.append(r["mapa"])
        if r["erro"]:
            erros.append(r["erro"])

    db.commit()
    return {
        "dias_verificados": dias_atras + 1,
        "dias_com_movimentos_novos": dias_com_movimentos_novos,
        "dias_com_saldos_novos": dias_com_saldos_novos,
        "dias_com_mapa_novo": dias_com_mapa_novo,
        "erros": erros,
    }


def atualizar_dados_do_dia(db: Session, dia: date) -> dict:
    """Importa um dia arbitrário (qualquer mês/ano, não só os últimos N
    dias a contar de hoje) - usado pelo dashboard quando o utilizador
    escolhe uma data em 'Visão Geral' para garantir que os movimentos, o
    saldo e a folha do Mapa desse dia (e por extensão do mês certo do
    ficheiro do Mapa) estão importados antes de mostrar os dados."""
    _onedrive_raiz()
    r = _importar_dia(db, dia, forcar_resync_saldos=False)
    db.commit()
    return {
        "dias_com_movimentos_novos": [r["movimentos"]] if r["movimentos"] else [],
        "dias_com_saldos_novos": [r["saldos"]] if r["saldos"] else [],
        "dias_com_mapa_novo": [r["mapa"]] if r["mapa"] else [],
        "erros": [r["erro"]] if r["erro"] else [],
    }
