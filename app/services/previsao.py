"""Previsão de saldos (Fase 3.5 do roteiro): vários modelos de séries
temporais para comparar, sobre o histórico de saldos_diarios de uma
conta. ML clássico, não LLM - séries numéricas não precisam de um modelo
de linguagem.

Modelos:
- regressao_linear: tendência simples (sklearn).
- media_movel: baseline ingénuo (média dos últimos dias).
- suavizacao_exponencial: Holt (nível + tendência).
- arima: ARIMA(1,1,1) - capta autocorrelação, mais adequado a séries
  financeiras do que a regressão linear pura.
- markov_switching: modelo de mudança de regime (2 regimes, média por
  regime) - pensado para contas com quebras/mudanças estruturais no
  meio do histórico, onde os outros modelos extrapolam mal.
"""
from datetime import timedelta

import numpy as np
from sklearn.linear_model import LinearRegression
from sqlalchemy.orm import Session
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import Holt
from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

from app.db.models import SaldoDiario
from app.services.reconciliador import chave_empresa

MIN_PONTOS = 5
JANELA_MEDIA_MOVEL = 5


def _historico_saldo(db: Session, empresa: str):
    alvo = chave_empresa(empresa)
    todos = db.query(SaldoDiario).order_by(SaldoDiario.dia).all()
    return [
        s for s in todos
        if chave_empresa(s.entidade) == alvo and s.saldo_contabilistico is not None
    ]


def _prever_linear(n_pontos: int, valores: list, n_futuro: int) -> list:
    """Regressão linear sobre o índice do dia - tendência simples."""
    x = np.arange(n_pontos).reshape(-1, 1)
    y = np.array(valores)
    modelo = LinearRegression()
    modelo.fit(x, y)
    x_futuro = np.arange(n_pontos, n_pontos + n_futuro).reshape(-1, 1)
    return modelo.predict(x_futuro).tolist()


def _prever_media_movel(valores: list, n_futuro: int) -> list:
    """Baseline simples: média dos últimos dias, repetida (não capta
    tendência, só o nível recente)."""
    janela = valores[-JANELA_MEDIA_MOVEL:] if len(valores) >= JANELA_MEDIA_MOVEL else valores
    media = sum(janela) / len(janela)
    return [media] * n_futuro


def _prever_suavizacao_exponencial(valores: list, n_futuro: int) -> list:
    """Suavização exponencial de Holt (nível + tendência) - reage mais
    depressa a mudanças recentes do que a regressão linear sobre todo o
    histórico."""
    modelo = Holt(valores, initialization_method="estimated").fit()
    return modelo.forecast(n_futuro).tolist()


def _prever_arima(valores: list, n_futuro: int) -> list:
    """ARIMA(1,1,1) - modela autocorrelação e tendência (diferenciação),
    geralmente mais robusto que regressão linear pura para séries
    financeiras com ruído dia-a-dia."""
    modelo = ARIMA(valores, order=(1, 1, 1)).fit()
    return modelo.forecast(n_futuro).tolist()


def _prever_markov(valores: list, n_futuro: int) -> list:
    """Modelo de mudança de regime (Markov-switching, 2 regimes, média
    própria por regime). Em vez de assumir um único padrão para toda a
    série, aprende que pode haver "estados" diferentes (ex. antes/depois
    de uma quebra) e prevê como a mistura ponderada dos dois regimes,
    propagando as probabilidades de transição para a frente."""
    array = np.array(valores, dtype=float)
    modelo = MarkovRegression(array, k_regimes=2, trend="c", switching_variance=True).fit()

    # smoothed_marginal_probabilities pode vir como DataFrame ou ndarray
    # consoante a versão/config do statsmodels - np.asarray normaliza os dois.
    probs_atuais = np.asarray(modelo.smoothed_marginal_probabilities)[-1]
    # regime_transition[i, j, 0] = P(próximo=i | atual=j) - matriz "left
    # stochastic" (colunas somam 1); modelo.params vem sempre como ndarray
    # posicional, por isso mapeamos o nome para o índice via param_names.
    matriz_transicao = modelo.regime_transition[:, :, 0]
    nomes_parametros = list(modelo.model.param_names)
    medias_regime = [modelo.params[nomes_parametros.index(f"const[{k}]")] for k in range(2)]

    previsoes = []
    probs = np.asarray(probs_atuais, dtype=float)
    for _ in range(n_futuro):
        probs = matriz_transicao @ probs
        previsoes.append(float(sum(p * m for p, m in zip(probs, medias_regime))))
    return previsoes


_FUNCOES_MODELO = {
    "regressao_linear": lambda valores, n_futuro: _prever_linear(len(valores), valores, n_futuro),
    "media_movel": lambda valores, n_futuro: _prever_media_movel(valores, n_futuro),
    "suavizacao_exponencial": lambda valores, n_futuro: _prever_suavizacao_exponencial(valores, n_futuro),
    "arima": lambda valores, n_futuro: _prever_arima(valores, n_futuro),
    "markov_switching": lambda valores, n_futuro: _prever_markov(valores, n_futuro),
}


def _rmse(reais: list, previstos: list) -> float:
    return float(np.sqrt(np.mean((np.array(reais) - np.array(previstos)) ** 2)))


def prever_saldo(db: Session, empresa: str, dias_futuro: int = 7) -> dict:
    """Prevê o saldo contabilístico dos próximos dias com vários modelos,
    para comparação lado a lado. Um modelo que falhe (ex. ARIMA sem
    convergir numa série muito curta/irregular) é ignorado - os restantes
    continuam a aparecer. Levanta ValueError se não houver histórico
    suficiente para nenhum modelo."""
    historico = _historico_saldo(db, empresa)
    if len(historico) < MIN_PONTOS:
        raise ValueError(
            f"Histórico insuficiente para prever saldo de '{empresa}' "
            f"({len(historico)} pontos, mínimo {MIN_PONTOS})."
        )

    dias = [h.dia for h in historico]
    valores = [h.saldo_contabilistico for h in historico]
    ultimo_dia = dias[-1]
    dias_futuros = [ultimo_dia + timedelta(days=i + 1) for i in range(dias_futuro)]

    previsao = {}
    for nome, funcao in _FUNCOES_MODELO.items():
        try:
            previsao[nome] = funcao(valores, dias_futuro)
        except Exception:
            continue

    return {
        "historico": [{"dia": d.isoformat(), "valor": v} for d, v in zip(dias, valores)],
        "previsao": {
            modelo: [
                {"dia": d.isoformat(), "valor": v}
                for d, v in zip(dias_futuros, valores_previstos)
            ]
            for modelo, valores_previstos in previsao.items()
        },
    }


def avaliar_modelos(db: Session, empresa: str, dias_teste: int = 5) -> dict:
    """Avaliação honesta (treino/teste): retira os últimos `dias_teste`
    dias como conjunto de teste, treina cada modelo só com o resto, e
    compara a previsão de cada um com o valor real que já conhecemos
    (RMSE - erro quadrático médio, mesma unidade EUR). Responde à pergunta
    "qual modelo acerta mais nesta conta", em vez de só mostrar previsões
    lado a lado sem validação."""
    historico = _historico_saldo(db, empresa)
    if len(historico) < MIN_PONTOS + dias_teste:
        raise ValueError(
            f"Histórico insuficiente para avaliar '{empresa}' ({len(historico)} "
            f"pontos, preciso de pelo menos {MIN_PONTOS + dias_teste} para treino+teste)."
        )

    valores = [h.saldo_contabilistico for h in historico]
    treino = valores[:-dias_teste]
    teste_real = valores[-dias_teste:]

    rmse_por_modelo = {}
    falhas = {}
    for nome, funcao in _FUNCOES_MODELO.items():
        try:
            previsto = funcao(treino, dias_teste)
            rmse_por_modelo[nome] = _rmse(teste_real, previsto)
        except Exception as e:
            falhas[nome] = str(e)

    melhor_modelo = min(rmse_por_modelo, key=rmse_por_modelo.get) if rmse_por_modelo else None

    return {
        "dias_teste": dias_teste,
        "rmse_por_modelo": rmse_por_modelo,
        "melhor_modelo": melhor_modelo,
        "falhas": falhas,
    }
