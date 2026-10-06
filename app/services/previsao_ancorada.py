"""Previsão de saldo "ancorada": saldo de hoje + o que já se sabe que vai
entrar e sair, com o resto do histórico só a dar a banda de incerteza.

Porquê (backtest de setembro 2026, 20 cortes semanais, grupo inteiro):
extrapolar o histórico erra MAIS do que assumir "o saldo fica igual", em
todos os horizontes (ex. 30 dias: 126k€ vs 90k€ nos 6 cortes do backtest
antigo). Duas razões:
- o saldo do grupo mexe sobretudo com movimentos grandes e pontuais
  (escrituras, CPCV, mútuos intragrupo, reforços de saldo) que nenhum
  modelo de séries temporais adivinha;
- os movimentos importados não batem com os saldos (jan-set: movimentos
  -912k€, saldo -43k€ - falta 1-19 de abril nos extratos, e algumas
  entradas grandes), por isso qualquer tendência tirada dos movimentos
  aponta sempre para baixo.

Por isso a série central NÃO tem tendência: é o saldo de partida mais os
fluxos com data e valor conhecidos, nos dois sentidos -
- rendas e recorrentes (fluxos_conhecidos.py);
- linhas do Mapa de Pagamentos e Recebimentos com data FUTURA (o que a
  tesouraria já planeou - hoje o Mapa só é preenchido no próprio dia, por
  isso normalmente não há nenhuma; fora do backtest, onde seriam batota).
Os recebimentos do índice comercial (sinal/reforços/escritura - ver
comercial.py::listar_recebimentos_previstos) ficam numa segunda série,
"com recebimentos do comercial", e não na central: só se conhecem as
ENTRADAS grandes, não as saídas grandes, e somá-las sozinhas piorou o
backtest (60 dias: 748k€ de erro vs 516k€ de "fica igual"; a central
sem elas empata com "fica igual").

A banda (percentis 10-90) vem de dias reais do histórico sorteados
(previsao.py::_simular_dias) com a média retirada - a variabilidade real
dos dias, sem a deriva enviesada. Serve o grupo (`empresa=None`) e cada
empresa com o mesmo motor, para o Forecast e a Análise de Contas contarem
a mesma história."""
import re
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Optional

import numpy as np
from sqlalchemy.orm import Session

from app.services.comercial import listar_recebimentos_previstos
from app.services.fluxos_conhecidos import _dia_util, fluxos_nos_dias
from app.services.previsao import (
    JANELA_SIMULACAO,
    MIN_PONTOS,
    N_TRAJETORIAS,
    PERCENTIS_BANDA,
    _fluxos_conhecidos,
    _historico_saldo,
    _movimentos_por_dia,
    _simular_dias,
)
from app.db.models import LinhaMapa, MovimentoBancario
from app.services.reconciliador import (
    chave_empresa,
    empresa_do_mapa_corresponde,
    ids_intragrupo,
    listar_empresas,
    remover_acentos,
)

HORIZONTE_MAXIMO = 180
# risco de liquidez: dia em que pelo menos 20% das trajetórias estão negativas
LIMIAR_DIA_NEGATIVO = 0.2
# só conta como negativo abaixo disto - uma comissão bancária de -13 € numa
# empresa parada não é risco de liquidez
DESCOBERTO_MATERIAL_EUR = 1000
# sem leitura de saldo há mais do que isto, não se calcula risco (a Cerro
# Grande tinha a última leitura em março e aparecia com risco "em abril")
DIAS_LEITURA_RECENTE = 30
# classificação da probabilidade de o saldo ficar negativo no horizonte
NIVEIS_RISCO_LIQUIDEZ = ((0.5, "alto"), (0.2, "moderado"), (0.0, "baixo"))

# ---------------------------------------------------------------------------
# Empresa do índice comercial -> designação social dos extratos
# ---------------------------------------------------------------------------
# O índice escreve a empresa à mão e abreviada ("Habiserve - Invest.
# Imobiliários,Lda", "J. Pinto Construções"); os extratos usam a designação
# completa ("HABISERVE INVESTIMENTOS IMOBILIARIOS,LDA").

_PALAVRAS_SOCIETARIAS = {"LDA", "SA", "UNIPESSOAL"}


def _palavras(nome) -> list:
    return [
        p for p in re.findall(r"[A-Z0-9]+", remover_acentos(str(nome)).upper())
        if p not in _PALAVRAS_SOCIETARIAS
    ]


def _nome_abreviado_corresponde(abreviado: str, completo: str) -> bool:
    """Cada palavra de `abreviado` é o início de uma palavra de `completo`,
    pela mesma ordem ("INVEST" -> "INVESTIMENTOS")."""
    alvo, i = _palavras(completo), 0
    palavras = _palavras(abreviado)
    for p in palavras:
        while i < len(alvo) and not alvo[i].startswith(p):
            i += 1
        if i == len(alvo):
            return False
        i += 1
    return bool(palavras)


def empresa_do_indice(nome_indice: Optional[str], empresas: list) -> Optional[str]:
    """Designação social (de `empresas`) a que corresponde a empresa escrita
    no índice comercial, ou None se não houver uma correspondência clara.
    Com várias, fica a de nome mais curto (menos palavras por explicar)."""
    if not nome_indice:
        return None
    candidatas = [e for e in empresas if _nome_abreviado_corresponde(nome_indice, e)]
    if not candidatas:
        return None
    return min(candidatas, key=lambda e: len(_palavras(e)))


# ---------------------------------------------------------------------------
# Contexto (fluxos conhecidos) - calculado uma vez e partilhado por empresa
# ---------------------------------------------------------------------------


@dataclass
class ContextoPrevisao:
    fluxos: list = field(default_factory=list)          # FluxoRecorrente (rendas/recorrentes)
    ids_conhecidos: set = field(default_factory=set)    # movimentos que saem do sorteio
    recebimentos_comerciais: list = field(default_factory=list)  # com "empresa_extrato"
    linhas_mapa_futuras: list = field(default_factory=list)      # LinhaMapa com previsto


def carregar_contexto(db: Session, ate: date = None, recebimentos_comerciais: list = None) -> ContextoPrevisao:
    """Rendas/recorrentes detetados até `ate`, recebimentos do índice
    comercial já ligados à empresa dos extratos e, sem `ate`, as linhas do
    Mapa com data futura. `recebimentos_comerciais` permite ler o índice
    uma vez só (backtest, ranking)."""
    fluxos, ids = _fluxos_conhecidos(db, ate)
    if recebimentos_comerciais is None:
        recebimentos_comerciais = recebimentos_comerciais_por_empresa(db)
    # no backtest (`ate`), as linhas do Mapa dos dias seguintes foram
    # escritas nesses dias, já com o que aconteceu - não contam como plano.
    linhas_mapa = [] if ate is not None else (
        db.query(LinhaMapa).filter(LinhaMapa.dia > date.today(), LinhaMapa.previsto.isnot(None)).all()
    )
    return ContextoPrevisao(fluxos, ids, recebimentos_comerciais, linhas_mapa)


def recebimentos_comerciais_por_empresa(db: Session) -> list:
    try:
        recebimentos = listar_recebimentos_previstos()
    except Exception:
        return []
    empresas = listar_empresas(db)
    resultado = []
    for r in recebimentos:
        empresa = empresa_do_indice(r["empresa"], empresas)
        if empresa:
            resultado.append({**r, "empresa_extrato": empresa})
    return resultado


def _descricao_comercial(r: dict) -> str:
    partes = [r["tipo"], r.get("empreendimento"), f"Fração {r['fracao']}" if r.get("fracao") else None]
    texto = " - ".join(p for p in partes if p)
    return f"{texto} ({r['cliente']})" if r.get("cliente") else texto


def fluxos_conhecidos_no_horizonte(contexto: ContextoPrevisao, dias_futuros: list, empresa: str = None) -> list:
    """Lista de {dia, valor, fonte, empresa, descricao} que caem em
    `dias_futuros` - rendas/recorrentes no dia típico do mês, linhas do
    Mapa no dia delas e recebimentos comerciais no dia marcado (fim de
    semana -> dia útil seguinte). `fonte`: renda | recorrente | mapa |
    comercial (só "comercial" fica fora da série central)."""
    if not dias_futuros:
        return []
    alvo = chave_empresa(empresa) if empresa else None
    fluxos = [f for f in contexto.fluxos if alvo is None or chave_empresa(f.empresa) == alvo]
    resultado = [
        {"dia": d, "valor": float(valor), "fonte": f.fonte, "empresa": f.empresa, "descricao": f.descricao}
        for d, lista in fluxos_nos_dias(fluxos, dias_futuros).items() for f, valor in lista
    ]
    primeiro, ultimo = dias_futuros[0], dias_futuros[-1]
    for linha in contexto.linhas_mapa_futuras:
        if not (primeiro <= linha.dia <= ultimo) or (empresa and not empresa_do_mapa_corresponde(linha.empresa, empresa)):
            continue
        resultado.append({
            "dia": linha.dia, "valor": float(linha.previsto), "fonte": "mapa",
            "empresa": linha.empresa, "descricao": linha.descricao or linha.tipo,
        })
    for r in contexto.recebimentos_comerciais:
        if alvo is not None and chave_empresa(r["empresa_extrato"]) != alvo:
            continue
        dia = _dia_util(r["dia"])
        if primeiro <= dia <= ultimo:
            resultado.append({
                "dia": dia, "valor": float(r["valor"]), "fonte": "comercial",
                "empresa": r["empresa_extrato"], "descricao": _descricao_comercial(r),
            })
    resultado.sort(key=lambda f: (f["dia"], -abs(f["valor"])))
    return resultado


# ---------------------------------------------------------------------------
# Previsão
# ---------------------------------------------------------------------------


def _serie(dias: list, valores) -> list:
    return [{"dia": d.isoformat(), "valor": float(v)} for d, v in zip(dias, valores)]


def _cashflow_semanal(dias_futuros: list, receb_fixos, pag_fixos, desvios_diarios) -> list:
    """Por semana (segunda a domingo): o que já se sabe (recebimentos e
    pagamentos conhecidos) e o intervalo provável (10-90%) do líquido, com a
    variabilidade dos dias sorteados à volta do conhecido."""
    semanas = {}
    for j, d in enumerate(dias_futuros):
        semanas.setdefault(d - timedelta(days=d.weekday()), []).append(j)
    resultado = []
    for inicio, colunas in sorted(semanas.items()):
        receb, pag = float(receb_fixos[colunas].sum()), float(pag_fixos[colunas].sum())
        liquido = receb - pag
        if desvios_diarios is not None:
            baixo, alto = np.percentile(desvios_diarios[:, colunas].sum(axis=1), PERCENTIS_BANDA)
        else:
            baixo = alto = 0.0
        resultado.append({
            "semana": inicio.isoformat(), "dias": len(colunas),
            "recebimentos": receb, "pagamentos": pag, "liquido": liquido,
            "liquido_baixo": liquido + float(baixo), "liquido_alto": liquido + float(alto),
            "recebimentos_conhecidos": receb, "pagamentos_conhecidos": pag,
        })
    return resultado


JANELA_RITMO_DIAS = 90
# os movimentos "explicam" a variação do saldo se a diferença não passar de
# 20% (ou 5 000 €, o que for maior) - no grupo faltam entradas nos extratos
# (ex. abril: saldo +718 k€, movimentos +133 k€) e a diferença é ~70%
TOLERANCIA_COBERTURA = 0.2
TOLERANCIA_COBERTURA_MIN_EUR = 5000


DIAS_TEMPO_DE_VIDA = 365  # a linha é calculada para 1 ano, para o tempo de vida não depender do horizonte


def ritmo_atual(db: Session, empresa: Optional[str], historico: list, dias_futuros: list,
                contexto: ContextoPrevisao, ate: date = None, janela_dias: int = JANELA_RITMO_DIAS) -> Optional[dict]:
    """Saldo "ao ritmo atual": a previsão dos fluxos já conhecidos (rendas,
    recorrentes, Mapa com data futura, nos dias em que caem) mais o resto
    dos recebimentos e pagamentos ao ritmo dos últimos `janela_dias` - e
    quando essa linha chega a -DESCOBERTO_MATERIAL_EUR (o tempo de vida).

    O "resto" são os movimentos desse período que não são fluxos conhecidos
    (esses já entram pela previsão - contá-los também no ritmo seria contar
    duas vezes). Verifica-se contra a variação real do saldo: quando os
    extratos não a explicam (faltam entradas - é o caso do grupo), o ritmo
    do resto sai da variação do saldo menos os fluxos conhecidos do período,
    e `fonte` diz qual foi usada. O tempo de vida vem da linha a
    DIAS_TEMPO_DE_VIDA dias, por isso é o mesmo em qualquer horizonte."""
    partida = historico[-1]
    inicio = next((h for h in reversed(historico) if h.dia <= partida.dia - timedelta(days=janela_dias)), None)
    if inicio is None:
        return None
    dias = (partida.dia - inicio.dia).days
    variacao_saldo = float(partida.saldo_contabilistico - inicio.saldo_contabilistico)

    excluir = set()
    if empresa is None:  # no grupo, as transferências entre empresas anulam-se
        excluir = ids_intragrupo([m for m in db.query(MovimentoBancario).all() if ate is None or m.dia <= ate])

    def na_janela(excluir_ids):
        return [s for s in _movimentos_por_dia(db, empresa, ate, excluir_ids) if inicio.dia < s["dia"] <= partida.dia]

    janela = na_janela(excluir)
    recebimentos = float(sum(s["recebimentos"] for s in janela))
    pagamentos = float(sum(s["pagamentos"] for s in janela))
    liquido_movimentos = recebimentos - pagamentos
    liquido_resto = float(sum(s["recebimentos"] - s["pagamentos"] for s in na_janela(excluir | contexto.ids_conhecidos)))
    conhecidos_no_periodo = liquido_movimentos - liquido_resto

    diferenca = abs(liquido_movimentos - variacao_saldo)
    tolerancia = max(TOLERANCIA_COBERTURA_MIN_EUR,
                     TOLERANCIA_COBERTURA * max(abs(liquido_movimentos), abs(variacao_saldo)))
    fonte = "movimentos" if janela and diferenca <= tolerancia else "saldo"
    resto_diario = (liquido_resto if fonte == "movimentos" else variacao_saldo - conhecidos_no_periodo) / dias

    # os fluxos conhecidos do próximo ano, dia a dia; o comercial à parte
    dias_ano = [partida.dia + timedelta(days=k) for k in range(1, max(DIAS_TEMPO_DE_VIDA, len(dias_futuros)) + 1)]
    indice = {d: k for k, d in enumerate(dias_ano)}
    conhecidos_dia, comercial_dia = np.zeros(len(dias_ano)), np.zeros(len(dias_ano))
    for f in fluxos_conhecidos_no_horizonte(contexto, dias_ano, empresa):
        (comercial_dia if f["fonte"] == "comercial" else conhecidos_dia)[indice[f["dia"]]] += f["valor"]
    saldo = float(partida.saldo_contabilistico)
    limite = -DESCOBERTO_MATERIAL_EUR

    def vida_e_serie(diario):
        linha = saldo + np.cumsum(diario)
        medio = float((linha[-1] - saldo) / len(dias_ano))
        abaixo = np.nonzero(linha < limite)[0]
        if saldo <= limite:
            vida = {"dias": 0, "dia": partida.dia.isoformat()}
        elif len(abaixo):
            n = int(abaixo[0]) + 1
            vida = {"dias": n, "dia": dias_ano[n - 1].isoformat()}
        elif medio < 0:  # para lá de 1 ano: em linha reta com a média desse ano
            n = len(dias_ano) + int((linha[-1] - limite) // -medio) + 1
            vida = {"dias": n, "dia": (partida.dia + timedelta(days=n)).isoformat()}
        else:
            vida = {"dias": None, "dia": None}  # recebe mais do que paga: não se esgota a este ritmo
        vida.update(limite_eur=limite, alem_do_horizonte=vida["dias"] is not None and vida["dias"] > len(dias_futuros))
        return _serie(dias_futuros, linha[:len(dias_futuros)]), vida, medio

    previsao, vida, medio = vida_e_serie(conhecidos_dia + resto_diario)

    # cenário "só vendas do índice": as vendas (CPCV/escrituras recebidos no
    # Mapa) saem do ritmo e entram só as marcadas no índice comercial, nas
    # datas marcadas - pôr o índice por cima do ritmo contava as vendas duas
    # vezes (no grupo, out/2026: 1 040 k€ de vendas nos últimos 90 dias)
    from app.services.vendas import vendas_recebidas

    vendas = vendas_recebidas(db, empresa, inicio.dia, partida.dia)
    resto_sem_vendas = resto_diario - vendas / dias
    previsao_indice, vida_indice, medio_indice = vida_e_serie(conhecidos_dia + resto_sem_vendas + comercial_dia)
    return {
        "janela_dias": dias, "desde": inicio.dia.isoformat(),
        "recebimentos": recebimentos, "pagamentos": pagamentos, "liquido_movimentos": liquido_movimentos,
        "variacao_saldo": variacao_saldo, "fonte": fonte,
        "conhecidos_no_periodo": conhecidos_no_periodo, "resto_diario": resto_diario,
        "liquido_diario": medio,
        "previsao": previsao,
        "tempo_de_vida": vida,
        "so_vendas_do_indice": {
            "vendas_no_periodo": vendas, "resto_sem_vendas_diario": resto_sem_vendas,
            "comercial_no_horizonte": float(comercial_dia[:len(dias_futuros)].sum()),
            "liquido_diario": medio_indice,
            "previsao": previsao_indice,
            "tempo_de_vida": vida_indice,
        },
    }


def prever_saldo_ancorado(
    db: Session, empresa: Optional[str] = None, dias_futuro: int = 30, ate: date = None,
    contexto: ContextoPrevisao = None, com_banda: bool = True, excluir_intragrupo: Optional[bool] = None,
) -> dict:
    """Saldo previsto de `empresa` (None = grupo inteiro) nos próximos
    `dias_futuro` dias - ver docstring do módulo. `ate` corta o histórico
    (backtest). `com_banda=False` salta a simulação (ranking de risco, que
    só precisa da série central)."""
    dias_futuro = max(1, min(int(dias_futuro), HORIZONTE_MAXIMO))
    historico = _historico_saldo(db, empresa, ate)
    if len(historico) < MIN_PONTOS:
        raise ValueError(f"Histórico de saldo insuficiente ({len(historico)} pontos, mínimo {MIN_PONTOS}).")
    contexto = contexto or carregar_contexto(db, ate)

    partida = historico[-1]
    saldo_partida = partida.saldo_contabilistico
    dias_futuros = [partida.dia + timedelta(days=i) for i in range(1, dias_futuro + 1)]
    conhecidos = fluxos_conhecidos_no_horizonte(contexto, dias_futuros, empresa)

    indice = {d: j for j, d in enumerate(dias_futuros)}
    receb_fixos, pag_fixos, comercial = np.zeros(dias_futuro), np.zeros(dias_futuro), np.zeros(dias_futuro)
    for f in conhecidos:
        j = indice[f["dia"]]
        if f["fonte"] == "comercial":
            comercial[j] += f["valor"]
        elif f["valor"] >= 0:
            receb_fixos[j] += f["valor"]
        else:
            pag_fixos[j] -= f["valor"]
    central = saldo_partida + np.cumsum(receb_fixos - pag_fixos)
    com_comercial = central + np.cumsum(comercial)

    banda = trajetorias = desvios = risco_negativo = None
    historico_cf = []
    if com_banda:
        # no grupo, as transferências entre empresas anulam-se - fora da
        # banda e das barras semanais (numa empresa são caixa real dela)
        excluir = contexto.ids_conhecidos
        # numa empresa, por omissão ficam (são caixa real dela); o risco de
        # liquidez tira-os (excluir_intragrupo=True) - ver listar_risco_liquidez
        if excluir_intragrupo if excluir_intragrupo is not None else empresa is None:
            movimentos = [m for m in db.query(MovimentoBancario).all() if ate is None or m.dia <= ate]
            excluir = excluir | ids_intragrupo(movimentos)
        historico_cf = _movimentos_por_dia(db, empresa, ate, excluir)
        simulado = _simular_dias(historico_cf, dias_futuros)
        if simulado is not None:
            recebimentos, pagamentos = simulado
            media = float(np.mean([s["liquido"] for s in historico_cf[-JANELA_SIMULACAO:]]))
            desvios = (recebimentos - pagamentos) - media
            saldos = central + np.cumsum(desvios, axis=1)
            baixa, alta = np.percentile(saldos, PERCENTIS_BANDA, axis=0)
            # risco de liquidez: em quantas trajetórias o saldo fica abaixo
            # de -DESCOBERTO_MATERIAL_EUR nalgum dia, e o 1.º dia em que isso
            # acontece em pelo menos LIMIAR_DIA_NEGATIVO delas. Aqui as
            # trajetórias levam a TENDÊNCIA própria (sem tirar a média): numa
            # empresa, gastar todos os meses mais do que recebe é exatamente o
            # sinal de risco - a banda acima tira-a porque, no grupo, os
            # extratos incompletos puxavam sempre para baixo.
            com_tendencia = central + np.cumsum(recebimentos - pagamentos, axis=1)
            negativo = com_tendencia < -DESCOBERTO_MATERIAL_EUR
            fracao_por_dia = negativo.mean(axis=0)
            dias_em_risco = np.nonzero(fracao_por_dia >= LIMIAR_DIA_NEGATIVO)[0]
            risco_negativo = {
                "probabilidade": float(negativo.any(axis=1).mean()),
                "primeiro_dia": dias_futuros[int(dias_em_risco[0])].isoformat() if len(dias_em_risco) else None,
                "saldo_pior_caso_fim": float(np.percentile(com_tendencia[:, -1], PERCENTIS_BANDA[0])),
            }
            banda = {"baixa": _serie(dias_futuros, baixa), "alta": _serie(dias_futuros, alta)}
            ordem = np.argsort(saldos[:, -1])
            trajetorias = [_serie(dias_futuros, saldos[ordem[int(q * (N_TRAJETORIAS - 1))]]) for q in (0.2, 0.4, 0.6, 0.8)]

    return {
        "empresa": empresa,
        "saldo_partida": float(saldo_partida),
        "dia_partida": partida.dia.isoformat(),
        "historico": [{"dia": h.dia.isoformat(), "valor": h.saldo_contabilistico} for h in historico],
        "historico_cashflow": [
            {"dia": s["dia"].isoformat(), "recebimentos": s["recebimentos"], "pagamentos": s["pagamentos"]}
            for s in historico_cf
        ],
        "previsao": _serie(dias_futuros, central),
        # None quando não há recebimentos comerciais no horizonte (a linha
        # seria igual à central)
        "previsao_com_comercial": _serie(dias_futuros, com_comercial) if comercial.any() else None,
        "banda_incerteza": banda,
        "risco_saldo_negativo": risco_negativo,
        "trajetorias_exemplo": trajetorias,
        "ritmo_atual": ritmo_atual(db, empresa, historico, dias_futuros, contexto, ate) if com_banda else None,
        "cashflow_semanal_previsto": _cashflow_semanal(dias_futuros, receb_fixos, pag_fixos, desvios),
        "fluxos_conhecidos_previstos": [{**f, "dia": f["dia"].isoformat()} for f in conhecidos],
    }


def backtest_previsao_ancorada(
    db: Session, dias_futuro: int = 30, n_cortes: int = 20, passo_dias: int = 7, empresa: str = None,
) -> dict:
    """Repete a previsão a partir de `n_cortes` datas passadas (a cada
    `passo_dias`), só com os dados até esse dia, e compara com o saldo real
    ao fim de `dias_futuro` dias - ao lado da referência "o saldo fica
    igual". Os recebimentos comerciais vêm do índice atual (a agenda de
    cada venda, marcada no CPCV), não de uma cópia do índice em cada data."""
    historico = _historico_saldo(db, empresa)
    if not historico:
        raise ValueError("Sem histórico de saldo.")
    real = {h.dia: h.saldo_contabilistico for h in historico}
    ultimo = historico[-1].dia
    comerciais = recebimentos_comerciais_por_empresa(db)

    cortes = []
    for k in range(n_cortes):
        corte = ultimo - timedelta(days=dias_futuro + passo_dias * k)
        fim = corte + timedelta(days=dias_futuro)
        if corte not in real or fim not in real:
            continue
        try:
            contexto = carregar_contexto(db, corte, comerciais)
            r = prever_saldo_ancorado(db, empresa, dias_futuro, ate=corte, contexto=contexto)
        except ValueError:
            continue
        previsto = r["previsao"][-1]["valor"]
        previsto_comercial = (r["previsao_com_comercial"] or r["previsao"])[-1]["valor"]
        banda = r["banda_incerteza"]
        cortes.append({
            "corte": corte.isoformat(), "fim": fim.isoformat(),
            "saldo_no_corte": float(real[corte]), "real": float(real[fim]), "previsto": float(previsto),
            "erro_modelo": float(abs(previsto - real[fim])),
            "erro_com_comercial": float(abs(previsto_comercial - real[fim])),
            "erro_sem_alteracao": float(abs(real[corte] - real[fim])),
            "dentro_banda": (
                bool(banda["baixa"][-1]["valor"] <= real[fim] <= banda["alta"][-1]["valor"]) if banda else None
            ),
        })

    def _media(chave):
        return sum(c[chave] for c in cortes) / len(cortes) if cortes else None

    com_banda = [c for c in cortes if c["dentro_banda"] is not None]
    return {
        "dias_futuro": dias_futuro,
        "empresa": empresa,
        "cortes": cortes,
        "erro_medio_modelo": _media("erro_modelo"),
        "erro_medio_com_comercial": _media("erro_com_comercial"),
        "erro_medio_sem_alteracao": _media("erro_sem_alteracao"),
        "saldo_medio": _media("real"),
        "cobertura_banda": (sum(c["dentro_banda"] for c in com_banda) / len(com_banda)) if com_banda else None,
    }


# ---------------------------------------------------------------------------
# Risco de liquidez por empresa
# ---------------------------------------------------------------------------


def nivel_risco_liquidez(probabilidade: Optional[float]) -> str:
    if probabilidade is None:
        return "sem dados"
    return next(nome for limite, nome in NIVEIS_RISCO_LIQUIDEZ if probabilidade >= limite)


def listar_risco_liquidez(db: Session, dias_futuro: int = 30) -> list:
    """Para cada empresa: probabilidade de o saldo ficar negativo nalgum dia
    dos próximos `dias_futuro` dias (fração das trajetórias simuladas - as
    mesmas da banda da previsão), o 1.º dia provável, o saldo previsto e o
    pior caso provável (percentil 10) no fim. Da mais arriscada para a
    menos. Diferente do "ranking de risco" (que compara o saldo de hoje com
    a despesa média): aqui é a previsão a dizer se o dinheiro chega."""
    contexto = carregar_contexto(db)
    resultado, previsoes = [], {}
    for empresa in listar_empresas(db):
        try:
            # sem mútuos/reforços entre empresas do grupo: são decisões de
            # tesouraria, não acasos - o risco mede-se ANTES de os haver (a
            # Vidor SGPS aparecia com -1,1M€ de pior caso por sortear mútuos)
            previsoes[empresa] = prever_saldo_ancorado(db, empresa, dias_futuro, contexto=contexto,
                                                       excluir_intragrupo=True)
        except ValueError:
            continue  # sem histórico de saldo suficiente
    ultima_leitura = max((date.fromisoformat(r["dia_partida"]) for r in previsoes.values()), default=None)
    for empresa, r in previsoes.items():
        risco = r.get("risco_saldo_negativo") or {}
        probabilidade = risco.get("probabilidade")
        desatualizada = ultima_leitura and (ultima_leitura - date.fromisoformat(r["dia_partida"])).days > DIAS_LEITURA_RECENTE
        if desatualizada:
            risco, probabilidade = {}, None
        resultado.append({
            "empresa": empresa,
            "saldo_atual": r["saldo_partida"],
            "saldo_previsto_fim": r["previsao"][-1]["valor"],
            "saldo_pior_caso_fim": risco.get("saldo_pior_caso_fim"),
            "probabilidade_negativo": probabilidade,
            "primeiro_dia_provavel": risco.get("primeiro_dia"),
            "nivel": "sem leitura recente" if desatualizada else nivel_risco_liquidez(probabilidade),
            "ultima_leitura": r["dia_partida"],
        })
    resultado.sort(key=lambda x: (-(x["probabilidade_negativo"] or 0), x["saldo_atual"]))
    return resultado
