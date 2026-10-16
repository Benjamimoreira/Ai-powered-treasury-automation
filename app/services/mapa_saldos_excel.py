"""Mapa de Saldos Bancários (Excel da contabilidade, CONTABILIDADE\\
08- Saldos Bancários\\<ano>\\<MM>_Mapa de Saldos de <mês>.xlsx).

O Mapa tem uma folha por dia ("DD-MM"), com uma linha por conta e, no fim,
a linha "Saldo" (=SUM(E3:E62) / =SUM(F3:F62)). É esse o valor oficial do
saldo total - inclui as contas de outros bancos preenchidas à mão (Santander,
Abanca, ...) e só as contas que lá estão, ao contrário da soma dos extratos
CGD em saldos_diarios (que apanha também contas antigas/fechadas com a
última leitura de há meses).

O atualizar_mapa_saldos grava o ficheiro com openpyxl, que não guarda o
resultado das fórmulas - por isso a linha "Saldo" vem sempre vazia ao ler
com data_only e a soma é refeita aqui, sobre as linhas de contas.
"""
import bisect
import glob
import logging
import os
import re
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.services.onedrive_sync import _onedrive_raiz
from app.services.reconciliador import abrir_workbook_com_retry
from app.services.saldos import _serie_saldo_total_bruta, saldo_total_geral

logger = logging.getLogger(__name__)

PASTA_MAPA_SALDOS = os.path.join("CONTABILIDADE", "08- Saldos Bancários")
PRIMEIRA_LINHA_CONTAS = 3

# caminho -> (mtime, {dia: [linhas]}); o ficheiro só muda quando o
# atualizar_mapa_saldos corre ou alguém o edita, e cada abertura são ~1-2 s
_cache: dict = {}

# Na série, um dia sem folha usa a última folha até este número de dias
# antes (fins de semana, feriados); mais do que isso, os extratos.
DIAS_MAX_SEM_FOLHA = 7


def caminho_mapa_saldos(ano: int, mes: int, raiz: Optional[str] = None) -> Optional[str]:
    """Ficheiro do Mapa de Saldos desse mês, ou None. O nome do mês varia
    entre ficheiros ("Janeiro", "junho"), por isso procura pelo prefixo MM_."""
    raiz = raiz or _onedrive_raiz()
    pasta = os.path.join(raiz, PASTA_MAPA_SALDOS, str(ano))
    candidatos = sorted(glob.glob(os.path.join(pasta, f"{mes:02d}_Mapa de Saldos*.xlsx")))
    candidatos = [c for c in candidatos if not os.path.basename(c).startswith("~$")]
    return candidatos[0] if candidatos else None


def _numero(valor) -> Optional[float]:
    return float(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) else None


def _texto(valor) -> str:
    return str(valor or "").replace("\xa0", " ").strip()


def _linhas_da_folha(ws) -> list:
    """Contas da folha: da linha 3 até à linha anterior a "Saldo" (coluna
    D), as mesmas que a fórmula da própria folha soma. Linhas sem Banco
    ficam de fora."""
    ultima = ws.max_row
    for r in range(PRIMEIRA_LINHA_CONTAS, ws.max_row + 1):
        if _texto(ws[f"D{r}"].value).lower() == "saldo":
            ultima = r - 1
            break

    linhas = []
    for r in range(PRIMEIRA_LINHA_CONTAS, ultima + 1):
        banco = _texto(ws[f"B{r}"].value)
        if not banco:
            continue
        linhas.append({
            "banco": banco,
            "entidade": _texto(ws[f"C{r}"].value),
            "nib": _texto(ws[f"D{r}"].value),
            "saldo_contabilistico": _numero(ws[f"E{r}"].value),
            "saldo_disponivel": _numero(ws[f"F{r}"].value),
            "observacoes": _texto(ws[f"G{r}"].value) or None,
        })
    return linhas


def _totais(linhas: list) -> dict:
    return {
        "entidades": len(linhas),
        "saldo_contabilistico_total": round(sum(l["saldo_contabilistico"] or 0 for l in linhas), 2),
        "saldo_disponivel_total": round(sum(l["saldo_disponivel"] or 0 for l in linhas), 2),
    }


def _folhas_do_ficheiro(caminho: str, ano: int, mes: int) -> dict:
    mtime = os.path.getmtime(caminho)
    if _cache.get(caminho, (None,))[0] != mtime:
        wb = abrir_workbook_com_retry(caminho)
        por_dia = {}
        for nome in wb.sheetnames:
            m = re.match(r"(\d{2})-(\d{2})\s*$", nome)
            if not m or int(m.group(2)) != mes:
                continue
            try:
                dia = date(ano, mes, int(m.group(1)))
            except ValueError:
                continue
            por_dia[dia] = _linhas_da_folha(wb[nome])
        _cache[caminho] = (mtime, por_dia)
    return _cache[caminho][1]


def _folha_ate(dia: date, raiz: Optional[str]):
    """(dia_da_folha, linhas) da folha de `dia` ou da última antes dele,
    no mês de `dia` ou, se ainda não tiver folhas até lá (ex. dia 1), no
    mês anterior. None se não houver."""
    for referencia in (dia, dia.replace(day=1) - timedelta(days=1)):
        caminho = caminho_mapa_saldos(referencia.year, referencia.month, raiz)
        if not caminho:
            continue
        anteriores = [d for d in _folhas_do_ficheiro(caminho, referencia.year, referencia.month) if d <= dia]
        if anteriores:
            dia_folha = max(anteriores)
            return dia_folha, _folhas_do_ficheiro(caminho, referencia.year, referencia.month)[dia_folha]
    return None


def totais_mapa_saldos(dia: Optional[date] = None, raiz: Optional[str] = None) -> Optional[dict]:
    """Totais da folha do Mapa de Saldos de `dia` (hoje, sem `dia`) - ou da
    última folha antes dele, como os saldos de fim de semana, que ficam com
    a folha de sexta. None se não houver Mapa."""
    folha = _folha_ate(dia or date.today(), raiz)
    if not folha:
        return None
    dia_folha, linhas = folha
    return {**_totais(linhas), "fonte": "mapa_saldos", "dia_referencia": dia_folha.isoformat()}


def _chave_conta(linha: dict) -> tuple:
    return (linha["banco"].lower(), linha["entidade"].lower(), re.sub(r"\D", "", linha["nib"]))


def contas_mapa_saldos(dia: Optional[date] = None, raiz: Optional[str] = None) -> Optional[dict]:
    """As contas da folha do Mapa de Saldos de `dia` (ou a última antes
    dele), com o saldo da folha anterior e a variação - para a tabela do
    separador Saldos ser igual ao Mapa. None se não houver Mapa."""
    folha = _folha_ate(dia or date.today(), raiz)
    if not folha:
        return None
    dia_folha, linhas = folha
    anterior = _folha_ate(dia_folha - timedelta(days=1), raiz)
    por_chave = {_chave_conta(l): l for l in anterior[1]} if anterior else {}

    def variacao(atual, antes):
        if atual is None or antes is None:
            return None, None
        delta = round(atual - antes, 2)
        return delta, (delta / abs(antes) * 100) if antes else None

    contas = []
    for l in linhas:
        antes = por_chave.get(_chave_conta(l), {})
        var_c, pct_c = variacao(l["saldo_contabilistico"], antes.get("saldo_contabilistico"))
        var_d, pct_d = variacao(l["saldo_disponivel"], antes.get("saldo_disponivel"))
        contas.append({
            **l,
            "saldo_contabilistico_anterior": antes.get("saldo_contabilistico"),
            "saldo_disponivel_anterior": antes.get("saldo_disponivel"),
            "variacao_contabilistico": var_c,
            "variacao_pct_contabilistico": pct_c,
            "variacao_disponivel": var_d,
            "variacao_pct_disponivel": pct_d,
        })
    return {
        **_totais(linhas),
        "dia_referencia": dia_folha.isoformat(),
        "dia_anterior": anterior[0].isoformat() if anterior else None,
        "contas": contas,
    }


def _totais_de_todas_as_folhas(anos, raiz: Optional[str]) -> dict:
    totais = {}
    for ano in anos:
        for mes in range(1, 13):
            caminho = caminho_mapa_saldos(ano, mes, raiz)
            if caminho:
                for dia, linhas in _folhas_do_ficheiro(caminho, ano, mes).items():
                    totais[dia] = _totais(linhas)
    return totais


def serie_saldo_total_bruta(db: Session, raiz: Optional[str] = None) -> list:
    """Saldo total dia a dia como no Mapa de Saldos: cada dia com folha usa
    a linha "Saldo" dessa folha; um dia sem folha (fim de semana) a última
    folha até DIAS_MAX_SEM_FOLHA dias antes; sem folha perto (antes de haver
    Mapa, ou sem acesso a ele), a soma dos extratos de
    saldos._serie_saldo_total_bruta. `dia` como objeto date, como lá."""
    extratos = _serie_saldo_total_bruta(db)
    try:
        anos = range(min([p["dia"].year for p in extratos] or [date.today().year]), date.today().year + 1)
        mapa = _totais_de_todas_as_folhas(anos, raiz)
    except Exception:  # ficheiro bloqueado/corrompido, ONEDRIVE_RAIZ em falta, ...
        logger.warning("Não consegui ler o Mapa de Saldos - série só com os extratos", exc_info=True)
        return extratos
    if not mapa:
        return extratos

    dias_mapa = sorted(mapa)
    dias_extratos = [p["dia"] for p in extratos]
    resultado = []
    for dia in sorted(set(dias_mapa) | set(dias_extratos)):
        i = bisect.bisect_right(dias_mapa, dia) - 1
        if i >= 0 and (dia - dias_mapa[i]).days <= DIAS_MAX_SEM_FOLHA:
            t = mapa[dias_mapa[i]]
        else:
            j = bisect.bisect_right(dias_extratos, dia) - 1
            if j < 0:
                continue
            t = extratos[j]
        resultado.append({
            "dia": dia,
            "saldo_contabilistico_total": t["saldo_contabilistico_total"],
            "saldo_disponivel_total": t["saldo_disponivel_total"],
        })
    return resultado


def serie_saldo_total(db: Session) -> list:
    """serie_saldo_total_bruta() com `dia` em ISO, para a API (gráfico
    "Saldo bancário de todas as contas juntas" da Visão Geral)."""
    return [{**p, "dia": p["dia"].isoformat()} for p in serie_saldo_total_bruta(db)]


def saldo_total(db: Session, dia: Optional[date] = None) -> dict:
    """Saldo total como no Mapa de Saldos (o valor que a contabilidade vê);
    sem Mapa acessível (ex. servidor sem OneDrive, mês ainda sem ficheiro),
    a soma dos extratos CGD de saldos.saldo_total_geral."""
    try:
        totais = totais_mapa_saldos(dia)
    except Exception:  # ficheiro bloqueado/corrompido, ONEDRIVE_RAIZ em falta, ...
        logger.warning("Não consegui ler o Mapa de Saldos - a usar os extratos", exc_info=True)
        totais = None
    if totais:
        return totais
    return {**saldo_total_geral(db, dia), "fonte": "extratos", "dia_referencia": None}
