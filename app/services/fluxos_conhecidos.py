"""Fluxos futuros conhecidos com antecedência, para a previsão não ter de
os "adivinhar" pelo sorteio de dias do histórico (ver
previsao.py::_simular_saldo):

- **Rendas** - contratos da folha RENDAS do Mapa de Rendas do mês
  (FINANCEIRO/04 - Mapa de Rendas - CPCV - Condomínios a receber). Os
  recebimentos de renda nos extratos são reconhecidos pelo nome do
  arrendatário no descritivo ("TRF/TFI <NOME>"), com as mesmas regras do
  preencher_mapa.py (tesouraria preenchimento) - o banco corta o nome a
  ~17 caracteres e tira acentos.
- **Recorrentes** - pagamentos/recebimentos que aparecem (quase) todos os
  meses com valor estável (mesma ideia do gerar_previstos.py): mesmo tipo,
  empresa e descritivo sem números.

Nos dois casos, os movimentos do histórico que correspondem a um fluxo
conhecido são marcados, para saírem do sorteio (senão contavam a dobrar:
uma vez como fluxo conhecido, outra dentro do dia sorteado)."""
import os
import re
import shutil
import tempfile
from dataclasses import dataclass, field
from datetime import date, timedelta
from statistics import median
from typing import Optional

import openpyxl

from app.services.reconciliador import chave_empresa, normalizar, remover_acentos

# ---------------------------------------------------------------------------
# Mapa de Rendas
# ---------------------------------------------------------------------------

FOLHA_RENDAS = "RENDAS"
# Colunas (1 = A) da folha RENDAS - as mesmas do preencher_mapa.py
COL_EMPRESA, COL_CLIENTE, COL_ESPACO, COL_FRACAO = 1, 3, 6, 11
COL_DATA_TERMINO = 18
COL_RENDA_POR_ANO = {2023: 19, 2024: 21, 2025: 22, 2026: 23}
PREFIXOS_TRF = ("TRF ", "TFI ")
PALAVRAS_LIGACAO = {"DA", "DE", "DO", "DAS", "DOS", "E"}
TITULOS = {"DR", "DRA", "ENG", "ENGA", "ARQ", "SR", "SRA"}
TOLERANCIA_RENDA = 0.01
# O recebimento tem de ser um número inteiro de rendas, com esta folga
# relativa: a atualização anual (~2-3%) entra nos pagamentos antes de entrar
# no Mapa de Rendas. Com 1% (a folga antiga), a renda de 13 377,75 € da
# Sorte de Principiante, paga desde agosto de 2026 a 13 758,98 € (+2,85%),
# deixou de ser reconhecida - e a previsão contava-a duas vezes.
TOLERANCIA_ATUALIZACAO_RENDA = 0.05

# Siglas usadas no Mapa de Rendas (coluna Empresa) -> palavras que têm de
# aparecer na designação social dos extratos. Só para confirmar que a renda
# entrou na conta da empresa do contrato (desempate); nunca bloqueia.
_SIGLAS_EMPRESA = {
    "HCC": "HABISERVE CONSTRUCOES CENTRO", "HCN": "HABISERVE CONSTRUCOES NORTE",
    "HCS": "HABISERVE CONSTRUCOES SUL", "HII": "HABISERVE INVESTIMENTOS IMOBILIARIOS",
    "JPINTO": "J PINTO", "FC": "F C",
}


@dataclass
class ContratoRenda:
    empresa: str
    cliente: str
    espaco: str
    fracao: str
    renda: Optional[float]
    titulares: list = field(default_factory=list)


def caminho_mapa_rendas(raiz_documentos: str, ano: int, mes: int) -> str:
    return os.path.join(
        raiz_documentos, "FINANCEIRO", "04 - Mapa de Rendas - CPCV - Condomínios a receber",
        str(ano), f"{mes:02d}_{ano}", f"Mapa de Rendas - {mes:02d} {ano}.xlsx",
    )


def mapa_rendas_mais_recente(raiz_documentos: str, ate: date) -> Optional[str]:
    """Mapa de Rendas do mês de `ate` ou, se ainda não existir, do mês
    anterior mais próximo (até 12 meses para trás)."""
    ano, mes = ate.year, ate.month
    for _ in range(12):
        caminho = caminho_mapa_rendas(raiz_documentos, ano, mes)
        if os.path.isfile(caminho):
            return caminho
        ano, mes = (ano, mes - 1) if mes > 1 else (ano - 1, 12)
    return None


def palavras_nome(texto) -> list:
    return [
        p for p in re.findall(r"[A-Z0-9]+", remover_acentos(str(texto)).upper())
        if p not in PALAVRAS_LIGACAO
    ]


def ler_contratos_rendas(caminho: str, ano: int) -> list:
    """Contratos da folha RENDAS com renda definida para `ano` (ou o ano
    mais recente disponível) e sem data de término já passada. Lê de uma
    cópia temporária (o ficheiro pode estar aberto no Excel)."""
    with tempfile.TemporaryDirectory() as tmp:
        copia = os.path.join(tmp, "rendas.xlsx")
        shutil.copyfile(caminho, copia)
        wb = openpyxl.load_workbook(copia, data_only=True, read_only=True)
        linhas = [list(r) for r in wb[FOLHA_RENDAS].iter_rows(values_only=True)]
        wb.close()

    anos_disponiveis = sorted(a for a in COL_RENDA_POR_ANO if a <= ano) or [min(COL_RENDA_POR_ANO)]
    col_renda = COL_RENDA_POR_ANO[anos_disponiveis[-1]]
    ultima_col = max(col_renda, COL_DATA_TERMINO)
    contratos = []
    for v in linhas:
        v = v + [None] * (ultima_col - len(v))
        cliente, empresa = v[COL_CLIENTE - 1], v[COL_EMPRESA - 1]
        if not cliente or not empresa or str(cliente).strip().lower() == "cliente":
            continue
        termino = v[COL_DATA_TERMINO - 1]
        if hasattr(termino, "year") and termino.date() < date(ano, 1, 1):
            continue
        renda = v[col_renda - 1]
        contratos.append(ContratoRenda(
            empresa=str(empresa).strip(),
            cliente=str(cliente).strip(),
            espaco=str(v[COL_ESPACO - 1] or "").strip(),
            fracao=str(v[COL_FRACAO - 1] or "").strip(),
            renda=float(renda) if isinstance(renda, (int, float)) and renda > 0 else None,
            titulares=[palavras_nome(t) for t in str(cliente).split("/") if palavras_nome(t)],
        ))
    return contratos


def _palavra_bate(p, t, ultima) -> bool:
    # A última pode vir cortada; as outras só se tiverem perdido uma letra
    # acentuada no fim (o banco escreve "JOS  FRANCISCO" para "JOSÉ FRANCISCO").
    return t == p or (t.startswith(p) and (ultima or (len(p) >= 3 and len(t) == len(p) + 1)))


def _nome_bate(pal_desc, pal_titular) -> bool:
    """As palavras do descritivo aparecem, pela mesma ordem, no nome do
    titular - a primeira é o primeiro nome, a última pode estar cortada."""
    if len(pal_desc) < 2 or not pal_titular or not _palavra_bate(pal_desc[0], pal_titular[0], False):
        return False
    j = 1
    for k, p in enumerate(pal_desc[1:], start=1):
        ultima = k == len(pal_desc) - 1
        while j < len(pal_titular):
            t = pal_titular[j]
            j += 1
            if _palavra_bate(p, t, ultima):
                break
        else:
            return False
    return True


def _empresa_corresponde(empresa_extrato: str, sigla_contrato: str) -> bool:
    sigla = normalizar(sigla_contrato)
    alvo = _SIGLAS_EMPRESA.get(sigla, sigla_contrato)
    return chave_empresa(alvo) in chave_empresa(empresa_extrato) or sigla in normalizar(empresa_extrato)


def contrato_do_movimento(descricao: str, valor: float, empresa: str, contratos: list) -> Optional[ContratoRenda]:
    """Contrato de renda a que corresponde um recebimento (ou None). Mais
    conservador que o preencher_mapa.py: só aceita quando há um único
    contrato possível e o valor é a renda (ou um número inteiro de meses)."""
    if valor <= 0 or not str(descricao).upper().startswith(PREFIXOS_TRF):
        return None
    pal = [p for p in palavras_nome(descricao)[1:] if p not in TITULOS]
    tentativas = [pal] + ([pal[:-1]] if len(pal) > 2 and len(pal[-1]) <= 2 else [])
    candidatos = []
    for tentativa in tentativas:
        candidatos = [c for c in contratos if any(_nome_bate(tentativa, t) for t in c.titulares)]
        if candidatos:
            break
    if len(candidatos) > 1:
        candidatos = [c for c in candidatos if c.renda and abs(c.renda - valor) <= TOLERANCIA_RENDA] or candidatos
    if len(candidatos) > 1:
        candidatos = [c for c in candidatos if _empresa_corresponde(empresa, c.empresa)] or candidatos
    if len(candidatos) != 1:
        return None
    contrato = candidatos[0]
    if not contrato.renda:
        return None
    meses = valor / contrato.renda
    if round(meses) < 1 or abs(meses - round(meses)) > TOLERANCIA_ATUALIZACAO_RENDA * round(meses):
        return None
    return contrato


# ---------------------------------------------------------------------------
# Recorrentes (a partir dos extratos)
# ---------------------------------------------------------------------------

MESES_ANALISE_RECORRENTES = 6
MIN_MESES_RECORRENTE = 5  # presente em pelo menos 5 dos últimos 6 meses completos
MAX_COEF_VARIACAO_RECORRENTE = 0.25


def _chave_recorrente(m) -> tuple:
    sinal = "R" if m.valor >= 0 else "P"
    descricao = normalizar(re.sub(r"\d+", "", str(m.descricao or "")))
    return sinal, chave_empresa(m.empresa), descricao


def _meses_completos_antes(ate: date, n: int) -> list:
    """Os `n` meses civis completos antes do mês de `ate` (mais antigo primeiro)."""
    ano, mes = ate.year, ate.month
    meses = []
    for _ in range(n):
        ano, mes = (ano, mes - 1) if mes > 1 else (ano - 1, 12)
        meses.append((ano, mes))
    return list(reversed(meses))


@dataclass
class FluxoRecorrente:
    chave: tuple
    empresa: str
    descricao: str
    valor_mensal: float  # sinalizado: + recebimento, - pagamento
    dia_mes: int
    fonte: str  # "renda" | "recorrente"


def _dia_tipico(dias: list) -> int:
    return int(round(median(dias))) if dias else 1


def detetar_fluxos(movimentos: list, ate: date, contratos: list) -> tuple:
    """Devolve (fluxos, ids_conhecidos):
    - fluxos: lista de FluxoRecorrente (rendas por contrato + recorrentes
      por grupo), com valor mensal e dia típico do mês;
    - ids_conhecidos: ids dos movimentos do histórico que pertencem a um
      desses fluxos (saem do sorteio de dias - ver módulo).
    Só usa movimentos até `ate` (para o backtest ser honesto)."""
    movimentos = [m for m in movimentos if m.dia <= ate]
    meses = _meses_completos_antes(ate + timedelta(days=1), MESES_ANALISE_RECORRENTES)
    primeiro_mes = date(*meses[0], 1)

    fluxos, ids_conhecidos = [], set()

    # --- rendas: por contrato, com a renda do Mapa de Rendas como valor
    por_contrato = {}
    for m in movimentos:
        c = contrato_do_movimento(m.descricao, m.valor, m.empresa, contratos) if contratos else None
        if c is not None:
            ids_conhecidos.add(m.id)
            if m.dia >= primeiro_mes:
                por_contrato.setdefault(id(c), (c, []))[1].append(m)
    dias_todas_rendas = [m.dia.day for _, ms in por_contrato.values() for m in ms]
    for c, ms in por_contrato.values():
        ms.sort(key=lambda m: m.dia)
        # o valor que está de facto a entrar (o último pagamento, por mês) -
        # o do Mapa de Rendas pode ainda não ter a atualização anual
        ultimo = ms[-1]
        fluxos.append(FluxoRecorrente(
            chave=("renda", c.empresa, c.cliente, c.espaco, c.fracao),
            empresa=ultimo.empresa,
            descricao=f"Renda {c.espaco} - Fração {c.fracao} ({c.cliente})",
            valor_mensal=round(ultimo.valor / round(ultimo.valor / c.renda), 2),
            dia_mes=_dia_tipico([m.dia.day for m in ms] or dias_todas_rendas),
            fonte="renda",
        ))

    # --- recorrentes: mesmo tipo + empresa + descritivo sem números, quase
    # todos os meses, com total mensal estável
    # (o grupo junta TODO o histórico: só os últimos meses decidem se é
    # recorrente, mas os movimentos mais antigos do mesmo grupo também têm
    # de sair do sorteio, senão contavam a dobrar nos dias antigos sorteados)
    grupos = {}
    for m in movimentos:
        if m.id in ids_conhecidos:
            continue
        grupos.setdefault(_chave_recorrente(m), []).append(m)
    for chave, ms in grupos.items():
        por_mes = {}
        for m in ms:
            if (m.dia.year, m.dia.month) in meses:
                por_mes.setdefault((m.dia.year, m.dia.month), []).append(m)
        if len(por_mes) < MIN_MESES_RECORRENTE or any(len(v) > 2 for v in por_mes.values()):
            continue  # raro de mais, ou várias vezes por mês (semanal/variável)
        totais = [sum(m.valor for m in v) for v in por_mes.values()]
        media = sum(totais) / len(totais)
        if media == 0:
            continue
        desvio = (sum((t - media) ** 2 for t in totais) / len(totais)) ** 0.5
        if desvio / abs(media) > MAX_COEF_VARIACAO_RECORRENTE:
            continue
        ids_conhecidos.update(m.id for m in ms)
        fluxos.append(FluxoRecorrente(
            chave=("recorrente",) + chave,
            empresa=ms[-1].empresa,
            descricao=ms[-1].descricao,
            valor_mensal=float(median(totais)),
            dia_mes=_dia_tipico([m.dia.day for v in por_mes.values() for m in v]),
            fonte="recorrente",
        ))
    return fluxos, ids_conhecidos


def _dia_util(d: date) -> date:
    while d.weekday() >= 5:
        d += timedelta(days=1)
    return d


def fluxos_nos_dias(fluxos: list, dias_futuros: list) -> dict:
    """{dia: [(fluxo, valor)]} - cada fluxo mensal cai no seu dia típico do
    mês (passado para o dia útil seguinte ao fim de semana), uma vez por
    cada mês abrangido pelos `dias_futuros`."""
    if not dias_futuros:
        return {}
    conjunto = set(dias_futuros)
    meses = sorted({(d.year, d.month) for d in dias_futuros})
    resultado = {}
    for f in fluxos:
        for ano, mes in meses:
            ultimo = (date(ano + (mes == 12), mes % 12 + 1, 1) - timedelta(days=1)).day
            dia = _dia_util(date(ano, mes, min(f.dia_mes, ultimo)))
            if dia in conjunto:
                resultado.setdefault(dia, []).append((f, f.valor_mensal))
    return resultado
