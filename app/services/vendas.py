"""Vendas (CPCV, reforços de sinal, escrituras): o índice do Departamento
Comercial cruzado com o que de facto entrou, segundo o Mapa de Pagamentos
e Recebimentos.

- o índice (comercial.listar_negocios) diz o que cada comprador tem de
  pagar e quando: sinal no CPCV, reforços, escritura;
- o Mapa diz o que entrou: as linhas de recebimento CPCV/Escritura (ou
  outra imputação cuja descrição refira o código REF da fração - ver
  reconciliador._e_cpcv_ou_escritura), já confirmadas no extrato.

A ligação entre os dois é o código REF ("00.PO.23.035") escrito na
descrição da linha do Mapa: cada pagamento da agenda casa com um
recebimento dessa REF de valor e data parecidos (casar_pagamentos). Uma
linha que refira várias frações (ex. uma escritura de 4) reparte o valor
por igual. O sinal do CPCV conta como recebido pelo próprio índice quando
não aparece no Mapa (muitos sinais entraram em linhas sem a REF). "Por
confirmar" (reforço/escritura com data passada sem entrada identificada)
não quer dizer "em atraso": pode ter entrado numa linha sem o código.

Serve duas coisas: a vista detalhada de Análise de Extratos e a previsão
(previsao_ancorada.ritmo_atual), que tira as vendas do ritmo dos últimos
90 dias para poder pôr no lugar delas só as vendas marcadas no índice -
somá-las por cima contava as vendas duas vezes."""
from collections import defaultdict
from datetime import date, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from app.services.reconciliador import (
    _e_cpcv_ou_escritura,
    _linhas_mapa_filtradas,
    _refs_fracao_de_linha,
    chave_empresa,
    listar_empresas,
)

TOLERANCIA_RECEBIDO_EUR = 100  # abaixo disto, "falta receber" é arredondamento
TOLERANCIA_VALOR = 0.02  # um recebimento do Mapa casa com um pagamento da agenda até 2% de diferença
JANELA_CASAMENTO_DIAS = 45  # ... e até 45 dias antes/depois da data marcada
MESES_GRAFICO = 6  # meses para trás (realizado) e para a frente (agendado)


def _vendas_confirmadas(db: Session, empresa: Optional[str], dia_inicio=None, dia_fim=None) -> list:
    """Linhas de recebimento do Mapa do "mundo" CPCV/Escritura já confirmadas."""
    return [
        l for l in _linhas_mapa_filtradas(db, empresa=empresa, dia_inicio=dia_inicio, dia_fim=dia_fim)
        if l.tipo == "recebimento" and l.pago is not None and _e_cpcv_ou_escritura(l)
    ]


def vendas_recebidas(db: Session, empresa: Optional[str], depois_de: date, ate: date) -> float:
    """Total de vendas recebidas (CPCV/escrituras confirmados no Mapa) em
    ]depois_de, ate] - para a previsão tirar as vendas do ritmo."""
    return float(sum(abs(l.pago) for l in _vendas_confirmadas(db, empresa, depois_de + timedelta(days=1), ate)))


def recebimentos_por_ref(db: Session, empresa: Optional[str] = None) -> dict:
    """{REF: [(dia, valor), ...]} - o que entrou por fração, segundo o Mapa
    (uma linha com várias REF reparte o valor por igual)."""
    por_ref = defaultdict(list)
    for l in _vendas_confirmadas(db, empresa):
        refs = _refs_fracao_de_linha(l)
        for ref in refs:
            por_ref[ref].append((l.dia, abs(l.pago) / len(refs)))
    return {ref: sorted(v) for ref, v in por_ref.items()}


def casar_pagamentos(pagamentos: list, recebimentos: list, hoje: date) -> tuple:
    """Casa cada pagamento da agenda com um recebimento do Mapa da mesma REF
    (valor até TOLERANCIA_VALOR, data até JANELA_CASAMENTO_DIAS de
    distância, cada recebimento só uma vez). Devolve (pagamentos com
    `estado`, recebimentos que não casaram com nada).

    Estado de cada pagamento: "recebido" (casou com o Mapa), "recebido
    (índice)" (o sinal do CPCV, que o índice regista como recebido, quando
    não aparece no Mapa), "por confirmar" (data já passou, sem entrada
    identificada) ou "agendado"."""
    livres = list(recebimentos)
    resultado = []
    for p in sorted(pagamentos, key=lambda p: p["dia"]):
        par = next((r for r in livres if abs(r[1] - p["valor"]) <= TOLERANCIA_VALOR * p["valor"]
                    and abs((r[0] - p["dia"]).days) <= JANELA_CASAMENTO_DIAS), None)
        if par is not None:
            livres.remove(par)
            estado, recebido_em = "recebido", par[0].isoformat()
        elif p["tipo"] == "Sinal (CPCV)" and p["dia"] <= hoje:
            estado, recebido_em = "recebido (índice)", None
        elif p["dia"] <= hoje:
            estado, recebido_em = "por confirmar", None
        else:
            estado, recebido_em = "agendado", None
        resultado.append({**p, "estado": estado, "recebido_em": recebido_em})
    return resultado, livres


def _no_periodo(negocio: dict, dia_inicio, dia_fim) -> bool:
    datas = [negocio["data_cpcv"]] + [p["dia"] for p in negocio["pagamentos"]]
    return any(d and (dia_inicio is None or d >= dia_inicio) and (dia_fim is None or d <= dia_fim) for d in datas)


def detalhe_negocios(db: Session, empresa: Optional[str] = None, dia_inicio: date = None, dia_fim: date = None,
                     hoje: date = None, negocios: list = None) -> dict:
    """Cada negócio do índice com a agenda (cada pagamento com o seu estado),
    o recebido, o que falta e o próximo pagamento; um resumo; e as vendas
    por mês (recebidas nos últimos MESES_GRAFICO meses, agendadas nos
    próximos). `empresa` é a designação social dos extratos; `negocios`
    permite passar o índice já lido (testes)."""
    from app.services.comercial import listar_negocios
    from app.services.previsao_ancorada import empresa_do_indice

    hoje = hoje or date.today()
    negocios = listar_negocios() if negocios is None else negocios
    empresas = listar_empresas(db)
    alvo = chave_empresa(empresa) if empresa else None
    recebimentos = recebimentos_por_ref(db, empresa)

    linhas = []
    for n in negocios:
        empresa_extrato = empresa_do_indice(n["empresa"], empresas)
        if alvo is not None and (empresa_extrato is None or chave_empresa(empresa_extrato) != alvo):
            continue
        if not _no_periodo(n, dia_inicio, dia_fim):
            continue
        agenda, sobras = casar_pagamentos(n["pagamentos"], recebimentos.get(n["ref"], []), hoje)
        total = n["valor_proposto"] or sum(p["valor"] for p in agenda)
        recebido = (sum(p["valor"] for p in agenda if p["estado"].startswith("recebido"))
                    + sum(v for _, v in sobras))  # entradas da REF fora da agenda também contam
        por_confirmar = sum(p["valor"] for p in agenda if p["estado"] == "por confirmar")
        proximo = next((p for p in agenda if p["estado"] == "agendado"), None)
        if total and recebido >= total - TOLERANCIA_RECEBIDO_EUR:
            estado = "concluído"
        elif por_confirmar > TOLERANCIA_RECEBIDO_EUR:
            estado = "por confirmar"
        else:
            estado = "em dia"
        linhas.append({
            "ref": n["ref"], "empreendimento": n["empreendimento"], "fracao": n["fracao"], "cliente": n["cliente"],
            "empresa": empresa_extrato or n["empresa"],
            "valor_proposto": total,
            "data_cpcv": n["data_cpcv"].isoformat() if n["data_cpcv"] else None,
            "pagamentos": [{**p, "dia": p["dia"].isoformat()} for p in agenda],
            "recebido": round(recebido, 2),
            "por_receber": round(max(0.0, total - recebido), 2),
            "por_confirmar": round(por_confirmar, 2),
            "proximo_pagamento": ({"tipo": proximo["tipo"], "dia": proximo["dia"].isoformat(),
                                   "valor": proximo["valor"]} if proximo else None),
            "estado": estado,
        })
    linhas.sort(key=lambda l: (l["proximo_pagamento"] or {}).get("dia") or "9999")

    em_30_dias = hoje + timedelta(days=30)
    resumo = {
        "negocios": len(linhas),
        "valor_proposto": sum(l["valor_proposto"] or 0 for l in linhas),
        "recebido": sum(l["recebido"] for l in linhas),
        "por_receber": sum(l["por_receber"] for l in linhas),
        "por_confirmar": sum(l["por_confirmar"] for l in linhas),
        "n_por_confirmar": sum(1 for l in linhas if l["estado"] == "por confirmar"),
        "proximos_30_dias": sum(p["valor"] for l in linhas for p in l["pagamentos"]
                                if p["estado"] == "agendado" and date.fromisoformat(p["dia"]) <= em_30_dias),
    }
    return {"negocios": linhas, "resumo": resumo,
            "por_mes": _vendas_por_mes(db, empresa, negocios, empresas, alvo, hoje)}


def _vendas_por_mes(db, empresa, negocios, empresas, alvo, hoje: date) -> list:
    """[{mes, serie, valor}]: "Recebido (Mapa)" nos últimos MESES_GRAFICO
    meses e "Agendado (índice)" daqui para a frente - independente do
    filtro de datas da página, para a tendência se ver sempre."""
    from app.services.previsao_ancorada import empresa_do_indice

    inicio = (hoje.replace(day=1) - timedelta(days=31 * (MESES_GRAFICO - 1))).replace(day=1)
    fim = (hoje.replace(day=1) + timedelta(days=31 * (MESES_GRAFICO + 1))).replace(day=1)
    soma = defaultdict(float)
    for l in _vendas_confirmadas(db, empresa, inicio, hoje):
        soma[(l.dia.strftime("%Y-%m"), "Recebido (Mapa)")] += abs(l.pago)
    for n in negocios:
        empresa_extrato = empresa_do_indice(n["empresa"], empresas)
        if alvo is not None and (empresa_extrato is None or chave_empresa(empresa_extrato) != alvo):
            continue
        for p in n["pagamentos"]:
            if hoje < p["dia"] < fim:
                soma[(p["dia"].strftime("%Y-%m"), "Agendado (índice)")] += p["valor"]
    return [{"mes": mes, "serie": serie, "valor": round(v, 2)} for (mes, serie), v in sorted(soma.items())]
