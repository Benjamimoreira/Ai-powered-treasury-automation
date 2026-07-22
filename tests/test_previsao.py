from datetime import date, timedelta

import pytest

from app.db.models import SaldoDiario
from app.services.previsao import prever_saldo

DIA_INICIAL = date(2026, 7, 1)


def _adicionar_serie(db_session, empresa, valores):
    for i, valor in enumerate(valores):
        db_session.add(SaldoDiario(
            dia=DIA_INICIAL + timedelta(days=i), entidade=empresa,
            saldo_contabilistico=valor, saldo_disponivel=valor,
        ))
    db_session.commit()


def test_prever_saldo_falha_com_historico_insuficiente(db_session):
    _adicionar_serie(db_session, "EMPRESA NOVA,LDA", [100.0, 105.0])

    with pytest.raises(ValueError):
        prever_saldo(db_session, "EMPRESA NOVA,LDA")


def test_prever_saldo_devolve_historico_e_os_3_modelos(db_session):
    # tendência clara e constante: +10 por dia
    valores = [100.0 + 10 * i for i in range(10)]
    _adicionar_serie(db_session, "EMPRESA TESTE,LDA", valores)

    resultado = prever_saldo(db_session, "EMPRESA TESTE,LDA", dias_futuro=3)

    assert len(resultado["historico"]) == 10
    assert set(resultado["previsao"].keys()) == {
        "regressao_linear", "media_movel", "suavizacao_exponencial",
    }
    for pontos in resultado["previsao"].values():
        assert len(pontos) == 3

    # regressão linear sobre uma tendência perfeitamente linear deve
    # continuar a extrapolar exatamente +10/dia (dia 10 -> 100+10*10=200)
    primeiro_previsto_linear = resultado["previsao"]["regressao_linear"][0]["valor"]
    assert primeiro_previsto_linear == pytest.approx(200.0, abs=1.0)

    # ordem dos dias futuros é sequencial a partir do último dia histórico
    dias_previstos = [p["dia"] for p in resultado["previsao"]["regressao_linear"]]
    assert dias_previstos == ["2026-07-11", "2026-07-12", "2026-07-13"]


def test_prever_saldo_ignora_forma_legal_na_empresa(db_session):
    valores = [50.0] * 6
    _adicionar_serie(db_session, "Ancora Apogeu", valores)

    resultado = prever_saldo(db_session, "ANCORA APOGEU,LDA", dias_futuro=2)

    assert len(resultado["historico"]) == 6
