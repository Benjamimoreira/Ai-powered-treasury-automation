"""Previsão de saldos e de cash-flow (Fase 3.5 do roteiro): vários
modelos de séries temporais para comparar. ML clássico, não LLM - séries
numéricas não precisam de um modelo de linguagem.

Modelos de séries temporais puras (só usam os valores passados da própria
série, sem outras variáveis):
- regressao_linear: tendência simples (sklearn).
- media_movel: baseline ingénuo (média dos últimos dias).
- suavizacao_exponencial: Holt-Winters (nível + tendência + sazonalidade
  semanal) quando há pelo menos 2 semanas de histórico; cai para Holt
  simples (sem sazonalidade) em séries mais curtas.
- arima: ARIMA(1,1,1) com drift - capta autocorrelação, mais adequado a
  séries financeiras do que a regressão linear pura; com pelo menos 3
  semanas de histórico tenta primeiro um SARIMAX com termo sazonal
  semanal, caindo para o ARIMA simples se não convergir.
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

Antes de treinar qualquer um destes modelos, a série passa por
_suavizar_outliers() (winsorização por IQR) - um movimento excecional e
pontual (ex. um mútuo de centenas de milhares de euros) não deve ser
aprendido como se fosse o "novo normal" da série. O histórico real
mostrado no gráfico e usado para medir o erro (RMSE) nunca é alterado,
só a cópia que os modelos veem durante o treino.

Além destes, "ensemble" combina a previsão de todos os modelos acima
(incluindo gradient_boosting, quando aplicável) ponderada pelo RMSE de
cada um no teste retido - ver _pesos_ensemble().

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
import warnings
from collections import namedtuple
from datetime import timedelta
from typing import Optional

import numpy as np
from sklearn.ensemble import GradientBoostingRegressor
from sklearn.linear_model import LinearRegression
from sqlalchemy.orm import Session
from statsmodels.tsa.arima.model import ARIMA
from statsmodels.tsa.holtwinters import ExponentialSmoothing, Holt
from statsmodels.tsa.regime_switching.markov_regression import MarkovRegression
from statsmodels.tsa.statespace.sarimax import SARIMAX

from app.db.models import LinhaMapa, MovimentoBancario, SaldoDiario
from app.services.reconciliador import chave_empresa, empresa_do_mapa_corresponde, listar_empresas
from app.services.saldos import _serie_saldo_total_bruta

MIN_PONTOS = 5
JANELA_MEDIA_MOVEL = 5

# Janela de treino dos modelos: só os últimos N dias, não a série toda
# desde sempre. Uma conta pode ter mudado de regime muito antes de hoje
# (uma entrada/saída grande, uso diferente da conta) - um modelo treinado
# com o histórico completo aprende essa mistura de regimes como se fosse
# "a tendência" e extrapola de forma irrealista (visto em produção: um
# ARIMA(1,1,1) com trend="t" sobre 49 dias que incluíam uma queda de 90k
# para 464€ e depois um salto para 18k extrapolava uma queda constante
# de ~600€/dia dali em diante, quando os últimos 9 dias estavam
# estáveis). Prevê-se com base no comportamento recente, não com a conta
# desde a primeira leitura. Sem efeito em séries mais curtas que a janela
# (usa-se tudo o que houver); nunca reduz o "historico" devolvido ao
# dashboard, só o que os modelos veem para treinar."""
JANELA_TREINO_MODELOS = 30

# Para previsões de poucos dias, JANELA_TREINO_MODELOS (30 dias) chega e
# evita misturar regimes antigos (ver comentário acima). Mas para uma
# previsão de meses (dias_futuro grande - "gestão daqui a uns meses"),
# treinar só com os últimos 30 dias e extrapolar 90+ dias à frente é
# pouco credível: a janela cresce com o horizonte pedido (3x o número de
# dias a prever), até um teto de JANELA_TREINO_MAXIMA, para os modelos
# terem visto ciclos e amplitude suficientes para esse alcance. Nunca
# passa do histórico realmente disponível.
JANELA_TREINO_MAXIMA = 180


def _janela_treino(dias_futuro: int, n_disponiveis: int) -> int:
    """Tamanho da janela de treino a usar, em função de quantos dias se
    quer prever - ver JANELA_TREINO_MAXIMA."""
    return min(max(JANELA_TREINO_MODELOS, dias_futuro * 3), JANELA_TREINO_MAXIMA, n_disponiveis)


def _janela_recente(valores: list, dias: list = None, tamanho: int = JANELA_TREINO_MODELOS):
    """Últimos `tamanho` pontos de `valores` (e `dias`, se dado, mantendo
    o alinhamento) - ver JANELA_TREINO_MAXIMA."""
    valores_recentes = valores[-tamanho:]
    if dias is None:
        return valores_recentes
    return valores_recentes, dias[-tamanho:]

# O gradient boosting perde os primeiros N_LAGS dias a construir as
# features de lag, por isso precisa de mais histórico do que os modelos
# de séries temporais puras para sobrar treino suficiente.
N_LAGS = 3
MIN_PONTOS_ML = 15
NOMES_FEATURES_GB = [
    "dia_semana", "dia_mes", "fim_de_semana", "lag_1", "lag_2", "lag_3", "media_movel_5",
]

# Sazonalidade semanal (dia da semana costuma ter padrão - ex. menos
# movimento ao fim de semana) para ARIMA/Holt, que hoje só captam
# tendência, nunca um ciclo. Cada modelo sazonal exige mais parâmetros
# que a sua versão simples, por isso só se tenta com histórico suficiente
# para os estimar com alguma confiança - com pouco histórico cai-se de
# volta ao modelo simples (sem sazonalidade), em vez de arriscar um ajuste
# instável ou que não converge.
PERIODO_SAZONAL = 7
MIN_PONTOS_SAZONAL_HOLT = PERIODO_SAZONAL * 2
MIN_PONTOS_SAZONAL_ARIMA = PERIODO_SAZONAL * 3


_PontoSaldo = namedtuple("_PontoSaldo", ["dia", "saldo_contabilistico"])


def _preencher_dias_em_falta(pontos: list) -> list:
    """Preenche buracos de calendário entre leituras reais de saldo com o
    último saldo conhecido - mesma convenção já usada por serie_saldo_total
    ("uma conta sem movimento nesse dia entra com o saldo que já tinha, não
    com zero"), aqui aplicada ponto a ponto para nunca deixar um buraco na
    série. Sem isto, os modelos sazonais (suavização exponencial
    Holt-Winters e SARIMAX, ambos com PERIODO_SAZONAL=7 aplicado por
    POSIÇÃO na lista) assumem que a posição N e a posição N+7 estão sempre
    a 7 dias de calendário uma da outra - falso sempre que há dias sem
    leitura (nem todas as contas têm leitura todos os dias, e mesmo o saldo
    total pode ter dias sem nenhuma leitura de nenhuma entidade). Visto em
    produção: uma conta com leituras espaçadas de 2-3 dias tinha a
    suavização exponencial "sazonal" a oscilar para valores sem relação
    com o histórico (uma queda de 150€ para -60€), por estar a apanhar um
    padrão semanal que não existia. Só preenche ENTRE a primeira e a
    última leitura real - nunca estende a série para trás/à frente."""
    if len(pontos) < 2:
        return pontos
    resultado = [pontos[0]]
    for anterior, atual in zip(pontos, pontos[1:]):
        dia = anterior.dia + timedelta(days=1)
        while dia < atual.dia:
            resultado.append(_PontoSaldo(dia, anterior.saldo_contabilistico))
            dia += timedelta(days=1)
        resultado.append(atual)
    return resultado


def _historico_saldo(db: Session, empresa: Optional[str] = None):
    """Histórico de saldo contabilístico usado para prever/avaliar.
    `empresa=None` devolve a "riqueza" da empresa como um todo: o saldo
    total (soma da última leitura conhecida de cada entidade, dia a dia -
    ver _serie_saldo_total_bruta), a mesma série do gráfico "saldo
    bancário de todas as contas juntas" da Visão Geral. Com `empresa`,
    devolve o histórico dessa entidade só (comportamento original). Em
    ambos os casos, os buracos de calendário são preenchidos (ver
    _preencher_dias_em_falta) antes de devolver."""
    if empresa is None:
        pontos = [
            _PontoSaldo(p["dia"], p["saldo_contabilistico_total"])
            for p in _serie_saldo_total_bruta(db)
        ]
    else:
        alvo = chave_empresa(empresa)
        todos = db.query(SaldoDiario).order_by(SaldoDiario.dia).all()
        pontos = [
            s for s in todos
            if chave_empresa(s.entidade) == alvo and s.saldo_contabilistico is not None
        ]
    return _preencher_dias_em_falta(pontos)


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
    """Suavização exponencial de Holt-Winters (nível + tendência +
    sazonalidade semanal) quando há histórico para pelo menos 2 semanas
    completas (MIN_PONTOS_SAZONAL_HOLT) - reage mais depressa a mudanças
    recentes do que a regressão linear sobre todo o histórico, e agora
    também capta um padrão que se repita a cada 7 dias (ex. menos
    movimento ao fim de semana). Com menos histórico, ou se o ajuste
    sazonal falhar (séries curtas/irregulares por vezes não convergem),
    cai para o Holt simples (nível + tendência, sem sazonalidade)."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if len(valores) >= MIN_PONTOS_SAZONAL_HOLT:
            try:
                modelo = ExponentialSmoothing(
                    valores, trend="add", seasonal="add", seasonal_periods=PERIODO_SAZONAL,
                    initialization_method="estimated",
                ).fit()
                return modelo.forecast(n_futuro).tolist()
            except Exception:
                pass
        modelo = Holt(valores, initialization_method="estimated").fit()
        return modelo.forecast(n_futuro).tolist()


def _prever_arima(valores: list, n_futuro: int) -> list:
    """ARIMA(1,1,1) com drift - modela autocorrelação e tendência
    (diferenciação), geralmente mais robusto que regressão linear pura
    para séries financeiras com ruído dia-a-dia. Com histórico para pelo
    menos 3 semanas completas (MIN_PONTOS_SAZONAL_ARIMA), tenta primeiro
    um SARIMAX com um termo sazonal semanal (AR sazonal de ordem 1) - a
    componente sazonal tem mais parâmetros para estimar do que a
    tendência simples, por isso só se arrisca com mais histórico, e cai
    para o ARIMA simples abaixo se não convergir.

    trend="t" é essencial aqui: com d=1, uma constante ("c") é eliminada
    pela própria diferenciação - o statsmodels rejeita-a com erro. Uma
    tendência linear ("t") tem o efeito equivalente a um termo de "drift"
    na série diferenciada. Sem isto, a previsão converge quase de
    imediato para uma linha praticamente constante em vez de continuar
    a tendência observada."""
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
        if len(valores) >= MIN_PONTOS_SAZONAL_ARIMA:
            try:
                modelo = SARIMAX(
                    valores, order=(1, 1, 1), seasonal_order=(1, 0, 0, PERIODO_SAZONAL), trend="t",
                    enforce_stationarity=False, enforce_invertibility=False,
                ).fit(disp=False)
                return modelo.forecast(n_futuro).tolist()
            except Exception:
                pass
        modelo = ARIMA(valores, order=(1, 1, 1), trend="t").fit()
        return modelo.forecast(n_futuro).tolist()


def _prever_markov(valores: list, n_futuro: int) -> list:
    """Modelo de mudança de regime (Markov-switching, 2 regimes, média
    própria por regime). Em vez de assumir um único padrão para toda a
    série, aprende que pode haver "estados" diferentes (ex. antes/depois
    de uma quebra) e prevê como a mistura ponderada dos dois regimes,
    propagando as probabilidades de transição para a frente."""
    array = np.array(valores, dtype=float)
    with warnings.catch_warnings():
        warnings.simplefilter("ignore")
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


# Modelos com termo de tendência/drift (ARIMA com trend="t", Holt-Winters)
# ajustados a uma série quase constante ou com muitos zeros (conta
# adormecida, ou com um único movimento no meio do resto a zero) podem
# estimar um coeficiente de tendência instável e, ao extrapolar dezenas
# de dias à frente (sobretudo com o horizonte de meses - ver
# JANELA_TREINO_MAXIMA), o erro composto explode exponencialmente: visto
# em produção, um ARIMA sazonal previa -2.7 mil milhões de euros para uma
# conta que nunca saiu de 0€. Nenhum modelo de séries temporais "sabe"
# que isto é absurdo - o ajuste em si corre sem erro, só a extrapolação é
# irrealista. FATOR_LIMITE_SENSATO é generoso (50x a maior amplitude ou
# nível já visto na conta) para nunca rejeitar uma tendência real, por
# mais acentuada - só apanha divergências de ordens de grandeza, o tipo
# de "lixo" que não tem correspondência com nenhum cenário de negócio
# plausível.
FATOR_LIMITE_SENSATO = 50


def _limite_sensato(valores_referencia: list) -> float:
    """Magnitude acima da qual uma previsão deixa de ser credível para
    esta série - ver FATOR_LIMITE_SENSATO. O nível absoluto entra no
    cálculo (não só a amplitude) para não deixar o limite colapsar para
    quase zero numa conta parada num valor constante bem acima de zero."""
    amplitude = max(valores_referencia) - min(valores_referencia)
    nivel = max(abs(v) for v in valores_referencia)
    referencia = max(amplitude, nivel, 1.0)
    return referencia * FATOR_LIMITE_SENSATO


def _previsao_sensata(previsoes: list, limite: float) -> bool:
    return all(np.isfinite(v) and abs(v) <= limite for v in previsoes)


def _prever_com_guarda(funcao, valores_treino: list, n_futuro: int, limite: float, *args):
    """Invólucro à volta de qualquer função de `_FUNCOES_MODELO` (ou do
    gradient boosting) que rejeita a previsão se ela divergir para além
    de `_limite_sensato` - ver comentário de FATOR_LIMITE_SENSATO. Levanta
    ValueError em vez de devolver o valor absurdo, para ser apanhado pelo
    mesmo `except Exception: continue` que já trata modelos que não
    convergem - um modelo "insensato" é tratado exatamente como um
    modelo que falhou, em vez de entrar no ensemble ou aparecer no
    gráfico."""
    resultado = funcao(valores_treino, n_futuro, *args)
    previsoes = resultado[0] if isinstance(resultado, tuple) else resultado
    if not _previsao_sensata(previsoes, limite):
        raise ValueError(
            f"previsão divergiu para além do plausível para esta série "
            f"(limite ±{limite:,.2f})"
        )
    return resultado


def _suavizar_outliers(valores: list) -> list:
    """Reduz o peso de valores atípicos antes de treinar (winsorização por
    IQR, limite clássico de 1.5x): um movimento excecional e pontual (ex.
    um mútuo de centenas de milhares de euros no meio de um histórico
    normalmente na casa dos milhares - ver a aba Análise de Extratos) não
    deve ser aprendido pelos modelos como se fosse o "novo normal" da
    série, distorcendo o nível/tendência aprendidos. Nunca toca no
    histórico mostrado no gráfico nem no valor real usado para calcular o
    erro (RMSE) - só na série que os modelos veem durante o treino."""
    if len(valores) < 4:
        return valores  # amostra pequena de mais para quartis terem sentido
    array = np.array(valores, dtype=float)
    q1, q3 = np.percentile(array, [25, 75])
    iqr = q3 - q1
    if iqr == 0:
        return valores
    limite_inferior = q1 - 1.5 * iqr
    limite_superior = q3 + 1.5 * iqr
    return np.clip(array, limite_inferior, limite_superior).tolist()


def _pesos_por_rmse(rmse_por_modelo: dict) -> dict:
    """Converte RMSE por modelo em pesos de ensemble: peso proporcional ao
    inverso do erro AO QUADRADO (quem erra menos no período de teste pesa
    muito mais - combinação clássica de previsões por inverso da variância
    do erro, não só do erro), normalizado para somar 1. Ao quadrado em vez
    de linear porque 1/RMSE é indulgente demais com um modelo claramente
    mau: visto em produção (previsão de saldo total a 90 dias), um ARIMA
    com RMSE 5x pior que o melhor modelo ainda ficava com ~10% do peso do
    ensemble (1/RMSE), quando devia ficar residual - ao quadrado cai para
    ~3%. EPSILON evita divisão por zero quando um modelo acerta perfeito
    no teste (RMSE=0)."""
    EPSILON = 1e-6
    inversos = {modelo: 1.0 / (rmse + EPSILON) ** 2 for modelo, rmse in rmse_por_modelo.items()}
    total = sum(inversos.values())
    return {modelo: peso / total for modelo, peso in inversos.items()}


DIAS_TESTE_ENSEMBLE_MIN = 5
DIAS_TESTE_ENSEMBLE_MAX = 30


def _dias_teste_ensemble(dias_futuro: int, n_disponiveis: int) -> int:
    """Quantos dias retidos para medir o erro de cada modelo antes de
    calcular os pesos do ensemble (ver _pesos_ensemble) - ao contrário do
    `dias_teste` fixo em 5 dias de avaliar_modelos/avaliar_cashflow (esse é
    só diagnóstico, controlado pelo utilizador no dashboard), este teste
    tem de refletir o horizonte que se vai realmente prever: um modelo que
    erra pouco a 5 dias (ex. média móvel, Markov-switching, que revertem
    para a média) pode errar muito mais a 90 dias, e um ARIMA cujo termo de
    drift parece bem calibrado a 5 dias é exatamente o que diverge nesse
    horizonte mais longo (ver o comentário de JANELA_TREINO_MODELOS sobre
    extrapolação irrealista). Sem isto, o ensemble de uma previsão de
    meses acabava a pesar um modelo pela sua exatidão a curtíssimo prazo,
    escondendo instabilidade que só aparece mais à frente. Cresce com
    dias_futuro (mesma lógica de _janela_treino), com um teto para não
    consumir histórico de treino a mais em séries curtas. Nunca abaixo de
    1 (mesmo com histórico rente ao mínimo) - `valores[:-0]` seria a
    lista vazia em vez de "sem teste", o que corromperia o treino."""
    return max(1, min(max(DIAS_TESTE_ENSEMBLE_MIN, dias_futuro), DIAS_TESTE_ENSEMBLE_MAX, n_disponiveis - MIN_PONTOS))


DIAS_TESTE_BANDA = 5


def _rmse_estimado_banda(valores: list, pesos: dict) -> Optional[float]:
    """RMSE "base" (curto prazo) para _banda_incerteza, que faz a banda
    crescer com sqrt(horizonte) a partir daqui (ver ali) - assume que
    `rmse_estimado` é um erro de poucos passos que se acumula tipo random
    walk. Por isso usa sempre uma janela curta e FIXA (DIAS_TESTE_BANDA),
    nunca o `dias_teste` (potencialmente de meses, ver _dias_teste_ensemble)
    usado para decidir os pesos: usar esse mesmo `dias_teste` longo faria a
    banda crescer sqrt(horizonte) a partir de um erro que já é de várias
    semanas, aplicando o crescimento do erro com o horizonte duas vezes -
    banda visto em produção a ir de -2,6M a +3,6M numa série que nunca saiu
    dos 150-800 mil euros. Pondera pelos MESMOS pesos já decididos (que
    esses sim refletem o desempenho a longo prazo) - só a base do erro é
    curta, não a confiança em cada modelo."""
    dias_teste_banda = min(DIAS_TESTE_BANDA, len(valores) - MIN_PONTOS)
    if dias_teste_banda < 1:
        return None
    rmse_por_modelo = _avaliar_valores(valores, dias_teste_banda)["rmse_por_modelo"]
    pesos_relevantes = {m: pesos[m] for m in rmse_por_modelo if m in pesos}
    total = sum(pesos_relevantes.values())
    if not total:
        return None
    return sum((peso / total) * rmse_por_modelo[m] for m, peso in pesos_relevantes.items())


def _pesos_ensemble(valores: list, modelos_disponiveis: list, dias: list = None, dias_teste: int = 5) -> tuple:
    """Pesos de cada modelo no ensemble: baseados no RMSE de treino/teste
    quando há histórico suficiente para o calcular (mesma avaliação de
    `avaliar_modelos`/`avaliar_cashflow`); caso contrário, pesos iguais
    entre os modelos disponíveis - um ensemble sem avaliação não pode
    fingir saber qual modelo é melhor. `dias` só é necessário quando
    "gradient_boosting" está em `modelos_disponiveis` (precisa das datas
    para construir features de calendário no reteste).

    Devolve (pesos, rmse_estimado): rmse_estimado é uma base de erro de
    curto prazo (ver _rmse_estimado_banda) para desenhar a banda de
    incerteza à volta da previsão (ver _banda_incerteza) - não o RMSE do
    mesmo `dias_teste` usado para pesar os modelos, que pode ser de meses.
    None quando não há avaliação (fallback de pesos iguais): sem medir o
    erro de ninguém, não há base para uma banda."""
    if len(modelos_disponiveis) < 2:
        return {}, None

    if len(valores) >= MIN_PONTOS + dias_teste:
        avaliacao = _avaliar_valores(valores, dias_teste)
        rmse_por_modelo = {m: r for m, r in avaliacao["rmse_por_modelo"].items() if m in modelos_disponiveis}

        if "gradient_boosting" in modelos_disponiveis and dias is not None:
            try:
                dias_treino, valores_treino = dias[:-dias_teste], valores[:-dias_teste]
                teste_real = valores[-dias_teste:]
                previsto_gb, _ = _prever_gradient_boosting(dias_treino, _suavizar_outliers(valores_treino), dias_teste)
                if not _previsao_sensata(previsto_gb, _limite_sensato(valores)):
                    raise ValueError("previsão do gradient boosting divergiu para além do plausível")
                rmse_por_modelo["gradient_boosting"] = _rmse(teste_real, previsto_gb)
            except Exception:
                pass

        if rmse_por_modelo:
            pesos = _pesos_por_rmse(rmse_por_modelo)
            rmse_estimado = _rmse_estimado_banda(valores, pesos)
            return pesos, rmse_estimado

    return {m: 1.0 / len(modelos_disponiveis) for m in modelos_disponiveis}, None


# Fator (desvios-padrão) da banda de incerteza à volta do ensemble -
# 1.28 corresponde a um intervalo de confiança de ~80% assumindo erro
# aproximadamente normal, um meio-termo entre "banda estreita demais para
# ser honesta" e "banda tão larga que deixa de dizer nada".
Z_BANDA_INCERTEZA = 1.28


def _banda_incerteza(serie_central: list, rmse_estimado: Optional[float]) -> Optional[tuple]:
    """Banda (baixa, alta) à volta da previsão central do ensemble: a
    incerteza de uma previsão não é a mesma no dia 1 e no dia 90 - cresce
    com a raiz do número de dias à frente (mesma lógica de um random walk:
    erro acumulado ~ sqrt(horizonte)), a partir do erro medido no teste
    retido (rmse_estimado - ver _pesos_ensemble). Sem isso a previsão
    aparenta uma precisão que nenhum modelo tem, sobretudo em horizontes
    de meses - mostrar só a linha central "finge" uma certeza que não
    existe; a banda é o que torna a previsão de longo prazo credível em
    vez de uma linha reta arbitrária. Devolve None quando não há
    rmse_estimado (sem avaliação, não há como estimar a banda)."""
    if rmse_estimado is None:
        return None
    baixa, alta = [], []
    for i, valor in enumerate(serie_central):
        largura = Z_BANDA_INCERTEZA * rmse_estimado * np.sqrt(i + 1)
        baixa.append(valor - largura)
        alta.append(valor + largura)
    return baixa, alta


LIMIAR_RELATIVO_MELHORES_MODELOS = 0.5


# Séries que não competem por RMSE - nunca filtradas por
# _manter_melhores_modelos: "ensemble" é a combinação de todos os modelos,
# "previsto_mapa" nem é um modelo estatístico, é o que já está planeado no
# Mapa de Pagamentos e Recebimentos (ver _previsto_mapa_por_dia).
_SERIES_NAO_COMPETITIVAS = {"ensemble", "previsto_mapa"}


def _pesos_dos_melhores(pesos: dict) -> dict:
    """Mesmo critério de _manter_melhores_modelos (peso a pelo menos
    metade do melhor), mas devolve os PESOS renormalizados só dos
    sobreviventes, para usar na combinação do Ensemble - em vez dos pesos
    completos, que ainda incluem modelos que nem sequer são bons o
    suficiente para aparecer sozinhos no gráfico. Sem isto, um modelo
    filtrado da vista (ex. suavização exponencial a divergir para valores
    absurdos numa previsão de meses) continuava a puxar o Ensemble na
    mesma direção só que escondido - visto em produção: um Ensemble a
    cair para bem abaixo do histórico recente só por causa de ~15% de
    peso residual de um modelo que a própria filtragem já tinha
    reconhecido como mau demais para mostrar. Mesmo fallback de
    _manter_melhores_modelos quando não há avaliação real (pesos iguais):
    devolve tudo, não há base para excluir ninguém."""
    if not pesos or len(set(pesos.values())) <= 1:
        return pesos
    melhor_peso = max(pesos.values())
    limiar = melhor_peso * LIMIAR_RELATIVO_MELHORES_MODELOS
    sobreviventes = {m: w for m, w in pesos.items() if w >= limiar}
    total = sum(sobreviventes.values())
    return {m: w / total for m, w in sobreviventes.items()} if total else pesos


def _manter_melhores_modelos(previsao: dict, pesos: dict) -> dict:
    """Mantém no resultado só os modelos com melhor desempenho no teste
    retido - peso no ensemble (ver _pesos_por_rmse) a pelo menos metade do
    peso do melhor - em vez de mostrar sempre todos os modelos disputados
    lado a lado no gráfico, mesmo os que claramente erram mais nesta
    série. Sem avaliação real (pesos vazio, ou todos iguais - o fallback
    de _pesos_ensemble quando não há histórico suficiente para testar),
    não há como julgar quem é melhor, por isso mantém-se tudo em vez de
    filtrar às cegas."""
    if not pesos or len(set(pesos.values())) <= 1:
        return previsao
    melhor_peso = max(pesos.values())
    limiar = melhor_peso * LIMIAR_RELATIVO_MELHORES_MODELOS
    return {
        modelo: valores for modelo, valores in previsao.items()
        if modelo in _SERIES_NAO_COMPETITIVAS or pesos.get(modelo, 0) >= limiar
    }


def _previsto_mapa_por_dia(db: Session, dias: list, empresa: str = None) -> dict:
    """Soma diária do valor PREVISTO no Mapa de Pagamentos e Recebimentos
    para os dias indicados - o que já está planeado (recebimentos e
    pagamentos, já sinalizados: positivo recebimento, negativo pagamento -
    ver mapa_importer.py), independentemente de já ter sido confirmado
    (pago) ou não. Serve para desenhar "Previsto (Mapa)" nos gráficos de
    previsão, ao lado dos modelos estatísticos - uma referência que não é
    um modelo, é o plano que já existe. `empresa`, quando dado, é a
    designação social completa (mesmo vocabulário de listar_empresas) -
    comparada com o código curto do Mapa via empresa_do_mapa_corresponde."""
    if not dias:
        return {}
    linhas = db.query(LinhaMapa).filter(LinhaMapa.dia.in_(dias), LinhaMapa.previsto.isnot(None)).all()
    if empresa:
        linhas = [l for l in linhas if empresa_do_mapa_corresponde(l.empresa, empresa)]

    por_dia: dict = {}
    for l in linhas:
        por_dia[l.dia] = por_dia.get(l.dia, 0.0) + l.previsto
    return por_dia


def _previsao_ensemble(previsao_por_modelo: dict, pesos: dict, n_futuro: int) -> list:
    """Combina, dia a dia, a previsão de cada modelo ponderada por
    `pesos` - normalizados de novo entre só os modelos com previsão nesse
    dia, para um modelo que falhou não distorcer o resultado."""
    resultado = []
    for i in range(n_futuro):
        disponiveis = {m: pesos[m] for m in previsao_por_modelo if m in pesos}
        total = sum(disponiveis.values())
        if not total:
            continue
        resultado.append(sum(previsao_por_modelo[m][i] * (peso / total) for m, peso in disponiveis.items()))
    return resultado


def prever_saldo(db: Session, empresa: Optional[str] = None, dias_futuro: int = 7) -> dict:
    """Prevê o saldo contabilístico dos próximos dias com vários modelos,
    para comparação lado a lado. `empresa=None` prevê a "riqueza" da
    empresa como um todo (saldo total de todas as contas juntas - ver
    _historico_saldo) em vez de uma entidade só. Um modelo que falhe (ex.
    ARIMA sem convergir numa série muito curta/irregular) é ignorado - os
    restantes continuam a aparecer. Levanta ValueError se não houver
    histórico suficiente para nenhum modelo."""
    alvo = empresa or "saldo total (todas as entidades)"
    historico = _historico_saldo(db, empresa)
    if len(historico) < MIN_PONTOS:
        raise ValueError(
            f"Histórico insuficiente para prever saldo de '{alvo}' "
            f"({len(historico)} pontos, mínimo {MIN_PONTOS})."
        )

    dias = [h.dia for h in historico]
    valores = [h.saldo_contabilistico for h in historico]
    ultimo_dia = dias[-1]
    dias_futuros = [ultimo_dia + timedelta(days=i + 1) for i in range(dias_futuro)]

    valores_recentes = _janela_recente(valores, tamanho=_janela_treino(dias_futuro, len(valores)))

    previsao = {}
    valores_treino = _suavizar_outliers(valores_recentes)
    limite_sensato = _limite_sensato(valores_recentes)
    for nome, funcao in _FUNCOES_MODELO.items():
        try:
            previsao[nome] = _prever_com_guarda(funcao, valores_treino, dias_futuro, limite_sensato)
        except Exception:
            continue

    pesos, rmse_estimado = _pesos_ensemble(
        valores_recentes, list(previsao.keys()),
        dias_teste=_dias_teste_ensemble(dias_futuro, len(valores_recentes)),
    )
    banda = None
    if pesos:
        previsao["ensemble"] = _previsao_ensemble(previsao, _pesos_dos_melhores(pesos), dias_futuro)
        banda = _banda_incerteza(previsao["ensemble"], rmse_estimado)
        previsao = _manter_melhores_modelos(previsao, pesos)

    previsto_mapa_por_dia = _previsto_mapa_por_dia(db, dias_futuros, empresa=empresa)
    if previsto_mapa_por_dia:
        # o Mapa dá o líquido de cada dia (recebimentos - pagamentos já
        # sinalizados), não o saldo em si - acumula a partir do último
        # saldo real conhecido, dia a dia, para ficar na mesma escala das
        # restantes séries (saldo, não fluxo diário).
        acumulado = valores[-1]
        serie_previsto_mapa = []
        for dia_futuro in dias_futuros:
            acumulado += previsto_mapa_por_dia.get(dia_futuro, 0.0)
            serie_previsto_mapa.append(acumulado)
        previsao["previsto_mapa"] = serie_previsto_mapa

    return {
        "historico": [{"dia": d.isoformat(), "valor": v} for d, v in zip(dias, valores)],
        "previsao": {
            modelo: [
                {"dia": d.isoformat(), "valor": v}
                for d, v in zip(dias_futuros, valores_previstos)
            ]
            for modelo, valores_previstos in previsao.items()
        },
        "banda_incerteza": _serializar_banda(dias_futuros, banda),
    }


def _serializar_banda(dias_futuros: list, banda: Optional[tuple]) -> Optional[dict]:
    """Formata a banda de incerteza (ver _banda_incerteza) no mesmo
    formato dia/valor das restantes séries, para o dashboard desenhar a
    área sombreada sem lógica extra."""
    if banda is None:
        return None
    baixa, alta = banda
    return {
        "baixa": [{"dia": d.isoformat(), "valor": v} for d, v in zip(dias_futuros, baixa)],
        "alta": [{"dia": d.isoformat(), "valor": v} for d, v in zip(dias_futuros, alta)],
    }


def _avaliar_valores(valores: list, dias_teste: int) -> dict:
    """Núcleo comum da avaliação treino/teste (RMSE por modelo), usado
    tanto para o saldo como para o cash-flow - só muda de onde vem a
    lista de valores. `teste_real` fica sempre com os valores reais (não
    suavizados) - só o treino é winsorizado, para o RMSE continuar a medir
    o erro contra a realidade, não contra uma versão already-limada dela."""
    treino = valores[:-dias_teste]
    teste_real = valores[-dias_teste:]
    treino_suave = _suavizar_outliers(treino)
    limite_sensato = _limite_sensato(valores)

    rmse_por_modelo = {}
    falhas = {}
    for nome, funcao in _FUNCOES_MODELO.items():
        try:
            previsto = _prever_com_guarda(funcao, treino_suave, dias_teste, limite_sensato)
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


def avaliar_modelos(db: Session, empresa: Optional[str] = None, dias_teste: int = 5) -> dict:
    """Avaliação honesta (treino/teste): retira os últimos `dias_teste`
    dias como conjunto de teste, treina cada modelo só com o resto, e
    compara a previsão de cada um com o valor real que já conhecemos
    (RMSE - erro quadrático médio, mesma unidade EUR). Responde à pergunta
    "qual modelo acerta mais nesta conta", em vez de só mostrar previsões
    lado a lado sem validação. `empresa=None` avalia o saldo total (ver
    prever_saldo)."""
    alvo = empresa or "saldo total (todas as entidades)"
    historico = _historico_saldo(db, empresa)
    if len(historico) < MIN_PONTOS + dias_teste:
        raise ValueError(
            f"Histórico insuficiente para avaliar '{alvo}' ({len(historico)} "
            f"pontos, preciso de pelo menos {MIN_PONTOS + dias_teste} para treino+teste)."
        )

    valores = [h.saldo_contabilistico for h in historico]
    return _avaliar_valores(_janela_recente(valores), dias_teste)


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

    dias_todos = [s["dia"] for s in serie]
    liquidos_todos = [s["liquido"] for s in serie]
    liquidos, dias = _janela_recente(liquidos_todos, dias_todos)
    resultado = _avaliar_valores(liquidos, dias_teste)

    dias_treino, liquidos_treino = dias[:-dias_teste], liquidos[:-dias_teste]
    teste_real = liquidos[-dias_teste:]
    try:
        previsto_gb, _ = _prever_gradient_boosting(dias_treino, _suavizar_outliers(liquidos_treino), dias_teste)
        if not _previsao_sensata(previsto_gb, _limite_sensato(liquidos)):
            raise ValueError("previsão do gradient boosting divergiu para além do plausível")
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

    liquidos_recentes, dias_recentes = _janela_recente(
        liquidos, dias, tamanho=_janela_treino(dias_futuro, len(liquidos)),
    )

    previsao = {}
    liquidos_treino = _suavizar_outliers(liquidos_recentes)
    limite_sensato = _limite_sensato(liquidos_recentes)
    for nome, funcao in _FUNCOES_MODELO.items():
        try:
            previsao[nome] = _prever_com_guarda(funcao, liquidos_treino, dias_futuro, limite_sensato)
        except Exception:
            continue

    importancia_features = None
    try:
        previsao_gb, importancia_features = _prever_gradient_boosting(
            dias_recentes, liquidos_treino, dias_futuro,
        )
        if not _previsao_sensata(previsao_gb, limite_sensato):
            raise ValueError("previsão do gradient boosting divergiu para além do plausível")
        previsao["gradient_boosting"] = previsao_gb
    except Exception:
        importancia_features = None

    pesos, rmse_estimado = _pesos_ensemble(
        liquidos_recentes, list(previsao.keys()), dias=dias_recentes,
        dias_teste=_dias_teste_ensemble(dias_futuro, len(liquidos_recentes)),
    )
    banda = None
    if pesos:
        previsao["ensemble"] = _previsao_ensemble(previsao, _pesos_dos_melhores(pesos), dias_futuro)
        banda = _banda_incerteza(previsao["ensemble"], rmse_estimado)
        # importancia_features fica sempre que o gradient boosting tiver
        # sido calculado, mesmo que a linha em si seja filtrada abaixo por
        # não estar entre os melhores desta série - é informação sobre o
        # que o modelo aprendeu, não uma previsão a comparar com as outras.
        previsao = _manter_melhores_modelos(previsao, pesos)

    previsto_mapa_por_dia = _previsto_mapa_por_dia(db, dias_futuros, empresa=empresa)
    if previsto_mapa_por_dia:
        # aqui não acumula - o Mapa já dá diretamente o líquido esperado
        # de cada dia, na mesma unidade da série de cash-flow.
        previsao["previsto_mapa"] = [previsto_mapa_por_dia.get(d, 0.0) for d in dias_futuros]

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
        "banda_incerteza": _serializar_banda(dias_futuros, banda),
    }


# "Zona de risco" do saldo: quantos dias/meses de despesa média o saldo
# atual (ou previsto) ainda cobre. ZONA_CRITICA = menos de ~1 semana de
# despesa média; ZONA_ALERTA = menos de 1 mês - limiares deliberadamente
# simples (fração da despesa mensal média), não um modelo à parte, para
# serem fáceis de justificar a quem lê o aviso.
ZONA_CRITICA_FRACAO_DESPESA = 0.25
ZONA_ALERTA_FRACAO_DESPESA = 1.0
_ORDEM_ZONA = {"ok": 0, "alerta": 1, "critico": 2}


JANELA_TAXA_DIARIA = 30


def _despesa_media_mensal(db: Session, empresa: str) -> float:
    """Média da despesa mensal (soma de pagamentos por mês, extrato
    bancário) desta empresa - referência para as zonas de risco do saldo
    e para o pior caso de autonomia (ver estimar_autonomia): "se as
    receitas parassem, quanto tempo a despesa sozinha aguentaria". Meses
    parciais (o primeiro/último mês do histórico, se não cobrirem o mês
    inteiro) pesam na média como qualquer outro - uma aproximação
    aceitável para um aviso, não uma contabilidade exata."""
    serie = _movimentos_por_dia(db, empresa)
    if not serie:
        return 0.0
    por_mes: dict = {}
    for ponto in serie:
        chave_mes = (ponto["dia"].year, ponto["dia"].month)
        por_mes[chave_mes] = por_mes.get(chave_mes, 0.0) + ponto["pagamentos"]
    return sum(por_mes.values()) / len(por_mes) if por_mes else 0.0


def _taxa_diaria_liquida(db: Session, empresa: str, janela_dias: int = JANELA_TAXA_DIARIA) -> float:
    """Ritmo médio diário real de caixa (recebimentos - pagamentos) dos
    últimos `janela_dias` dias com movimento - a "queima" (burn rate) de
    facto da empresa: negativo se está a gastar mais do que recebe,
    positivo se está a acumular. Ao contrário de _despesa_media_mensal
    (só pagamentos), isto conta as receitas - uma empresa com entradas
    regulares não deve ser tratada como se estivesse só a esvaziar a
    conta."""
    serie = _movimentos_por_dia(db, empresa)
    if not serie:
        return 0.0
    janela = serie[-janela_dias:] if len(serie) > janela_dias else serie
    return sum(p["liquido"] for p in janela) / len(janela)


def estimar_autonomia(saldo_atual: float, taxa_diaria_liquida: float) -> Optional[float]:
    """Dias até o saldo chegar a zero ao ritmo de caixa atual (projeção
    linear simples a partir de taxa_diaria_liquida - ver
    _taxa_diaria_liquida). Devolve None quando a tendência não é de queima
    (taxa_diaria_liquida >= 0): ao ritmo atual o saldo não se esgota, não
    há "quanto tempo aguenta" para calcular. Um saldo já negativo ou nulo
    (a empresa já está em descoberto) devolve sempre 0 dias, mesmo que a
    tendência atual seja positiva - "aguentar" não pode ser um número
    negativo."""
    if saldo_atual <= 0:
        return 0.0
    if taxa_diaria_liquida >= 0:
        return None
    return saldo_atual / abs(taxa_diaria_liquida)


def _classificar_zona(saldo: float, despesa_media_mensal: float) -> str:
    """Um saldo negativo é sempre "critico", mesmo sem despesa média para
    comparar (empresa só com recebimentos no histórico, despesa_media=0) -
    a previsão pode legitimamente apontar para saldo negativo (ver
    avaliar_zona_risco), e isso tem de ficar sinalizado como o pior caso,
    não cair no "ok" por falta de referência."""
    if saldo < 0:
        return "critico"
    if despesa_media_mensal <= 0:
        return "ok"
    if saldo <= despesa_media_mensal * ZONA_CRITICA_FRACAO_DESPESA:
        return "critico"
    if saldo <= despesa_media_mensal * ZONA_ALERTA_FRACAO_DESPESA:
        return "alerta"
    return "ok"


def avaliar_zona_risco(db: Session, empresa: str, dias_futuro: int = 7) -> dict:
    """Classifica a saúde do saldo de `empresa` em três zonas, com base na
    despesa mensal média (extrato bancário): "critico" (saldo cobre menos
    de 1 semana de despesa média, ou já é negativo), "alerta" (cobre menos
    de 1 mês) e "ok". Usa também a previsão de saldo (ensemble, quando
    disponível - que pode legitimamente prever valores negativos) para
    avisar com antecedência se o saldo vai entrar numa zona pior nos
    próximos `dias_futuro` dias, mesmo partindo de uma zona atual "ok" -
    a falha da previsão (histórico curto, modelo que não converge) nunca
    impede a classificação da zona atual, só fica sem aviso antecipado.

    "Quanto tempo aguenta" tem duas leituras, ambas devolvidas:
    dias_autonomia_tendencia (ver estimar_autonomia) usa o ritmo de caixa
    REAL dos últimos 30 dias (recebimentos incluídos) - a resposta direta
    à pergunta; dias_autonomia_despesa é o pior caso "se as receitas
    parassem amanhã", só com a despesa média - útil mesmo quando a
    tendência atual não é de queima."""
    historico = _historico_saldo(db, empresa)
    if not historico:
        raise ValueError(f"Sem histórico de saldo para '{empresa}'.")

    despesa_media = _despesa_media_mensal(db, empresa)
    taxa_diaria = _taxa_diaria_liquida(db, empresa)
    saldo_atual = historico[-1].saldo_contabilistico

    dias_autonomia_tendencia = estimar_autonomia(saldo_atual, taxa_diaria)
    dias_autonomia_despesa = estimar_autonomia(saldo_atual, -despesa_media / 30) if despesa_media > 0 else None
    zona_atual = _classificar_zona(saldo_atual, despesa_media)

    zona_prevista = zona_atual
    dia_risco = None
    saldo_previsto_fim = None
    try:
        previsao = prever_saldo(db, empresa, dias_futuro)
        serie_prevista = previsao["previsao"].get("ensemble") or next(iter(previsao["previsao"].values()), [])
        if serie_prevista:
            saldo_previsto_fim = serie_prevista[-1]["valor"]
        for ponto in serie_prevista:
            zona_ponto = _classificar_zona(ponto["valor"], despesa_media)
            if _ORDEM_ZONA[zona_ponto] > _ORDEM_ZONA[zona_prevista]:
                zona_prevista = zona_ponto
                dia_risco = ponto["dia"]
    except Exception:
        pass

    return {
        "saldo_atual": saldo_atual,
        "despesa_media_mensal": despesa_media,
        "taxa_diaria_liquida": taxa_diaria,
        "dias_autonomia_tendencia": dias_autonomia_tendencia,
        "dias_autonomia_despesa": dias_autonomia_despesa,
        "zona_atual": zona_atual,
        "zona_prevista": zona_prevista,
        "dia_risco": dia_risco,
        # Último ponto do ensemble (ou do primeiro modelo disponível, se o
        # histórico for curto de mais para pesar o ensemble - ver
        # _pesos_ensemble) ao fim de `dias_futuro` dias. None só quando a
        # previsão falhou mesmo (histórico insuficiente para todos os
        # modelos) - não é um "sem dados" por omissão, é o forecast a
        # sério aplicado a esta empresa.
        "saldo_previsto_fim": saldo_previsto_fim,
    }


_ORDEM_ZONA_PIOR_PRIMEIRO = {"critico": 0, "alerta": 1, "ok": 2}


def listar_ranking_risco(db: Session, dias_futuro: int = 30) -> list:
    """Ranking de todas as empresas por saúde do saldo (crítico -> alerta
    -> ok, e dentro da mesma zona, saldo mais baixo primeiro) - para a
    tabela "Ranking de risco" da aba Análise de Contas: visão geral de
    quem precisa de atenção primeiro, sem ter de abrir empresa a empresa.

    Reaproveita avaliar_zona_risco por empresa (mesma lógica usada na
    análise individual) em vez de só classificar a zona ATUAL: sem isto,
    "Autonomia" e "zona prevista" ficavam sempre vazios/"-" nesta tabela
    mesmo quando o forecast está disponível - um ranking que não aplica o
    forecast que já existe no resto do dashboard. Uma empresa cujo
    histórico chumbe a previsão (poucos pontos, modelo que não converge)
    ainda aparece no ranking - avaliar_zona_risco nunca falha só por causa
    da previsão, ela fica apenas sem zona_prevista/saldo_previsto_fim
    (ver avaliar_zona_risco). Ignora silenciosamente empresas sem
    histórico de saldo (nada a classificar)."""
    resultado = []
    for empresa in listar_empresas(db):
        if not _historico_saldo(db, empresa):
            continue
        try:
            risco = avaliar_zona_risco(db, empresa, dias_futuro)
        except ValueError:
            continue
        resultado.append({"empresa": empresa, **risco})

    resultado.sort(key=lambda r: (_ORDEM_ZONA_PIOR_PRIMEIRO[r["zona_atual"]], r["saldo_atual"]))
    return resultado
