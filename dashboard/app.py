from datetime import date
from html import escape
from pathlib import Path
import re
import sys

dashboard_dir = str(Path(__file__).resolve().parent)
if dashboard_dir not in sys.path:
    sys.path.insert(0, dashboard_dir)

import altair as alt
import pandas as pd
import streamlit as st

import api_client as api

# Paleta de estado validada (skill de dataviz - references/palette.md):
# bom/fechado = verde, precisa de decisão humana = amarelo. Nunca escolhida
# "a olho".
COR_CASADOS = "#0ca30c"
COR_AMBIGUOS = "#fab219"
COR_ERRO = "#d32f2f"

# Mesma tolerância de arredondamento usada em app/services/reconciliador.py
# (TOLERANCIA_VALOR) para decidir se um movimento bate com uma linha do
# mapa - repetida aqui porque a secção de Auditoria compara os mesmos
# valores (soma_extrato vs. soma_mapa) do lado do dashboard.
TOLERANCIA_AUDITORIA = 0.01

# Duas séries na mesma unidade (EUR) - hues categóricos 1 e 3, não usados
# como cor de estado noutro sítio do dashboard.
COR_SALDO_CONTABILISTICO = "#2a78d6"
COR_SALDO_DISPONIVEL = "#1baf7a"

# Par divergente (fluxo diário à volta de zero) - ver references/palette.md.
COR_FLUXO_POSITIVO = "#2a78d6"
COR_FLUXO_NEGATIVO = "#e34948"

# Ranking de uma única medida (saldo) por conta - um hue só, não categórico.
COR_RANKING_SALDO = "#2a78d6"

# Recebimentos/pagamentos do mês - pedido explicitamente verde/vermelho
# (mesmos tons já usados para positivo/negativo noutro sítio do dashboard).
COR_RECEBIMENTOS_MES = "#1baf7a"
COR_PAGAMENTOS_MES = "#e34948"

# Previsão de saldo/cash-flow: histórico + modelos, na ordem de slots
# categóricos validada (references/palette.md) - garante pares adjacentes
# seguros para daltonismo. "Gradient Boosting" ocupa o slot 7 (violeta) -
# só aparece na previsão de cash-flow, nunca na de saldo, por isso não
# colide visualmente com os outros seis.
# Cinzento neutro para séries de referência/plano (não competem por hue
# categórico - ver COR_IMPUTACAO_OUTRAS mais abaixo, mesmo racional: uma
# série "à parte", não mais um concorrente na legenda).
COR_REFERENCIA_PLANO = "#898781"

CORES_PREVISAO = {
    "Histórico": "#2a78d6",
    "Regressão linear": "#eb6834",
    "Média móvel": "#1baf7a",
    "Suavização exponencial": "#eda100",
    "ARIMA": "#e87ba4",
    "Markov-switching": "#008300",
    "Gradient Boosting": "#4a3aa7",
    # 8º e último slot categórico validado (ver skill de dataviz) - o
    # ensemble é uma combinação dos modelos acima, não mais um modelo
    # igual aos outros, por isso fica no slot que sobra, não numa cor
    # inventada.
    "Ensemble (ponderado)": "#e34948",
    # Já não há slot categórico livre (8/8 usados) - e nem devia ter um: o
    # que já está planeado no Mapa não é um modelo estatístico a comparar,
    # é referência, por isso fica em cinzento neutro em vez de gerar uma
    # 9ª cor.
    "Previsto (Mapa)": COR_REFERENCIA_PLANO,
}
COR_IMPORTANCIA_FEATURES = "#4a3aa7"  # mesma cor do Gradient Boosting - a barra "pertence" a esse modelo

NOMES_MODELO = {
    "regressao_linear": "Regressão linear",
    "media_movel": "Média móvel",
    "suavizacao_exponencial": "Suavização exponencial",
    "arima": "ARIMA",
    "markov_switching": "Markov-switching",
    "gradient_boosting": "Gradient Boosting",
    "ensemble": "Ensemble (ponderado)",
    "previsto_mapa": "Previsto (Mapa)",
}

# Gráficos circulares de "Análise de Extratos": até 6 imputações reais (slots
# categóricos 1-6, mesma ordem validada acima) + "Outras" em cinzento neutro
# - nunca gerar uma 7ª/8ª cor nova, dobra-se a cauda em "Outras" (ver skill de
# dataviz, ladder de séries: acima de ~6-7 fatias uma pie deixa de se ler).
CORES_IMPUTACAO = ["#2a78d6", "#eb6834", "#1baf7a", "#eda100", "#e87ba4", "#008300"]
COR_IMPUTACAO_OUTRAS = "#898781"
MAX_FATIAS_IMPUTACAO = 6

# Formatação partilhada para colunas EUR em tabelas cruas (st.dataframe)
# - separador de milhares, sem depender de cada chamada repetir a config.
COLUNA_VALOR_EUR = {"valor": st.column_config.NumberColumn("valor", format="euro")}
COLUNAS_SALDO_EUR = {
    "saldo_contabilistico": st.column_config.NumberColumn("saldo_contabilistico", format="euro"),
    "saldo_disponivel": st.column_config.NumberColumn("saldo_disponivel", format="euro"),
}


def seta_saldo(variacao):
    """Seta de tendência do mapa de saldos - reaproveitada na Visão Geral e na aba Saldos."""
    if variacao is None or pd.isna(variacao):
        return "➖"
    return "🔼" if variacao > 0 else "🔽"


def fmt_pct_saldo(pct):
    if pct is None or pd.isna(pct):
        return "—"
    return f"{pct:+.1f}%"

NOMES_FEATURES = {
    "dia_semana": "Dia da semana",
    "dia_mes": "Dia do mês",
    "fim_de_semana": "Fim de semana",
    "lag_1": "Valor de ontem",
    "lag_2": "Valor de há 2 dias",
    "lag_3": "Valor de há 3 dias",
    "media_movel_5": "Média móvel (5 dias)",
}


def grafico_importancia_features(importancia_features):
    """Barra horizontal de importância de cada feature do Gradient
    Boosting - uma única medida, um único hue (mesma cor do modelo),
    ordenada por magnitude. Prova visual de que o modelo aprendeu de
    variáveis explícitas, não só extrapolou uma curva."""
    df_importancia = pd.DataFrame([
        {"feature": NOMES_FEATURES.get(f, f), "importancia": v}
        for f, v in importancia_features.items()
    ]).sort_values("importancia", ascending=False)
    return alt.Chart(df_importancia).mark_bar(
        cornerRadiusTopRight=4, cornerRadiusBottomRight=4,
    ).encode(
        y=alt.Y("feature:N", title=None, sort="-x"),
        x=alt.X("importancia:Q", title="Importância (Gradient Boosting)"),
        color=alt.value(COR_IMPORTANCIA_FEATURES),
        tooltip=["feature:N", alt.Tooltip("importancia:Q", format=".2f")],
    ).properties(height=220)


# Séries sempre em destaque no gráfico de previsão (traço mais grosso,
# visíveis mesmo com muitos modelos ligados): o histórico real e o
# ensemble (a "resposta" recomendada) - os modelos individuais servem
# para comparar/justificar o ensemble, não para ler todos ao mesmo tempo.
SERIES_DESTAQUE_PREVISAO = {"Histórico", "Ensemble (ponderado)", "Previsto (Mapa)"}


def grafico_previsao(historico_pontos, previsao_por_modelo, y_title, banda_incerteza=None):
    """Histórico (linha sólida) + previsão de cada modelo (tracejada),
    reaproveitado pela previsão de saldo e pela de cash-flow - mesma
    paleta e mesma convenção visual para não obrigar a reaprender o
    gráfico entre secções.

    Com o ensemble, chega a haver 7 séries na mesma legenda (acima do
    "token ceiling" para uma leitura confortável - ver skill de dataviz) -
    por isso Histórico/Ensemble ficam sempre com traço mais grosso, e
    clicar num nome da legenda isola essa série (as outras esbatem),
    em vez de obrigar a distinguir 7 cores sobrepostas de vez.

    `banda_incerteza` (opcional, {"baixa": [...], "alta": [...]} - ver
    previsao.py::_banda_incerteza) desenha-se como área sombreada por
    baixo da linha do ensemble: a incerteza cresce com o horizonte, por
    isso um valor único "daqui a 3 meses" sem banda passa uma falsa
    sensação de precisão. Sem banda (histórico curto de mais para avaliar
    os modelos), o gráfico fica só com as linhas, como antes."""
    linhas = [{"dia": p["dia"], "valor": p["valor"], "serie": "Histórico"} for p in historico_pontos]
    # cada linha de previsão começa no último ponto real do histórico -
    # sem isto, a Vega-Lite desenha cada série de previsão isolada, "a
    # flutuar" sem ligação visual a onde a história parou, e a linha
    # deixa de corresponder à leitura óbvia "a previsão continua daqui".
    ultimo_ponto_historico = historico_pontos[-1] if historico_pontos else None
    for modelo, pontos in previsao_por_modelo.items():
        nome = NOMES_MODELO.get(modelo, modelo)
        pontos_ligados = ([ultimo_ponto_historico] if ultimo_ponto_historico else []) + list(pontos)
        linhas.extend({"dia": p["dia"], "valor": p["valor"], "serie": nome} for p in pontos_ligados)

    df_previsao = pd.DataFrame(linhas)
    df_previsao["tipo_linha"] = df_previsao["serie"].apply(
        lambda s: "Histórico" if s == "Histórico" else "Previsão"
    )
    df_previsao["destaque"] = df_previsao["serie"].isin(SERIES_DESTAQUE_PREVISAO)

    selecao_legenda = alt.selection_point(fields=["serie"], bind="legend")

    grafico_linhas = alt.Chart(df_previsao).mark_line().encode(
        x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
        y=alt.Y("valor:Q", title=y_title, axis=alt.Axis(format=",.0f")),
        color=alt.Color(
            "serie:N",
            scale=alt.Scale(domain=list(CORES_PREVISAO.keys()), range=list(CORES_PREVISAO.values())),
            legend=alt.Legend(title="Clica para isolar uma série"),
        ),
        strokeDash=alt.StrokeDash(
            "tipo_linha:N",
            scale=alt.Scale(domain=["Histórico", "Previsão"], range=[[1, 0], [6, 3]]),
            legend=None,
        ),
        strokeWidth=alt.condition(alt.datum.destaque, alt.value(3), alt.value(1.5)),
        opacity=alt.condition(selecao_legenda, alt.value(1), alt.value(0.15)),
        tooltip=["dia:T", "serie:N", alt.Tooltip("valor:Q", format=",.2f")],
    ).add_params(selecao_legenda)

    if not banda_incerteza:
        return grafico_linhas.properties(height=320)

    pontos_baixa = {p["dia"]: p["valor"] for p in banda_incerteza["baixa"]}
    pontos_alta = {p["dia"]: p["valor"] for p in banda_incerteza["alta"]}
    if ultimo_ponto_historico:
        pontos_baixa[ultimo_ponto_historico["dia"]] = ultimo_ponto_historico["valor"]
        pontos_alta[ultimo_ponto_historico["dia"]] = ultimo_ponto_historico["valor"]
    df_banda = pd.DataFrame([
        {"dia": dia, "baixa": pontos_baixa[dia], "alta": pontos_alta[dia]}
        for dia in sorted(pontos_baixa)
    ])
    grafico_banda = alt.Chart(df_banda).mark_area(opacity=0.12, color=CORES_PREVISAO["Ensemble (ponderado)"]).encode(
        x="dia:T",
        y=alt.Y("baixa:Q"),
        y2="alta:Q",
        tooltip=[
            "dia:T",
            alt.Tooltip("baixa:Q", title="Cenário pessimista", format=",.2f"),
            alt.Tooltip("alta:Q", title="Cenário otimista", format=",.2f"),
        ],
    )
    return (grafico_banda + grafico_linhas).properties(height=320)


# Séries sempre relevantes mesmo em "modo simplificado" (ver
# filtrar_series_previsao): o ensemble é a resposta recomendada, e
# Previsto (Mapa) não é um modelo a comparar, é o plano já lançado.
_SERIES_SEMPRE_VISIVEIS = {"ensemble", "previsto_mapa"}


def filtrar_series_previsao(previsao_por_modelo: dict, mostrar_todos_modelos: bool) -> dict:
    """Em horizontes longos, sete linhas de modelo à volta do ensemble
    tornam-se ruído repetitivo em vez de informação (a pergunta de gestão
    é "qual o cenário provável", não "compara sete curvas"). Por omissão
    o gráfico mostra só o ensemble (+ Previsto (Mapa), quando existe);
    "mostrar_todos_modelos" liga de volta os modelos individuais para
    quem quer justificar/auditar o ensemble."""
    if mostrar_todos_modelos or "ensemble" not in previsao_por_modelo:
        return previsao_por_modelo
    return {m: v for m, v in previsao_por_modelo.items() if m in _SERIES_SEMPRE_VISIVEIS}


def resumo_mensal_previsao(previsao_por_modelo: dict, chave_serie: str = "ensemble") -> pd.DataFrame:
    """Agrega a previsão diária de `chave_serie` (ensemble por omissão)
    em totais por mês - para um horizonte de meses, ler 90+ pontos diários
    não responde à pergunta de gestão ("como fica a empresa em outubro,
    novembro..."); um resumo por mês responde-a diretamente. Devolve
    soma (total do mês - leitura certa para cash-flow), média diária e o
    último valor do mês (leitura certa para saldo, uma "escada" em vez de
    um fluxo) - o dashboard escolhe a coluna certa consoante o contexto.
    Cai para o primeiro modelo disponível se não houver ensemble
    (histórico curto de mais para avaliação, ver _pesos_ensemble)."""
    serie = previsao_por_modelo.get(chave_serie) or next(iter(previsao_por_modelo.values()), [])
    if not serie:
        return pd.DataFrame()
    df = pd.DataFrame(serie)
    df["mes"] = pd.to_datetime(df["dia"]).dt.to_period("M").dt.to_timestamp()
    resumo = df.groupby("mes", as_index=False)["valor"].agg(["sum", "mean", "last"])
    return resumo.rename(columns={"sum": "total_mes", "mean": "media_diaria", "last": "fim_do_mes"})


def grafico_pizza_imputacoes(linhas: list, titulo: str):
    """Gráfico circular (donut) da % de cada imputação no total de `linhas`
    (já um dos dois lados - recebimentos OU pagamentos - devolvidos por
    /analise/imputacoes). Mantém no máximo MAX_FATIAS_IMPUTACAO categorias
    reais (as de maior valor) e dobra o resto em "Outras", em vez de gerar
    mais cores categóricas (ver CORES_IMPUTACAO).

    Devolve (gráfico, cor_por_imputação) - o segundo elemento mapeia CADA
    imputação original (incluindo as dobradas em "Outras") à cor final da
    sua fatia, para a tabela de extratos por baixo do gráfico pintar cada
    linha na mesma cor da fatia a que pertence."""
    if not linhas:
        return None, {}

    df = pd.DataFrame(linhas).sort_values("valor", ascending=False)
    cor_por_imputacao = {}
    if len(df) > MAX_FATIAS_IMPUTACAO:
        df_principais = df.iloc[:MAX_FATIAS_IMPUTACAO].copy()
        df_resto = df.iloc[MAX_FATIAS_IMPUTACAO:]
        for categoria in df_resto["imputacao"]:
            cor_por_imputacao[categoria] = COR_IMPUTACAO_OUTRAS
        valor_outras = df_resto["valor"].sum()
        df = pd.concat([
            df_principais,
            pd.DataFrame([{"imputacao": "Outras", "valor": valor_outras}]),
        ], ignore_index=True)
    else:
        df_principais = df

    for categoria, cor in zip(df_principais["imputacao"], CORES_IMPUTACAO):
        cor_por_imputacao[categoria] = cor

    total = df["valor"].sum()
    df["percentagem"] = df["valor"] / total if total else 0.0

    categorias = list(df_principais["imputacao"])
    tem_outras = "Outras" in df["imputacao"].values
    if tem_outras:
        cor_por_imputacao["Outras"] = COR_IMPUTACAO_OUTRAS
    dominio = categorias + (["Outras"] if tem_outras else [])
    intervalo = CORES_IMPUTACAO[: len(categorias)] + ([COR_IMPUTACAO_OUTRAS] if tem_outras else [])

    grafico = alt.Chart(df).mark_arc(innerRadius=60).encode(
        theta=alt.Theta("valor:Q", stack=True),
        color=alt.Color(
            "imputacao:N",
            scale=alt.Scale(domain=dominio, range=intervalo),
            legend=alt.Legend(title=None),
        ),
        order=alt.Order("valor:Q", sort="descending"),
        tooltip=[
            alt.Tooltip("imputacao:N", title="Imputação"),
            alt.Tooltip("valor:Q", title="Valor", format=",.2f"),
            alt.Tooltip("percentagem:Q", title="%", format=".1%"),
        ],
    ).properties(height=320, title=titulo)
    return grafico, cor_por_imputacao


def tabela_extratos_colorida(linhas: list, cor_por_imputacao: dict, legenda_vazio: str):
    """Tabela de linhas do Mapa por baixo do gráfico circular, cada linha
    pintada com a mesma cor da fatia da sua imputação (cor_por_imputacao,
    devolvido por grafico_pizza_imputacoes) - imputações sem cor atribuída
    (nenhuma linha caiu nessa categoria no gráfico) usam a cor de "Outras"."""
    if not linhas:
        st.info(legenda_vazio)
        return

    df = pd.DataFrame(linhas)[["dia", "empresa", "imputacao", "valor"]].sort_values("dia", ascending=False)

    def _pintar_linha(linha):
        cor = cor_por_imputacao.get(linha["imputacao"], COR_IMPUTACAO_OUTRAS)
        return [f"background-color: {cor}33"] * len(linha)

    estilo = df.style.apply(_pintar_linha, axis=1).format({"valor": "{:,.2f} €"})
    st.dataframe(
        estilo,
        use_container_width=True,
        hide_index=True,
        column_config={
            "dia": st.column_config.TextColumn("Dia"),
            "empresa": st.column_config.TextColumn("Empresa"),
            "imputacao": st.column_config.TextColumn("Imputação"),
            "valor": st.column_config.TextColumn("Valor"),
        },
    )


def grafico_ranking_imputacoes(linhas: list, titulo: str):
    """Gráfico de barras horizontal com TODAS as imputações do período,
    ordenadas por valor (maior primeiro) - complementa o gráfico circular
    (que só mostra as 6 maiores + "Outras"): aqui dá para ver o ranking
    completo, sem limite de fatias. Magnitude/ranking = um hue só
    (sequencial), não categórico - ver skill de dataviz."""
    if not linhas:
        return None
    df = pd.DataFrame(linhas).sort_values("valor", ascending=False)
    altura = max(120, 24 * len(df))
    return alt.Chart(df).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
        y=alt.Y("imputacao:N", title=None, sort="-x"),
        x=alt.X("valor:Q", title="Valor (EUR)", axis=alt.Axis(format=",.0f")),
        color=alt.value(COR_PAGAMENTOS_MES),
        tooltip=[alt.Tooltip("imputacao:N", title="Imputação"), alt.Tooltip("valor:Q", title="Valor", format=",.2f")],
    ).properties(height=altura, title=titulo)


def secao_avaliacao_modelos(avaliar_fn, key_prefix):
    """Expander "qual modelo acerta mais" - reaproveitado pela previsão
    de saldo e pela de cash-flow (agregado e por entidade). `avaliar_fn`
    recebe `dias_teste` e devolve o dict de avaliação (chamada HTTP já
    resolvida pelo chamador para a rota certa: saldo ou cash-flow)."""
    with st.expander("Qual modelo acerta mais? (avaliação treino/teste)"):
        dias_teste = st.slider(
            "Dias retidos para teste", min_value=2, max_value=15, value=5, key=f"{key_prefix}_dias_teste",
        )
        try:
            avaliacao = avaliar_fn(dias_teste)
        except Exception as e:
            avaliacao = None
            st.info(f"Sem avaliação disponível: {e}")

        if avaliacao:
            if avaliacao["rmse_por_modelo"]:
                df_rmse = pd.DataFrame([
                    {"modelo": NOMES_MODELO.get(m, m), "rmse_eur": erro}
                    for m, erro in avaliacao["rmse_por_modelo"].items()
                ]).sort_values("rmse_eur")
                st.dataframe(
                    df_rmse, use_container_width=True, hide_index=True,
                    column_config={"rmse_eur": st.column_config.NumberColumn("rmse_eur", format="accounting")},
                )
                st.success(
                    f"Melhor modelo: **{NOMES_MODELO.get(avaliacao['melhor_modelo'], avaliacao['melhor_modelo'])}** "
                    f"(erro médio {avaliacao['rmse_por_modelo'][avaliacao['melhor_modelo']]:,.2f} €, "
                    f"comparado com os últimos {dias_teste} dias reais retidos para teste)."
                )
            if avaliacao["falhas"]:
                st.caption(f"Modelos que não convergiram: {list(avaliacao['falhas'].keys())}")


st.set_page_config(page_title="Plataforma de Análise de Tesouraria", page_icon="💶", layout="wide")

# Cabeçalho discreto de BI executivo: barra escura, título em branco, um
# único traço vermelho fino como assinatura de marca - o vermelho fica
# reservado a KPIs/alertas no resto da interface (ver _cartao_kpi,
# COR_ERRO, COR_PAGAMENTOS_MES), não usado como cor de UI genérica.
st.markdown(
    f"""
    <div style="border-top:2px solid #2c3440;background:#1c1f26;
                margin:-1rem -1rem 1.5rem -1rem;padding:20px 32px;
                border-bottom:4px solid #c8102e;">
      <div style="display:flex;align-items:baseline;justify-content:space-between;">
        <div style="color:#8f97a3;font-size:0.78rem;font-weight:700;letter-spacing:.12em;">
          VIDÓR
        </div>
        <div style="color:#8f97a3;font-size:0.78rem;font-weight:500;letter-spacing:.04em;">
          {date.today().strftime('%d/%m/%Y')}
        </div>
      </div>
      <div style="display:flex;align-items:baseline;justify-content:space-between;margin-top:6px;">
        <div style="color:#ffffff;font-size:1.4rem;font-weight:700;letter-spacing:.01em;">
          Plataforma de Análise de Tesouraria
        </div>
        <div style="color:#8f97a3;font-size:0.82rem;font-weight:500;letter-spacing:.03em;">
          Dashboard financeiro consolidado
        </div>
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)

# Acabamento "BI executivo": fundo neutro (config.toml) com cada
# separador a funcionar como um cartão branco bem definido; navegação
# sóbria (cinzento, sublinhado vermelho fino só na aba ativa); títulos de
# secção discretos, sem cor de marca - o vermelho só aparece em
# KPIs/alertas (cartões de estado, zonas de risco), nunca como cor de UI
# genérica.
st.markdown(
    """
    <style>
    .stTabs [data-baseweb="tab-list"] {
        gap: 8px; background: #ffffff; border-radius: 10px 10px 0 0;
        border-bottom: 1px solid #e4e6ea; padding: 4px 8px 0 8px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }
    .stTabs [data-baseweb="tab"] { height: auto; padding: 12px 18px; }
    .stTabs [data-baseweb="tab"] p {
        font-size: 0.85rem; font-weight: 700; color: #5c6370;
        letter-spacing: .06em; text-transform: uppercase;
    }
    .stTabs [aria-selected="true"] { border-bottom: 3px solid #c8102e; }
    .stTabs [aria-selected="true"] p { color: #1c1f26 !important; }
    .stTabs [data-baseweb="tab-panel"] {
        background: #ffffff; border: 1px solid #e4e6ea; border-top: none;
        border-radius: 0 0 10px 10px; padding: 28px 26px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
    }

    h3 {
        color: #1c1f26; font-weight: 700; letter-spacing: .01em;
        margin: 1.8rem 0 1rem 0 !important; padding-bottom: 8px;
        border-bottom: 1px solid #e4e6ea;
    }

    div[data-testid="stMetric"] {
        background: #f8f9fb; border: 1px solid #e4e6ea; border-radius: 8px;
        padding: 12px 16px;
    }
    div[data-testid="stDataFrame"], div[data-testid="stExpander"] {
        border-radius: 8px;
    }

    button[kind="primary"] { background: #1c1f26; border-radius: 6px; font-weight: 600; }
    button[kind="secondary"] { border-radius: 6px; font-weight: 600; }
    </style>
    """,
    unsafe_allow_html=True,
)

if "startup_sync_done" not in st.session_state:
    st.session_state.startup_sync_done = False

if not st.session_state.startup_sync_done:
    with st.spinner("A atualizar os dados do OneDrive e do Mapa de Pagamentos e Recebimentos..."):
        try:
            resultado = api.atualizar_dados()
            novos = (
                len(resultado["dias_com_movimentos_novos"])
                + len(resultado["dias_com_saldos_novos"])
                + len(resultado["dias_com_mapa_novo"])
            )
            if novos > 0:
                st.success(
                    f"Atualizado na abertura: {resultado['dias_com_movimentos_novos']} movimentos, "
                    f"{resultado['dias_com_saldos_novos']} saldos, "
                    f"{resultado['dias_com_mapa_novo']} mapa."
                )
            else:
                st.info("Tudo já estava atualizado quando abriu a dashboard.")
            if resultado.get("erros"):
                st.warning(f"Erros de sincronização: {resultado['erros']}")
        except Exception as e:
            st.warning(f"Não foi possível atualizar ao abrir a dashboard: {e}")
    st.session_state.startup_sync_done = True

col_titulo, col_botao = st.columns([4, 1])
with col_botao:
    if st.button("🔄 Atualizar dados do OneDrive", use_container_width=True):
        with st.spinner("A importar dias novos do OneDrive (só leitura)..."):
            try:
                resultado = api.atualizar_dados()
                novos = (
                    len(resultado["dias_com_movimentos_novos"])
                    + len(resultado["dias_com_saldos_novos"])
                    + len(resultado["dias_com_mapa_novo"])
                )
                if novos > 0:
                    st.success(
                        f"Atualizado: {resultado['dias_com_movimentos_novos']} movimentos, "
                        f"{resultado['dias_com_saldos_novos']} saldos, "
                        f"{resultado['dias_com_mapa_novo']} mapa."
                    )
                else:
                    st.info("Nada de novo - já estava tudo atualizado.")
                if resultado["erros"]:
                    st.warning(f"Erros: {resultado['erros']}")
                st.rerun()
            except Exception as e:
                st.error(f"Erro: {e}")

(
    aba_visao_geral, aba_monitorizacao, aba_faturas, aba_saldos,
    aba_analise_extratos, aba_contas, aba_assistente,
) = st.tabs(
    ["Visão Geral", "Monitorização", "Faturas", "Saldos", "Análise de Extratos", "Análise de Contas", "Assistente"]
)

with aba_visao_geral:
    st.subheader("Visão geral do dia")
    dia_vg = st.date_input("Dia", value=date.today(), key="dia_visao_geral")
    dia_vg_str = dia_vg.isoformat()

    if st.session_state.get("dia_vg_sincronizado") != dia_vg_str:
        with st.spinner(f"A importar dados de {dia_vg_str} do OneDrive (se ainda não existirem)..."):
            try:
                api.atualizar_dados_do_dia(dia_vg_str)
            except Exception as e:
                st.warning(f"Não foi possível sincronizar {dia_vg_str}: {e}")
        st.session_state["dia_vg_sincronizado"] = dia_vg_str

    try:
        movimentos_vg = api.listar_movimentos(dia_vg_str)
    except Exception as e:
        st.error(f"Erro a ligar à API: {e}")
        movimentos_vg = []

    st.markdown("**Extratos do dia**")
    recebimentos_vg = [m for m in movimentos_vg if m["valor"] > 0]
    pagamentos_vg = [m for m in movimentos_vg if m["valor"] < 0]
    total_recebimentos = sum(m["valor"] for m in recebimentos_vg)
    total_pagamentos = sum(-m["valor"] for m in pagamentos_vg)

    try:
        resumo_todos_os_dias = api.resumo_diario()
    except Exception as e:
        st.error(f"Erro a consultar o resumo diário: {e}")
        resumo_todos_os_dias = []

    ano_mes_vg = dia_vg_str[:7]
    resumo_mensal = [r for r in resumo_todos_os_dias if r["dia"].startswith(ano_mes_vg)]

    balanco_ate_ao_dia = sum(
        r["recebimentos"] - r["pagamentos"] for r in resumo_mensal if r["dia"] <= dia_vg_str
    )

    col_receb, col_pag, col_balanco = st.columns(3)
    col_receb.metric(
        "Recebimentos do dia", f"{total_recebimentos:,.2f} €",
        f"{len(recebimentos_vg)} movimento(s)",
    )
    col_pag.metric(
        "Pagamentos do dia", f"{total_pagamentos:,.2f} €",
        f"{len(pagamentos_vg)} movimento(s)",
    )
    col_balanco.metric("Balanço até ao dia", f"{balanco_ate_ao_dia:,.2f} €")

    col_tab_receb, col_tab_pag = st.columns(2)
    with col_tab_receb:
        st.caption("Recebimentos")
        if recebimentos_vg:
            st.dataframe(
                pd.DataFrame(recebimentos_vg)[["empresa", "descricao", "valor"]],
                use_container_width=True, hide_index=True, column_config=COLUNA_VALOR_EUR,
            )
        else:
            st.info("Sem recebimentos neste dia.")
    with col_tab_pag:
        st.caption("Pagamentos")
        if pagamentos_vg:
            st.dataframe(
                pd.DataFrame(pagamentos_vg)[["empresa", "descricao", "valor"]],
                use_container_width=True, hide_index=True, column_config=COLUNA_VALOR_EUR,
            )
        else:
            st.info("Sem pagamentos neste dia.")

    st.markdown(f"**Recebimentos vs Pagamentos - Extrato bancário de {dia_vg.strftime('%m/%Y')}**")
    st.caption(
        "Somas do extrato bancário (CGD), não do Mapa de Pagamentos e Recebimentos - "
        "os dois podem divergir num dia ainda não conciliado. Ver aba Monitorização > "
        "Auditoria para comparar extrato vs. Mapa dia a dia."
    )
    if resumo_mensal:
        df_mensal = pd.DataFrame(resumo_mensal)
        df_mensal_longo = df_mensal.melt(
            id_vars=["dia"], value_vars=["recebimentos", "pagamentos"],
            var_name="tipo", value_name="valor",
        )
        df_mensal_longo["tipo"] = df_mensal_longo["tipo"].map({
            "recebimentos": "Recebimentos", "pagamentos": "Pagamentos",
        })
        cores_mensal = {"Recebimentos": COR_RECEBIMENTOS_MES, "Pagamentos": COR_PAGAMENTOS_MES}
        grafico_mensal = alt.Chart(df_mensal_longo).mark_line(point=True, strokeWidth=2).encode(
            x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
            y=alt.Y(
                "valor:Q", title="EUR", axis=alt.Axis(format=",.0f"),
                scale=alt.Scale(zero=False),
            ),
            color=alt.Color(
                "tipo:N",
                scale=alt.Scale(domain=list(cores_mensal.keys()), range=list(cores_mensal.values())),
                legend=alt.Legend(title=None),
            ),
            tooltip=["dia:T", "tipo:N", alt.Tooltip("valor:Q", format=",.2f")],
        ).properties(height=300)
        st.altair_chart(grafico_mensal, use_container_width=True)
    else:
        st.info(f"Sem movimentos importados em {dia_vg.strftime('%m/%Y')} para desenhar o gráfico mensal.")

    st.divider()

    try:
        totais = api.saldo_total(dia_vg_str)
    except Exception as e:
        st.error(f"Erro a consultar saldo total: {e}")
        totais = None

    if totais:
        st.markdown(
            f"**Saldo contabilístico geral** (última leitura conhecida de "
            f"cada conta até {dia_vg_str})"
        )
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Saldo contabilístico total", f"{totais['saldo_contabilistico_total']:,.2f} €")
        col_b.metric("Saldo disponível total", f"{totais['saldo_disponivel_total']:,.2f} €")
        col_c.metric("Contas incluídas", totais["entidades"])

        st.markdown("**Variação do saldo bancário (todas as contas juntas) ao longo do tempo**")
        try:
            serie_total = api.saldo_serie_total()
        except Exception as e:
            st.error(f"Erro a consultar a evolução do saldo total: {e}")
            serie_total = []

        if serie_total:
            df_serie_total = pd.DataFrame(serie_total)
            df_serie_total_longo = df_serie_total.melt(
                id_vars=["dia"],
                value_vars=["saldo_contabilistico_total", "saldo_disponivel_total"],
                var_name="tipo", value_name="valor",
            )
            df_serie_total_longo["tipo"] = df_serie_total_longo["tipo"].map({
                "saldo_contabilistico_total": "Saldo contabilístico",
                "saldo_disponivel_total": "Saldo disponível",
            })
            cores_serie_total = {
                "Saldo contabilístico": COR_SALDO_CONTABILISTICO,
                "Saldo disponível": COR_SALDO_DISPONIVEL,
            }
            grafico_serie_total = alt.Chart(df_serie_total_longo).mark_line(point=True, strokeWidth=2).encode(
                x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
                y=alt.Y("valor:Q", title="EUR", axis=alt.Axis(format=",.0f"), scale=alt.Scale(zero=False)),
                color=alt.Color(
                    "tipo:N",
                    scale=alt.Scale(domain=list(cores_serie_total.keys()), range=list(cores_serie_total.values())),
                    legend=alt.Legend(title=None),
                ),
                tooltip=["dia:T", "tipo:N", alt.Tooltip("valor:Q", format=",.2f")],
            ).properties(height=300)
            st.altair_chart(grafico_serie_total, use_container_width=True)
        else:
            st.info("Sem histórico de saldos suficiente para desenhar a evolução.")

        try:
            saldos_atuais = api.listar_saldos_atuais(dia_vg_str)
        except Exception:
            saldos_atuais = []

        if saldos_atuais:
            st.markdown("**Contas com mais saldo contabilístico**")
            df_ranking = pd.DataFrame(saldos_atuais)
            df_ranking = df_ranking.sort_values("saldo_contabilistico", ascending=False).head(10)
            grafico_ranking = alt.Chart(df_ranking).mark_bar(
                cornerRadiusTopRight=4, cornerRadiusBottomRight=4,
            ).encode(
                y=alt.Y("entidade:N", title=None, sort="-x"),
                x=alt.X("saldo_contabilistico:Q", title="Saldo contabilístico (EUR)", axis=alt.Axis(format=",.0f")),
                color=alt.value(COR_RANKING_SALDO),
                tooltip=["entidade:N", alt.Tooltip("saldo_contabilistico:Q", format=",.2f")],
            ).properties(height=320)
            st.altair_chart(grafico_ranking, use_container_width=True)

        st.markdown("**Previsão da riqueza da empresa (saldo total, próximos meses)**")
        st.caption(
            "Previsão do saldo total (todas as contas juntas), com os mesmos modelos "
            "de séries temporais da previsão por conta (regressão linear, média móvel, "
            "suavização exponencial, ARIMA, Markov-switching) - ARIMA(1,1,1) com drift, "
            "e com SARIMAX sazonal semanal quando há histórico suficiente. Ensemble "
            "combina-os ponderados pelo erro (RMSE) medido num conjunto de teste retido - "
            "ver \"Qual modelo acerta mais\" abaixo para a validação treino/teste."
        )
        dias_previsao_total = st.slider(
            "Meses a prever", min_value=1, max_value=6, value=3, key="meses_previsao_total",
            help="Cada mês ~30 dias - até 6 meses (180 dias) de horizonte.",
        ) * 30
        mostrar_todos_total = st.checkbox(
            "Mostrar todos os modelos individuais", key="mostrar_todos_total",
            help="Por omissão só se vê o Ensemble (a previsão recomendada) - liga isto para comparar/auditar os modelos que o compõem.",
        )
        try:
            previsao_total = api.previsao_saldo_total(dias_previsao_total)
        except Exception as e:
            previsao_total = None
            st.info(f"Sem previsão disponível: {e}")

        if previsao_total:
            st.altair_chart(
                grafico_previsao(
                    previsao_total["historico"],
                    filtrar_series_previsao(previsao_total["previsao"], mostrar_todos_total),
                    "Saldo total (EUR)",
                    banda_incerteza=previsao_total.get("banda_incerteza"),
                ),
                use_container_width=True,
            )
            resumo_mensal_total = resumo_mensal_previsao(previsao_total["previsao"])
            if not resumo_mensal_total.empty:
                st.markdown("**Saldo total previsto por mês (fim de cada mês, Ensemble)**")
                st.dataframe(
                    resumo_mensal_total.assign(mes=resumo_mensal_total["mes"].dt.strftime("%Y-%m"))[
                        ["mes", "fim_do_mes"]
                    ].rename(columns={"mes": "Mês", "fim_do_mes": "Saldo estimado"}),
                    use_container_width=True, hide_index=True,
                    column_config={"Saldo estimado": st.column_config.NumberColumn(format="euro")},
                )
            secao_avaliacao_modelos(api.avaliar_previsao_saldo_total, "saldo_total")

        st.divider()

def _cartao_kpi(coluna, titulo, valor, cor):
    coluna.markdown(
        f"<div style='padding:12px 14px;border-radius:8px;background:{cor}1f;border:1px solid {cor};'>"
        f"<div style='font-size:0.85em;color:{cor};'>{escape(titulo)}</div>"
        f"<div style='font-size:1.7em;font-weight:700;color:{cor};'>{escape(str(valor))}</div></div>",
        unsafe_allow_html=True,
    )


_PADRAO_TAREFA_COM_ERRO = re.compile(r":\s*erro\b", re.IGNORECASE)


def _renderizar_detalhe_tarefas(detalhe: list):
    """Mostra cada linha do detalhe de uma execução (uma por task/função do
    script, ex.: "caminho_mapa(2026, 8): Sucesso" ou "caminho_mapa_saldos
    (2026, 8): Erro: ...") a verde ou vermelho consoante o resultado depois
    dos dois pontos comece por "Erro" - para ver de relance quais tasks
    dentro do script falharam, sem abrir o detalhe completo. Não basta
    procurar a substring "erro" na linha toda: nomes de tasks legítimos
    (ex. "verificar_sem_erros(...): Sucesso") também a contêm."""
    for linha in detalhe:
        texto = str(linha)
        cor_linha = COR_ERRO if _PADRAO_TAREFA_COM_ERRO.search(texto) else COR_CASADOS
        st.markdown(
            f"<div style='margin-left:16px;color:{cor_linha};white-space:pre-wrap;font-family:monospace;font-size:0.9em;'>"
            f"{escape(texto)}</div>",
            unsafe_allow_html=True,
        )


with aba_monitorizacao:
    st.markdown("### Estado da Monitorização")

    try:
        scripts_status_kpi = api.listar_monitorizacao_scripts()
        scripts_kpi = scripts_status_kpi.get("scripts", [])
    except Exception:
        scripts_kpi = []
    try:
        logs_recentes_kpi = api.listar_monitorizacao_logs(limit=100).get("logs", [])
    except Exception:
        logs_recentes_kpi = []

    n_ok = sum(1 for s in scripts_kpi if s.get("status") == "ok" and not s.get("atrasado"))
    n_erro = sum(1 for s in scripts_kpi if s.get("status") == "erro")
    n_atrasado = sum(1 for s in scripts_kpi if s.get("atrasado"))
    n_execucoes_erro = sum(1 for l in logs_recentes_kpi if str(l.get("nivel", "")).lower() == "erro")
    n_execucoes = len(logs_recentes_kpi)

    col_ok, col_erro, col_atraso, col_taxa = st.columns(4)
    _cartao_kpi(col_ok, "Scripts OK", n_ok, COR_CASADOS)
    _cartao_kpi(col_erro, "Scripts com erro", n_erro, COR_ERRO)
    _cartao_kpi(col_atraso, "Atrasados", n_atrasado, COR_AMBIGUOS)
    if n_execucoes:
        taxa_sucesso = 100 * (n_execucoes - n_execucoes_erro) / n_execucoes
        cor_taxa = COR_CASADOS if taxa_sucesso >= 90 else (COR_AMBIGUOS if taxa_sucesso >= 70 else COR_ERRO)
        _cartao_kpi(col_taxa, f"Sucesso (últimas {n_execucoes} execuções)", f"{taxa_sucesso:.0f}%", cor_taxa)
    else:
        col_taxa.info("Sem execuções registadas ainda.")

    col_titulo_scripts, col_botao_scripts = st.columns([5, 1])
    col_titulo_scripts.subheader("Estado dos scripts")
    if col_botao_scripts.button("Atualizar", key="botao_atualizar_estado_scripts"):
        st.rerun()
    try:
        scripts_status = api.listar_monitorizacao_scripts()
        scripts = scripts_status.get("scripts", [])
        if scripts:
            df_scripts = pd.DataFrame(scripts)
            df_scripts["status_badge"] = df_scripts["status"].map({"ok": "✅ OK", "erro": "❌ Erro", "warning": "⚠️ Aviso"})
            if "atrasado" in df_scripts.columns:
                atrasados_mask = df_scripts["atrasado"].fillna(False)
                df_scripts.loc[atrasados_mask, "status_badge"] = (
                    "⏰ Atrasado (esperado " + df_scripts.loc[atrasados_mask, "hora_em_falta"] + ")"
                )
                nomes_atrasados = df_scripts.loc[atrasados_mask, "nome"].tolist()
                if nomes_atrasados:
                    st.warning(
                        f"⏰ Script(s) sem execução reportada dentro da hora esperada: {', '.join(nomes_atrasados)}."
                    )
            else:
                atrasados_mask = pd.Series(False, index=df_scripts.index)
            st.dataframe(
                df_scripts[["nome", "descricao", "hora_execucao", "status_badge", "ultima_execucao", "ultima_erro"]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "ultima_execucao": st.column_config.TextColumn("última execução"),
                    "ultima_erro": st.column_config.TextColumn("último erro"),
                    "status_badge": st.column_config.TextColumn("estado"),
                },
            )

            scripts_em_falha = df_scripts.loc[(df_scripts["status"] == "erro") | atrasados_mask, "nome"].tolist()
            if scripts_em_falha:
                st.caption("Correr agora o(s) script(s) em falha (na máquina onde a API corre nativamente):")
                colunas_correr = st.columns(len(scripts_em_falha))
                for coluna, nome_script in zip(colunas_correr, scripts_em_falha):
                    if coluna.button(f"▶ Correr {nome_script}", key=f"botao_correr_{nome_script}"):
                        try:
                            api.correr_script(nome_script)
                            st.success(f"Execução de '{nome_script}' iniciada. O estado atualiza quando terminar.")
                        except Exception as e:
                            st.error(f"Não foi possível iniciar '{nome_script}': {e}")
        else:
            st.info("Sem dados de execução dos scripts.")
    except Exception as e:
        st.warning(f"Não foi possível carregar a monitorização: {e}")

    st.subheader("Erros em tempo real")
    st.caption(
        "Eventos [ERRO]/[AVISO] reportados assim que acontecem durante uma corrida "
        "ainda a decorrer - não é preciso esperar o script terminar para os ver aqui."
    )
    if st.button("Atualizar", key="botao_atualizar_eventos_tempo_real"):
        st.rerun()
    try:
        eventos = api.listar_monitorizacao_eventos(limit=30).get("eventos", [])
        if eventos:
            for evento in reversed(eventos):
                nivel = str(evento.get("nivel", "info")).lower()
                mensagem = evento.get("mensagem") or "Sem mensagem"
                script = evento.get("script") or "script"
                timestamp = evento.get("timestamp") or "-"
                cor = COR_ERRO if nivel == "erro" else COR_AMBIGUOS
                st.markdown(
                    f"<div style='margin-bottom: 8px; color: {cor}; white-space: pre-wrap;'>"
                    f"<strong>{escape(timestamp)}</strong> · <strong>{escape(script)}</strong> · {escape(nivel.upper())}<br/>"
                    f"{escape(mensagem)}</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("Sem eventos em tempo real reportados ainda.")
    except Exception as e:
        st.warning(f"Não foi possível carregar os eventos em tempo real: {e}")

    st.subheader("Histórico de logs")
    filtrar_por_dia = st.checkbox("Filtrar por dia", value=False, key="filtrar_dia_monitorizacao_logs")
    dia_logs = None
    if filtrar_por_dia:
        dia_logs = st.date_input("Dia", value=date.today(), key="dia_monitorizacao_logs")
    try:
        logs = api.listar_monitorizacao_logs(limit=20, dia=dia_logs.isoformat() if dia_logs else None)
        logs_lista = logs.get("logs", [])
        if logs_lista:
            for item in reversed(logs_lista):
                nivel = str(item.get("nivel", "info")).lower()
                mensagem = item.get("mensagem") or "Sem mensagem"
                script = item.get("script") or "script"
                timestamp = item.get("timestamp") or "-"
                detalhe = item.get("detalhe") or []
                cor = COR_ERRO if nivel == "erro" else COR_CASADOS
                st.markdown(
                    f"<div style='margin-bottom: 4px; color: {cor}; white-space: pre-wrap;'>"
                    f"<strong>{escape(timestamp)}</strong> · <strong>{escape(script)}</strong> · {escape(nivel.upper())}<br/>"
                    f"{escape(mensagem)}</div>",
                    unsafe_allow_html=True,
                )
                if detalhe:
                    _renderizar_detalhe_tarefas(detalhe)
        else:
            st.info("Sem logs de execução neste dia." if filtrar_por_dia else "Sem logs de execução.")
    except Exception as e:
        st.warning(f"Não foi possível carregar os logs: {e}")

    st.subheader("Detalhe por script")
    try:
        scripts_status = api.listar_monitorizacao_scripts()
        scripts = scripts_status.get("scripts", [])
        nomes_scripts = [item.get("nome") for item in scripts if item.get("nome")]
        if not nomes_scripts:
            st.info("Ainda não há scripts registados.")
        else:
            script_escolhido = st.selectbox("Escolhe o script", nomes_scripts, index=0, key="script_monitorizacao_detalhe")
            script_info = next((item for item in scripts if item.get("nome") == script_escolhido), None)

            if script_info:
                ultima_execucao = script_info.get("ultima_execucao") or "Nunca"
                ultima_erro = script_info.get("ultima_erro")
                st.caption(f"Status: {script_info.get('status', 'ok')} | Última execução: {ultima_execucao}")
                if ultima_erro:
                    st.markdown(
                        f"<div style='color: {COR_ERRO}; white-space: pre-wrap;'><strong>Erro da última execução:</strong><br/>{escape(ultima_erro)}</div>",
                        unsafe_allow_html=True,
                    )
                else:
                    st.success("Sem erros na última execução.")

            logs = api.listar_monitorizacao_logs(limit=100)
            logs_filtrados = [
                item for item in logs.get("logs", [])
                if str(item.get("script", "")).lower() == script_escolhido.lower()
            ]

            if logs_filtrados:
                st.markdown("**Execuções registadas para este script**")
                for item in reversed(logs_filtrados):
                    nivel = str(item.get("nivel", "info")).lower()
                    mensagem = item.get("mensagem") or "Sem mensagem"
                    timestamp = item.get("timestamp") or "-"
                    detalhe = item.get("detalhe") or []
                    cor = COR_ERRO if nivel == "erro" else COR_CASADOS
                    st.markdown(
                        f"<div style='margin-bottom: 10px; padding: 8px 10px; border-left: 4px solid {cor}; background: rgba(255,255,255,0.02); white-space: pre-wrap;'>"
                        f"<strong>{escape(timestamp)}</strong> · {escape(nivel.upper())}<br/>"
                        f"{escape(mensagem)}</div>",
                        unsafe_allow_html=True,
                    )
                    if detalhe:
                        _renderizar_detalhe_tarefas(detalhe)
            else:
                st.info(f"O script '{script_escolhido}' ainda não tem execuções registadas.")
    except Exception as e:
        st.warning(f"Não foi possível carregar o detalhe do script: {e}")

    st.subheader("Auditoria")
    st.caption(
        "Compara os extratos bancários reais desse dia com o que está no Mapa, "
        "nos dois sentidos - o mesmo que 'python preencher_mapa.py auditoria', "
        "aqui já com a fonte (ficheiro de extrato) de cada movimento."
    )
    dia_auditoria = st.date_input("Dia a auditar", value=date.today(), key="dia_monitorizacao_auditoria")
    dia_auditoria_str = dia_auditoria.isoformat()
    if st.button("Auditar este dia (regista no histórico)", key="botao_auditoria_monitorizacao"):
        try:
            resultado = api.registar_auditoria(dia_auditoria_str)  # já sincroniza o dia antes de auditar
        except Exception as e:
            st.error(f"Erro a consultar a auditoria: {e}")
        else:
            movimentos_sem_match = resultado.get("movimentos_sem_match", [])
            linhas_sem_match = resultado.get("linhas_sem_match", [])
            movimentos_dia = resultado.get("movimentos_dia", [])
            soma_extrato = resultado.get("soma_extrato", 0.0)
            soma_mapa = resultado.get("soma_mapa", 0.0)
            diferenca = resultado.get("diferenca_extrato_mapa", soma_extrato - soma_mapa)

            st.markdown(
                f"**{resultado['sem_match_fwd']}** movimento(s) bancário(s) sem linha correspondente no Mapa "
                f"(deviam estar e não estão) · "
                f"**{resultado['sem_match_rev']}** linha(s) do Mapa por confirmar sem movimento correspondente "
                f"no extrato (estão à espera ou podem estar erradas)."
            )

            col_soma1, col_soma2, col_soma3 = st.columns(3)
            col_soma1.metric("Soma do extrato bancário", f"{soma_extrato:,.2f} €")
            col_soma2.metric("Soma no Mapa (confirmado)", f"{soma_mapa:,.2f} €")
            _cartao_kpi(
                col_soma3, "Diferença", f"{diferenca:,.2f} €",
                COR_CASADOS if abs(diferenca) <= TOLERANCIA_AUDITORIA else COR_ERRO,
            )
            if abs(diferenca) > TOLERANCIA_AUDITORIA:
                st.warning(
                    "As somas não batem certo - há movimento(s) do extrato ainda não refletido(s) no Mapa "
                    "(ou vice-versa). Ver as tabelas de discrepâncias acima/abaixo."
                )

            st.markdown("**Extrato bancário do dia** (todos os movimentos, fonte incluída)")
            if movimentos_dia:
                st.dataframe(
                    pd.DataFrame(movimentos_dia)[["empresa", "descricao", "valor", "ficheiro_origem"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        **COLUNA_VALOR_EUR,
                        "ficheiro_origem": st.column_config.TextColumn("fonte (ficheiro de extrato)"),
                    },
                )
            else:
                st.info("Sem movimentos de extrato importados para este dia.")

            st.markdown("**Movimentos do extrato SEM linha correspondente no Mapa**")
            if movimentos_sem_match:
                st.dataframe(
                    pd.DataFrame(movimentos_sem_match)[["empresa", "descricao", "valor", "ficheiro_origem"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        **COLUNA_VALOR_EUR,
                        "ficheiro_origem": st.column_config.TextColumn("fonte (ficheiro de extrato)"),
                    },
                )
            else:
                st.success("Todos os movimentos bancários deste dia já têm linha correspondente no Mapa.")

            st.markdown("**Linhas do Mapa por confirmar SEM movimento correspondente no extrato**")
            if linhas_sem_match:
                st.dataframe(
                    pd.DataFrame(linhas_sem_match)[["linha", "empresa", "previsto", "imputacao"]],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "previsto": st.column_config.NumberColumn("previsto", format="euro"),
                    },
                )
            else:
                st.success("Todas as linhas por confirmar deste dia têm movimento correspondente no extrato.")

    st.markdown("**Auditoria geral** (todos os dias já importados, de uma vez)")
    dias_atras_geral = st.number_input(
        "Sincronizar e auditar os últimos N dias", min_value=1, max_value=90, value=31, key="dias_atras_auditoria_geral",
    )
    if st.button("Fazer auditoria geral (todos os dias)", key="botao_auditoria_geral"):
        with st.spinner("A sincronizar o OneDrive e a auditar todos os dias já importados..."):
            try:
                resultado_geral = api.auditoria_geral(int(dias_atras_geral))
            except Exception as e:
                st.error(f"Erro na auditoria geral: {e}")
            else:
                st.success(f"{resultado_geral['dias_auditados']} dia(s) auditado(s) e registado(s) no histórico.")
                dias_com_diferenca = {
                    dia: r for dia, r in resultado_geral["resultados"].items()
                    if r.get("erro") or abs(r.get("diferenca_extrato_mapa", 0)) > TOLERANCIA_AUDITORIA or r.get("sem_match_fwd") or r.get("sem_match_rev")
                }
                if dias_com_diferenca:
                    st.warning(f"{len(dias_com_diferenca)} dia(s) com discrepância ou erro - ver tabela abaixo.")
                    st.dataframe(
                        pd.DataFrame.from_dict(dias_com_diferenca, orient="index"),
                        use_container_width=True,
                    )
                else:
                    st.success("Todos os dias auditados batem certo (extrato = Mapa, sem movimentos por confirmar).")

    st.markdown("**Histórico de auditorias registadas**")
    try:
        historico = api.historico_auditorias(limit=100).get("historico", [])
        if historico:
            df_historico = pd.DataFrame(historico)
            st.dataframe(
                df_historico[["dia", "timestamp", "sem_match_fwd", "sem_match_rev", "soma_extrato", "soma_mapa", "diferenca"]],
                use_container_width=True,
                hide_index=True,
                column_config={
                    "soma_extrato": st.column_config.NumberColumn("soma extrato", format="euro"),
                    "soma_mapa": st.column_config.NumberColumn("soma mapa", format="euro"),
                    "diferenca": st.column_config.NumberColumn("diferença", format="euro"),
                },
            )
        else:
            st.info("Ainda não há nenhuma auditoria registada - usa 'Auditar este dia' ou 'Auditoria geral' acima.")
    except Exception as e:
        st.warning(f"Não foi possível carregar o histórico de auditorias: {e}")

with aba_faturas:
    st.subheader("Faturas recebidas (faturas@vidor.pt)")
    st.caption(
        "Alimentado pelo recolher_faturas_recebidas.py (corre de hora a hora, "
        "Agendador de Tarefas) - mesmos dados que já vão para o Excel mensal em "
        "Documentos a Tratar/AFaturas."
    )
    pesquisa_livre = st.text_input(
        "🔍 Pesquisar fatura em qualquer dia (empresa, fornecedor, NIF, assunto, remetente ou valor)",
        key="faturas_pesquisa",
    )

    filtrar_por_dia = st.checkbox(
        "Filtrar por um único dia", value=False, key="faturas_filtrar_dia", disabled=bool(pesquisa_livre)
    )
    dia_faturas_str = desde_faturas_str = ate_faturas_str = None
    if filtrar_por_dia and not pesquisa_livre:
        dia_faturas = st.date_input("Dia", value=date.today(), key="dia_faturas")
        dia_faturas_str = dia_faturas.isoformat()
    elif not pesquisa_livre:
        # por omissão mostra todas as faturas desde o dia 1 do mês corrente
        inicio_mes = date.today().replace(day=1)
        periodo_faturas = st.date_input(
            "Período", value=(inicio_mes, date.today()), key="faturas_periodo"
        )
        if isinstance(periodo_faturas, tuple) and len(periodo_faturas) == 2:
            desde_faturas_str = periodo_faturas[0].isoformat()
            ate_faturas_str = periodo_faturas[1].isoformat()
    if pesquisa_livre:
        st.caption("A pesquisar em todos os dias - o filtro por período fica desligado enquanto houver texto na pesquisa.")
    limite_faturas = st.number_input(
        "Máximo de linhas", min_value=50, max_value=5000, value=1000, step=50, key="faturas_limit"
    )

    try:
        faturas = api.listar_faturas_recebidas(
            dia_faturas_str,
            desde=desde_faturas_str,
            ate=ate_faturas_str,
            pesquisa=pesquisa_livre or None,
            limit=int(limite_faturas),
        )
        if faturas:
            df_faturas = pd.DataFrame(faturas)
            # Servido pela própria API (GET /faturas/recebidas/{id}/pdf), não
            # um link file:// - o Chrome bloqueia navegação file:// a partir
            # de uma página http:// (bug reportado, confirmado no Chrome).
            if "pdf_relativo" in df_faturas.columns:
                df_faturas["pdf"] = df_faturas.apply(
                    lambda r: api.url_pdf_fatura(r["id"]) if isinstance(r["pdf_relativo"], str) and r["pdf_relativo"] else None,
                    axis=1,
                )

            col_f1, col_f2, col_f3 = st.columns(3)
            with col_f1:
                empresas_disponiveis = sorted(e for e in df_faturas["empresa"].dropna().unique() if e)
                empresa_filtro = st.selectbox(
                    "Empresa", ["Todas"] + empresas_disponiveis, key="faturas_filtro_empresa"
                )
            with col_f2:
                fornecedor_filtro = st.text_input("Fornecedor contém", key="faturas_filtro_fornecedor")
            with col_f3:
                valor_filtro = st.text_input("Valor fatura contém", key="faturas_filtro_valor")

            if empresa_filtro != "Todas":
                df_faturas = df_faturas[df_faturas["empresa"] == empresa_filtro]
            if fornecedor_filtro:
                df_faturas = df_faturas[
                    df_faturas["fornecedor"].fillna("").str.contains(fornecedor_filtro, case=False, na=False)
                ]
            if valor_filtro:
                df_faturas = df_faturas[
                    df_faturas["valor_fatura"].fillna("").str.contains(valor_filtro, case=False, na=False)
                ]

            colunas = [
                "dia", "hora", "empresa", "fornecedor", "nif_fornecedor",
                "valor_fatura", "debito", "credito", "saldo",
                "n_anexos_pdf", "pdf", "assunto", "remetente",
            ]
            colunas_existentes = [c for c in colunas if c in df_faturas.columns]
            st.caption(f"{len(df_faturas)} fatura(s) - aumenta o limite acima se faltarem dias.")
            if df_faturas.empty:
                st.info("Nenhuma fatura corresponde aos filtros escolhidos.")
            else:
                st.dataframe(
                    df_faturas[colunas_existentes],
                    use_container_width=True,
                    hide_index=True,
                    column_config={
                        "nif_fornecedor": st.column_config.TextColumn("NIF fornecedor"),
                        "valor_fatura": st.column_config.TextColumn("valor fatura"),
                        "n_anexos_pdf": st.column_config.NumberColumn("nº anexos PDF"),
                        "pdf": st.column_config.LinkColumn("PDF", display_text="Abrir PDF"),
                    },
                )
        else:
            st.info("Ainda não há faturas recebidas registadas para este filtro.")
    except Exception as e:
        st.warning(f"Não foi possível carregar as faturas recebidas: {e}")

with aba_saldos:
    st.subheader("Consultar saldos")
    try:
        empresas_saldo = api.listar_empresas()
    except Exception as e:
        st.error(f"Erro a consultar empresas: {e}")
        empresas_saldo = []

    if not empresas_saldo:
        st.info("Ainda não há empresas com movimentos importados.")
    else:
        empresa = st.selectbox("Empresa", empresas_saldo, key="empresa_consultar_saldo")
        dia_saldo = st.date_input("Dia (opcional)", value=None, key="dia_saldo")

        if st.button("Consultar saldo"):
            try:
                resultado = api.consultar_saldo(empresa, dia_saldo.isoformat() if dia_saldo else None)
                if resultado:
                    st.dataframe(resultado, use_container_width=True, column_config=COLUNAS_SALDO_EUR)
                else:
                    st.warning("Nenhum saldo encontrado para essa empresa/dia.")
            except Exception as e:
                st.error(f"Erro: {e}")

            st.markdown("**Variação do saldo ao longo do mês**")
            try:
                saldos_mes = api.consultar_saldo(empresa)
            except Exception as e:
                st.error(f"Erro a consultar o histórico do mês: {e}")
                saldos_mes = []

            if saldos_mes:
                df_saldos_mes = pd.DataFrame(saldos_mes).sort_values("dia")
                df_saldos_mes_longo = df_saldos_mes.melt(
                    id_vars=["dia"],
                    value_vars=["saldo_contabilistico", "saldo_disponivel"],
                    var_name="tipo", value_name="valor",
                )
                df_saldos_mes_longo["tipo"] = df_saldos_mes_longo["tipo"].map({
                    "saldo_contabilistico": "Saldo contabilístico",
                    "saldo_disponivel": "Saldo disponível",
                })
                cores_saldo_mes = {
                    "Saldo contabilístico": COR_SALDO_CONTABILISTICO,
                    "Saldo disponível": COR_SALDO_DISPONIVEL,
                }
                grafico_saldo_mes = alt.Chart(df_saldos_mes_longo).mark_line(point=True, strokeWidth=2).encode(
                    x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
                    y=alt.Y("valor:Q", title="EUR", axis=alt.Axis(format=",.0f")),
                    color=alt.Color(
                        "tipo:N",
                        scale=alt.Scale(domain=list(cores_saldo_mes.keys()), range=list(cores_saldo_mes.values())),
                        legend=alt.Legend(title=None),
                    ),
                    tooltip=["dia:T", "tipo:N", alt.Tooltip("valor:Q", format=",.2f")],
                ).properties(height=300)
                st.altair_chart(grafico_saldo_mes, use_container_width=True)
            else:
                st.info("Sem histórico do mês para esta empresa.")

    st.divider()
    st.markdown("**Mapa de saldos de todas as empresas**")
    st.caption(
        "Seta e variação % face à leitura anterior de cada conta "
        "(não necessariamente o dia de calendário anterior, já que nem "
        "todas as contas têm leitura todos os dias)."
    )
    dia_mapa = st.date_input("Dia do mapa (opcional)", value=None, key="dia_mapa_saldos")

    try:
        mapa = api.mapa_saldos(dia_mapa.isoformat() if dia_mapa else None)
    except Exception as e:
        st.error(f"Erro a consultar o mapa de saldos: {e}")
        mapa = []

    if not mapa:
        st.info("Sem saldos guardados (precisas de correr /saldos/atualizar primeiro).")
    else:
        df_mapa = pd.DataFrame(mapa).sort_values("entidade")
        df_mapa["Seta (contab.)"] = df_mapa["variacao_contabilistico"].apply(seta_saldo)
        df_mapa["Var. % (contab.)"] = df_mapa["variacao_pct_contabilistico"].apply(fmt_pct_saldo)
        df_mapa["Seta (disp.)"] = df_mapa["variacao_disponivel"].apply(seta_saldo)
        df_mapa["Var. % (disp.)"] = df_mapa["variacao_pct_disponivel"].apply(fmt_pct_saldo)

        tabela_mapa = df_mapa[[
            "entidade", "dia", "saldo_contabilistico", "Seta (contab.)", "Var. % (contab.)",
            "saldo_disponivel", "Seta (disp.)", "Var. % (disp.)",
        ]].rename(columns={
            "entidade": "Empresa",
            "dia": "Dia",
            "saldo_contabilistico": "Saldo contabilístico",
            "saldo_disponivel": "Saldo disponível",
        })

        linha_total = pd.DataFrame([{
            "Empresa": "Total", "Dia": "",
            "Saldo contabilístico": df_mapa["saldo_contabilistico"].sum(),
            "Seta (contab.)": "", "Var. % (contab.)": "",
            "Saldo disponível": df_mapa["saldo_disponivel"].sum(),
            "Seta (disp.)": "", "Var. % (disp.)": "",
        }])
        tabela_mapa_com_total = pd.concat([tabela_mapa, linha_total], ignore_index=True)

        def _destacar_total(linha):
            estilo = "font-weight: bold; border-top: 2px solid currentColor" if linha["Empresa"] == "Total" else ""
            return [estilo] * len(linha)

        st.dataframe(
            tabela_mapa_com_total.style.apply(_destacar_total, axis=1),
            use_container_width=True,
            hide_index=True,
            # altura calculada para caber todas as linhas sem scroll interno
            # (linha ~35px + cabeçalho ~38px + margem) - por omissão o
            # st.dataframe só mostra ~10 linhas e obriga a fazer scroll.
            height=int(35 * len(tabela_mapa_com_total) + 38 + 3),
            column_config={
                "Saldo contabilístico": st.column_config.NumberColumn(format="euro"),
                "Saldo disponível": st.column_config.NumberColumn(format="euro"),
            },
        )

with aba_analise_extratos:
    st.subheader("Análise de Extratos")
    st.caption(
        "Distribuição do que é recebido/pago por imputação (categoria do Mapa "
        "de Pagamentos e Recebimentos) no período selecionado."
    )

    col_ini, col_fim = st.columns(2)
    hoje = date.today()
    with col_ini:
        dia_inicio_extratos = st.date_input("De", value=hoje.replace(day=1), key="dia_inicio_extratos")
    with col_fim:
        dia_fim_extratos = st.date_input("Até", value=hoje, key="dia_fim_extratos")

    try:
        analise_geral = api.analise_imputacoes(
            dia_inicio=dia_inicio_extratos.isoformat(), dia_fim=dia_fim_extratos.isoformat(),
        )
    except Exception as e:
        st.error(f"Erro a consultar a análise de extratos: {e}")
        analise_geral = {"recebimentos": [], "pagamentos": []}

    try:
        linhas_geral = api.listar_linhas_imputacao(
            dia_inicio=dia_inicio_extratos.isoformat(), dia_fim=dia_fim_extratos.isoformat(),
        )
    except Exception as e:
        st.error(f"Erro a consultar os extratos: {e}")
        linhas_geral = []
    linhas_geral_receb = [l for l in linhas_geral if l["tipo"] == "recebimento"]
    linhas_geral_pag = [l for l in linhas_geral if l["tipo"] == "pagamento"]

    st.markdown("**CPCVs / Escrituras do período**")

    col_cpcv_mapa, col_cpcv_comercial = st.columns(2)

    with col_cpcv_mapa:
        st.caption(
            "Do Mapa de Pagamentos e Recebimentos: conta bancária e dia em que o "
            "recebimento (imputação CPCV/Escritura) foi confirmado no extrato."
        )
        linhas_cpcv_escritura = [
            l for l in linhas_geral_receb
            if any(termo in l["imputacao"].lower() for termo in ("cpcv", "escritura"))
        ]
        if linhas_cpcv_escritura:
            df_cpcv = pd.DataFrame(linhas_cpcv_escritura)[["dia", "empresa", "imputacao", "previsto", "valor"]]
            df_cpcv = df_cpcv.sort_values("dia", ascending=False).rename(columns={
                "dia": "Dia", "empresa": "Empresa", "imputacao": "Tipo",
                "previsto": "Valor tabelado/proposto", "valor": "Valor recebido",
            })
            st.dataframe(
                df_cpcv,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Valor tabelado/proposto": st.column_config.NumberColumn(format="euro"),
                    "Valor recebido": st.column_config.NumberColumn(format="euro"),
                },
            )
        else:
            st.info("Sem CPCVs/Escrituras recebidos registados no período.")

    with col_cpcv_comercial:
        st.caption(
            "Do Índice do Departamento Comercial (SharePoint): referência, "
            "empreendimento e cliente do negócio - não sincronizado "
            "automaticamente (ver COMERCIAL_INDICE_CSV)."
        )
        try:
            cpcv_escrituras = api.listar_cpcv_escrituras(
                dia_inicio=dia_inicio_extratos.isoformat(), dia_fim=dia_fim_extratos.isoformat(),
            )
        except Exception as e:
            st.error(f"Erro a consultar o índice comercial: {e}")
            cpcv_escrituras = []

        if cpcv_escrituras:
            df_cpcv_comercial = pd.DataFrame(cpcv_escrituras)[[
                "ref", "empreendimento", "cliente", "valor_proposto", "valor_recebido", "data_cpcv",
            ]].rename(columns={
                "ref": "Ref.", "empreendimento": "Empreendimento", "cliente": "Cliente",
                "valor_proposto": "Valor proposto", "valor_recebido": "Valor recebido",
                "data_cpcv": "Data CPCV",
            })
            st.dataframe(
                df_cpcv_comercial,
                use_container_width=True,
                hide_index=True,
                column_config={
                    "Valor proposto": st.column_config.NumberColumn(format="euro"),
                    "Valor recebido": st.column_config.NumberColumn(format="euro"),
                },
            )
        else:
            st.info(
                "Sem CPCVs/Escrituras no período (ou o índice comercial não está "
                "acessível nesta máquina)."
            )

    st.divider()
    st.markdown("**Todas as empresas**")
    col_receb_pizza, col_pag_pizza = st.columns(2)
    with col_receb_pizza:
        grafico_receb, cores_receb = grafico_pizza_imputacoes(analise_geral.get("recebimentos", []), "Recebimentos por imputação")
        if grafico_receb is not None:
            st.altair_chart(grafico_receb, use_container_width=True)
        else:
            st.info("Sem recebimentos registados no período.")
    with col_pag_pizza:
        grafico_pag, cores_pag = grafico_pizza_imputacoes(analise_geral.get("pagamentos", []), "Pagamentos por imputação")
        if grafico_pag is not None:
            st.altair_chart(grafico_pag, use_container_width=True)
        else:
            st.info("Sem pagamentos registados no período.")

    col_extrato_receb, col_extrato_pag = st.columns(2)
    with col_extrato_receb:
        tabela_extratos_colorida(linhas_geral_receb, cores_receb, "Sem recebimentos registados no período.")
    with col_extrato_pag:
        tabela_extratos_colorida(linhas_geral_pag, cores_pag, "Sem pagamentos registados no período.")

    st.markdown("**Ranking de imputações que mais gastam (todas, sem limite de 6)**")
    grafico_ranking_pag = grafico_ranking_imputacoes(analise_geral.get("pagamentos", []), "Pagamentos por imputação - ranking completo")
    if grafico_ranking_pag is not None:
        st.altair_chart(grafico_ranking_pag, use_container_width=True)
    else:
        st.info("Sem pagamentos registados no período.")

    st.divider()
    st.markdown("**Por empresa**")
    try:
        empresas_extratos = api.listar_empresas()
    except Exception as e:
        st.error(f"Erro a consultar empresas: {e}")
        empresas_extratos = []

    if not empresas_extratos:
        st.info("Ainda não há empresas com movimentos importados.")
    else:
        empresa_extratos = st.selectbox("Empresa", empresas_extratos, key="empresa_analise_extratos")
        try:
            analise_empresa = api.analise_imputacoes(
                empresa=empresa_extratos,
                dia_inicio=dia_inicio_extratos.isoformat(),
                dia_fim=dia_fim_extratos.isoformat(),
            )
        except Exception as e:
            st.error(f"Erro a consultar a análise de extratos desta empresa: {e}")
            analise_empresa = {"recebimentos": [], "pagamentos": []}

        try:
            linhas_empresa = api.listar_linhas_imputacao(
                empresa=empresa_extratos,
                dia_inicio=dia_inicio_extratos.isoformat(),
                dia_fim=dia_fim_extratos.isoformat(),
            )
        except Exception as e:
            st.error(f"Erro a consultar os extratos desta empresa: {e}")
            linhas_empresa = []
        linhas_empresa_receb = [l for l in linhas_empresa if l["tipo"] == "recebimento"]
        linhas_empresa_pag = [l for l in linhas_empresa if l["tipo"] == "pagamento"]

        col_receb_emp, col_pag_emp = st.columns(2)
        with col_receb_emp:
            grafico_receb_emp, cores_receb_emp = grafico_pizza_imputacoes(
                analise_empresa.get("recebimentos", []), f"Recebimentos - {empresa_extratos}",
            )
            if grafico_receb_emp is not None:
                st.altair_chart(grafico_receb_emp, use_container_width=True)
            else:
                st.info("Sem recebimentos registados no período para esta empresa.")
        with col_pag_emp:
            grafico_pag_emp, cores_pag_emp = grafico_pizza_imputacoes(
                analise_empresa.get("pagamentos", []), f"Pagamentos - {empresa_extratos}",
            )
            if grafico_pag_emp is not None:
                st.altair_chart(grafico_pag_emp, use_container_width=True)
            else:
                st.info("Sem pagamentos registados no período para esta empresa.")

        col_extrato_receb_emp, col_extrato_pag_emp = st.columns(2)
        with col_extrato_receb_emp:
            tabela_extratos_colorida(
                linhas_empresa_receb, cores_receb_emp, "Sem recebimentos registados no período para esta empresa.",
            )
        with col_extrato_pag_emp:
            tabela_extratos_colorida(
                linhas_empresa_pag, cores_pag_emp, "Sem pagamentos registados no período para esta empresa.",
            )

with aba_contas:
    st.subheader("Ranking de risco (todas as empresas)")
    st.caption(
        "Do mais crítico para o mais saudável, com base no saldo atual, na "
        "despesa média mensal e no ritmo de caixa real dos últimos 30 dias, "
        "e com a previsão de saldo (Ensemble) já aplicada por empresa - "
        "\"Zona prevista\" e \"Saldo previsto\" respondem a \"como fica esta "
        "empresa daqui a uns dias\", não só \"como está hoje\" (ver aviso de "
        "zona de risco mais abaixo, por empresa)."
    )
    dias_ranking_risco = st.slider(
        "Horizonte da previsão no ranking", min_value=7, max_value=180, value=30, key="dias_ranking_risco",
    )
    try:
        ranking_risco = api.previsao_risco_ranking(dias_ranking_risco)
    except Exception as e:
        st.error(f"Erro a consultar o ranking de risco: {e}")
        ranking_risco = []

    if ranking_risco:
        df_ranking_risco = pd.DataFrame(ranking_risco)
        df_ranking_risco["Zona"] = df_ranking_risco["zona_atual"].map({
            "critico": "🔴 Crítico", "alerta": "🟡 Alerta", "ok": "🟢 Saudável",
        })
        # zona_prevista só difere de zona_atual quando o forecast aponta
        # para uma degradação nos próximos `dias_ranking_risco` dias (ver
        # avaliar_zona_risco) - mostrar sempre as duas colunas tornaria a
        # tabela repetitiva para a maioria das empresas (as duas iguais);
        # em vez disso, um aviso só aparece quando há mesmo uma mudança.
        df_ranking_risco["Zona prevista"] = df_ranking_risco.apply(
            lambda r: (
                {"critico": "🔴 Crítico", "alerta": "🟡 Alerta", "ok": "🟢 Saudável"}[r["zona_prevista"]]
                + (f" (a partir de {r['dia_risco']})" if r["dia_risco"] else "")
            ) if r["zona_prevista"] != r["zona_atual"] else "— (sem alteração)",
            axis=1,
        )
        df_ranking_risco["Saldo previsto"] = df_ranking_risco["saldo_previsto_fim"].apply(
            lambda v: f"{v:,.2f} €" if v is not None else "sem previsão (histórico insuficiente)"
        )
        df_ranking_risco["Autonomia (tendência)"] = df_ranking_risco["dias_autonomia_tendencia"].apply(
            lambda d: f"{d:.0f} dias" if d is not None else "sem risco de esgotar"
        )
        df_ranking_risco["Autonomia (pior caso)"] = df_ranking_risco["dias_autonomia_despesa"].apply(
            lambda d: f"{d:.0f} dias" if d is not None else "sem despesa registada"
        )
        colunas_exibicao = df_ranking_risco[[
            "empresa", "Zona", "saldo_atual", "taxa_diaria_liquida", "Saldo previsto", "Zona prevista",
            "Autonomia (tendência)", "Autonomia (pior caso)",
        ]].rename(columns={
            "empresa": "Empresa", "saldo_atual": "Saldo atual", "taxa_diaria_liquida": "Ritmo de caixa (€/dia)",
        })

        cores_zona_risco = {"critico": COR_ERRO, "alerta": COR_AMBIGUOS, "ok": COR_CASADOS}

        def _pintar_risco(linha):
            cor = cores_zona_risco.get(df_ranking_risco.loc[linha.name, "zona_atual"], "#ffffff")
            return [f"background-color: {cor}33"] * len(linha)

        estilo_risco = colunas_exibicao.style.apply(_pintar_risco, axis=1).format({
            "Saldo atual": "{:,.2f} €", "Ritmo de caixa (€/dia)": "{:+,.2f}",
        })
        st.dataframe(estilo_risco, use_container_width=True, hide_index=True)
    else:
        st.info("Ainda não há dados suficientes para o ranking de risco.")

    st.divider()

    st.subheader("Análise de uma conta")

    try:
        empresas_disponiveis = api.listar_empresas()
    except Exception as e:
        st.error(f"Erro a ligar à API: {e}")
        empresas_disponiveis = []

    if not empresas_disponiveis:
        st.info("Ainda não há empresas com movimentos importados.")
    else:
        empresa_escolhida = st.selectbox("Conta / empresa", empresas_disponiveis)

        st.markdown("**Saldo ao longo do tempo**")
        try:
            saldos = api.consultar_saldo(empresa_escolhida)
        except Exception as e:
            st.error(f"Erro: {e}")
            saldos = []

        if saldos:
            df_saldos = pd.DataFrame(saldos).sort_values("dia")
            df_saldos_longo = df_saldos.melt(
                id_vars=["dia"],
                value_vars=["saldo_contabilistico", "saldo_disponivel"],
                var_name="tipo", value_name="valor",
            )
            df_saldos_longo["tipo"] = df_saldos_longo["tipo"].map({
                "saldo_contabilistico": "Saldo contabilístico",
                "saldo_disponivel": "Saldo disponível",
            })
            cores_saldo = {
                "Saldo contabilístico": COR_SALDO_CONTABILISTICO,
                "Saldo disponível": COR_SALDO_DISPONIVEL,
            }
            grafico_saldo = alt.Chart(df_saldos_longo).mark_line(point=True, strokeWidth=2).encode(
                x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
                y=alt.Y("valor:Q", title="EUR", axis=alt.Axis(format=",.0f")),
                color=alt.Color(
                    "tipo:N",
                    scale=alt.Scale(domain=list(cores_saldo.keys()), range=list(cores_saldo.values())),
                    legend=alt.Legend(title=None),
                ),
                tooltip=["dia:T", "tipo:N", alt.Tooltip("valor:Q", format=",.2f")],
            ).properties(height=300)
            st.altair_chart(grafico_saldo, use_container_width=True)
        else:
            st.info("Sem saldos guardados para esta empresa (precisas de correr /saldos/atualizar primeiro).")

        try:
            risco = api.previsao_risco(empresa_escolhida)
        except Exception:
            risco = None

        if risco:
            despesa_mensal = risco["despesa_media_mensal"]
            taxa_diaria = risco["taxa_diaria_liquida"]
            dias_tendencia = risco["dias_autonomia_tendencia"]
            dias_despesa = risco["dias_autonomia_despesa"]
            zona_atual = risco["zona_atual"]

            if dias_tendencia is not None:
                texto_autonomia = f" · Ao ritmo de caixa atual, aguenta cerca de {dias_tendencia:.0f} dias."
            elif taxa_diaria >= 0:
                texto_autonomia = " · Ao ritmo de caixa atual, o saldo não está a esgotar-se (entra tanto ou mais do que sai)."
            else:
                texto_autonomia = ""

            mensagem = (
                f"Saldo atual: {risco['saldo_atual']:,.2f} € · "
                f"Ritmo de caixa (últimos 30 dias): {taxa_diaria:+,.2f} €/dia{texto_autonomia}"
            )
            if zona_atual == "critico":
                st.error(f"🔴 Zona crítica de saldo. {mensagem}")
            elif zona_atual == "alerta":
                st.warning(f"🟡 Zona de alerta de saldo. {mensagem}")
            else:
                st.success(f"🟢 Saldo saudável. {mensagem}")

            if dias_despesa is not None:
                st.caption(
                    f"Pior caso: se as receitas parassem amanhã, a despesa média mensal "
                    f"({despesa_mensal:,.2f} €) sozinha aguentaria cerca de {dias_despesa:.0f} dias."
                )

            if risco["zona_prevista"] != zona_atual and risco["dia_risco"]:
                st.warning(
                    f"⚠️ A previsão de saldo (ensemble) aponta para entrar em zona "
                    f"\"{risco['zona_prevista']}\" a partir de {risco['dia_risco']}."
                )

        st.markdown("**Previsão de saldo**")
        st.caption(
            "O saldo só muda em dias com movimento - se os últimos dias estiverem "
            "parados, uma previsão \"sem alteração\" está correta, não avariada. "
            "Para ver a previsão reagir todos os dias, ver \"Previsão de cash-flow\" mais abaixo. "
            "Gradient Boosting não entra aqui de propósito (só na previsão de cash-flow) - "
            "para não misturar duas famílias de modelo diferentes na mesma comparação."
        )
        dias_previsao = st.slider(
            "Dias a prever", min_value=3, max_value=180, value=30, key="dias_previsao",
            help="Até 180 dias (~6 meses) - para uma leitura de gestão a médio prazo, não só os próximos dias.",
        )
        mostrar_todos_saldo = st.checkbox(
            "Mostrar todos os modelos individuais", key="mostrar_todos_saldo",
            help="Por omissão só se vê o Ensemble (a previsão recomendada) - liga isto para comparar/auditar os modelos que o compõem.",
        )
        try:
            previsao = api.previsao_saldo(empresa_escolhida, dias_previsao)
        except Exception as e:
            previsao = None
            st.info(f"Sem previsão disponível: {e}")

        if previsao:
            st.altair_chart(
                grafico_previsao(
                    previsao["historico"],
                    filtrar_series_previsao(previsao["previsao"], mostrar_todos_saldo),
                    "Saldo contabilístico (EUR)",
                    banda_incerteza=previsao.get("banda_incerteza"),
                ),
                use_container_width=True,
            )
            st.caption(
                "Linhas tracejadas = previsão; área sombreada = banda de incerteza a "
                "~80% à volta do Ensemble (cresce com o horizonte - uma previsão a 6 "
                "meses é sempre menos certa que a de amanhã, a banda mostra isso em vez "
                "de fingir uma precisão que nenhum modelo tem). Mostram-se só os modelos "
                "com melhor desempenho no teste retido desta conta (ver \"Qual modelo "
                "acerta mais\" abaixo) - os visivelmente piores ficam de fora, em vez de "
                "sete linhas sobrepostas na mesma cor. Regressão linear extrapola a "
                "tendência; média móvel repete o nível recente; suavização exponencial "
                "(Holt-Winters) reage depressa a mudanças recentes e capta um padrão "
                "semanal, quando há histórico para isso; ARIMA capta autocorrelação (e "
                "sazonalidade semanal, com mais histórico); Markov-switching tenta "
                "detetar mudanças de regime (útil se a conta teve uma quebra grande); "
                "Ensemble combina os modelos acima ponderados pelo erro (RMSE) de cada "
                "um - geralmente mais robusto do que qualquer um sozinho. Previsto "
                "(Mapa), em cinzento, não é um modelo estatístico - é o saldo que "
                "resultaria só do que já está planeado (recebimentos/pagamentos "
                "previstos) no Mapa de Pagamentos e Recebimentos para estes dias; só "
                "aparece se já houver algo lançado."
            )
            if dias_previsao >= 45:
                resumo_mensal = resumo_mensal_previsao(previsao["previsao"])
                if not resumo_mensal.empty:
                    st.markdown("**Saldo previsto por mês (fim de cada mês, Ensemble)**")
                    st.dataframe(
                        resumo_mensal.assign(mes=resumo_mensal["mes"].dt.strftime("%Y-%m"))[
                            ["mes", "fim_do_mes"]
                        ].rename(columns={"mes": "Mês", "fim_do_mes": "Saldo estimado"}),
                        use_container_width=True, hide_index=True,
                        column_config={"Saldo estimado": st.column_config.NumberColumn(format="euro")},
                    )
            secao_avaliacao_modelos(
                lambda dt: api.avaliar_previsao(empresa_escolhida, dt), "saldo_entidade",
            )

        st.markdown("**Fluxo diário (movimentos)**")
        try:
            historico = api.historico_movimentos(empresa_escolhida)
        except Exception as e:
            st.error(f"Erro: {e}")
            historico = []

        if historico:
            df_hist = pd.DataFrame(historico)
            df_fluxo = df_hist.groupby("dia", as_index=False)["valor"].sum()
            df_fluxo["sinal"] = df_fluxo["valor"].apply(
                lambda v: "Entradas" if v >= 0 else "Saídas"
            )
            cores_fluxo = {"Entradas": COR_FLUXO_POSITIVO, "Saídas": COR_FLUXO_NEGATIVO}
            grafico_fluxo = alt.Chart(df_fluxo).mark_bar().encode(
                x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
                y=alt.Y("valor:Q", title="EUR (líquido do dia)", axis=alt.Axis(format=",.0f")),
                color=alt.Color(
                    "sinal:N",
                    scale=alt.Scale(domain=list(cores_fluxo.keys()), range=list(cores_fluxo.values())),
                    legend=alt.Legend(title=None),
                ),
                tooltip=["dia:T", alt.Tooltip("valor:Q", format=",.2f")],
            ).properties(height=250)
            st.altair_chart(grafico_fluxo, use_container_width=True)

            with st.expander("Ver movimentos individuais"):
                st.dataframe(df_hist, use_container_width=True, column_config=COLUNA_VALOR_EUR)
        else:
            st.info("Sem movimentos importados para esta empresa.")

        st.markdown("**Previsão de cash-flow (esta entidade, próximos dias)**")
        dias_previsao_cf_ent = st.slider(
            "Dias a prever", min_value=3, max_value=180, value=30, key="dias_previsao_cf_entidade",
            help="Até 180 dias (~6 meses) - para uma leitura de gestão a médio prazo, não só os próximos dias.",
        )
        mostrar_todos_cf_ent = st.checkbox(
            "Mostrar todos os modelos individuais", key="mostrar_todos_cf_ent",
            help="Por omissão só se vê o Ensemble (a previsão recomendada) - liga isto para comparar/auditar os modelos que o compõem.",
        )
        try:
            previsao_cf_ent = api.previsao_cashflow(empresa_escolhida, dias_previsao_cf_ent)
        except Exception as e:
            previsao_cf_ent = None
            st.info(f"Sem previsão disponível: {e}")

        if previsao_cf_ent:
            historico_liquido_ent = [
                {"dia": p["dia"], "valor": p["liquido"]} for p in previsao_cf_ent["historico"]
            ]
            st.altair_chart(
                grafico_previsao(
                    historico_liquido_ent,
                    filtrar_series_previsao(previsao_cf_ent["previsao"], mostrar_todos_cf_ent),
                    "Cash-flow líquido (EUR)",
                    banda_incerteza=previsao_cf_ent.get("banda_incerteza"),
                ),
                use_container_width=True,
            )
            st.caption(
                "Como esta entidade pode ter poucos dias com movimento real, a série "
                "inclui os dias sem movimento como 0 - reduz o risco de a previsão "
                "extrapolar picos isolados como se fossem tendência. Área sombreada = "
                "banda de incerteza a ~80% à volta do Ensemble, cresce com o horizonte. "
                "Mostram-se só os modelos com melhor desempenho no teste retido (ver "
                "\"Qual modelo acerta mais\" abaixo); Gradient Boosting precisa de mais "
                "histórico que os outros (mínimo 15 dias) - pode faltar por isso, ou por "
                "ter sido superado por outro modelo nesta série, mesmo tendo corrido sem "
                "erro. Previsto (Mapa), em cinzento, não é um modelo - é o líquido "
                "(recebimentos - pagamentos) já planeado no Mapa de Pagamentos e "
                "Recebimentos para estes dias; só aparece se já houver algo lançado."
            )
            if dias_previsao_cf_ent >= 45:
                resumo_mensal_cf = resumo_mensal_previsao(previsao_cf_ent["previsao"])
                if not resumo_mensal_cf.empty:
                    st.markdown("**Cash-flow previsto por mês (total do mês, Ensemble)**")
                    st.dataframe(
                        resumo_mensal_cf.assign(mes=resumo_mensal_cf["mes"].dt.strftime("%Y-%m"))[
                            ["mes", "total_mes"]
                        ].rename(columns={"mes": "Mês", "total_mes": "Cash-flow líquido estimado"}),
                        use_container_width=True, hide_index=True,
                        column_config={"Cash-flow líquido estimado": st.column_config.NumberColumn(format="euro")},
                    )
            if previsao_cf_ent.get("importancia_features"):
                st.markdown("**O que pesou mais na previsão do Gradient Boosting**")
                st.altair_chart(
                    grafico_importancia_features(previsao_cf_ent["importancia_features"]),
                    use_container_width=True,
                )
            secao_avaliacao_modelos(
                lambda dt: api.avaliar_previsao_cashflow(empresa_escolhida, dt), "cf_entidade",
            )

with aba_assistente:
    st.subheader("Assistente")
    st.caption(
        "Explica os dados apresentados e vai buscar informação real através "
        "de ferramentas de leitura - nunca reconcilia nem resolve nada sozinho."
    )

    if "chat_mensagens" not in st.session_state:
        st.session_state["chat_mensagens"] = []

    if st.button("Reiniciar conversa"):
        try:
            api.reiniciar_chat()
        except Exception as e:
            st.error(f"Erro: {e}")
        st.session_state["chat_mensagens"] = []
        st.rerun()

    for mensagem in st.session_state["chat_mensagens"]:
        with st.chat_message(mensagem["role"]):
            st.write(mensagem["content"])
            if mensagem.get("ferramentas_usadas"):
                st.caption("🔧 consultou: " + ", ".join(mensagem["ferramentas_usadas"]))

    pergunta = st.chat_input("Pergunta sobre os dados da tesouraria...")
    if pergunta:
        st.session_state["chat_mensagens"].append({"role": "user", "content": pergunta})
        with st.chat_message("user"):
            st.write(pergunta)

        with st.chat_message("assistant"):
            with st.spinner("A pensar..."):
                try:
                    resultado = api.perguntar_chat(pergunta)
                    resposta = resultado["resposta"]
                    ferramentas = resultado.get("ferramentas_usadas", [])
                except Exception as e:
                    resposta = f"Erro a contactar o assistente: {e}"
                    ferramentas = []
            st.write(resposta)
            if ferramentas:
                st.caption("🔧 consultou: " + ", ".join(ferramentas))

        st.session_state["chat_mensagens"].append({
            "role": "assistant", "content": resposta, "ferramentas_usadas": ferramentas,
        })

# Rodapé corporativo - fecha visualmente com o cabeçalho (mesma barra
# escura, mesmo traço vermelho fino), assinala que é uma ferramenta
# interna.
st.markdown(
    f"""
    <div style="border-top:3px solid #c8102e;background:#1c1f26;
                margin:2rem -1rem -1rem -1rem;padding:14px 32px;
                display:flex;align-items:center;justify-content:space-between;">
      <div style="color:#8f97a3;font-size:0.75rem;letter-spacing:.04em;">
        VIDÓR &middot; Documento interno &middot; Uso exclusivo
      </div>
      <div style="color:#8f97a3;font-size:0.75rem;letter-spacing:.04em;">
        Plataforma de Análise de Tesouraria &middot; {date.today().year}
      </div>
    </div>
    """,
    unsafe_allow_html=True,
)
