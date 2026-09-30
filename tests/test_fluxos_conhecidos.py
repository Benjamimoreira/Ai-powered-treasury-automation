from dataclasses import dataclass
from datetime import date

from app.services.fluxos_conhecidos import (
    ContratoRenda,
    contrato_do_movimento,
    detetar_fluxos,
    fluxos_nos_dias,
    palavras_nome,
)


@dataclass
class Mov:
    id: int
    dia: date
    empresa: str
    descricao: str
    valor: float


def _contrato(cliente, renda, empresa="J.Pinto"):
    return ContratoRenda(
        empresa=empresa, cliente=cliente, espaco="Tavarede", fracao="I", renda=renda,
        titulares=[palavras_nome(t) for t in cliente.split("/")],
    )


def test_renda_reconhecida_com_nome_cortado_pelo_banco():
    contratos = [_contrato("Aixing Zhao Silva Pereira", 381.23)]
    c = contrato_do_movimento("TRF AIXING ZHAO SIL", 381.23, "J PINTO CONSTRUCOES,LDA", contratos)
    assert c is contratos[0]


def test_renda_de_dois_meses_tambem_conta():
    contratos = [_contrato("Aixing Zhao", 381.23)]
    assert contrato_do_movimento("TRF AIXING ZHAO", 762.46, "J PINTO", contratos) is contratos[0]


def test_valor_que_nao_e_a_renda_nao_conta():
    contratos = [_contrato("Aixing Zhao", 381.23)]
    assert contrato_do_movimento("TRF AIXING ZHAO", 500.0, "J PINTO", contratos) is None


def test_pagamento_ou_sem_prefixo_trf_nao_e_renda():
    contratos = [_contrato("Aixing Zhao", 381.23)]
    assert contrato_do_movimento("TRF AIXING ZHAO", -381.23, "J PINTO", contratos) is None
    assert contrato_do_movimento("DEPOSITO AIXING ZHAO", 381.23, "J PINTO", contratos) is None


def test_deteta_renda_e_recorrente_e_marca_movimentos_do_historico():
    contratos = [_contrato("Aixing Zhao", 400.0)]
    movs, n = [], 0
    for mes in range(3, 10):  # mar-set 2026
        n += 1
        movs.append(Mov(n, date(2026, mes, 8), "J PINTO", "TRF AIXING ZHAO", 400.0))
        n += 1
        movs.append(Mov(n, date(2026, mes, 27), "PALAVRA ADICIONAL", f"TICKET RESTAURANT {mes}", -8600.0))
        n += 1
        movs.append(Mov(n, date(2026, mes, 15), "VIDOR SGPS", "TRF CAIXADIRECTA", -1000.0 * mes ** 2))

    fluxos, ids = detetar_fluxos(movs, date(2026, 9, 30), contratos)
    por_fonte = {f.fonte: f for f in fluxos}
    assert por_fonte["renda"].valor_mensal == 400.0 and por_fonte["renda"].dia_mes == 8
    assert por_fonte["recorrente"].valor_mensal == -8600.0 and por_fonte["recorrente"].dia_mes == 27
    assert len(fluxos) == 2  # a transferência de valor muito variável não é "recorrente"
    assert all(m.id in ids for m in movs if "CAIXADIRECTA" not in m.descricao)
    assert not any(m.id in ids for m in movs if "CAIXADIRECTA" in m.descricao)


def test_backtest_honesto_so_usa_movimentos_ate_a_data_de_corte():
    contratos = [_contrato("Aixing Zhao", 400.0)]
    movs = [Mov(i, date(2026, 9, 8), "J PINTO", "TRF AIXING ZHAO", 400.0) for i in range(1, 2)]
    fluxos, ids = detetar_fluxos(movs, date(2026, 8, 31), contratos)
    assert fluxos == [] and ids == set()


def test_fluxo_mensal_cai_no_dia_util_seguinte_ao_fim_de_semana():
    contratos = [_contrato("Aixing Zhao", 400.0)]
    movs = [Mov(m, date(2026, m, 3), "J PINTO", "TRF AIXING ZHAO", 400.0) for m in range(4, 10)]
    fluxos, _ = detetar_fluxos(movs, date(2026, 9, 30), contratos)
    # 03/10/2026 é sábado -> segunda 05/10
    dias = [date(2026, 10, d) for d in range(1, 32)]
    por_dia = fluxos_nos_dias(fluxos, dias)
    assert list(por_dia) == [date(2026, 10, 5)]
