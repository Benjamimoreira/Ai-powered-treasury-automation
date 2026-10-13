"""Previsão de tesouraria mensal (Predição > Previsão mensal, e a folha
"Previsão" de cada Mapa de Pagamentos e Recebimentos).

O ficheiro da direção financeira ("Previsão Tesouraria_OUT2026.xlsx") tem
blocos assim, uns por baixo dos outros:

    (EUR)              REA       FOR       FOR   ...  FOR         FOR
    DESPESAS           SET 2026  OUT 2026  ...       YTD DEZ 26  Grand Total
    Ordenados          103000    103000    ...
    ...
    Grand Total        ...

Blocos conhecidos: AUTOS DE OBRA (detalhe por obra da rubrica "Autos de
Obra" das despesas - não se soma outra vez), DESPESAS (pagamentos),
Vendas Comerciais e Sinais e Reforços (recebimentos). As colunas YTD/Grand
Total e as linhas de total ("Grand Total", "Vendas Diretas", "Sinais +
Reforços", "GT Comercial") ignoram-se - os totais calculam-se aqui.

O realizado de cada mês vem das linhas do Mapa (linhas_mapa) já
confirmadas no extrato - o mesmo "Recebido"/"Pago" do Resumo mensal
(resumo_saldos.py).
"""
import io
import re
from collections import defaultdict
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional, Tuple

import openpyxl
from sqlalchemy.orm import Session

from app.db.models import LinhaMapa, PrevisaoMensal
from app.services.reconciliador import remover_acentos
from app.services.resumo_saldos import tipo_venda

MESES = {"JAN": 1, "FEV": 2, "MAR": 3, "ABR": 4, "MAI": 5, "JUN": 6,
         "JUL": 7, "AGO": 8, "SET": 9, "OUT": 10, "NOV": 11, "DEZ": 12}

# título do bloco (sem acentos, minúsculas, começa por) -> (tipo, grupo)
BLOCOS = [
    ("autos de obra", ("detalhe", "Autos de obra (por obra)")),
    ("despesas", ("pagamento", "Despesas")),
    ("vendas comerciais", ("recebimento", "Vendas diretas")),
    ("sinais e reforcos", ("recebimento", "Sinais e reforços")),
]
LINHAS_DE_TOTAL = {"grand total", "vendas diretas", "sinais + reforcos", "gt comercial", "total"}


def _normal(texto) -> str:
    return re.sub(r"\s+", " ", remover_acentos(str(texto or "")).strip().lower())


def _mes_da_coluna(rotulo) -> Optional[Tuple[int, int]]:
    """"SET 2026" -> (2026, 9); "JAN 27" -> (2027, 1); "YTD DEZ 26" -> None."""
    m = re.fullmatch(r"([A-Za-z]{3})\s+(\d{2}|\d{4})", str(rotulo or "").strip())
    if not m or m.group(1).upper() not in MESES:
        return None
    ano = int(m.group(2))
    return (ano + 2000 if ano < 100 else ano), MESES[m.group(1).upper()]


def ler_ficheiro(conteudo: bytes) -> Tuple[List[Dict[str, Any]], List[str]]:
    """Linhas {ano, mes, tipo, grupo, rubrica, valor, realizado} e avisos
    (blocos desconhecidos que ficaram de fora)."""
    wb = openpyxl.load_workbook(io.BytesIO(conteudo), data_only=True, read_only=True)
    linhas: List[Dict[str, Any]] = []
    avisos: List[str] = []
    for ws in wb.worksheets:
        grelha = [list(r) for r in ws.iter_rows(values_only=True)]
        i = 0
        while i < len(grelha):
            linha = grelha[i]
            col_eur = next((j for j, v in enumerate(linha) if _normal(v) == "(eur)"), None)
            if col_eur is None or i + 1 >= len(grelha):
                i += 1
                continue
            cabecalho = grelha[i + 1]
            titulo = _normal(cabecalho[col_eur])
            bloco = next((b for prefixo, b in BLOCOS if titulo.startswith(prefixo)), None)
            if bloco is None:
                avisos.append(f"Bloco desconhecido ignorado: {cabecalho[col_eur]!r}")
                i += 2
                continue
            tipo, grupo = bloco
            # colunas de meses: rótulo do mês na linha do título, REA/FOR na de cima
            colunas = {j: (_mes_da_coluna(v), _normal(linha[j] if j < len(linha) else "") == "rea")
                       for j, v in enumerate(cabecalho) if j > col_eur}
            colunas = {j: c for j, c in colunas.items() if c[0]}
            k = i + 2
            while k < len(grelha):
                dados = grelha[k]
                rotulo = dados[col_eur] if col_eur < len(dados) else None
                if rotulo is None or str(rotulo).strip() == "":
                    if all(v is None for v in dados):
                        break  # linha em branco = fim do bloco
                    k += 1
                    continue
                if _normal(rotulo) == "(eur)":
                    break
                if _normal(rotulo) not in LINHAS_DE_TOTAL:
                    for j, ((ano, mes), real) in colunas.items():
                        valor = dados[j] if j < len(dados) else None
                        if isinstance(valor, (int, float)) and abs(valor) >= 0.005:
                            linhas.append({"ano": ano, "mes": mes, "tipo": tipo, "grupo": grupo,
                                           "rubrica": str(rotulo).strip(), "valor": round(float(valor), 2),
                                           "realizado": 1 if real else 0})
                k += 1
            i = k
    if not linhas:
        raise ValueError("Não encontrei nenhum bloco de previsão no ficheiro (procuro a célula \"(EUR)\" "
                         "com os meses ao lado, ex. \"OUT 2026\").")
    return linhas, avisos


def importar(db: Session, conteudo: bytes, ficheiro: str) -> Dict[str, Any]:
    """Substitui a previsão dos meses que o ficheiro tem (os outros meses ficam)."""
    linhas, avisos = ler_ficheiro(conteudo)
    meses = sorted({(l["ano"], l["mes"]) for l in linhas})
    for ano, mes in meses:
        db.query(PrevisaoMensal).filter(PrevisaoMensal.ano == ano, PrevisaoMensal.mes == mes).delete()
    agora = datetime.now(timezone.utc).replace(tzinfo=None)
    db.add_all([PrevisaoMensal(**l, ficheiro=ficheiro, importado_em=agora) for l in linhas])
    db.commit()
    return {"ficheiro": ficheiro, "linhas": len(linhas), "meses": [f"{a}-{m:02d}" for a, m in meses],
            "avisos": avisos}


def _realizado_por_mes(db: Session, meses: List[Tuple[int, int]]) -> Dict[Tuple[int, int], Dict[str, float]]:
    """Recebido/pago confirmados no Mapa por mês, e as vendas (escrituras;
    CPCVs + reforços de sinal) para comparar com "Vendas diretas" e "Sinais
    e reforços"."""
    resultado: Dict[Tuple[int, int], Dict[str, float]] = defaultdict(lambda: defaultdict(float))
    if not meses:
        return resultado
    from datetime import date
    inicio = date(min(meses)[0], min(meses)[1], 1)
    for l in db.query(LinhaMapa).filter(LinhaMapa.dia >= inicio).all():
        chave = (l.dia.year, l.dia.month)
        if chave not in meses or l.pago is None:
            continue
        r = resultado[chave]
        if l.tipo == "recebimento":
            r["recebido"] += l.pago
            venda = tipo_venda(l)
            if venda == "escritura":
                r["vendas_diretas"] += l.pago
            elif venda in ("cpcv", "reforco_sinal"):
                r["sinais_reforcos"] += l.pago
        elif l.tipo == "pagamento":
            r["pago"] += abs(l.pago)
    return resultado


def resumo(db: Session) -> Dict[str, Any]:
    linhas = db.query(PrevisaoMensal).order_by(PrevisaoMensal.ano, PrevisaoMensal.mes, PrevisaoMensal.id).all()
    if not linhas:
        return {"ficheiro": None, "importado_em": None, "meses": []}
    por_mes: Dict[Tuple[int, int], List[PrevisaoMensal]] = defaultdict(list)
    for l in linhas:
        por_mes[(l.ano, l.mes)].append(l)
    realizado = _realizado_por_mes(db, list(por_mes))
    meses = []
    for (ano, mes), itens in sorted(por_mes.items()):
        rec = sum(l.valor for l in itens if l.tipo == "recebimento")
        pag = sum(l.valor for l in itens if l.tipo == "pagamento")
        r = realizado.get((ano, mes), {})
        meses.append({
            "ano": ano, "mes": mes, "realizado_no_ficheiro": any(l.realizado for l in itens),
            "previsto_recebimentos": round(rec, 2), "previsto_pagamentos": round(pag, 2),
            "previsto_liquido": round(rec - pag, 2),
            "previsto_vendas_diretas": round(sum(l.valor for l in itens if l.grupo == "Vendas diretas"), 2),
            "previsto_sinais_reforcos": round(sum(l.valor for l in itens if l.grupo == "Sinais e reforços"), 2),
            "recebido": round(r.get("recebido", 0.0), 2), "pago": round(r.get("pago", 0.0), 2),
            "liquido": round(r.get("recebido", 0.0) - r.get("pago", 0.0), 2),
            "vendas_diretas": round(r.get("vendas_diretas", 0.0), 2),
            "sinais_reforcos": round(r.get("sinais_reforcos", 0.0), 2),
            "linhas": [{"tipo": l.tipo, "grupo": l.grupo, "rubrica": l.rubrica, "valor": l.valor} for l in itens],
        })
    ultima = max(linhas, key=lambda l: l.importado_em)
    return {"ficheiro": ultima.ficheiro, "importado_em": ultima.importado_em.isoformat(timespec="seconds") + "Z",
            "meses": meses}
