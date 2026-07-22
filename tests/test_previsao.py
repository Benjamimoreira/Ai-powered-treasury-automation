from datetime import date, timedelta

import pytest

from app.db.models import SaldoDiario
from app.services.previsao import avaliar_modelos, prever_saldo

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


def test_prever_saldo_devolve_historico_e_os_modelos_basicos(db_session):
    # tendência clara e constante: +10 por dia
    valores = [100.0 + 10 * i for i in range(10)]
    _adicionar_serie(db_session, "EMPRESA TESTE,LDA", valores)

    resultado = prever_saldo(db_session, "EMPRESA TESTE,LDA", dias_futuro=3)

    assert len(resultado["historico"]) == 10
    # os modelos mais simples nunca deviam falhar com uma série tão limpa;
    # markov_switching pode falhar aqui (sem "regimes" a distinguir numa
    # tendência perfeitamente linear e sem ruído) - por isso não é exigido.
    assert {"regressao_linear", "media_movel", "suavizacao_exponencial", "arima"} <= set(
        resultado["previsao"].keys()
    )
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


def test_prever_markov_switching_quando_converge_fica_dentro_da_gama_dos_regimes(db_session):
    # markov-switching é sensível à otimização (MLE) - em séries sintéticas
    # curtas pode não convergir (é intencionalmente ignorado nesse caso, ver
    # prever_saldo). Este teste só confirma que, quando aparece no
    # resultado, os valores previstos são plausíveis - nunca explodem para
    # fora da gama observada nos dois regimes.
    import numpy as np

    rng = np.random.default_rng(42)
    valores = []
    for bloco in range(4):
        nivel = 100.0 if bloco % 2 == 0 else 500.0
        valores.extend((nivel + rng.normal(0, 5, 8)).tolist())
    _adicionar_serie(db_session, "EMPRESA REGIME,LDA", valores)

    resultado = prever_saldo(db_session, "EMPRESA REGIME,LDA", dias_futuro=3)

    if "markov_switching" in resultado["previsao"]:
        previstos = [p["valor"] for p in resultado["previsao"]["markov_switching"]]
        for valor in previstos:
            assert -50.0 <= valor <= 600.0


def test_avaliar_modelos_calcula_rmse_e_escolhe_melhor(db_session):
    valores = [100.0 + 10 * i for i in range(15)]
    _adicionar_serie(db_session, "EMPRESA TESTE,LDA", valores)

    resultado = avaliar_modelos(db_session, "EMPRESA TESTE,LDA", dias_teste=3)

    assert resultado["dias_teste"] == 3
    assert "regressao_linear" in resultado["rmse_por_modelo"]
    # tendência perfeitamente linear -> o modelo escolhido deve acertar quase
    # em cheio (não fixamos qual modelo especificamente vence - vários
    # captam bem uma tendência linear sem ruído; o que importa é que o
    # erro do vencedor é próximo de zero)
    assert resultado["melhor_modelo"] is not None
    assert resultado["rmse_por_modelo"][resultado["melhor_modelo"]] == pytest.approx(0.0, abs=0.5)


def test_avaliar_modelos_falha_com_historico_insuficiente(db_session):
    _adicionar_serie(db_session, "EMPRESA NOVA,LDA", [100.0, 105.0, 110.0])

    with pytest.raises(ValueError):
        avaliar_modelos(db_session, "EMPRESA NOVA,LDA", dias_teste=5)
