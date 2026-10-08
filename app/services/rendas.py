"""Rendas: o Mapa de Rendas (folha RENDAS, FINANCEIRO/04 - Mapa de Rendas -
CPCV - Condomínios a receber) cruzado com o que entrou nos extratos.

Cada recebimento "TRF/TFI <NOME>" é atribuído a um contrato pelo nome do
arrendatário e pelo valor (um número inteiro de rendas) - as mesmas regras
da previsão, ver fluxos_conhecidos.contrato_do_movimento. Um recebimento de
k rendas paga k meses do ano (distribuir_pagamentos). Depois compara-se com
o que a folha já tem marcado ("X" no bloco do ano): um mês pago no extrato
sem "X" fica "por registar" no Mapa; um "X" sem recebimento identificado
pode ter entrado por outra via (numerário, outro nome) - fica "registado no
Mapa". Um mês com extratos incompletos (ex. abril de 2026, com só os
últimos dias importados) não dá "em falta": fica "sem extrato"."""
import os
from datetime import date, timedelta
from functools import lru_cache
from typing import Optional

from sqlalchemy.orm import Session

from app.db.models import MovimentoBancario
from app.services.fluxos_conhecidos import (
    _empresa_corresponde,
    contrato_do_movimento,
    ler_contratos_rendas,
    mapa_rendas_mais_recente,
)
from app.services.onedrive_sync import _onedrive_raiz

MARCA_PAGO, MARCA_SEM_CONTRATO = "X", "-"
MAX_RENDAS_POR_RECEBIMENTO = 12  # acima disto não é renda (ex. transferência intragrupo com o mesmo nome)
MIN_COBERTURA_EXTRATO = 0.5  # fração dos dias úteis do mês com movimentos para o mês contar como completo


@lru_cache(maxsize=8)
def _contratos_em_cache(caminho: str, modificado: float, ano: int) -> tuple:
    # `modificado` (mtime) faz parte da chave: um Mapa de Rendas atualizado
    # volta a ser lido, sem reler o Excel a cada pedido.
    return tuple(ler_contratos_rendas(caminho, ano))


def distribuir_pagamentos(pagamentos: list, meses_validos: list) -> dict:
    """{mês: pagamento} - cada pagamento (dia, valor, n_meses) paga primeiro
    o mês em que entrou, depois os meses em atraso mais antigos e só então
    os seguintes (adiantado). Assim, uma renda de agosto paga a 2/9 e a de
    setembro a 28/9 dão agosto e setembro pagos, e 3 rendas pagas em
    setembro por quem devia julho e agosto tapam os três meses."""
    pagos = {}
    for p in sorted(pagamentos, key=lambda p: p["dia"]):
        mes_pag = p["dia"].month
        livres = [m for m in meses_validos if m not in pagos]
        ordem = ([mes_pag] if mes_pag in livres else []) + \
                [m for m in livres if m < mes_pag] + [m for m in livres if m > mes_pag]
        for m in ordem[:p["n_meses"]]:
            pagos[m] = p
    return pagos


def meses_incompletos(dias_com_movimentos: set, ano: int, ate: date) -> set:
    """Meses (até ao anterior ao de `ate`) em que menos de
    MIN_COBERTURA_EXTRATO dos dias úteis têm movimentos importados."""
    incompletos = set()
    for mes in range(1, ate.month):
        dia, uteis, com_mov = date(ano, mes, 1), 0, 0
        while dia.month == mes:
            if dia.weekday() < 5:
                uteis += 1
                com_mov += dia in dias_com_movimentos
            dia += timedelta(days=1)
        if com_mov < MIN_COBERTURA_EXTRATO * uteis:
            incompletos.add(mes)
    return incompletos


def _estado_mes(mes: int, mes_atual: int, marca, pagamento, sem_extrato: bool = False) -> Optional[str]:
    if marca == MARCA_SEM_CONTRATO:
        return "sem contrato"
    if pagamento is not None:
        return "pago" if marca == MARCA_PAGO else "por registar"
    if marca == MARCA_PAGO:
        return "registado no Mapa"
    if mes < mes_atual:
        return "sem extrato" if sem_extrato else "em falta"
    if mes == mes_atual:
        return "por receber"
    return None  # mês futuro


def detalhe_rendas(db: Session, empresa: Optional[str] = None, ate: Optional[date] = None) -> dict:
    """Contratos do Mapa de Rendas com o estado de cada mês do ano de `ate`
    (até ao mês de `ate`), os recebimentos identificados nos extratos e um
    resumo. Sem Mapa de Rendas acessível, devolve uma lista vazia e `erro`."""
    ate = min(ate or date.today(), date.today())
    ano, mes_atual = ate.year, ate.month
    try:
        caminho = mapa_rendas_mais_recente(_onedrive_raiz(), ate)
    except Exception as e:
        caminho, erro = None, str(e)
    else:
        erro = None if caminho else f"Mapa de Rendas de {ano} não encontrado."
    if not caminho:
        return {"contratos": [], "resumo": {}, "mapa": None, "erro": erro}

    contratos = [
        c for c in _contratos_em_cache(caminho, os.path.getmtime(caminho), ano)
        if (c.renda or MARCA_PAGO in c.meses_mapa.values())
        and any(c.meses_mapa.get(m) != MARCA_SEM_CONTRATO for m in range(1, 13))
    ]

    movimentos = db.query(MovimentoBancario).filter(
        MovimentoBancario.dia >= date(ano, 1, 1), MovimentoBancario.dia <= ate, MovimentoBancario.valor > 0,
    ).all()
    incompletos = meses_incompletos({m.dia for m in movimentos}, ano, ate)
    por_contrato = {}
    for m in movimentos:
        c = contrato_do_movimento(m.descricao, m.valor, m.empresa, contratos)
        if c is not None and round(m.valor / c.renda) <= MAX_RENDAS_POR_RECEBIMENTO:
            por_contrato.setdefault(id(c), []).append({
                "dia": m.dia, "valor": round(m.valor, 2), "descricao": m.descricao, "empresa": m.empresa,
                "n_meses": max(1, round(m.valor / c.renda)),
            })

    if empresa:
        contratos = [
            c for c in contratos
            if _empresa_corresponde(empresa, c.empresa)
            or any(p["empresa"] == empresa for p in por_contrato.get(id(c), []))
        ]

    linhas = []
    for c in contratos:
        pagamentos = por_contrato.get(id(c), [])
        meses_validos = [m for m in range(1, 13) if c.meses_mapa.get(m) != MARCA_SEM_CONTRATO]
        pagos = distribuir_pagamentos(pagamentos, meses_validos)
        meses = []
        for mes in range(1, 13):
            p = pagos.get(mes)
            meses.append({
                "mes": mes, "estado": _estado_mes(mes, mes_atual, c.meses_mapa.get(mes), p, mes in incompletos),
                "dia": p["dia"] if p else None,
                "valor": round(p["valor"] / p["n_meses"], 2) if p else None,
            })
        em_falta = [m["mes"] for m in meses if m["estado"] == "em falta"]
        linhas.append({
            "empresa": c.empresa, "cliente": c.cliente, "espaco": c.espaco, "fracao": c.fracao,
            "renda": c.renda, "meses": meses,
            "recebido": round(sum(p["valor"] for p in pagamentos), 2),
            "meses_em_falta": em_falta,
            "valor_em_falta": round(len(em_falta) * (c.renda or 0), 2),
            "meses_por_registar": [m["mes"] for m in meses if m["estado"] == "por registar"],
            "pagamentos": [{k: p[k] for k in ("dia", "valor", "descricao", "n_meses")} for p in pagamentos],
        })
    linhas.sort(key=lambda l: (-len(l["meses_em_falta"]), l["empresa"], l["cliente"]))

    return {
        "ano": ano, "mes_atual": mes_atual, "meses_sem_extrato": sorted(incompletos), "mapa": os.path.basename(caminho), "erro": None,
        "contratos": linhas,
        "resumo": {
            "contratos": len(linhas),
            "renda_mensal": round(sum(l["renda"] or 0 for l in linhas), 2),
            "recebido": round(sum(l["recebido"] for l in linhas), 2),
            "meses_em_falta": sum(len(l["meses_em_falta"]) for l in linhas),
            "valor_em_falta": round(sum(l["valor_em_falta"] for l in linhas), 2),
            "contratos_em_falta": sum(1 for l in linhas if l["meses_em_falta"]),
            "meses_por_registar": sum(len(l["meses_por_registar"]) for l in linhas),
        },
    }
