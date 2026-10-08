"""Sincronização automática a partir do OneDrive: só lê os ficheiros
reais (extratos CGD + Mapa de Pagamentos e Recebimentos), nunca escreve
neles. Importa apenas os dias que ainda não existem na base de dados
local, para nunca duplicar movimentos/linhas já importados."""
import glob
import os
import re
from collections import Counter
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.db.models import LinhaMapa, MovimentoBancario, SaldoDiario
from app.services.mapa_importer import importar_dia_do_mapa
from app.services.reconciliador import (
    abrir_workbook_com_retry,
    chave_empresa,
    ler_movimentos_do_extrato,
    nome_empresa_do_ficheiro,
)
from app.services.saldos import parse_valor_eur, registar_saldos_do_dia

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
    """Caminho configurado em ONEDRIVE_RAIZ. O .env é partilhado (vive no
    OneDrive) entre máquinas com utilizadores Windows diferentes - se o
    caminho configurado não existir nesta máquina, tenta a mesma pasta
    sincronizada debaixo do utilizador atual (~/VIDÓR/<pasta>) antes de
    desistir. Sem isto, a sincronização não encontrava pasta nenhuma e
    falhava em silêncio (sem erro, só "nada de novo")."""
    raiz = os.environ.get("ONEDRIVE_RAIZ")
    if not raiz:
        raise RuntimeError(
            "ONEDRIVE_RAIZ não definido - cria/edita o ficheiro .env com o caminho "
            "para a pasta '...Documentos' sincronizada do OneDrive."
        )
    if not os.path.isdir(raiz):
        alternativa = os.path.join(
            os.path.expanduser("~"), "VIDÓR", os.path.basename(raiz.rstrip("\\/")),
        )
        if os.path.isdir(alternativa):
            return alternativa
    return raiz


def pasta_extratos_cgd() -> str:
    return os.path.join(
        _onedrive_raiz(), "FINANCEIRO", "03 - Extratos Bancários", "Movimentos Diários", "CGD",
    )


def pasta_extratos_do_dia(dia: date) -> str:
    return os.path.join(pasta_extratos_cgd(), MESES_PASTA[dia.month], dia.strftime("%d-%m-%Y"))


def caminho_mapa(dia: date) -> str:
    return os.path.join(
        _onedrive_raiz(), "CONTABILIDADE", "12- Mapa de Pagamentos e Recebimentos", str(dia.year),
        f"{dia.month:02d} - Mapa de Pagamentos  e Recebimentos de {MESES_NOME[dia.month]}.xlsx",
    )


def _importar_movimentos_em_falta(db: Session, caminho: str, dia: date) -> int:
    """Acrescenta os movimentos do extrato que ainda não estão na BD para
    este (dia, ficheiro), comparando por (descrição, valor) com contagem -
    dois movimentos iguais no mesmo dia continuam a ser dois. O CGD publica
    uma versão provisória do dia às 14:00 e substitui a pasta pela final na
    manhã seguinte, com os movimentos da tarde: antes, um dia com algum
    movimento já importado nunca mais era lido, e esses ficavam de fora
    (confirmado em 06/10/2026: 30/09 com 41 movimentos no extrato e 24 na
    BD). Nunca apaga - os movimentos já importados podem ter reconciliações
    e resoluções manuais associadas."""
    origem = os.path.basename(caminho)
    ja_importados = Counter(
        (descricao, valor) for descricao, valor in
        db.query(MovimentoBancario.descricao, MovimentoBancario.valor)
        .filter(MovimentoBancario.dia == dia, MovimentoBancario.ficheiro_origem == origem)
    )
    empresa = nome_empresa_do_ficheiro(caminho)
    inseridos = 0
    for mov in ler_movimentos_do_extrato(caminho):
        chave = (mov["descricao"], mov["valor"])
        if ja_importados[chave] > 0:
            ja_importados[chave] -= 1
            continue
        db.add(MovimentoBancario(
            dia=dia, empresa=empresa, descricao=mov["descricao"], valor=mov["valor"],
            ficheiro_origem=origem,
        ))
        inseridos += 1
    db.commit()
    return inseridos


def _importar_dia(db: Session, dia: date) -> dict:
    """Importa um único dia (movimentos, saldos, mapa): o que ainda não
    existir localmente e, nos movimentos e saldos, o que mudou desde a
    última leitura do extrato. Partilhado entre `atualizar_dados_recentes`
    (varre os últimos N dias a partir de hoje) e `atualizar_dados_do_dia`
    (um dia arbitrário, ex. escolhido no date_input do dashboard - pode
    ser de um mês/ano completamente fora da janela dos últimos N dias)."""
    resultado = {"movimentos": None, "saldos": None, "mapa": None, "erro": None}
    pasta = pasta_extratos_do_dia(dia)
    if not os.path.isdir(pasta):
        return resultado

    try:
        total_importado = 0
        for caminho in sorted(glob.glob(os.path.join(pasta, "*.xlsx"))):
            total_importado += _importar_movimentos_em_falta(db, caminho, dia)
        # só marca "novo" se algo foi mesmo inserido - a pasta do dia pode
        # existir mas ainda sem nenhum .xlsx dentro (extratos do dia a
        # decorrer ainda não gerados pelo banco), e sem isto o dashboard
        # reportava "atualizado" mesmo sem nenhum movimento novo, o que
        # parecia "a análise de contas não atualiza" quando na verdade
        # não havia nada para importar ainda.
        if total_importado > 0:
            resultado["movimentos"] = dia.isoformat()
    except Exception as e:
        resultado["erro"] = f"movimentos {dia.isoformat()}: {e}"

    # Sempre: registar_saldos_do_dia só mexe nas entidades cujo saldo no
    # extrato mudou (versão provisória -> final), por isso é seguro repetir.
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
                n_receb, n_pag = importar_dia_do_mapa(db, caminho_mapa_ficheiro, dia)
                # só marca "novo" se alguma linha foi mesmo importada - a
                # folha do dia pode já existir no Mapa mas ainda estar vazia
                # (ninguém a preencheu ainda hoje), mesmo problema do "total
                # importado" em movimentos acima.
                if n_receb + n_pag > 0:
                    resultado["mapa"] = dia.isoformat()
            except KeyError:
                pass  # folha do dia ainda não existe no Mapa - normal para o dia de hoje
            except Exception as e:
                resultado["erro"] = f"mapa {dia.isoformat()}: {e}"

    return resultado


def atualizar_dados_recentes(db: Session, dias_atras: int = 7) -> dict:
    """Percorre os últimos `dias_atras` dias (incluindo hoje) e importa,
    para cada um, os dados que ainda não existem localmente: movimentos
    bancários, linhas do mapa e saldos. Nunca duplica - seguro chamar
    repetidamente (ex. a partir de um botão no dashboard).

    O CGD publica os extratos em duas fases - às 14:00 uma versão
    PROVISÓRIA do dia a decorrer, e só na manhã seguinte (8:30) é que a
    pasta inteira é SUBSTITUÍDA pela versão final/fechada (confirmado em
    28/07/2026: saldo disponível da HCN passou de 57.089,68 € para
    202.354,33 € nessa troca). Por isso, mesmo nos dias já importados, os
    saldos são atualizados quando o extrato mudou e os movimentos que
    entretanto apareceram são acrescentados (`_importar_movimentos_em_falta`
    - nunca apaga, por causa das reconciliações e resoluções manuais).
    Antes só se corrigiam os saldos de ontem, e os de sexta/sábado vistos
    na segunda ficavam provisórios para sempre.

    Nota: isto só cobre os últimos `dias_atras` dias a contar de hoje. Um
    dia escolhido no dashboard fora dessa janela (mês anterior, etc.)
    nunca é importado por esta função - ver `atualizar_dados_do_dia`."""
    _onedrive_raiz()  # falha cedo e com mensagem clara se não estiver configurado

    hoje = date.today()
    dias_com_movimentos_novos = []
    dias_com_saldos_novos = []
    dias_com_mapa_novo = []
    erros = []

    for i in range(dias_atras, -1, -1):
        dia = hoje - timedelta(days=i)
        r = _importar_dia(db, dia)
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
    r = _importar_dia(db, dia)
    db.commit()
    return {
        "dias_com_movimentos_novos": [r["movimentos"]] if r["movimentos"] else [],
        "dias_com_saldos_novos": [r["saldos"]] if r["saldos"] else [],
        "dias_com_mapa_novo": [r["mapa"]] if r["mapa"] else [],
        "erros": [r["erro"]] if r["erro"] else [],
    }


# ---------------------------------------------------------------------------
# Histórico completo (para a previsão ter dados suficientes)
# ---------------------------------------------------------------------------
# A sincronização normal só olha para os últimos N dias - para o forecast
# isso deixava a base de dados com ~2 meses de histórico, apesar de a pasta
# de extratos ter o ano inteiro. Há dois formatos na pasta:
#   - pastas diárias "dd-mm-aaaa" (a partir de 20/04/2026) - mesmo formato
#     que a sincronização diária já importa (_importar_dia);
#   - meses sem pastas diárias (Jan-Mar 2026): um extrato MENSAL por
#     empresa, com a data real de cada movimento e o "Saldo contabilístico
#     após movimento" - dá para reconstruir movimentos e saldo de fim de
#     dia com as datas certas. O saldo do cabeçalho destes ficheiros é o
#     do dia em que foram exportados (ex. 21/09), não o do mês - ignorado.

_PADRAO_PASTA_DIA = re.compile(r"^\d{2}-\d{2}-\d{4}$")
PREFIXO_ORIGEM_MENSAL = "mensal:"


def _ler_extrato_mensal(caminho: str) -> list:
    """Linhas de um extrato mensal, pela ordem do ficheiro (mais recente
    primeiro, como o CGD exporta): [{dia, descricao, valor, saldo_apos}]."""
    ws = abrir_workbook_com_retry(caminho).active
    linhas, a_ler = [], False
    for row in ws.iter_rows(values_only=True):
        primeira = str(row[0]).strip() if row[0] is not None else ""
        if not a_ler:
            a_ler = primeira.startswith("Data mov")
            continue
        if not primeira and (len(row) < 3 or row[2] is None):
            break
        if len(row) < 4 or row[3] is None:
            continue
        try:
            dia = datetime.strptime(primeira, "%d-%m-%Y").date()
        except ValueError:
            continue
        linhas.append({
            "dia": dia,
            "descricao": str(row[2]).strip() if row[2] else "",
            "valor": parse_valor_eur(row[3]),
            "saldo_apos": parse_valor_eur(row[4]) if len(row) > 4 else None,
        })
    return linhas


def importar_extratos_mensais(db: Session, ano: int) -> dict:
    """Importa os meses de `ano` que só têm extratos mensais (sem pastas
    diárias). Idempotente: um ficheiro cuja empresa já tem movimentos
    nesse intervalo de datas é ignorado."""
    movimentos_novos, saldos_novos, ficheiros = 0, 0, 0
    for mes, nome_pasta in MESES_PASTA.items():
        pasta_mes = os.path.join(pasta_extratos_cgd(), nome_pasta)
        if not os.path.isdir(pasta_mes):
            continue
        if any(_PADRAO_PASTA_DIA.match(n) for n in os.listdir(pasta_mes)):
            continue  # mês com pastas diárias - tratado por _importar_dia
        for caminho in sorted(glob.glob(os.path.join(pasta_mes, "*.xlsx"))):
            if os.path.basename(caminho).lower().startswith("resumo"):
                continue
            empresa = nome_empresa_do_ficheiro(caminho)
            linhas = [
                l for l in _ler_extrato_mensal(caminho)
                if l["dia"].year == ano and l["valor"] is not None
            ]
            if not linhas:
                continue
            inicio, fim = min(l["dia"] for l in linhas), max(l["dia"] for l in linhas)
            ja_existe = db.query(MovimentoBancario).filter(
                MovimentoBancario.empresa == empresa,
                MovimentoBancario.dia >= inicio,
                MovimentoBancario.dia <= fim,
            ).first()
            if ja_existe:
                continue

            ficheiros += 1
            origem = PREFIXO_ORIGEM_MENSAL + os.path.basename(caminho)
            for l in linhas:
                db.add(MovimentoBancario(
                    dia=l["dia"], empresa=empresa, descricao=l["descricao"],
                    valor=l["valor"], ficheiro_origem=origem,
                ))
            movimentos_novos += len(linhas)

            # Saldo de fim de dia = saldo após o movimento mais recente
            # desse dia (a primeira linha do dia, pela ordem do ficheiro);
            # saldo de abertura do mês = saldo antes do movimento mais
            # antigo, registado no dia 1 para a conta não "aparecer" a
            # meio do mês na série do saldo total.
            saldo_fim_dia = {}
            for l in linhas:
                if l["saldo_apos"] is not None:
                    saldo_fim_dia.setdefault(l["dia"], l["saldo_apos"])
            mais_antiga = linhas[-1]
            primeiro_do_mes = date(ano, mes, 1)
            if mais_antiga["saldo_apos"] is not None and primeiro_do_mes not in saldo_fim_dia:
                saldo_fim_dia[primeiro_do_mes] = mais_antiga["saldo_apos"] - mais_antiga["valor"]

            ja_registados = {
                s.dia for s in db.query(SaldoDiario.dia).filter(SaldoDiario.entidade == empresa)
            }
            for dia, saldo in saldo_fim_dia.items():
                if dia in ja_registados:
                    continue
                db.add(SaldoDiario(
                    dia=dia, entidade=empresa, saldo_contabilistico=saldo, saldo_disponivel=saldo,
                ))
                saldos_novos += 1
    db.commit()
    return {"ficheiros_mensais": ficheiros, "movimentos": movimentos_novos, "saldos": saldos_novos}


def _preencher_saldo_inicial(db: Session, desde: date) -> int:
    """Contas sem nenhuma leitura em `desde` (ex. sem movimentos em
    Jan-Mar, logo sem saldo reconstruível) entram com a primeira leitura
    conhecida, datada de `desde`. Aproximação (assume que o saldo não mudou
    até essa primeira leitura), mas sem ela o saldo total dava um salto
    artificial no dia em que cada conta aparece - e a previsão lia esse
    salto como tendência."""
    primeira_por_chave = {}
    for s in db.query(SaldoDiario).order_by(SaldoDiario.dia).all():
        primeira_por_chave.setdefault(chave_empresa(s.entidade), s)
    novos = 0
    for s in primeira_por_chave.values():
        if s.dia > desde:
            db.add(SaldoDiario(
                dia=desde, entidade=s.entidade,
                saldo_contabilistico=s.saldo_contabilistico, saldo_disponivel=s.saldo_disponivel,
            ))
            novos += 1
    db.commit()
    return novos


def importar_historico(db: Session, desde: date = None) -> dict:
    """Importa todo o histórico disponível na pasta de extratos desde
    `desde` (1 de janeiro do ano corrente, por omissão): extratos mensais
    e depois todas as pastas diárias. Seguro repetir - só importa o que
    falta."""
    _onedrive_raiz()
    hoje = date.today()
    desde = desde or date(hoje.year, 1, 1)

    mensais = importar_extratos_mensais(db, desde.year)

    dias_movimentos, dias_saldos, dias_mapa, erros = [], [], [], []
    dia = desde
    while dia <= hoje:
        r = _importar_dia(db, dia)
        if r["movimentos"]:
            dias_movimentos.append(r["movimentos"])
        if r["saldos"]:
            dias_saldos.append(r["saldos"])
        if r["mapa"]:
            dias_mapa.append(r["mapa"])
        if r["erro"]:
            erros.append(r["erro"])
        dia += timedelta(days=1)
    db.commit()

    return {
        "pasta": pasta_extratos_cgd(),
        "extratos_mensais": mensais,
        "dias_com_movimentos_novos": dias_movimentos,
        "dias_com_saldos_novos": dias_saldos,
        "dias_com_mapa_novo": dias_mapa,
        "saldos_iniciais_preenchidos": _preencher_saldo_inicial(db, desde),
        "erros": erros,
    }
