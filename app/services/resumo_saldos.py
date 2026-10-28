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

Num mês com Mapa de Pagamentos e Recebimentos (pedido 19/10/2026: o resumo
tem de bater com o Mapa do mês), recebido, pago, saldo bancário, saldo
bancário disponível, CPCVs e escrituras vêm da folha Resumo desse Mapa
(resumo_mapa_excel.py) - "fonte": "mapa" - os valores das células, como o
Excel os mostra (21/10/2026: a soma do banco não é o que o Resumo mostra).
O que está acima fica só para os meses sem Mapa.

Janeiro-março só tinham extratos mensais; a 20/10/2026 passaram a ter
também pastas diárias e os mesmos movimentos entraram outra vez na BD, vindos
dos ficheiros diários. Não se apagam (pedido explícito): num dia/empresa com
movimentos do extrato mensal, os dos ficheiros diários não contam
(sem_duplicados_mensal_diario).
"""
from collections import defaultdict
from datetime import date
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import LinhaMapa, MovimentoBancario
from app.services.reconciliador import PADRAO_REF_FRACAO, _e_cpcv_ou_escritura, remover_acentos
from app.services.mapa_saldos_excel import serie_saldo_total_bruta
from app.services.resumo_mapa_excel import avisos_do_mes, resumo_do_mes

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


def refs_da_linha(l: LinhaMapa) -> List[str]:
    """Códigos REF (ex. 00.PO.23.035) da Descrição e da Imputação, sem
    repetir - nos CPCVs o REF vem muitas vezes só na Imputação ("00.PO.23.035
    Cpcv ALVA Lote 3 AI")."""
    refs: List[str] = []
    for texto in (l.descricao, l.imputacao):
        for ref in PADRAO_REF_FRACAO.findall(texto or ""):
            if ref.upper() not in refs:
                refs.append(ref.upper())
    return refs


def _vazio_vendas() -> Dict[str, float]:
    campos = {t: 0.0 for t in TIPOS_VENDA}
    campos.update({f"n_{t}": 0 for t in TIPOS_VENDA})
    campos.update({f"pendente_{t}": 0.0 for t in TIPOS_VENDA})
    return campos


PREFIXO_ORIGEM_MENSAL = "mensal:"  # = onedrive_sync.PREFIXO_ORIGEM_MENSAL


def sem_duplicados_mensal_diario(movimentos: list) -> list:
    """Os movimentos sem os dos ficheiros diários de um dia/empresa que
    também tem movimentos do extrato mensal (os mesmos, importados duas
    vezes - ver docstring do módulo)."""
    com_mensal = {(m.dia, m.empresa) for m in movimentos
                  if (m.ficheiro_origem or "").startswith(PREFIXO_ORIGEM_MENSAL)}
    return [m for m in movimentos
            if (m.ficheiro_origem or "").startswith(PREFIXO_ORIGEM_MENSAL) or (m.dia, m.empresa) not in com_mensal]


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


def _totais_do_mapa(itens: list) -> Dict[str, Any]:
    """Os totais de um mês tirados do Resumo do Mapa - itens = [(dia, valores)]
    por ordem."""
    dias = [d for _, d in itens]
    soma = lambda chave: round(sum(d[chave] or 0.0 for d in dias), 2)
    com_saldo = [d for d in dias if d["saldo_disponivel"] is not None]
    dias_saldo = [dia for dia, d in itens if d["saldo_disponivel"] is not None]
    totais: Dict[str, Any] = {
        "fonte": "mapa", "recebido": soma("recebido"), "pago": soma("pago"), "transferencias_internas": None,
        "cpcv": soma("cpcv"), "n_cpcv": sum(d["n_cpcv"] for d in dias),
        "escritura": soma("escritura"), "n_escritura": sum(d["n_escritura"] for d in dias),
    }
    # reforços de sinal não têm coluna no Resumo: ficam à parte, fora do total
    totais["total_vendas"] = round(totais["cpcv"] + totais["escritura"], 2)
    if com_saldo:
        liquidez = [d["saldo_disponivel"] for d in com_saldo]
        totais.update({
            "dias_com_saldo": len(com_saldo),
            "saldo_contabilistico_fim": round(com_saldo[-1]["saldo_bancario"], 2),
            "saldo_inicio": round(liquidez[0], 2), "saldo_fim": round(liquidez[-1], 2),
            "variacao": round(liquidez[-1] - liquidez[0], 2),
            "liquidez_media": round(sum(liquidez) / len(liquidez), 2),
            "liquidez_minima": round(min(liquidez), 2), "liquidez_maxima": round(max(liquidez), 2),
            "dia_minimo": dias_saldo[liquidez.index(min(liquidez))].isoformat(),
            "dia_maximo": dias_saldo[liquidez.index(max(liquidez))].isoformat(),
        })
    return totais


def resumo_mensal_saldos(db: Session, ano: int) -> Dict[str, Any]:
    inicio, fim = date(ano, 1, 1), date(ano, 12, 31)

    # vendas por dia: recebido (pago) e ainda por confirmar no extrato (só previsto)
    vendas_dia: Dict[date, Dict[str, float]] = defaultdict(_vazio_vendas)
    # recebido/pago de cada dia: movimentos dos extratos, pelo dia do banco
    # (ver docstring do módulo)
    recebido_dia: Dict[date, float] = defaultdict(float)
    pago_dia: Dict[date, float] = defaultdict(float)
    internas_dia: Dict[date, float] = defaultdict(float)
    movimentos = sem_duplicados_mensal_diario(
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
    # cada CPCV/reforço de sinal/escritura com a referência, para o Resumo mensal
    vendas_detalhe: List[Dict[str, Any]] = []
    for l in linhas:
        tipo = tipo_venda(l)
        if tipo is None:
            continue
        if l.pago is not None or l.previsto:
            vendas_detalhe.append({
                "dia": l.dia.isoformat(), "tipo": tipo, "refs": refs_da_linha(l), "empresa": l.empresa,
                "descricao": l.descricao, "imputacao": l.imputacao,
                "valor": round(l.pago if l.pago is not None else l.previsto, 2),
                "confirmado": l.pago is not None,
            })
        v = vendas_dia[l.dia]
        if l.pago is not None:
            v[tipo] += l.pago
            v[f"n_{tipo}"] += 1
        elif l.previsto:
            v[f"pendente_{tipo}"] += l.previsto

    serie = [p for p in serie_saldo_total_bruta(db) if inicio <= p["dia"] <= fim]

    # o Resumo do Mapa de cada mês (até ao mês de hoje): manda nesses dias
    mapa: Dict[date, Dict[str, Any]] = {}
    meses_com_mapa = set()
    for mes in range(1, 13):
        if date(ano, mes, 1) > date.today():
            break
        dias_mapa = resumo_do_mes(ano, mes)
        if dias_mapa:
            mapa.update(dias_mapa)
            meses_com_mapa.add(mes)
    # CPCVs/escrituras que o Resumo desse mês não tem (jan-jun): das linhas do Mapa
    for dia, m in mapa.items():
        v = vendas_dia.get(dia) or _vazio_vendas()
        for tipo in ("cpcv", "escritura"):
            if m[tipo] is None:
                m[tipo] = v[tipo]
            if m[f"n_{tipo}"] is None:
                m[f"n_{tipo}"] = v[f"n_{tipo}"]
    # o saldo do Mapa num dia sem saldo na linha (fim de semana) é o do dia antes
    ultimo = None
    for dia in sorted(mapa):
        if mapa[dia]["saldo_disponivel"] is None and ultimo is not None:
            mapa[dia] = {**mapa[dia], "saldo_disponivel": ultimo["saldo_disponivel"],
                         "saldo_bancario": ultimo["saldo_bancario"]}
        elif mapa[dia]["saldo_disponivel"] is not None:
            ultimo = mapa[dia]
    por_dia_serie = {p["dia"]: p for p in serie}

    diario: List[Dict[str, Any]] = []
    for dia in sorted(set(por_dia_serie) | {d for d in mapa if d <= date.today()}):
        p, m = por_dia_serie.get(dia), mapa.get(dia)
        v = vendas_dia.get(dia) or _vazio_vendas()
        if m is not None:
            liquidez = m["saldo_disponivel"] if m["saldo_disponivel"] is not None else (p and p["saldo_disponivel_total"])
            contabilistico = m["saldo_bancario"] if m["saldo_bancario"] is not None else (
                p and p["saldo_contabilistico_total"])
            if liquidez is None:
                continue
            diario.append({
                "dia": dia.isoformat(), "liquidez": round(liquidez, 2),
                "saldo_contabilistico": round(contabilistico or 0.0, 2),
                "recebido": round(m["recebido"] or 0.0, 2), "pago": round(m["pago"] or 0.0, 2),
                "transferencias_internas": None,
                "cpcv": round(m["cpcv"] or 0.0, 2), "reforco_sinal": round(v["reforco_sinal"], 2),
                "escritura": round(m["escritura"] or 0.0, 2), "fonte": "mapa",
            })
            continue
        diario.append({
            "dia": dia.isoformat(),
            "liquidez": round(p["saldo_disponivel_total"], 2),
            "saldo_contabilistico": round(p["saldo_contabilistico_total"], 2),
            "recebido": round(recebido_dia.get(dia, 0.0), 2),
            "pago": round(pago_dia.get(dia, 0.0), 2),
            "transferencias_internas": round(internas_dia.get(dia, 0.0), 2),
            **{t: round(v[t], 2) for t in TIPOS_VENDA},
            "fonte": "extratos",
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
        linha["fonte"] = "extratos"
        if mes in meses_com_mapa:
            linha.update(_totais_do_mapa([(d, v) for d, v in sorted(mapa.items()) if d.month == mes and d <= date.today()]))
            # células do Resumo que não leem o total do dia (ver referencias_desalinhadas)
            linha["avisos_resumo"] = avisos_do_mes(ano, mes)
        meses.append(linha)

    vendas_detalhe.sort(key=lambda d: (d["dia"], d["tipo"], d["refs"]))
    return {"ano": ano, "meses": meses, "diario": diario, "vendas": vendas_detalhe}
