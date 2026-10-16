"""Totais do Mapa de Saldos Bancários (Excel da contabilidade, CONTABILIDADE\\
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
import glob
import logging
import os
import re
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.services.onedrive_sync import _onedrive_raiz
from app.services.reconciliador import abrir_workbook_com_retry
from app.services.saldos import saldo_total_geral

logger = logging.getLogger(__name__)

PASTA_MAPA_SALDOS = os.path.join("CONTABILIDADE", "08- Saldos Bancários")
PRIMEIRA_LINHA_CONTAS = 3

# (caminho, mtime) -> {dia: totais}; o ficheiro só muda quando o
# atualizar_mapa_saldos corre ou alguém o edita, e cada abertura são ~1-2 s
_cache: dict = {}


def caminho_mapa_saldos(ano: int, mes: int, raiz: Optional[str] = None) -> Optional[str]:
    """Ficheiro do Mapa de Saldos desse mês, ou None. O nome do mês varia
    entre ficheiros ("Janeiro", "junho"), por isso procura pelo prefixo MM_."""
    raiz = raiz or _onedrive_raiz()
    pasta = os.path.join(raiz, PASTA_MAPA_SALDOS, str(ano))
    candidatos = sorted(glob.glob(os.path.join(pasta, f"{mes:02d}_Mapa de Saldos*.xlsx")))
    candidatos = [c for c in candidatos if not os.path.basename(c).startswith("~$")]
    return candidatos[0] if candidatos else None


def _numero(valor) -> float:
    return float(valor) if isinstance(valor, (int, float)) and not isinstance(valor, bool) else 0.0


def _totais_da_folha(ws) -> dict:
    """Soma Saldo Contabilístico (E) e Disponível (F) das linhas de contas:
    da linha 3 até à linha anterior a "Saldo" (coluna D), como a fórmula
    da própria folha."""
    ultima = ws.max_row
    for r in range(PRIMEIRA_LINHA_CONTAS, ws.max_row + 1):
        if str(ws[f"D{r}"].value or "").strip().lower() == "saldo":
            ultima = r - 1
            break

    contabilistico = disponivel = 0.0
    contas = 0
    for r in range(PRIMEIRA_LINHA_CONTAS, ultima + 1):
        if not str(ws[f"B{r}"].value or "").strip():
            continue
        contas += 1
        contabilistico += _numero(ws[f"E{r}"].value)
        disponivel += _numero(ws[f"F{r}"].value)
    return {
        "entidades": contas,
        "saldo_contabilistico_total": round(contabilistico, 2),
        "saldo_disponivel_total": round(disponivel, 2),
    }


def _totais_por_dia(caminho: str, ano: int, mes: int) -> dict:
    chave = (caminho, os.path.getmtime(caminho))
    if chave not in _cache:
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
            por_dia[dia] = _totais_da_folha(wb[nome])
        _cache.clear()  # só interessa o ficheiro mais recente lido
        _cache[chave] = por_dia
    return _cache[chave]


def totais_mapa_saldos(dia: Optional[date] = None, raiz: Optional[str] = None) -> Optional[dict]:
    """Totais da folha do Mapa de Saldos de `dia` (hoje, sem `dia`) - ou da
    última folha antes dele, como os saldos de fim de semana, que ficam com
    a folha de sexta. Procura no mês de `dia` e, se ainda não tiver folhas
    até lá (ex. dia 1), no mês anterior. None se não houver Mapa."""
    dia = dia or date.today()
    for referencia in (dia, dia.replace(day=1) - timedelta(days=1)):
        caminho = caminho_mapa_saldos(referencia.year, referencia.month, raiz)
        if not caminho:
            continue
        anteriores = {d: t for d, t in _totais_por_dia(caminho, referencia.year, referencia.month).items() if d <= dia}
        if anteriores:
            dia_folha = max(anteriores)
            return {**anteriores[dia_folha], "fonte": "mapa_saldos", "dia_referencia": dia_folha.isoformat()}
    return None


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
