"""Folha "Resumo" do Mapa de Pagamentos e Recebimentos de um mês, dia a dia
(pedido 19/10/2026: o resumo mensal do dashboard tem de bater com o Mapa
desse mês - recebido, pago, saldo bancário, saldo bancário disponível,
CPCVs e escrituras).

Colunas do Resumo desde julho (linha 3 é o cabeçalho, um dia por linha):
  A dia | B previsão receb. | C Recebido | E CPCVs (um por linha) |
  F Valor Tabela | G Valor Venda | H Valor Recebido (CPCVs) |
  I Escrituras (refs) | J Valor Recebido (escrituras) | K previsão pag. |
  L Pago | O Saldo Bancário Disponível | P Saldo Bancário Indisponível
Saldo bancário = disponível + indisponível. Os Mapas de janeiro a junho
têm outras formas - ver colunas_do_resumo, que encontra as colunas pelo
nome. Um valor que dá erro no Mapa (ex. #REF!) fica None.

C e L são fórmulas para o total da folha do dia (='06'!D95, que é
=SUM(D5:D94) ou =SUBTOTAL(109,Tabela1[Valor Pago])). Os scripts gravam o
Mapa com o openpyxl, que não guarda o valor calculado das fórmulas, por
isso aqui calculam-se como o Excel: só as formas que o Mapa usa (número,
referência a outra célula, SUM/SUBTOTAL de um intervalo ou coluna de
Tabela, somas de referências). Uma célula que não se sabe calcular fica
None e quem chama usa outra fonte para esse valor.
"""
import logging
import os
import re
from datetime import date
from typing import Any, Dict, Optional

import openpyxl
from openpyxl.utils import range_boundaries

from app.services.onedrive_sync import caminho_mapa

logger = logging.getLogger(__name__)

_REF = re.compile(r"^\+?(?:'([^']+)'|([A-Za-z0-9_]+))!\$?([A-Z]{1,3})\$?(\d+)$")
_LOCAL = re.compile(r"^\+?\$?([A-Z]{1,3})\$?(\d+)$")
_SOMA = re.compile(r"^(?:SUM|SUBTOTAL\(\d+,)\(?\$?([A-Z]{1,3})\$?(\d+):\$?([A-Z]{1,3})\$?(\d+)\)$", re.I)
_TABELA = re.compile(r"^SUBTOTAL\(\d+,([A-Za-z0-9_]+)\[([^\]]+)\]\)$", re.I)
_CACHE: Dict[tuple, Optional[Dict[int, Dict[str, Any]]]] = {}


class _Calculadora:
    def __init__(self, wb):
        self.wb = wb

    def valor(self, ws, coord: str, profundidade: int = 0) -> Optional[float]:
        v = ws[coord].value
        if v is None:
            return 0.0
        if isinstance(v, (int, float)):
            return float(v)
        if not isinstance(v, str) or not v.startswith("=") or profundidade > 5:
            return None
        return self._formula(ws, v[1:].replace(" ", ""), profundidade)

    def _formula(self, ws, f: str, prof: int) -> Optional[float]:
        m = _REF.match(f)
        if m:
            nome = m.group(1) or m.group(2)
            return self.valor(self.wb[nome], f"{m.group(3)}{m.group(4)}", prof + 1) if nome in self.wb.sheetnames else None
        m = _LOCAL.match(f)
        if m:
            return self.valor(ws, f"{m.group(1)}{m.group(2)}", prof + 1)
        m = _SOMA.match(f)
        if m:
            c1, r1, c2, r2 = range_boundaries(f"{m.group(1)}{m.group(2)}:{m.group(3)}{m.group(4)}")
            return float(sum(x for row in ws.iter_rows(min_row=r1, max_row=r2, min_col=c1, max_col=c2, values_only=True)
                             for x in row if isinstance(x, (int, float))))
        m = _TABELA.match(f)
        if m:
            return self._coluna_tabela(m.group(1), m.group(2))
        if "+" in f.lstrip("+") and not any(c in f for c in "*/(-"):
            partes = [self._formula(ws, p, prof + 1) for p in f.lstrip("+").split("+")]
            return None if any(p is None for p in partes) else float(sum(partes))
        return None

    def _coluna_tabela(self, nome: str, coluna: str) -> Optional[float]:
        dono = next((w for w in self.wb.worksheets if nome in w.tables), None)
        if dono is None:
            return None
        tabela = dono.tables[nome]
        c1, r1, c2, r2 = range_boundaries(tabela.ref)
        # a fórmula chega sem espaços ("Valor Pago" -> "ValorPago"): comparar sem eles
        sem_espacos = lambda texto: re.sub(r"\s+", "", str(texto or "")).lower()
        cabecalho = [sem_espacos(dono.cell(r1, c).value) for c in range(c1, c2 + 1)]
        if sem_espacos(coluna) not in cabecalho:
            return None
        col = c1 + cabecalho.index(sem_espacos(coluna))
        fim = r2 - (tabela.totalsRowCount or 0)
        return float(sum(x for (x,) in dono.iter_rows(min_row=r1 + 1, max_row=fim, min_col=col, max_col=col,
                                                       values_only=True) if isinstance(x, (int, float))))


def _linhas_de_texto(valor) -> int:
    return len([l for l in str(valor or "").splitlines() if l.strip()])


def _texto(valor) -> str:
    return re.sub(r"\s+", " ", str(valor or "")).strip().lower()


def colunas_do_resumo(ws) -> Optional[Dict[str, Any]]:
    """O Resumo mudou de forma ao longo do ano (jan-mai: Recebido/Total
    RECEBIDO, Pago/Valor PAGO e um só Saldo Bancário; jun: Escrituras e CPCV
    à cabeça; desde jul: CPCVs/escrituras com Valor Recebido e saldo
    disponível/indisponível) - as colunas encontram-se pelo nome no
    cabeçalho. Devolve {campo: letra} e a linha do cabeçalho, ou None."""
    from openpyxl.utils import get_column_letter

    linha = next((r for r in range(1, 7) if any(_texto(ws.cell(r, c).value) == "previsão" for c in range(2, 20))), None)
    if linha is None:
        return None
    cab = {c: _texto(ws.cell(linha, c).value) for c in range(2, 25)}
    grupo = {c: _texto(ws.cell(linha - 1, c).value) for c in range(2, 25)} if linha > 1 else {}
    inicio_pag = next((c for c, g in grupo.items() if g.startswith("pagamentos")), None)
    if inicio_pag is None:
        return None
    letra = get_column_letter
    achar = lambda pred, de=2, ate=25: next((c for c in range(de, ate) if pred(cab.get(c, ""), grupo.get(c, ""))), None)

    rec = achar(lambda h, g: h == "total recebido", ate=inicio_pag) or achar(lambda h, g: h == "recebido", ate=inicio_pag)
    pag = achar(lambda h, g: h == "valor pago", de=inicio_pag) or achar(lambda h, g: h == "pago", de=inicio_pag)
    if not rec or not pag:
        return None
    colunas: Dict[str, Any] = {"linha": linha, "recebido": letra(rec), "pago": letra(pag)}
    disp = achar(lambda h, g: ("dispon" in h or "dispon" in g) and "indispon" not in h + g)
    indisp = achar(lambda h, g: "indispon" in h or "indispon" in g)
    saldo = achar(lambda h, g: "bancário" in h + " " + g and "dispon" not in h + g, de=inicio_pag)
    for campo, c in (("saldo_disponivel", disp), ("saldo_indisponivel", indisp), ("saldo", saldo)):
        if c:
            colunas[campo] = letra(c)
    for tipo, nomes in (("cpcv", ("# de cpcvs", "cpcv")), ("escritura", ("# de escrituras", "escrituras"))):
        lista = achar(lambda h, g, n=nomes[0]: h == n, ate=inicio_pag)
        if lista:  # desde jul: a lista e, à frente, o Valor Recebido
            valor = achar(lambda h, g: h == "valor recebido", de=lista + 1, ate=inicio_pag)
            colunas[f"n_{tipo}"] = letra(lista)
            if valor:
                colunas[tipo] = letra(valor)
        else:  # junho: a coluna "CPCV"/"Escrituras" é o número deles, não o valor
            c = achar(lambda h, g, n=nomes[1]: h == n, ate=inicio_pag)
            if c:
                colunas[f"contagem_{tipo}"] = letra(c)
    return colunas


def _vendas(ws, calc, colunas: Dict[str, Any], tipo: str, r: int) -> Dict[str, Any]:
    """{tipo: valor recebido ou None, n_tipo: quantos ou None} - None quando
    o Resumo desse mês não tem essa informação."""
    valor = calc.valor(ws, f"{colunas[tipo]}{r}") if tipo in colunas else None
    if f"n_{tipo}" in colunas:
        n = _linhas_de_texto(ws[f"{colunas[f'n_{tipo}']}{r}"].value)
    elif f"contagem_{tipo}" in colunas:
        n = int(calc.valor(ws, f"{colunas[f'contagem_{tipo}']}{r}") or 0)
    else:
        n = None
    return {tipo: valor, f"n_{tipo}": n}


def ler_resumo(caminho: str) -> Optional[Dict[int, Dict[str, Any]]]:
    """{dia: {recebido, pago, saldo_disponivel, saldo_indisponivel,
    saldo_bancario, cpcv, n_cpcv, escritura, n_escritura}} do Resumo deste
    ficheiro (None num valor que o Resumo não tem ou que dá erro), ou None se
    não existe/não se reconhece o formato. Guardado em memória enquanto o
    ficheiro não mudar."""
    try:
        estado = os.stat(caminho)
    except OSError:
        return None
    chave = (caminho, estado.st_mtime_ns, estado.st_size)
    if chave not in _CACHE:
        if len(_CACHE) > 24:
            _CACHE.clear()
        _CACHE[chave] = _ler(caminho)
    return _CACHE[chave]


def _ler(caminho: str) -> Optional[Dict[int, Dict[str, Any]]]:
    try:
        wb = openpyxl.load_workbook(caminho)
    except Exception:
        logger.warning("Não consegui abrir o Mapa %s", caminho, exc_info=True)
        return None
    colunas = colunas_do_resumo(wb["Resumo"]) if "Resumo" in wb.sheetnames else None
    if not colunas:
        logger.warning("Resumo do Mapa %s sem o cabeçalho esperado - não o leio", caminho)
        return None
    ws, calc = wb["Resumo"], _Calculadora(wb)

    def ler(campo: str, r: int) -> Optional[float]:
        coluna = colunas.get(campo)
        if not coluna or ws[f"{coluna}{r}"].value is None and campo.startswith("saldo"):
            return None  # sem esse valor no Resumo, ou sem saldo nessa linha (fim de semana)
        return calc.valor(ws, f"{coluna}{r}")

    dias: Dict[int, Dict[str, Any]] = {}
    for r in range(colunas["linha"] + 1, ws.max_row + 1):
        dia = ws[f"A{r}"].value
        if not isinstance(dia, int) or not 1 <= dia <= 31:
            continue
        disponivel, indisponivel, saldo = ler("saldo_disponivel", r), ler("saldo_indisponivel", r), ler("saldo", r)
        dias[dia] = {
            "recebido": ler("recebido", r), "pago": ler("pago", r),
            "saldo_disponivel": disponivel, "saldo_indisponivel": indisponivel,
            "saldo_bancario": (disponivel + (indisponivel or 0.0)) if disponivel is not None else saldo,
            **{chave: valor for tipo in ("cpcv", "escritura") for chave, valor in _vendas(ws, calc, colunas, tipo, r).items()},
        }
    return dias


def resumo_do_mes(ano: int, mes: int) -> Optional[Dict[date, Dict[str, Any]]]:
    """O Resumo do Mapa desse mês por data, ou None (sem Mapa/sem acesso)."""
    try:
        caminho = caminho_mapa(date(ano, mes, 1))
    except Exception:  # ONEDRIVE_RAIZ em falta, ...
        return None
    dias = ler_resumo(caminho)
    if not dias:
        return None
    resultado = {}
    for d, v in dias.items():
        try:
            resultado[date(ano, mes, d)] = dict(v)  # cópia: quem chama pode mexer, a cache não
        except ValueError:  # 31 num mês de 30 dias
            continue
    return resultado
