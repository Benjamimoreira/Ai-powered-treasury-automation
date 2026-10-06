"""Linha "previsão ao ritmo atual" e tempo de vida (previsao_ancorada.ritmo_atual):
os fluxos conhecidos da previsão + o resto ao ritmo dos últimos 90 dias."""
from datetime import date, timedelta
from types import SimpleNamespace

from app.services import previsao_ancorada as pa

HOJE = date(2026, 10, 6)
DIAS_FUTUROS = [HOJE + timedelta(days=i) for i in range(1, 31)]
ID_RENDA = 7  # movimento do histórico que é um fluxo conhecido (uma renda recebida)


def _historico(saldo_inicio: float, saldo_hoje: float) -> list:
    return [SimpleNamespace(dia=HOJE - timedelta(days=90), saldo_contabilistico=saldo_inicio),
            SimpleNamespace(dia=HOJE, saldo_contabilistico=saldo_hoje)]


def _cenario(monkeypatch, recebimentos: float, pagamentos: float, renda_passada: float = 0.0,
             fluxos_futuros: list = ()):
    """Movimentos dos últimos 90 dias (a renda passada à parte, com o id de
    fluxo conhecido) e os fluxos conhecidos previstos."""
    dia = HOJE - timedelta(days=10)

    def movimentos_por_dia(db, empresa, ate, excluir):
        renda = 0.0 if ID_RENDA in excluir else renda_passada
        return [{"dia": dia, "recebimentos": recebimentos + renda, "pagamentos": pagamentos},
                {"dia": HOJE - timedelta(days=200), "recebimentos": 1e6, "pagamentos": 0}]  # fora da janela

    monkeypatch.setattr(pa, "_movimentos_por_dia", movimentos_por_dia)
    monkeypatch.setattr(pa, "fluxos_conhecidos_no_horizonte", lambda contexto, dias, empresa: list(fluxos_futuros))
    return SimpleNamespace(ids_conhecidos={ID_RENDA})


def test_resto_ao_ritmo_dos_movimentos_quando_explicam_o_saldo(monkeypatch):
    # recebeu 10 000, pagou 19 000 em 90 dias -> -100 €/dia; o saldo desceu 9 000: bate certo
    contexto = _cenario(monkeypatch, 10_000, 19_000)
    r = pa.ritmo_atual(None, "EMPRESA X", _historico(14_000, 5_000), DIAS_FUTUROS, contexto)

    assert r["fonte"] == "movimentos"
    assert r["resto_diario"] == -100
    assert r["previsao"][0]["valor"] == 4_900 and r["previsao"][-1]["valor"] == 2_000
    # 5 000 € a -100 €/dia ficam em -1 000 € ao dia 60: 1.º dia abaixo = 61
    assert r["tempo_de_vida"]["dias"] == 61
    assert r["tempo_de_vida"]["dia"] == (HOJE + timedelta(days=61)).isoformat()
    assert r["tempo_de_vida"]["alem_do_horizonte"] is True  # horizonte de 30 dias


def test_fluxos_conhecidos_entram_pela_previsao_e_nao_contam_duas_vezes(monkeypatch):
    # nos últimos 90 dias: 9 000 de renda (fluxo conhecido) + 9 000 de outros recebimentos - 18 000 de pagamentos
    # -> o resto é -9 000 / 90 = -100 €/dia; a renda volta pela previsão, no dia em que cai
    renda_futura = {"dia": HOJE + timedelta(days=10), "valor": 3_000.0, "fonte": "renda"}
    contexto = _cenario(monkeypatch, 9_000, 18_000, renda_passada=9_000, fluxos_futuros=[renda_futura])
    r = pa.ritmo_atual(None, "EMPRESA X", _historico(5_000, 5_000), DIAS_FUTUROS, contexto)

    assert r["fonte"] == "movimentos"
    assert r["conhecidos_no_periodo"] == 9_000
    assert r["resto_diario"] == -100
    assert r["previsao"][8]["valor"] == 5_000 - 900          # dia 9: ainda sem a renda
    assert r["previsao"][9]["valor"] == 5_000 - 1_000 + 3_000  # dia 10: a renda entra uma vez


def test_resto_pela_variacao_do_saldo_quando_faltam_movimentos(monkeypatch):
    # os extratos dizem -545 k€, mas o saldo só desceu 90 k€ (faltam entradas) -> usa o saldo
    contexto = _cenario(monkeypatch, 100_000, 645_000)
    r = pa.ritmo_atual(None, "EMPRESA X", _historico(260_000, 170_000), DIAS_FUTUROS, contexto)

    assert r["fonte"] == "saldo"
    assert r["resto_diario"] == -1_000
    assert r["tempo_de_vida"]["dias"] == 172  # ao dia 171 está em -1 000 €, ainda não abaixo


def test_sem_fim_a_vista_quando_recebe_mais_do_que_paga(monkeypatch):
    contexto = _cenario(monkeypatch, 20_000, 11_000)
    r = pa.ritmo_atual(None, "EMPRESA X", _historico(1_000, 10_000), DIAS_FUTUROS, contexto)
    assert r["resto_diario"] == 100
    assert r["tempo_de_vida"]["dias"] is None


def test_tempo_de_vida_nao_depende_do_horizonte(monkeypatch):
    contexto = _cenario(monkeypatch, 10_000, 19_000)
    vidas = {h: pa.ritmo_atual(None, "EMPRESA X", _historico(14_000, 5_000),
                               [HOJE + timedelta(days=i) for i in range(1, h + 1)], contexto)["tempo_de_vida"]["dias"]
             for h in (7, 30, 180)}
    assert set(vidas.values()) == {61}


def test_sem_historico_de_90_dias_nao_ha_ritmo(monkeypatch):
    contexto = _cenario(monkeypatch, 0, 0)
    curto = [SimpleNamespace(dia=HOJE - timedelta(days=30), saldo_contabilistico=1),
             SimpleNamespace(dia=HOJE, saldo_contabilistico=1)]
    assert pa.ritmo_atual(None, "EMPRESA X", curto, DIAS_FUTUROS, contexto) is None
