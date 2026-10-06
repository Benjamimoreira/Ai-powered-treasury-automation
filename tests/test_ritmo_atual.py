"""Linha "ao ritmo dos últimos 90 dias" e tempo de vida (previsao_ancorada.ritmo_atual)."""
from datetime import date, timedelta
from types import SimpleNamespace

from app.services import previsao_ancorada as pa

HOJE = date(2026, 10, 6)


def _historico(saldo_inicio: float, saldo_hoje: float) -> list:
    inicio = HOJE - timedelta(days=90)
    return [SimpleNamespace(dia=inicio, saldo_contabilistico=saldo_inicio),
            SimpleNamespace(dia=HOJE, saldo_contabilistico=saldo_hoje)]


def _movimentos(monkeypatch, recebimentos: float, pagamentos: float):
    dia = HOJE - timedelta(days=10)
    monkeypatch.setattr(pa, "_movimentos_por_dia", lambda db, empresa, ate, excluir: [
        {"dia": dia, "recebimentos": recebimentos, "pagamentos": pagamentos},
        {"dia": HOJE - timedelta(days=200), "recebimentos": 1e6, "pagamentos": 0},  # fora da janela
    ])


DIAS_FUTUROS = [HOJE + timedelta(days=i) for i in range(1, 31)]


def test_ritmo_pelos_movimentos_quando_explicam_o_saldo(monkeypatch):
    # recebeu 10 000, pagou 19 000 em 90 dias -> -100 €/dia; o saldo desceu 9 000: bate certo
    _movimentos(monkeypatch, 10_000, 19_000)
    r = pa.ritmo_atual(None, "EMPRESA X", _historico(14_000, 5_000), DIAS_FUTUROS)

    assert r["fonte"] == "movimentos"
    assert r["liquido_diario"] == -100
    assert r["previsao"][0]["valor"] == 4_900 and r["previsao"][-1]["valor"] == 2_000
    # 5 000 € a -100 €/dia chegam a -1 000 € ao fim de 60 dias: 1.º dia abaixo = 61
    assert r["tempo_de_vida"]["dias"] == 61
    assert r["tempo_de_vida"]["dia"] == (HOJE + timedelta(days=61)).isoformat()
    assert r["tempo_de_vida"]["alem_do_horizonte"] is True  # horizonte de 30 dias


def test_ritmo_pela_variacao_do_saldo_quando_faltam_movimentos(monkeypatch):
    # os extratos dizem -545 k€, mas o saldo só desceu 90 k€ (faltam entradas) -> usa o saldo
    _movimentos(monkeypatch, 100_000, 645_000)
    r = pa.ritmo_atual(None, "EMPRESA X", _historico(260_000, 170_000), DIAS_FUTUROS)

    assert r["fonte"] == "saldo"
    assert r["liquido_diario"] == -1_000
    assert r["tempo_de_vida"]["dias"] == 172  # ao dia 171 está em -1 000 €, ainda não abaixo


def test_sem_fim_a_vista_quando_recebe_mais_do_que_paga(monkeypatch):
    _movimentos(monkeypatch, 20_000, 11_000)
    r = pa.ritmo_atual(None, "EMPRESA X", _historico(1_000, 10_000), DIAS_FUTUROS)
    assert r["liquido_diario"] == 100
    assert r["tempo_de_vida"]["dias"] is None


def test_sem_historico_de_90_dias_nao_ha_ritmo(monkeypatch):
    _movimentos(monkeypatch, 0, 0)
    curto = [SimpleNamespace(dia=HOJE - timedelta(days=30), saldo_contabilistico=1), SimpleNamespace(dia=HOJE, saldo_contabilistico=1)]
    assert pa.ritmo_atual(None, "EMPRESA X", curto, DIAS_FUTUROS) is None
