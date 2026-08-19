"""Previsão de saldos e de cash-flow (Fase 3.5 do roteiro): vários
modelos de séries temporais para comparar. ML clássico, não LLM - séries
numéricas não precisam de um modelo de linguagem.

Modelos de séries temporais puras (só usam os valores passados da própria
série, sem outras variáveis):
- regressao_linear: tendência simples (sklearn).
- media_movel: baseline ingénuo (média dos últimos dias).
- suavizacao_exponencial: Holt (nível + tendência).
- arima: ARIMA(1,1,1) - capta autocorrelação, mais adequado a séries
  financeiras do que a regressão linear pura.
- markov_switching: modelo de mudança de regime (2 regimes, média por
  regime) - pensado para contas com quebras/mudanças estruturais no
  meio do histórico, onde os outros modelos extrapolam mal.

Estes cinco são estatística clássica (só a regressão linear é
tecnicamente "ML", scikit-learn) - todos assumem que o futuro é uma
função só da posição no tempo/dos valores anteriores. Só se aplicam à
previsão de cash-flow (não à de saldo, para não misturar as duas
famílias de modelo na mesma comparação):
- gradient_boosting: Gradient Boosting Regressor (sklearn) sobre
  features explícitas (dia da semana, dia do mês, lags, média móvel) -
  aprende padrões a partir de variáveis, não só a forma da curva no
  tempo, e expõe `feature_importances_` (quais variáveis pesaram mais),
  algo que nenhum dos modelos de séries temporais consegue mostrar.

Previsão de saldo vs. previsão de cash-flow: o saldo diário
(SaldoDiario) só muda em dias com movimento - na prática é uma "escada"
que fica vários dias seguidos exatamente igual e só salta quando entra
ou sai dinheiro. Um modelo treinado nessa série, quando os últimos dias
estão parados, prevê corretamente "sem alteração" - o que é fácil de
confundir com "não está a prever nada". O cash-flow (recebimentos -
pagamentos, a partir de MovimentoBancario) varia todos os dias porque
inclui explicitamente os dias sem movimento (valor 0), por isso a
previsão fica visualmente percetível mesmo sem tendência forte.
"""
from datetime import timedelta

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sqlalchemy.orm import Session
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import Holt
from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression

from app.db.models import MovimentoBancario, SaldoDiario
from app.services.reconciliador import chave_empresa

MIN_PONTOS = 5
JANELA_MEDIA_MOVEL = 5

# O gradient boosting perde os primeiros N_LAGS dias a construir as
# features de lag, por isso precisa de mais histórico do que os modelos
# de séries temporais puras para sobrar treino suficiente.
N_LAGS = 3
MIN_PONTOS_ML = 15
NOMES_FEATURES_GB = [
    "dia_semana", "dia_mes", "fim_de_semana", "lag_1", "lag_2", "lag_3", "media_movel_5",
]


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
    """ARIMA(1,1,1) com drift - modela autocorrelação e tendência
    (diferenciação), geralmente mais robusto que regressão linear pura
    para séries financeiras com ruído dia-a-dia.

    trend="t" é essencial aqui: com d=1, uma constante ("c") é eliminada
    pela própria diferenciação - o statsmodels rejeita-a com erro. Uma
    tendência linear ("t") tem o efeito equivalente a um termo de "drift"
    na série diferenciada. Sem isto, a previsão converge quase de
    imediato para uma linha praticamente constante em vez de continuar
    a tendência observada."""
    modelo = ARIMA(valores, order=(1, 1, 1), trend="t").fit()
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


def _features_dia_gb(dia, valores_passados: list) -> list:
    """Vetor de features de um dia para o gradient boosting: calendário
    (conhecido antecipadamente, incluindo para dias futuros) + lags e
    média móvel do cash-flow líquido, calculados só a partir de valores
    já conhecidos/previstos até esse dia (nunca "espreita" o futuro)."""
    janela = valores_passados[-JANELA_MEDIA_MOVEL:]
    return [
        dia.weekday(),
        dia.day,
        int(dia.weekday() >= 5),
        valores_passados[-1],
        valores_passados[-2] if len(valores_passados) >= 2 else valores_passados[-1],
        valores_passados[-3] if len(valores_passados) >= 3 else valores_passados[-1],
        sum(janela) / len(janela),
    ]


def _prever_gradient_boosting(dias: list, valores: list, n_futuro: int):
    """Gradient Boosting (scikit-learn) sobre features de calendário e
    lags/média móvel do cash-flow. Ao contrário dos modelos de séries
    temporais puras (que só extrapolam a forma da série ao longo do
    tempo), este aprende a relação entre variáveis explícitas e o
    cash-flow - por isso pode reagir de forma diferente da simples
    continuação da tendência (ex. aprender que sextas-feiras têm mais
    saídas). Devolve (previsões, importância de cada feature)."""
    if len(valores) < MIN_PONTOS_ML:
        raise ValueError(
            f"histórico insuficiente para gradient boosting "
            f"({len(valores)} pontos, mínimo {MIN_PONTOS_ML})"
        )

    x_treino = [_features_dia_gb(dias[i], valores[:i]) for i in range(N_LAGS, len(valores))]
    y_treino = valores[N_LAGS:]

    modelo = GradientBoostingRegressor(
        n_estimators=80, max_depth=2, learning_rate=0.1, subsample=0.8, random_state=42,
    )
    modelo.fit(np.array(x_treino), np.array(y_treino))

    historico_estendido = list(valores)
    dia_seguinte = dias[-1]
    previsoes = []
    for _ in range(n_futuro):
        dia_seguinte = dia_seguinte + timedelta(days=1)
        features = _features_dia_gb(dia_seguinte, historico_estendido)
        previsto = float(modelo.predict([features])[0])
        previsoes.append(previsto)
        historico_estendido.append(previsto)

    importancias = dict(zip(NOMES_FEATURES_GB, modelo.feature_importances_.tolist()))
    return previsoes, importancias


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


def _avaliar_valores(valores: list, dias_teste: int) -> dict:
    """Núcleo comum da avaliação treino/teste (RMSE por modelo), usado
    tanto para o saldo como para o cash-flow - só muda de onde vem a
    lista de valores."""
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
    return _avaliar_valores(valores, dias_teste)


def avaliar_cashflow(db: Session, empresa: str = None, dias_teste: int = 5) -> dict:
    """Mesma avaliação treino/teste de `avaliar_modelos`, mas sobre o
    cash-flow líquido diário em vez do saldo - e inclui também o
    gradient_boosting (só disponível para cash-flow), para responder
    honestamente se o modelo de ML bate os de séries temporais puras
    nesta série, em vez de assumir que sim."""
    serie = _movimentos_por_dia(db, empresa)
    if len(serie) < MIN_PONTOS + dias_teste:
        alvo = empresa or "todas as entidades"
        raise ValueError(
            f"Histórico insuficiente para avaliar cash-flow de '{alvo}' ({len(serie)} "
            f"dias, preciso de pelo menos {MIN_PONTOS + dias_teste} para treino+teste)."
        )

    dias = [s["dia"] for s in serie]
    liquidos = [s["liquido"] for s in serie]
    resultado = _avaliar_valores(liquidos, dias_teste)

    dias_treino, liquidos_treino = dias[:-dias_teste], liquidos[:-dias_teste]
    teste_real = liquidos[-dias_teste:]
    try:
        previsto_gb, _ = _prever_gradient_boosting(dias_treino, liquidos_treino, dias_teste)
        rmse_gb = _rmse(teste_real, previsto_gb)
        resultado["rmse_por_modelo"]["gradient_boosting"] = rmse_gb
        if rmse_gb < resultado["rmse_por_modelo"][resultado["melhor_modelo"]]:
            resultado["melhor_modelo"] = "gradient_boosting"
    except Exception as e:
        resultado["falhas"]["gradient_boosting"] = str(e)

    return resultado


def _movimentos_por_dia(db: Session, empresa: str = None) -> list:
    """Recebimentos/pagamentos/líquido por dia. `empresa=None` agrega
    todas as entidades (série mais densa, melhor para ver o padrão geral
    da tesouraria); com `empresa`, filtra só essa entidade. Preenche a
    zero todos os dias do calendário entre o primeiro e o último
    movimento - um dia sem movimento é sinal real (ao contrário do saldo,
    aqui não há "falta de leitura"), e é o que torna esta série boa para
    prever (a de saldo fica plana demasiados dias seguidos)."""
    query = db.query(MovimentoBancario)
    if empresa is not None:
        alvo = chave_empresa(empresa)
        movimentos = [m for m in query.all() if chave_empresa(m.empresa) == alvo]
    else:
        movimentos = query.all()

    if not movimentos:
        return []

    por_dia = {}
    for m in movimentos:
        totais = por_dia.setdefault(m.dia, {"recebimentos": 0.0, "pagamentos": 0.0})
        if m.valor >= 0:
            totais["recebimentos"] += m.valor
        else:
            totais["pagamentos"] += -m.valor

    primeiro, ultimo = min(por_dia), max(por_dia)
    serie = []
    dia = primeiro
    while dia <= ultimo:
        totais = por_dia.get(dia, {"recebimentos": 0.0, "pagamentos": 0.0})
        serie.append({
            "dia": dia,
            "recebimentos": totais["recebimentos"],
            "pagamentos": totais["pagamentos"],
            "liquido": totais["recebimentos"] - totais["pagamentos"],
        })
        dia += timedelta(days=1)
    return serie


def prever_cashflow(db: Session, empresa: str = None, dias_futuro: int = 7) -> dict:
    """Prevê o cash-flow líquido diário (recebimentos - pagamentos) dos
    próximos dias, com os mesmos modelos usados em `prever_saldo`.
    `empresa=None` prevê o cash-flow agregado de toda a carteira.
    Levanta ValueError se não houver histórico suficiente."""
    serie = _movimentos_por_dia(db, empresa)
    if len(serie) < MIN_PONTOS:
        alvo = empresa or "todas as entidades"
        raise ValueError(
            f"Histórico insuficiente para prever cash-flow de '{alvo}' "
            f"({len(serie)} dias, mínimo {MIN_PONTOS})."
        )

    dias = [s["dia"] for s in serie]
    liquidos = [s["liquido"] for s in serie]
    ultimo_dia = dias[-1]
    dias_futuros = [ultimo_dia + timedelta(days=i + 1) for i in range(dias_futuro)]

    previsao = {}
    for nome, funcao in _FUNCOES_MODELO.items():
        try:
            previsao[nome] = funcao(liquidos, dias_futuro)
        except Exception:
            continue

    importancia_features = None
    try:
        previsao["gradient_boosting"], importancia_features = _prever_gradient_boosting(
            dias, liquidos, dias_futuro,
        )
    except Exception:
        pass

    return {
        "historico": [
            {
                "dia": s["dia"].isoformat(),
                "recebimentos": s["recebimentos"],
                "pagamentos": s["pagamentos"],
                "liquido": s["liquido"],
            }
            for s in serie
        ],
        "previsao": {
            modelo: [
                {"dia": d.isoformat(), "valor": v}
                for d, v in zip(dias_futuros, valores_previstos)
            ]
            for modelo, valores_previstos in previsao.items()
        },
        "importancia_features": importancia_features,
    }
