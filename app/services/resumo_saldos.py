"""Resumo mensal dos saldos bancários (Análise de Extratos > Resumo mensal).

Por mês: liquidez (saldo disponível de todas as contas juntas - a mesma
série do gráfico da Visão Geral, mapa_saldos_excel.serie_saldo_total) no início, no
fim, média, mínimo e máximo; e o que entrou de vendas de imóveis, separado
em CPCV, reforço de sinal e escritura (linhas de recebimento do Mapa - a
mesma deteção da tabela "CPCVs / Escrituras", reconciliador._e_cpcv_ou_escritura,
mais as linhas "Reforço Sinal - <ref>").

Recebido e pago de cada dia vêm dos movimentos dos extratos bancários, pelo
dia do banco - não das linhas do Mapa de Pagamentos e Recebimentos, que põe
os movimentos do fim de semana na folha de segunda e fazia contar duas
vezes o que já tinha saído do saldo de sábado (19/10/2026: 202 107,71 +
37 447,71 dava 239 555,42 contra 242 141,15). Assim, saldo de um dia +
recebido − pago do dia seguinte = saldo do dia seguinte.

Sem as transferências entre empresas do grupo no mesmo dia (um pagamento
numa empresa e um recebimento do mesmo valor noutra): anulam-se no saldo
total, mas somadas inchavam o recebido e o pago (09/10/2026: 328 216,91
recebido e 354 404,51 pago). Ficam à parte, em transferencias_internas.

Também devolve a liquidez dia a dia com essas entradas, para o gráfico do
mês escolhido no dashboard.
"""
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import LinhaMapa, MovimentoBancario
from app.services.reconciliador import _e_cpcv_ou_escritura, remover_acentos
from app.services.mapa_saldos_excel import serie_saldo_total_bruta

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


def transferencias_internas_mesmo_dia(movimentos: list) -> set:
    """Ids dos pares pagamento/recebimento do mesmo valor (ao cêntimo), no
    mesmo dia, em empresas diferentes - dinheiro que só mudou de empresa
    dentro do grupo. Cada movimento entra no máximo num par."""
    por_dia_valor: Dict[tuple, List[MovimentoBancario]] = defaultdict(list)
    for m in movimentos:
        if m.valor < 0:
            por_dia_valor[(m.dia, round(-m.valor, 2))].append(m)
    ids = set()
    for m in sorted((m for m in movimentos if m.valor > 0), key=lambda m: (m.dia, m.id)):
        candidatos = [p for p in por_dia_valor.get((m.dia, round(m.valor, 2)), [])
                      if p.id not in ids and p.empresa != m.empresa]
        if candidatos:
            ids.update((m.id, candidatos[0].id))
    return ids


def resumo_mensal_saldos(db: Session, ano: int) -> Dict[str, Any]:
    inicio, fim = date(ano, 1, 1), date(ano, 12, 31)

    # vendas por dia: recebido (pago) e ainda por confirmar no extrato (só previsto)
    vendas_dia: Dict[date, Dict[str, float]] = defaultdict(_vazio_vendas)
    # recebido/pago de cada dia: movimentos dos extratos, pelo dia do banco
    # (ver docstring do módulo)
    recebido_dia: Dict[date, float] = defaultdict(float)
    pago_dia: Dict[date, float] = defaultdict(float)
    internas_dia: Dict[date, float] = defaultdict(float)
    movimentos = (
        db.query(MovimentoBancario)
        .filter(MovimentoBancario.dia >= inicio, MovimentoBancario.dia <= fim)
        .all()
    )
    internos = transferencias_internas_mesmo_dia(movimentos)
    for m in movimentos:
        if m.id in internos:
            if m.valor > 0:
                internas_dia[m.dia] += m.valor
        elif m.valor >= 0:
            recebido_dia[m.dia] += m.valor
        else:
            pago_dia[m.dia] += -m.valor
    linhas = (
        db.query(LinhaMapa)
        .filter(LinhaMapa.dia >= inicio, LinhaMapa.dia <= fim)
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

    serie = [p for p in serie_saldo_total_bruta(db) if inicio <= p["dia"] <= fim]

    diario: List[Dict[str, Any]] = []
    for p in serie:
        v = vendas_dia.get(p["dia"]) or _vazio_vendas()
        diario.append({
            "dia": p["dia"].isoformat(),
            "liquidez": round(p["saldo_disponivel_total"], 2),
            "saldo_contabilistico": round(p["saldo_contabilistico_total"], 2),
            "recebido": round(recebido_dia.get(p["dia"], 0.0), 2),
            "pago": round(pago_dia.get(p["dia"], 0.0), 2),
            "transferencias_internas": round(internas_dia.get(p["dia"], 0.0), 2),
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
                "saldo_contabilistico_fim": round(dias_mes[-1]["saldo_contabilistico_total"], 2),
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
        linha["recebido"] = round(sum(v for d, v in recebido_dia.items() if d.month == mes), 2)
        linha["pago"] = round(sum(v for d, v in pago_dia.items() if d.month == mes), 2)
        linha["transferencias_internas"] = round(sum(v for d, v in internas_dia.items() if d.month == mes), 2)
        meses.append(linha)

    return {"ano": ano, "meses": meses, "diario": diario}
