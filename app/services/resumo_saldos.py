"""Resumo mensal dos saldos bancários (Análise de Extratos > Resumo mensal).

Por mês: liquidez (saldo disponível de todas as contas juntas - a mesma
série do gráfico da Visão Geral, saldos.serie_saldo_total) no início, no
fim, média, mínimo e máximo; e o que entrou de vendas de imóveis, separado
em CPCV, reforço de sinal e escritura (linhas de recebimento do Mapa - a
mesma deteção da tabela "CPCVs / Escrituras", reconciliador._e_cpcv_ou_escritura,
mais as linhas "Reforço Sinal - <ref>").

Também devolve a liquidez dia a dia com essas entradas, para o gráfico do
mês escolhido no dashboard.
"""
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import LinhaMapa
from app.services.reconciliador import _e_cpcv_ou_escritura, remover_acentos
from app.services.saldos import _serie_saldo_total_bruta

TIPOS_VENDA = ("cpcv", "reforco_sinal", "escritura")


def tipo_venda(l: LinhaMapa) -> Optional[str]:
    """cpcv | reforco_sinal | escritura para recebimentos de vendas de
    imóveis; None para o resto. Olha para a Descrição e a Imputação juntas
    (há escrituras com Imputação "Escritura - 00.AV.04.002, ..." ou
    "Vendas"). "Reforço Sinal - 00.PO.23.001" tem muitas vezes a Imputação
    "... Cpcv ...", por isso o reforço vê-se primeiro. "Reforço de caixa"/
    "REFORCO SALDO" não são vendas (não dizem "sinal"). Sem nenhuma das
    palavras, uma linha com código de fração (ex. "DEPOSITO" de um sinal -
    reconciliador._e_cpcv_ou_escritura) conta como CPCV."""
    if l.tipo != "recebimento":
        return None
    texto = remover_acentos(f"{l.descricao or ''} {l.imputacao or ''}").lower()
    if "reforco sinal" in texto or "reforco de sinal" in texto:
        return "reforco_sinal"
    if "escritura" in texto:
        return "escritura"
    if "cpcv" in texto or _e_cpcv_ou_escritura(l):
        return "cpcv"
    return None


def _vazio_vendas() -> Dict[str, float]:
    campos = {t: 0.0 for t in TIPOS_VENDA}
    campos.update({f"n_{t}": 0 for t in TIPOS_VENDA})
    campos.update({f"pendente_{t}": 0.0 for t in TIPOS_VENDA})
    return campos


def resumo_mensal_saldos(db: Session, ano: int) -> Dict[str, Any]:
    inicio, fim = date(ano, 1, 1), date(ano, 12, 31)

    # vendas por dia: recebido (pago) e ainda por confirmar no extrato (só previsto)
    vendas_dia: Dict[date, Dict[str, float]] = defaultdict(_vazio_vendas)
    linhas = (
        db.query(LinhaMapa)
        .filter(LinhaMapa.tipo == "recebimento", LinhaMapa.dia >= inicio, LinhaMapa.dia <= fim)
        .all()
    )
    for l in linhas:
        tipo = tipo_venda(l)
        if tipo is None:
            continue
        v = vendas_dia[l.dia]
        if l.pago is not None:
            v[tipo] += l.pago
            v[f"n_{tipo}"] += 1
        elif l.previsto:
            v[f"pendente_{tipo}"] += l.previsto

    serie = [p for p in _serie_saldo_total_bruta(db) if inicio <= p["dia"] <= fim]

    diario: List[Dict[str, Any]] = []
    for p in serie:
        v = vendas_dia.get(p["dia"]) or _vazio_vendas()
        diario.append({
            "dia": p["dia"].isoformat(),
            "liquidez": round(p["saldo_disponivel_total"], 2),
            "saldo_contabilistico": round(p["saldo_contabilistico_total"], 2),
            **{t: round(v[t], 2) for t in TIPOS_VENDA},
        })

    meses: List[Dict[str, Any]] = []
    for mes in range(1, 13):
        dias_mes = [p for p in serie if p["dia"].month == mes]
        vendas_mes = _vazio_vendas()
        for dia, v in vendas_dia.items():
            if dia.month == mes:
                for chave, valor in v.items():
                    vendas_mes[chave] += valor
        if not dias_mes and not any(vendas_mes.values()):
            continue
        linha: Dict[str, Any] = {"mes": mes, "dias_com_saldo": len(dias_mes)}
        if dias_mes:
            liquidez = [p["saldo_disponivel_total"] for p in dias_mes]
            minimo = min(dias_mes, key=lambda p: p["saldo_disponivel_total"])
            maximo = max(dias_mes, key=lambda p: p["saldo_disponivel_total"])
            linha.update({
                "saldo_inicio": round(liquidez[0], 2),
                "saldo_fim": round(liquidez[-1], 2),
                "variacao": round(liquidez[-1] - liquidez[0], 2),
                "liquidez_media": round(sum(liquidez) / len(liquidez), 2),
                "liquidez_minima": round(minimo["saldo_disponivel_total"], 2),
                "dia_minimo": minimo["dia"].isoformat(),
                "liquidez_maxima": round(maximo["saldo_disponivel_total"], 2),
                "dia_maximo": maximo["dia"].isoformat(),
            })
        linha.update({k: round(v, 2) if isinstance(v, float) else v for k, v in vendas_mes.items()})
        linha["total_vendas"] = round(sum(vendas_mes[t] for t in TIPOS_VENDA), 2)
        meses.append(linha)

    return {"ano": ano, "meses": meses, "diario": diario}
