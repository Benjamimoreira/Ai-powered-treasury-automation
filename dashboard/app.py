from datetime import date
from html import escape
from pathlib import Path
import os
import re
import sys
import unicodedata

dashboard_dir = str(Path(__file__).resolve().parent)
if dashboard_dir not in sys.path:
    sys.path.insert(0, dashboard_dir)

import altair as alt
import pandas as pd
import streamlit as st

import api_client as api

# Números e datas dos gráficos em português (170 249 em vez de 170,249;
# meses em português) - os eixos usavam o formato inglês do Vega, e as
# tabelas e os KPIs o português, na mesma página.
LOCALE_PT = {
    "number": {"decimal": ",", "thousands": " ", "grouping": [3], "currency": ["", " €"]},
    "time": {
        "dateTime": "%A, %e de %B de %Y, %X", "date": "%d/%m/%Y", "time": "%H:%M:%S",
        "periods": ["AM", "PM"],
        "days": ["domingo", "segunda", "terça", "quarta", "quinta", "sexta", "sábado"],
        "shortDays": ["dom", "seg", "ter", "qua", "qui", "sex", "sáb"],
        "months": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro",
                   "outubro", "novembro", "dezembro"],
        "shortMonths": ["jan", "fev", "mar", "abr", "mai", "jun", "jul", "ago", "set", "out", "nov", "dez"],
    },
}


@alt.theme.register("tesouraria_pt", enable=True)
def _tema_pt():
    return alt.theme.ThemeConfig({"config": {"locale": LOCALE_PT}})

# O Streamlit volta a correr o script INTEIRO (todos os separadores) a cada
# interação - sem cache, mexer num slider ou num toggle voltava a pedir à
# API todas as previsões (cada uma treina vários modelos; o ranking de risco
# fá-lo empresa a empresa), e a página inteira ficava a recarregar durante
# segundos. As previsões só mudam quando entram dados novos, por isso ficam
# 10 minutos em cache (limpa-se ao carregar em "Atualizar dados" - ver
# _limpar_cache_previsoes). Guardado em `api` com uma marca para não voltar
# a envolver a mesma função a cada rerun (o módulo persiste entre reruns).
# Com refresh_mode="background", uma previsão expirada devolve logo o valor
# anterior e recalcula em segundo plano - o risco de liquidez e o ranking
# de risco demoram ~24 s cada, e era isso que se esperava a cada 10 minutos.
FUNCOES_PREVISAO_EM_CACHE = (
    "previsao_saldo", "avaliar_previsao", "previsao_saldo_total", "avaliar_previsao_saldo_total",
    "previsao_risco", "previsao_risco_ranking", "previsao_cashflow", "avaliar_previsao_cashflow",
    "previsao_saldo_total_cashflow", "previsao_ancorada", "previsao_risco_liquidez",
)
if not getattr(api, "_previsoes_em_cache", False):
    for _nome in FUNCOES_PREVISAO_EM_CACHE:
        setattr(api, _nome, st.cache_data(ttl=600, refresh_mode="background", show_spinner="A calcular a previsão...")(getattr(api, _nome)))
    api._previsoes_em_cache = True


def _limpar_cache_previsoes():
    st.cache_data.clear()

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
    # Mesmo slot do Markov-switching: só existe no cash-flow, onde o
    # Markov-switching raramente sobrevive ao filtro dos melhores modelos.
    "Perfil de calendário": "#008300",
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
    # o que o comercial tem marcado para receber - verde de "recebimentos"
    "Com recebimentos do comercial": COR_RECEBIMENTOS_MES,
}

NOMES_MODELO = {
    "regressao_linear": "Regressão linear",
    "media_movel": "Média móvel",
    "suavizacao_exponencial": "Suavização exponencial",
    "arima": "ARIMA",
    "markov_switching": "Markov-switching",
    "gradient_boosting": "Gradient Boosting",
    "perfil_calendario": "Perfil de calendário",
    "ensemble": "Ensemble (ponderado)",
    "previsto_mapa": "Previsto (Mapa)",
    "com_comercial": "Com recebimentos do comercial",
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
COLUNA_VALOR_EUR = {"valor": st.column_config.NumberColumn("Valor", format="euro")}
COLUNAS_SALDO_EUR = {
    "saldo_contabilistico": st.column_config.NumberColumn("Saldo contabilístico", format="euro"),
    "saldo_disponivel": st.column_config.NumberColumn("Saldo disponível", format="euro"),
}


def seta_saldo(variacao):
    """Seta de tendência do mapa de saldos - reaproveitada na Visão Geral e na aba Saldos."""
    if variacao is None or pd.isna(variacao) or variacao == 0:
        return "➖"  # (antes, variação 0 dava 🔽 - todas as contas paradas pareciam a descer)
    return "🔼" if variacao > 0 else "🔽"


def fmt_pct_saldo(pct):
    if pct is None or pd.isna(pct):
        return "—"
    return f"{_n(pct, 1, sinal=True)} %"


def variacao_saldo(variacao, pct) -> str:
    """"🔼 +2,3 %" - seta e percentagem numa coluna só."""
    return f"{seta_saldo(variacao)} {fmt_pct_saldo(pct)}"


DIAS_LEITURA_ANTIGA = 7
# extratos exportados sem o nome da empresa ficam com o nome de ficheiro
# genérico "<dia>_Empresa.xlsx" e a conta aparece como "Empresa"
NOMES_SEM_EMPRESA = {"EMPRESA"}


def rotulo_conta(entidade: str, dia, dia_mais_recente) -> str:
    """Nome da conta no mapa de saldos, com ⚠️ quando a última leitura é antiga
    (continua a contar no total) ou quando o extrato veio sem o nome da empresa."""
    if str(entidade).strip().upper() in NOMES_SEM_EMPRESA:
        return "⚠️ Conta sem nome (extrato \"…_Empresa.xlsx\")"
    antiga = dia and dia_mais_recente and (pd.to_datetime(dia_mais_recente) - pd.to_datetime(dia)).days > DIAS_LEITURA_ANTIGA
    return f"⚠️ {entidade}" if antiga else entidade


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


def _sem_acentos(texto) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", str(texto or "")) if not unicodedata.combining(c)).lower()


def procurar_linhas_extratos(linhas: list, pesquisa: str) -> list:
    """Linhas do Mapa que têm TODAS as palavras da pesquisa na descrição,
    empresa, imputação, dia ou valor - sem acentos nem maiúsculas ("agua"
    apanha "ÁGUAS"; "1500" apanha 1 500,00 €)."""
    palavras = _sem_acentos(pesquisa).split()
    if not palavras:
        return linhas

    def _texto(l):
        valor = l.get("valor")
        valores = f"{valor:.2f} {valor:.2f}".replace(".", ",", 1) if isinstance(valor, (int, float)) else ""
        return _sem_acentos(" ".join(str(l.get(c) or "") for c in ("descricao", "empresa", "imputacao", "dia"))
                            + " " + valores)

    return [l for l in linhas if all(p in _texto(l) for p in palavras)]


def tabela_extratos_colorida(linhas: list, cor_por_imputacao: dict, legenda_vazio: str):
    """Tabela de linhas do Mapa por baixo do gráfico circular, cada linha
    pintada com a mesma cor da fatia da sua imputação (cor_por_imputacao,
    devolvido por grafico_pizza_imputacoes) - imputações sem cor atribuída
    (nenhuma linha caiu nessa categoria no gráfico) usam a cor de "Outras".

    Inclui linhas pendentes (confirmado=False, valor = previsto - ver
    listar_linhas_imputacao) além das já confirmadas no extrato, com a
    coluna "Estado" a distingui-las - o gráfico circular acima só soma as
    confirmadas, por isso o total desta tabela pode ser maior."""
    if not linhas:
        st.info(legenda_vazio)
        return

    df = pd.DataFrame(linhas)
    if "descricao" not in df:  # API ainda sem a descrição (versão anterior)
        df["descricao"] = None
    df = df[["dia", "empresa", "imputacao", "descricao", "valor", "confirmado"]].sort_values("dia", ascending=False)
    df["estado"] = df["confirmado"].map({True: "Confirmado", False: "Pendente"})
    df = df.drop(columns="confirmado")

    def _pintar_linha(linha):
        cor = cor_por_imputacao.get(linha["imputacao"], COR_IMPUTACAO_OUTRAS)
        return [f"background-color: {cor}33"] * len(linha)

    estilo = df.style.apply(_pintar_linha, axis=1).format({"valor": lambda v: f"{_n(v, 2)} €"})
    st.dataframe(
        estilo,
        width="stretch",
        hide_index=True,
        column_config={
            "dia": st.column_config.TextColumn("Dia"),
            "empresa": st.column_config.TextColumn("Empresa"),
            "imputacao": st.column_config.TextColumn("Imputação"),
            "descricao": st.column_config.TextColumn("Descrição"),
            "valor": st.column_config.TextColumn("Valor"),
            "estado": st.column_config.TextColumn("Estado"),
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


st.set_page_config(page_title="Predição Financeira & Tesouraria", page_icon="💶", layout="wide")

# Cabeçalho discreto de BI executivo: barra escura, título em branco, um
# único traço vermelho fino como assinatura de marca - o vermelho fica
# reservado a alertas no resto da interface (ver COR_ERRO,
# COR_PAGAMENTOS_MES), não usado como cor de UI genérica.
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
    /* flex-wrap: com 8 separadores em maiúsculas a lista deixa de caber
       na largura do ecrã e o Streamlit faz scroll horizontal sem barra
       visível - os últimos separadores ficavam escondidos. */
    .stTabs [data-baseweb="tab-list"] {
        gap: 4px; background: #ffffff; border-radius: 10px 10px 0 0;
        border-bottom: 1px solid #e4e6ea; padding: 4px 8px 0 8px;
        box-shadow: 0 1px 3px rgba(0,0,0,0.05);
        flex-wrap: wrap; overflow: visible;
    }
    .stTabs [data-baseweb="tab-highlight"], .stTabs [data-baseweb="tab-border"] { display: none; }
    .stTabs [data-baseweb="tab"] { height: auto; padding: 10px 14px; }
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

def _n(valor, casas: int = 0, sinal: bool = False) -> str:
    """Número no formato português: 170 249 / 1 234,56 / +7 885 (espaço
    inquebrável nos milhares, vírgula decimal) - as tabelas já mostravam
    assim e os KPIs/legendas em inglês (170,249), na mesma página."""
    texto = format(valor, f"{'+' if sinal else ''},.{casas}f")
    return texto.replace(",", " ").replace(".", ",")


def _resumo_atualizacao(resultado: dict) -> str:
    """"Importados: movimentos de 03/10 e 06/10; saldos de 05/10." em vez das
    listas de dias em bruto que a API devolve."""
    def dias(chave):
        lista = sorted(resultado.get(chave) or [])
        return " e ".join(", ".join(f"{pd.to_datetime(d):%d/%m}" for d in lista).rsplit(", ", 1))
    partes = [f"{nome} de {dias(chave)}" for chave, nome in (
        ("dias_com_movimentos_novos", "movimentos"), ("dias_com_saldos_novos", "saldos"),
        ("dias_com_mapa_novo", "Mapa de Pagamentos e Recebimentos"),
    ) if resultado.get(chave)]
    return "Importados: " + "; ".join(partes) + "." if partes else "Já estava tudo atualizado."


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
                _limpar_cache_previsoes()
            # notificação que desaparece sozinha - a faixa fixa ocupava o topo de todos os separadores
            st.toast(_resumo_atualizacao(resultado), icon="🔄")
            if resultado.get("erros"):
                st.warning(f"Erros de sincronização: {resultado['erros']}")
        except Exception as e:
            st.warning(f"Não foi possível atualizar ao abrir a dashboard: {e}")
    st.session_state.startup_sync_done = True

col_titulo, col_botao = st.columns([4, 1])
with col_botao:
    if st.button("🔄 Atualizar dados do OneDrive", width="stretch"):
        with st.spinner("A importar dias novos do OneDrive (só leitura)..."):
            try:
                resultado = api.atualizar_dados()
                novos = (
                    len(resultado["dias_com_movimentos_novos"])
                    + len(resultado["dias_com_saldos_novos"])
                    + len(resultado["dias_com_mapa_novo"])
                )
                if novos > 0:
                    _limpar_cache_previsoes()
                st.toast(_resumo_atualizacao(resultado), icon="🔄")
                if resultado["erros"]:
                    st.warning(f"Erros: {resultado['erros']}")
                st.rerun()
            except Exception as e:
                st.error(f"Erro: {e}")

(
    aba_visao_geral, aba_monitorizacao, aba_faturas, aba_saldos,
    aba_analise_extratos, aba_forecast,
) = st.tabs(
    ["Visão Geral", "Monitorização", "Faturas", "Saldos", "Análise de Extratos", "Predição"],
    # Separadores dinâmicos: só o aberto corre (ver `if aba_*.open:` abaixo).
    # Antes, cada clique em qualquer separador voltava a correr os seis - com
    # todos os gráficos e pedidos à API - e a página ficava lenta.
    key="aba_principal", on_change="rerun",
)

@st.cache_data(ttl=3600, show_spinner="A correr o backtest da previsão...")
def _backtest_em_cache(empresa, dias: int) -> dict:
    """O backtest repete a previsão 20 vezes - guardado 1h para não o
    recalcular a cada interação com os filtros do painel."""
    return api.backtest_previsao_ancorada(empresa, dias, 20)


TIPOS_FLUXO_CONHECIDO = {
    "renda": "Renda", "recorrente": "Recorrente", "regular": "Regular (média)", "mapa": "Mapa (planeado)",
    "comercial": "Comercial",
}


def _secao_fiabilidade(empresa, horizonte: int):
    st.caption(
        "A previsão é repetida a partir de 20 datas passadas (uma por semana), só com "
        "os dados que existiam nesse dia, e comparada com o saldo real ao fim do "
        "horizonte. A referência é o palpite mais simples: \"o saldo fica igual\"."
    )
    try:
        bt = _backtest_em_cache(empresa, horizonte)
    except Exception as e:
        st.warning(f"Backtest indisponível: {e}")
        return
    if not bt["cortes"]:
        st.info("Histórico ainda curto para um backtest neste horizonte.")
        return
    b1, b2, b3, b4 = st.columns(4)
    b1.metric("Erro médio da previsão", f"{_n(bt['erro_medio_modelo'])} €")
    b2.metric("Erro médio \"saldo fica igual\"", f"{_n(bt['erro_medio_sem_alteracao'])} €")
    b3.metric("Erro com recebimentos do comercial", f"{_n(bt['erro_medio_com_comercial'])} €")
    b4.metric(
        "Real dentro da banda",
        f"{bt['cobertura_banda']:.0%}" if bt["cobertura_banda"] is not None else "—",
        help="Com uma banda de ~80%, o esperado é o real cair dentro dela em cerca de 8 de cada 10 cortes.",
    )
    if bt["erro_medio_modelo"] > bt["erro_medio_sem_alteracao"] * 1.02:
        st.warning(
            "Neste horizonte a previsão erra mais do que assumir que o saldo fica igual - "
            "lê sobretudo a banda e a lista de fluxos conhecidos."
        )
    else:
        st.info(
            "A previsão acerta pelo menos tanto como \"o saldo fica igual\". O erro que "
            "sobra vem dos movimentos grandes que não estão planeados em lado nenhum "
            "(mútuos intragrupo, pagamentos de obra, escrituras fora da data) - só lançá-los "
            "no Mapa com data futura o reduz."
        )
    st.dataframe(
        pd.DataFrame([{
            "Previsto a partir de": c["corte"], "Para o dia": c["fim"],
            "Saldo nesse dia": c["saldo_no_corte"], "Previsto": c["previsto"], "Real": c["real"],
            "Erro": c["erro_modelo"], "Dentro da banda": "✔" if c["dentro_banda"] else "✘",
        } for c in bt["cortes"]]),
        hide_index=True, width="stretch",
        column_config={
            col: st.column_config.NumberColumn(format="euro")
            for col in ("Saldo nesse dia", "Previsto", "Real", "Erro")
        },
    )


ENTIDADE_GRUPO = "Grupo (todas as contas)"
HORIZONTES_FC = [7, 14, 30, 60, 90, 180]


def _abrir_empresa_do_ranking(chave_tabela: str, empresas: list):
    """Clicar numa linha do ranking de risco muda a entidade do painel para
    essa empresa (callback: corre antes do rerun, por isso pode mexer no
    estado do selectbox)."""
    linhas = st.session_state[chave_tabela].selection.rows
    if linhas:
        st.session_state["fc_entidade"] = empresas[linhas[0]]


def _cartao_fluxos_proximos(fluxos: list, incluir_comercial: bool):
    visiveis = [f for f in fluxos if incluir_comercial or f["fonte"] != "comercial"]
    por_fonte = {}
    for f in visiveis:
        por_fonte[f["fonte"]] = por_fonte.get(f["fonte"], 0.0) + f["valor"]
    with st.container(border=True):
        st.markdown("**Fluxos já conhecidos**")
        if not visiveis:
            st.info("Nenhum fluxo conhecido no horizonte escolhido.")
            return
        with st.container(horizontal=True):
            for fonte, total in por_fonte.items():
                st.badge(
                    f"{TIPOS_FLUXO_CONHECIDO.get(fonte, fonte)} {_n(total, sinal=True)} €",
                    color="green" if total >= 0 else "red",
                )
        st.dataframe(
            pd.DataFrame([{
                "Dia": pd.to_datetime(f["dia"]).strftime("%d/%m"),
                "Tipo": TIPOS_FLUXO_CONHECIDO.get(f["fonte"], f["fonte"]),
                "Descrição": f["descricao"], "Valor": f["valor"],
            } for f in visiveis]),
            hide_index=True, width="stretch", height=300,
            column_config={
                "Valor": st.column_config.NumberColumn(format="euro"),
                "Descrição": st.column_config.TextColumn(width="large"),
            },
        )
        st.caption(
            "Rendas e recorrentes vêm dos extratos e do Mapa de Rendas; Regular = pagamentos de todos os "
            "meses com valor variável (TSU, AT, fornecedores), pela média mensal; Mapa = linhas com "
            "data futura; Comercial = sinal, reforços e escritura do índice comercial."
        )


def _tempo_de_vida(ritmo):
    """(valor, delta) do KPI "Tempo de vida" - ver previsao_ancorada.ritmo_atual."""
    if not ritmo:
        return "—", None
    vida, por_dia = ritmo["tempo_de_vida"], ritmo["liquido_diario"]
    if vida["dias"] == 0:
        return "esgotado", "saldo já abaixo de −1 000 €"
    if vida["dias"] is None:
        return "sem fim à vista", f"{_n(por_dia, sinal=True)} €/dia"
    if vida["dias"] > 730:
        return "mais de 2 anos", f"{_n(por_dia, sinal=True)} €/dia"
    return f"{vida['dias']} dias", f"até {pd.to_datetime(vida['dia']):%d/%m/%Y} · {_n(por_dia, sinal=True)} €/dia"


def _explicar_ritmo(ritmo, fluxos_previstos: list, dias_horizonte: int) -> str:
    """De onde vem a linha "previsão ao ritmo atual": os fluxos conhecidos
    da previsão + o resto ao ritmo dos últimos 180 dias - e quanto os fluxos
    conhecidos previstos diferem dos que se viram nesse período (é aí que a
    linha mais pode enganar: ex. rendas previstas que não aparecem nos extratos)."""
    if not ritmo:
        return "Menos de 90 dias de histórico de saldo - sem linha ao ritmo atual."
    n = ritmo["janela_dias"]
    desde = f"{pd.to_datetime(ritmo['desde']):%d/%m}"
    if ritmo["fonte"] == "movimentos":
        origem = (f"o resto dos recebimentos e pagamentos ao ritmo dos últimos {n} dias (desde {desde}): "
                  f"{_n(ritmo['resto_diario'], sinal=True)} €/dia.")
    else:
        origem = (f"o resto ao ritmo dos últimos {n} dias (desde {desde}), tirado da variação real do saldo - os "
                  f"extratos não a explicam (movimentos {_n(ritmo['liquido_movimentos'], sinal=True)} € contra saldo "
                  f"{_n(ritmo['variacao_saldo'], sinal=True)} €): {_n(ritmo['resto_diario'], sinal=True)} €/dia.")
    texto = "A laranja: os fluxos já conhecidos da previsão, nos dias em que caem, mais " + origem
    if dias_horizonte:
        por_dia_previsto = sum(f["valor"] for f in fluxos_previstos if f["fonte"] != "comercial") / dias_horizonte
        por_dia_visto = ritmo["conhecidos_no_periodo"] / n
        if abs(por_dia_previsto - por_dia_visto) > max(50, 0.5 * abs(por_dia_visto)):
            texto += (f" Atenção: a previsão conta com {_n(por_dia_previsto, sinal=True)} €/dia de fluxos conhecidos, mas nos "
                      f"últimos {n} dias os extratos só mostraram {_n(por_dia_visto, sinal=True)} €/dia desses fluxos - se a "
                      f"diferença não estiver a entrar (ex. rendas), a linha e o tempo de vida estão otimistas.")
    return texto


def _explicar_indice(indice, ritmo) -> str:
    """Frase do cenário "só vendas do índice" (linha roxa)."""
    n = ritmo["janela_dias"]
    return (f" A roxo: o mesmo sem as vendas dos últimos {n} dias ({_n(indice['vendas_no_periodo'])} € de CPCV/"
            f"escrituras, {_n(indice['vendas_no_periodo'] / n)} €/dia), com só as vendas marcadas no índice comercial "
            f"nas datas marcadas ({_n(indice['comercial_no_horizonte'])} € neste horizonte) - quanto tempo dura o "
            f"dinheiro se não se assinar mais nenhum negócio.")


def _kpis_painel(fc: dict, horizonte: int, incluir_comercial: bool, risco):
    """Desenha os KPIs e devolve um espaço reservado para o KPI do risco de
    liquidez - esse demora ~20 s a calcular para as 31 empresas e é
    preenchido no fim (ver _kpi_liquidez), para não atrasar o resto."""
    central = fc["previsao"]
    serie_fim = (fc.get("previsao_com_comercial") if incluir_comercial else None) or central
    saldo_atual = fc["saldo_partida"]
    fluxos = fc.get("fluxos_conhecidos_previstos") or []
    comercial_total = sum(f["valor"] for f in fluxos if f["fonte"] == "comercial")
    historico_30 = [p["valor"] for p in fc["historico"][-30:]]

    with st.container(horizontal=True):
        st.metric(
            f"Saldo em {pd.to_datetime(fc['dia_partida']):%d/%m}", f"{_n(saldo_atual)} €",
            f"{_n(saldo_atual - historico_30[0], sinal=True)} € em 30 dias" if historico_30 else None,
            border=True, height="stretch", chart_data=historico_30, chart_type="area",
        )
        st.metric(
            f"Saldo previsto a {horizonte} dias", f"{_n(serie_fim[-1]['valor'])} €",
            f"{_n(serie_fim[-1]['valor'] - saldo_atual, sinal=True)} €", border=True, height="stretch",
            chart_data=[p["valor"] for p in serie_fim], chart_type="line",
            help="Só o que já se sabe (rendas, recorrentes, pagamentos regulares, Mapa com data futura); com "
                 "recebimentos do comercial, se o interruptor estiver ligado."
                 + (f" Metade das vezes o saldo fica entre {_n(fc['banda_50']['baixa'][-1]['valor'])} € e "
                    f"{_n(fc['banda_50']['alta'][-1]['valor'])} € - os movimentos grandes (escrituras, obras, "
                    f"impostos) não se sabem com antecedência." if fc.get("banda_50") else ""),
        )
        vida_valor, vida_delta = _tempo_de_vida(fc.get("ritmo_atual"))
        janela = (fc.get("ritmo_atual") or {}).get("janela_dias", 180)
        indice = (fc.get("ritmo_atual") or {}).get("so_vendas_do_indice") if incluir_comercial else None
        st.metric(
            "Tempo de vida", vida_valor, vida_delta, delta_color="off", delta_arrow="off", border=True, height="stretch",
            help="Quantos dias até o saldo ficar abaixo de −1 000 € (o mesmo limite do risco de liquidez), "
                 "seguindo a linha laranja: os fluxos já conhecidos da previsão mais o resto dos recebimentos e "
                 f"pagamentos ao ritmo dos últimos {janela} dias. Calculado a 1 ano, por isso não depende do horizonte.",
        )
        if indice:
            vida_indice_valor, vida_indice_delta = _tempo_de_vida(indice)
            st.metric(
                "Tempo de vida · só vendas do índice", vida_indice_valor, vida_indice_delta, delta_color="off",
                delta_arrow="off", border=True, height="stretch",
                help=f"O mesmo, mas sem as vendas (CPCV/escrituras) dos últimos {janela} dias no ritmo e com só os "
                     "sinais, reforços e escrituras marcados no índice comercial, nas datas marcadas - quanto "
                     "tempo dura o dinheiro se não se assinar mais nenhum negócio. Linha roxa no gráfico.",
            )
            # (o "Intervalo provável", de -531 k a +956 k € no grupo, saiu: confundia
            # mais do que informava - fica no gráfico, com o interruptor)
            st.metric(
                "Recebimentos do comercial", f"{_n(comercial_total)} €", border=True, height="stretch",
                help="Sinais, reforços e escrituras marcados no índice comercial dentro do horizonte.",
            )
        if risco is not None:
            st.metric(
                "Zona de risco", ZONAS_ROTULO[risco["zona_atual"]],
                (f"prevista: {ZONAS_ROTULO[risco['zona_prevista']]}" if risco["zona_prevista"] != risco["zona_atual"] else None),
                delta_color="inverse", delta_arrow="off", border=True, height="stretch",
                help="Crítico: o saldo cobre menos de 1 semana de despesa média; alerta: menos de 1 mês.",
            )
        lugar_liquidez = st.empty()
        lugar_liquidez.metric("Risco de liquidez", "a calcular…", border=True, height="stretch", help=AJUDA_LIQUIDEZ)
    return lugar_liquidez


def _kpi_liquidez(lugar, liquidez_fc: list, empresa):
    if empresa:
        # uma empresa: a probabilidade dela
        r = next((x for x in liquidez_fc if x["empresa"] == empresa), None)
        if r is None:
            lugar.metric("Risco de liquidez", "—", border=True, help=AJUDA_LIQUIDEZ)
            return
        p = r.get("probabilidade_negativo")
        lugar.metric(
            "Risco de liquidez", NIVEL_LIQUIDEZ_ROTULO.get(r["nivel"], r["nivel"]),
            f"{p:.0%} de ficar a descoberto" if p is not None else None, delta_color="off", delta_arrow="off",
            border=True, height="stretch",
            help=AJUDA_LIQUIDEZ,
        )
    else:
        # o grupo: quantas empresas em risco alto/moderado
        em_risco = [x for x in liquidez_fc if x["nivel"] in ("alto", "moderado")]
        altos = sum(1 for x in em_risco if x["nivel"] == "alto")
        lugar.metric(
            "Empresas com risco de liquidez", f"{len(em_risco)}",
            f"{altos} em risco alto" if altos else None, delta_color="inverse", delta_arrow="off", border=True,
            height="stretch",
            help=AJUDA_LIQUIDEZ,
        )


ZONAS_ROTULO = {"critico": "🔴 Crítico", "alerta": "🟡 Alerta", "ok": "🟢 Saudável"}
NIVEL_LIQUIDEZ_ROTULO = {
    "alto": "🔴 Alto", "moderado": "🟡 Moderado", "baixo": "🟢 Baixo",
    "sem leitura recente": "⚪ Sem leitura recente", "sem dados": "⚪ Sem dados",
}
AJUDA_LIQUIDEZ = (
    "Probabilidade de o saldo ficar a descoberto (abaixo de -1 000 €) nalgum dia do horizonte, nas 1000 "
    "trajetórias simuladas da previsão - SEM contar com mútuos/reforços entre empresas do grupo, ou seja, "
    "se a empresa aguenta sozinha. Alto ≥ 50%, moderado ≥ 20%."
)


def _tabela_liquidez(linhas: list) -> pd.DataFrame:
    return pd.DataFrame([{
        "Empresa": r["empresa"],
        "Risco": NIVEL_LIQUIDEZ_ROTULO.get(r["nivel"], r["nivel"]),
        "Prob. de descoberto": r["probabilidade_negativo"],
        "1.º dia provável": r["primeiro_dia_provavel"] or "",
        "Saldo atual": r["saldo_atual"],
        "Saldo previsto": r["saldo_previsto_fim"],
        "Pior caso (fim)": r["saldo_pior_caso_fim"],
    } for r in linhas])


COLUNAS_TABELA_LIQUIDEZ = {
    "Prob. de descoberto": st.column_config.ProgressColumn(format="percent", min_value=0, max_value=1),
    "Saldo atual": st.column_config.NumberColumn(format="euro"),
    "Saldo previsto": st.column_config.NumberColumn(format="euro"),
    "Pior caso (fim)": st.column_config.NumberColumn(
        format="euro", help="Percentil 10 das trajetórias no fim do horizonte, sem reforços intragrupo."),
}
COLUNAS_TABELA_RISCO = {
    "Saldo atual": st.column_config.NumberColumn(format="euro"),
    "Saldo previsto": st.column_config.NumberColumn(format="euro"),
    "Ritmo de caixa (€/dia)": st.column_config.NumberColumn(format="%+,.0f"),
}


def _tabela_risco(linhas: list) -> pd.DataFrame:
    return pd.DataFrame([{
        "Empresa": r["empresa"],
        "Zona atual": ZONAS_ROTULO[r["zona_atual"]],
        "Zona prevista": ZONAS_ROTULO[r["zona_prevista"]] + (f" (a partir de {r['dia_risco']})" if r["dia_risco"] else ""),
        "Saldo atual": r["saldo_atual"],
        "Saldo previsto": r["saldo_previsto_fim"],
        "Ritmo de caixa (€/dia)": r["taxa_diaria_liquida"],
        "Autonomia (tendência)": (
            f"{r['dias_autonomia_tendencia']:.0f} dias" if r["dias_autonomia_tendencia"] is not None else "sem risco de esgotar"
        ),
        "Autonomia (pior caso)": (
            f"{r['dias_autonomia_despesa']:.0f} dias" if r["dias_autonomia_despesa"] is not None else "sem despesa registada"
        ),
    } for r in linhas])



SERIE_HISTORICO_FC = "Saldo real"
# a série central só soma o que já se sabe (rendas, recorrentes, Mapa com
# data futura) - "Previsão" fazia parecer que era o saldo esperado, e por
# isso a linha depois de "Hoje" (quase plana, aos degraus) confundia
SERIE_MEDIA_FC = "Só fluxos já conhecidos"
SERIE_RITMO_FC = "Previsão ao ritmo atual"
SERIE_INDICE_FC = "Só vendas do índice"
# 2.º slot categórico validado (o 1.º, azul, é o saldo real)
COR_RITMO_FC = "#eb6834"
# slot categórico validado (o do Gradient Boosting, que não aparece no Forecast)
COR_INDICE_FC = "#4a3aa7"
SERIE_TRAJETORIA_FC = "Trajetória possível"
SERIE_INTERVALO_FC = "Intervalo provável (80%)"
SERIE_INTERVALO_50_FC = "Metade das vezes (50%)"
COR_TRAJETORIA_FC = "#9aa3ad"


def _regra_hoje(dia_iso: str):
    """Linha vertical "Hoje" a separar o real do previsto."""
    df = pd.DataFrame({"dia": [dia_iso], "rotulo": ["Hoje"]})
    regra = alt.Chart(df).mark_rule(color="#1c1f26", strokeDash=[2, 2], strokeWidth=1).encode(x="dia:T")
    texto = alt.Chart(df).mark_text(align="left", dx=4, dy=-6, fontSize=11, color="#1c1f26").encode(
        x="dia:T", y=alt.value(0), text="rotulo:N",
    )
    return regra + texto


def grafico_saldo_forecast(historico_pontos, previsao_por_modelo, banda, trajetorias, y_titulo="Saldo total (EUR)",
                           ritmo=None, dia_esgota=None, ritmo_indice=None, dia_esgota_indice=None,
                           nome_banda=SERIE_INTERVALO_FC):
    """Saldo real + previsão, com UMA legenda para tudo o que está no
    gráfico (real, fluxos conhecidos, ritmo atual, trajetórias, intervalo),
    cores distintas e a linha "Hoje". `ritmo` é a reta "ao ritmo atual"
    (últimos 180 dias) e `dia_esgota` o dia em que ela passa abaixo do limite de
    descoberto (marcado se cair dentro do horizonte). `ritmo_indice` /
    `dia_esgota_indice`: o cenário "só vendas do índice" comercial."""
    ultimo = historico_pontos[-1] if historico_pontos else None
    ligar = [ultimo] if ultimo else []

    linhas = [{"dia": p["dia"], "valor": p["valor"], "serie": SERIE_HISTORICO_FC, "grupo": "h"} for p in historico_pontos]
    for n, trajetoria in enumerate(trajetorias or []):
        linhas.extend(
            {"dia": p["dia"], "valor": p["valor"], "serie": SERIE_TRAJETORIA_FC, "grupo": f"t{n}"}
            for p in ligar + list(trajetoria)
        )
    for modelo, pontos in previsao_por_modelo.items():
        nome = SERIE_MEDIA_FC if modelo == "ensemble" else NOMES_MODELO.get(modelo, modelo)
        linhas.extend({"dia": p["dia"], "valor": p["valor"], "serie": nome, "grupo": modelo} for p in ligar + list(pontos))
    if ritmo:
        linhas.extend({"dia": p["dia"], "valor": p["valor"], "serie": SERIE_RITMO_FC, "grupo": "ritmo"}
                      for p in ligar + list(ritmo))
    if ritmo_indice:
        linhas.extend({"dia": p["dia"], "valor": p["valor"], "serie": SERIE_INDICE_FC, "grupo": "indice"}
                      for p in ligar + list(ritmo_indice))
    df = pd.DataFrame(linhas)

    # a linha "só fluxos já conhecidos" é referência (cinzenta, fina) - a
    # laranja ao lado ficava indistinguível do rosa/vermelho anterior
    cores = {
        SERIE_HISTORICO_FC: CORES_PREVISAO["Histórico"],
        SERIE_RITMO_FC: COR_RITMO_FC,
        SERIE_INDICE_FC: COR_INDICE_FC,
        SERIE_MEDIA_FC: COR_REFERENCIA_PLANO,
        SERIE_TRAJETORIA_FC: COR_TRAJETORIA_FC,
    }
    for serie in df["serie"].unique():
        if serie not in cores:
            cores[serie] = CORES_PREVISAO.get(serie, COR_REFERENCIA_PLANO)
    # só as séries desenhadas vão para a legenda (sem trajetórias, não há
    # "Trajetória possível")
    cores = {s: c for s, c in cores.items() if s in set(df["serie"])}
    escala = alt.Scale(domain=list(cores), range=list(cores.values()))
    # linhas com símbolo de traço (não círculo) e opacidade total na
    # legenda; o intervalo tem legenda própria, com símbolo de área - senão
    # a transparência da área passava para a legenda e "Previsão média" e
    # "Intervalo" ficavam com o mesmo rosa claro.
    legenda = alt.Legend(
        title=None, orient="top", direction="horizontal",
        symbolType="stroke", symbolStrokeWidth=3, symbolOpacity=1, symbolSize=300,
    )
    selecao = alt.selection_point(fields=["serie"], bind="legend")
    escala_intervalo = alt.Scale(domain=[nome_banda], range=[COR_REFERENCIA_PLANO])
    legenda_intervalo = alt.Legend(
        title=None, orient="top", direction="horizontal", symbolType="square", symbolOpacity=0.3, symbolSize=200,
    )

    espessura = {SERIE_HISTORICO_FC: 2.5, SERIE_RITMO_FC: 3, SERIE_INDICE_FC: 3, SERIE_MEDIA_FC: 1.5,
                 SERIE_TRAJETORIA_FC: 1}
    tracejado = {SERIE_MEDIA_FC: [6, 3], SERIE_RITMO_FC: [6, 3], SERIE_INDICE_FC: [6, 3]}
    df["espessura"] = df["serie"].map(lambda s: espessura.get(s, 1.5))
    df["tracejado"] = df["serie"].map(lambda s: "sim" if s in tracejado or s not in espessura else "nao")

    camadas = []
    if banda:
        pontos_banda = [{"dia": ultimo["dia"], "baixa": ultimo["valor"], "alta": ultimo["valor"]}] if ultimo else []
        pontos_banda += [
            {"dia": b["dia"], "baixa": b["valor"], "alta": a["valor"]} for b, a in zip(banda["baixa"], banda["alta"])
        ]
        df_banda = pd.DataFrame(pontos_banda).assign(faixa=nome_banda)
        camadas.append(alt.Chart(df_banda).mark_area(opacity=0.13).encode(
            x="dia:T", y="baixa:Q", y2="alta:Q",
            fill=alt.Fill("faixa:N", scale=escala_intervalo, legend=legenda_intervalo),
            tooltip=[
                "dia:T",
                alt.Tooltip("baixa:Q", title="Mínimo provável", format=",.0f"),
                alt.Tooltip("alta:Q", title="Máximo provável", format=",.0f"),
            ],
        ))
    camadas.append(alt.Chart(df).mark_line().encode(
        x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
        y=alt.Y("valor:Q", title=y_titulo, axis=alt.Axis(format=",.0f")),
        color=alt.Color("serie:N", scale=escala, legend=legenda),
        detail="grupo:N",
        strokeWidth=alt.StrokeWidth("espessura:Q", legend=None, scale=None),
        strokeDash=alt.StrokeDash(
            "tracejado:N", scale=alt.Scale(domain=["nao", "sim"], range=[[1, 0], [6, 3]]), legend=None,
        ),
        # clicar num item da legenda destaca essa série (as outras esbatem) -
        # interação só no browser, não faz a página voltar a correr.
        opacity=alt.condition(selecao, alt.value(1), alt.value(0.12)),
        tooltip=["dia:T", "serie:N", alt.Tooltip("valor:Q", title="Saldo", format=",.0f")],
    ).add_params(selecao))
    if ultimo:
        camadas.append(_regra_hoje(ultimo["dia"]))
    fim_horizonte = max((p["dia"] for p in (ritmo or ritmo_indice or [])), default=None)
    marcas = [(dia_esgota, "Esgota-se", COR_RITMO_FC, -8), (dia_esgota_indice, "Só índice: esgota-se", COR_INDICE_FC, -24)]
    for dia, texto, cor, dy in marcas:
        if not (dia and fim_horizonte and dia <= fim_horizonte):
            continue
        df_esgota = pd.DataFrame({"dia": [dia], "rotulo": [f"{texto} ~{pd.to_datetime(dia):%d/%m}"]})
        camadas.append(alt.Chart(df_esgota).mark_rule(color=cor, strokeDash=[4, 3], strokeWidth=1.5).encode(x="dia:T"))
        # em baixo, para não colidir com o rótulo "Hoje" quando o dia está perto
        camadas.append(alt.Chart(df_esgota).mark_text(
            align="left", dx=4, dy=dy, fontSize=11, fontWeight="bold", color=cor,
        ).encode(x="dia:T", y=alt.value(360), text="rotulo:N"))
    camadas.append(
        alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color=COR_ERRO, strokeWidth=1, opacity=0.5).encode(y="y:Q")
    )
    return alt.layer(*camadas).resolve_legend(fill="independent", color="independent").properties(height=360)


SEMANAS_HISTORICO_CF = 12


def grafico_cashflow_semanal(historico_cf: list, semanal_previsto: list):
    """Recebimentos (para cima) e pagamentos (para baixo) por semana, reais
    e previstos, com o líquido da semana como ponto - lido diretamente dos
    extratos no passado e das simulações (dias reais sorteados) no futuro,
    em vez de uma linha diária de modelos de média que fica colada ao zero."""
    df_hist = pd.DataFrame(historico_cf)
    df_hist["dia"] = pd.to_datetime(df_hist["dia"])
    df_hist["semana"] = df_hist["dia"] - pd.to_timedelta(df_hist["dia"].dt.weekday, unit="D")
    semanal_real = (
        df_hist.groupby("semana", as_index=False)
        .agg(recebimentos=("recebimentos", "sum"), pagamentos=("pagamentos", "sum"), dias=("dia", "count"))
        .tail(SEMANAS_HISTORICO_CF)
    )
    semanal_real["tipo"] = "Real"

    df_prev = pd.DataFrame(semanal_previsto)
    df_prev["semana"] = pd.to_datetime(df_prev["semana"])
    df_prev["tipo"] = "Previsto"
    # semana que já começou no histórico e acaba no previsto: junta as duas
    # partes numa só barra, para não haver duas barras na mesma semana.
    sobreposta = df_prev["semana"].isin(semanal_real["semana"])
    for _, linha in df_prev[sobreposta].iterrows():
        real = semanal_real["semana"] == linha["semana"]
        semanal_real.loc[real, ["recebimentos", "pagamentos"]] += [linha["recebimentos"], linha["pagamentos"]]
        semanal_real.loc[real, "tipo"] = "Real + previsto"
    df_prev = df_prev[~sobreposta]

    colunas = ["semana", "recebimentos", "pagamentos", "tipo", "dias"]
    df = pd.concat([semanal_real[colunas], df_prev[colunas]], ignore_index=True)
    df["liquido"] = df["recebimentos"] - df["pagamentos"]
    df["rotulo_semana"] = df.apply(
        lambda r: r["semana"].strftime("%d/%m") + ("" if r["dias"] >= 7 else f" ({r['dias']}d)"), axis=1,
    )
    ordem = list(df["rotulo_semana"])

    barras = pd.concat([
        df.assign(componente="Recebimentos", valor=df["recebimentos"]),
        df.assign(componente="Pagamentos", valor=-df["pagamentos"]),
    ])
    cores = {"Recebimentos": COR_RECEBIMENTOS_MES, "Pagamentos": COR_PAGAMENTOS_MES, "Líquido da semana": "#1c1f26"}
    escala = alt.Scale(domain=list(cores), range=list(cores.values()))
    legenda = alt.Legend(title=None, orient="top", direction="horizontal")
    eixo_x = alt.X("rotulo_semana:N", sort=ordem, title="Semana (início)", axis=alt.Axis(labelAngle=-45))

    grafico_barras = alt.Chart(barras).mark_bar(size=16).encode(
        x=eixo_x,
        y=alt.Y("valor:Q", title="EUR por semana", axis=alt.Axis(format=",.0f")),
        color=alt.Color("componente:N", scale=escala, legend=legenda),
        opacity=alt.Opacity(
            "tipo:N", scale=alt.Scale(domain=["Real", "Real + previsto", "Previsto"], range=[1, 0.75, 0.45]),
            legend=None,
        ),
        tooltip=[
            alt.Tooltip("rotulo_semana:N", title="Semana"), "tipo:N", "componente:N",
            alt.Tooltip("valor:Q", format=",.0f"),
        ],
    )
    df_liquido = df.assign(componente="Líquido da semana")
    pontos = alt.Chart(df_liquido).mark_point(filled=True, size=55).encode(
        x=eixo_x, y="liquido:Q", color=alt.Color("componente:N", scale=escala, legend=legenda),
        tooltip=[alt.Tooltip("rotulo_semana:N", title="Semana"), "tipo:N", alt.Tooltip("liquido:Q", title="Líquido", format=",.0f")],
    )
    linha_liquido = alt.Chart(df_liquido).mark_line(color="#1c1f26", strokeWidth=1, opacity=0.6).encode(
        x=eixo_x, y="liquido:Q",
    )
    df_intervalo = pd.DataFrame(semanal_previsto)
    df_intervalo["semana"] = pd.to_datetime(df_intervalo["semana"])
    df_intervalo = df_intervalo.merge(df[["semana", "rotulo_semana", "tipo"]], on="semana")
    df_intervalo = df_intervalo[df_intervalo["tipo"] == "Previsto"]
    intervalo = alt.Chart(df_intervalo).mark_rule(color="#1c1f26", strokeWidth=2, opacity=0.7).encode(
        x=eixo_x, y="liquido_baixo:Q", y2="liquido_alto:Q",
        tooltip=[
            alt.Tooltip("rotulo_semana:N", title="Semana"),
            alt.Tooltip("liquido_baixo:Q", title="Líquido mínimo provável", format=",.0f"),
            alt.Tooltip("liquido_alto:Q", title="Líquido máximo provável", format=",.0f"),
        ],
    )
    zero = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#5c6370", strokeWidth=1).encode(y="y:Q")

    # Fundo cinzento + rótulo "Previsão" nas semanas futuras - separa o real
    # do previsto sem precisar de uma legenda de opacidade (que só mostrava
    # quadrados pretos sem significado).
    semanas_previstas = df[df["tipo"] == "Previsto"][["rotulo_semana"]].assign(zona="Previsão")
    fundo = alt.Chart(semanas_previstas).mark_bar(color="#e9ecf0", opacity=0.6, width={"band": 1}).encode(
        x=eixo_x, y=alt.value(0), y2=alt.value(360),
    )
    rotulo = alt.Chart(semanas_previstas.head(1)).mark_text(
        align="left", dx=-10, dy=8, fontSize=11, fontWeight="bold", color="#5c6370",
    ).encode(x=eixo_x, y=alt.value(0), text="zona:N")
    return alt.layer(fundo, grafico_barras, zero, intervalo, linha_liquido, pontos, rotulo).properties(height=360)


# Fragmento: mudar o horizonte, a empresa ou o comercial só volta a correr
# o Forecast - sem isto o Streamlit refazia a página inteira (Visão Geral,
# Monitorização, Faturas...) a cada clique.
@st.fragment
def _painel_forecast():
    try:
        empresas_fc = api.listar_empresas()
    except Exception as e:
        st.error(f"Erro a ligar à API: {e}")
        empresas_fc = []
    opcoes_entidade = [ENTIDADE_GRUPO] + empresas_fc
    if st.session_state.get("fc_entidade") not in opcoes_entidade:
        st.session_state["fc_entidade"] = ENTIDADE_GRUPO

    # --- barra de filtros: tudo o que está abaixo reage a estes 3 controlos
    with st.container(border=True, horizontal=True, vertical_alignment="bottom"):
        entidade_fc = st.selectbox("Entidade", opcoes_entidade, persist_state="page", key="fc_entidade", width=380)
        horizonte_fc = st.segmented_control(
            "Horizonte", HORIZONTES_FC, default=30, format_func=lambda d: f"{d} dias",
            persist_state="page", key="horizonte_forecast", required=True,
        )
        incluir_comercial_fc = st.toggle(
            "Com recebimentos do comercial", persist_state="page", key="fc_comercial",
            help="Soma à previsão os sinais, reforços e escrituras marcados no índice comercial. "
                 "Fica desligado por omissão: só as entradas estão marcadas, não as saídas grandes, "
                 "e no backtest somá-las piorou a previsão.",
        )
    empresa_fc = None if entidade_fc == ENTIDADE_GRUPO else entidade_fc

    try:
        fc = api.previsao_ancorada(empresa_fc, horizonte_fc)
    except Exception as e:
        st.warning(f"Previsão de saldo indisponível: {e}")
        fc = None
    # Só a previsão (~2 s) é pedida antes de desenhar: o risco de liquidez
    # (~20 s) e o ranking por cobertura (~40 s) eram pedidos aqui e cada
    # mudança de horizonte deixava a página ~1 min a mostrar os números do
    # horizonte anterior ("aparece sempre o 30 dias"). A zona de risco de
    # uma empresa vem de um pedido só para ela.
    risco_fc = None
    if empresa_fc:
        try:
            risco_fc = api.previsao_risco(empresa_fc, horizonte_fc)
        except Exception:
            risco_fc = None

    if fc:
        # --- KPIs
        lugar_kpi_liquidez = _kpis_painel(fc, horizonte_fc, incluir_comercial_fc, risco_fc)
        if risco_fc and risco_fc["zona_prevista"] != risco_fc["zona_atual"] and risco_fc["dia_risco"]:
            st.warning(
                f"⚠️ A previsão aponta para entrar em zona \"{risco_fc['zona_prevista']}\" "
                f"a partir de {risco_fc['dia_risco']}."
            )

        # --- gráfico principal + fluxos conhecidos
        col_grafico, col_fluxos = st.columns([2, 1])
        with col_grafico, st.container(border=True):
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"**Saldo real e previsto · {entidade_fc}**")
                mostrar_intervalo = st.toggle(
                    "Intervalo de 80%", value=False, persist_state="page", key="fc_intervalo",
                    help="Por omissão a faixa mostra onde caem metade (50%) das 1000 simulações à volta dos fluxos "
                         "já conhecidos. Ligado, mostra onde caem 80% - no grupo é muito mais larga (a 30 dias, "
                         "~-0,5 a +0,8 M€), porque o saldo mexe com movimentos grandes que ninguém sabe com antecedência.",
                )
                mostrar_trajetorias = st.toggle(
                    "Mostrar trajetórias possíveis", value=False, persist_state="page", key="fc_trajetorias",
                    help="4 das 1000 simulações (linhas cinzentas), para ver como o saldo pode oscilar dia a dia.",
                )
            # com o comercial ligado, o cenário "só vendas do índice" substitui a
            # antiga linha "central + comercial" (o índice somado por cima da
            # central não é um cenário coerente, e eram 4 linhas depois de Hoje)
            series_fc = {"ensemble": fc["previsao"]}
            ritmo_fc = fc.get("ritmo_atual")
            indice_fc = (ritmo_fc or {}).get("so_vendas_do_indice") if incluir_comercial_fc else None
            st.altair_chart(
                grafico_saldo_forecast(
                    fc["historico"][-90:], series_fc,
                    fc.get("banda_incerteza") if mostrar_intervalo else fc.get("banda_50"),
                    fc.get("trajetorias_exemplo") if mostrar_trajetorias else None, "Saldo (EUR)",
                    ritmo=ritmo_fc["previsao"] if ritmo_fc else None,
                    dia_esgota=ritmo_fc["tempo_de_vida"]["dia"] if ritmo_fc else None,
                    ritmo_indice=indice_fc["previsao"] if indice_fc else None,
                    dia_esgota_indice=indice_fc["tempo_de_vida"]["dia"] if indice_fc else None,
                    nome_banda=SERIE_INTERVALO_FC if mostrar_intervalo else SERIE_INTERVALO_50_FC,
                ),
                width="stretch",
            )
            st.caption(
                "Depois de \"Hoje\", a cinzento: só o que já se sabe (rendas, recorrentes, Mapa com data futura). "
                + _explicar_ritmo(ritmo_fc, fc.get("fluxos_conhecidos_previstos") or [], len(fc["previsao"]))
                + (_explicar_indice(indice_fc, ritmo_fc) if indice_fc else
                   " Liga \"Com recebimentos do comercial\" para ver o cenário só com as vendas marcadas no índice.")
            )
        with col_fluxos:
            _cartao_fluxos_proximos(fc.get("fluxos_conhecidos_previstos") or [], incluir_comercial_fc)

        # --- cash-flow semanal + risco / fluxo diário
        col_cf, col_risco = st.columns(2)
        with col_cf, st.container(border=True):
            st.markdown("**Cash-flow por semana**")
            if fc.get("historico_cashflow") and fc.get("cashflow_semanal_previsto"):
                st.altair_chart(
                    grafico_cashflow_semanal(fc["historico_cashflow"], fc["cashflow_semanal_previsto"]),
                    width="stretch",
                )
                st.caption(
                    "Sólidas: semanas reais. Claras: o que já se sabe para as próximas "
                    "(sem o comercial). Traço: intervalo provável do líquido."
                )
            else:
                st.info("Sem movimentos suficientes para o cash-flow semanal.")
        with col_risco, st.container(border=True):
            if empresa_fc is None:
                st.markdown(f"**Risco de liquidez nos próximos {horizonte_fc} dias**", help=AJUDA_LIQUIDEZ)
                with st.spinner("A calcular o risco de liquidez das empresas (primeira vez para este horizonte ~20 s)…"):
                    try:
                        liquidez_fc = api.previsao_risco_liquidez(horizonte_fc)
                    except Exception as e:
                        st.warning(f"Risco de liquidez indisponível: {e}")
                        liquidez_fc = []
                _kpi_liquidez(lugar_kpi_liquidez, liquidez_fc, None)
                liquidez_em_risco = [r for r in liquidez_fc if r["nivel"] in ("alto", "moderado")]
                if not liquidez_fc:
                    st.info("Sem empresas com histórico de saldo suficiente.")
                else:
                    if not liquidez_em_risco:
                        st.success("Nenhuma empresa com risco alto ou moderado de ficar a descoberto.")
                    st.dataframe(
                        _tabela_liquidez(liquidez_fc),
                        hide_index=True, width="stretch", height=330, column_config=COLUNAS_TABELA_LIQUIDEZ,
                        key="fc_tabela_risco", on_select=lambda: _abrir_empresa_do_ranking(
                            "fc_tabela_risco", [r["empresa"] for r in liquidez_fc],
                        ),
                        selection_mode="single-row",
                    )
                    st.caption(
                        "Probabilidade de ficar abaixo de -1 000 € sem reforços entre empresas do grupo - "
                        "risco alto quer dizer que a empresa depende de reforços para pagar o que costuma "
                        "pagar. Clica numa linha para abrir a empresa."
                    )
            else:
                st.markdown("**Fluxo diário (movimentos)**")
                try:
                    historico_mov_fc = api.historico_movimentos(empresa_fc)
                except Exception as e:
                    st.error(f"Erro: {e}")
                    historico_mov_fc = []
                if historico_mov_fc:
                    df_fluxo_fc = pd.DataFrame(historico_mov_fc).groupby("dia", as_index=False)["valor"].sum()
                    df_fluxo_fc["sinal"] = df_fluxo_fc["valor"].apply(lambda v: "Entradas" if v >= 0 else "Saídas")
                    cores_fluxo = {"Entradas": COR_FLUXO_POSITIVO, "Saídas": COR_FLUXO_NEGATIVO}
                    st.altair_chart(
                        alt.Chart(df_fluxo_fc).mark_bar().encode(
                            x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
                            y=alt.Y("valor:Q", title="EUR (líquido do dia)", axis=alt.Axis(format=",.0f")),
                            color=alt.Color(
                                "sinal:N",
                                scale=alt.Scale(domain=list(cores_fluxo), range=list(cores_fluxo.values())),
                                legend=alt.Legend(title=None, orient="top"),
                            ),
                            tooltip=["dia:T", alt.Tooltip("valor:Q", format=",.2f")],
                        ).properties(height=330),
                        width="stretch",
                    )
                else:
                    st.info("Sem movimentos importados para esta empresa.")
                if risco_fc:
                    autonomia = risco_fc["dias_autonomia_tendencia"]
                    st.caption(
                        f"Ritmo de caixa (30 dias): {_n(risco_fc['taxa_diaria_liquida'], sinal=True)} €/dia · "
                        + (f"aguenta cerca de {autonomia:.0f} dias a este ritmo" if autonomia is not None
                           else "o saldo não está a esgotar-se")
                        + (f" · pior caso (sem receitas): {risco_fc['dias_autonomia_despesa']:.0f} dias"
                           if risco_fc["dias_autonomia_despesa"] is not None else "")
                    )

        # vista de uma empresa: o KPI do risco de liquidez preenche-se no fim
        # (a lista de todas as empresas fica em cache por horizonte)
        if empresa_fc:
            try:
                _kpi_liquidez(lugar_kpi_liquidez, api.previsao_risco_liquidez(horizonte_fc), empresa_fc)
            except Exception:
                lugar_kpi_liquidez.metric("Risco de liquidez", "indisponível", border=True)

        # --- detalhe
        separadores_detalhe = ["Resumo mensal", "Fiabilidade"]
        separadores_detalhe += ["Movimentos"] if empresa_fc else ["Todas as empresas"]
        det_resumo, det_fiabilidade, det_extra = st.tabs(separadores_detalhe)
        with det_resumo:
            resumo_fc = resumo_mensal_previsao({"s": fc["previsao"]}, "s")[["mes", "fim_do_mes"]].rename(
                columns={"fim_do_mes": "Saldo previsto no fim do mês"},
            )
            if fc.get("previsao_com_comercial"):
                resumo_fc = resumo_fc.merge(
                    resumo_mensal_previsao({"c": fc["previsao_com_comercial"]}, "c")[["mes", "fim_do_mes"]].rename(
                        columns={"fim_do_mes": "Com recebimentos do comercial"},
                    ),
                    on="mes", how="left",
                )
            st.dataframe(
                resumo_fc.assign(mes=resumo_fc["mes"].dt.strftime("%m/%Y")).rename(columns={"mes": "Mês"}),
                hide_index=True, width="stretch",
                column_config={
                    c: st.column_config.NumberColumn(format="euro")
                    for c in ("Saldo previsto no fim do mês", "Com recebimentos do comercial")
                },
            )
        with det_fiabilidade:
            # para uma empresa só corre quando pedido (~10 s por empresa)
            if empresa_fc is None or st.toggle("Calcular para esta empresa", persist_state="page", key="fc_backtest_empresa"):
                _secao_fiabilidade(empresa_fc, horizonte_fc)
        with det_extra:
            if empresa_fc:
                if historico_mov_fc:
                    st.dataframe(pd.DataFrame(historico_mov_fc), width="stretch", column_config=COLUNA_VALOR_EUR)
                else:
                    st.info("Sem movimentos importados para esta empresa.")
            elif st.toggle("Calcular o ranking por cobertura da despesa (~40 s)", persist_state="page", key="fc_ranking_cobertura",
                           help="Zona de cada empresa pela cobertura da despesa média mensal (crítico < 1 "
                                "semana, alerta < 1 mês) - complementa o risco de liquidez."):
                with st.spinner("A calcular o ranking…"):
                    try:
                        ranking_fc = api.previsao_risco_ranking(horizonte_fc)
                    except Exception as e:
                        st.error(f"Erro a consultar o ranking de risco: {e}")
                        ranking_fc = []
                if ranking_fc:
                    st.dataframe(
                        _tabela_risco(ranking_fc), hide_index=True, width="stretch",
                        column_config=COLUNAS_TABELA_RISCO,
                    )


def _rodape_resposta(mensagem: dict, chave: str):
    """Por baixo de cada resposta do Assistente: as ferramentas consultadas,
    o aviso do guardrail de números (valores que não vieram de nenhuma
    ferramenta - ver app/services/guardrail_numeros.py) e o 👍/👎."""
    if mensagem.get("ferramentas_usadas"):
        st.caption("🔧 consultou: " + ", ".join(mensagem["ferramentas_usadas"]))
    if mensagem.get("numeros_nao_verificados"):
        st.warning(
            "⚠ Estes valores não aparecem nos dados que o assistente consultou - confirma antes de os usar: "
            + ", ".join(mensagem["numeros_nao_verificados"])
        )
    _feedback_resposta(mensagem, chave)


def _feedback_resposta(mensagem: dict, chave: str):
    """👍/👎 por baixo de cada resposta do Assistente - vai para
    interacoes_assistente e alimenta Monitorização > Qualidade da IA."""
    if mensagem.get("role") != "assistant" or not mensagem.get("id"):
        return
    valor = st.feedback("thumbs", key=f"feedback_chat_{chave}_{mensagem['id']}")
    if valor is not None and valor != mensagem.get("feedback"):
        try:
            api.enviar_feedback_chat(mensagem["id"], util=bool(valor))
            mensagem["feedback"] = valor
        except Exception as e:
            st.caption(f"Não foi possível guardar o feedback: {e}")


@st.fragment
def _painel_assistente(chave: str, altura_conversa=None):
    """O Assistente (chat), no topo da Visão Geral. `chave` dá nome aos
    widgets (para poder ser posto noutro sítio sem colidirem). Fragmento: uma pergunta volta a correr só isto, e
    não a página toda (as respostas do modelo local demoram minutos).
    `altura_conversa`: altura fixa, com scroll, para a conversa não empurrar
    o resto da página para baixo."""
    with st.container(border=True, horizontal=True, vertical_alignment="center"):
        # transparência (AI Act, art. 50.º): quem conversa tem de saber que é
        # uma IA e o que ela pode fazer - ver docs/GOVERNANCA_IA.md
        st.markdown(
            "**Assistente de IA** · respostas geradas por um modelo de linguagem "
            f"local ({os.environ.get('OLLAMA_MODEL_ID', 'qwen2.5:3b')}, via Ollama - os dados não saem desta máquina) "
            "a partir dos dados reais, "
            "com ferramentas só de leitura - nunca reconcilia nem resolve nada sozinho. "
            "Pode errar: confirma os números importantes nos outros separadores."
        )
        if st.button("🗑 Reiniciar conversa", key=f"reiniciar_chat_{chave}"):
            try:
                api.reiniciar_chat()
            except Exception as e:
                st.error(f"Erro: {e}")
            st.session_state["chat_mensagens"] = []
            st.rerun()

    if "chat_mensagens" not in st.session_state:
        st.session_state["chat_mensagens"] = []

    if not st.session_state["chat_mensagens"]:
        st.caption("Exemplos: \"Qual é o saldo total hoje?\" · \"Que empresas estão em zona crítica?\" · "
                   "\"Quanto pagou a J. Pinto em agosto?\"")

    conversa = (st.container(height=altura_conversa) if altura_conversa and st.session_state["chat_mensagens"]
                else st.container())
    with conversa:
        for mensagem in st.session_state["chat_mensagens"]:
            with st.chat_message(mensagem["role"]):
                st.write(mensagem["content"])
                _rodape_resposta(mensagem, chave)

    pergunta = st.chat_input("Pergunta sobre os dados da tesouraria...", key=f"pergunta_chat_{chave}")
    if pergunta:
        st.session_state["chat_mensagens"].append({"role": "user", "content": pergunta})
        with conversa:
            with st.chat_message("user"):
                st.write(pergunta)

            with st.chat_message("assistant"):
                with st.spinner("A pensar... (o modelo local pode demorar até 5 minutos)"):
                    try:
                        resultado = api.perguntar_chat(pergunta)
                        resposta = resultado["resposta"]
                        ferramentas = resultado.get("ferramentas_usadas", [])
                        interacao_id = resultado.get("id")
                        nao_verificados = resultado.get("numeros_nao_verificados", [])
                    except Exception as e:
                        resposta = f"Erro a contactar o assistente: {e}"
                        ferramentas = []
                        interacao_id = None
                        nao_verificados = []
                st.write(resposta)
                mensagem_nova = {
                    "role": "assistant", "content": resposta, "ferramentas_usadas": ferramentas, "id": interacao_id,
                    "numeros_nao_verificados": nao_verificados,
                }
                _rodape_resposta(mensagem_nova, chave)

        st.session_state["chat_mensagens"].append(mensagem_nova)


if aba_forecast.open:
    with aba_forecast:
        _painel_forecast()

if aba_visao_geral.open:
    with aba_visao_geral:
        _painel_assistente("visao_geral", altura_conversa=360)

        with st.container(border=True, horizontal=True, vertical_alignment="bottom"):
            dia_vg = st.date_input("Dia", value=date.today(), persist_state="page", key="dia_visao_geral", width=220)
            st.caption(
                "Extratos CGD do dia escolhido, o mês até esse dia e o saldo de todas as contas. "
                "A previsão está no separador **Predição**."
            )
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
        recebimentos_vg = [m for m in movimentos_vg if m["valor"] > 0]
        pagamentos_vg = [m for m in movimentos_vg if m["valor"] < 0]
        total_recebimentos = sum(m["valor"] for m in recebimentos_vg)
        total_pagamentos = sum(-m["valor"] for m in pagamentos_vg)

        try:
            resumo_todos_os_dias = api.resumo_diario()
        except Exception as e:
            st.error(f"Erro a consultar o resumo diário: {e}")
            resumo_todos_os_dias = []
        resumo_mensal = [r for r in resumo_todos_os_dias if r["dia"].startswith(dia_vg_str[:7])]
        resumo_ate_dia = [r for r in resumo_mensal if r["dia"] <= dia_vg_str]
        balanco_ate_ao_dia = sum(r["recebimentos"] - r["pagamentos"] for r in resumo_ate_dia)

        try:
            totais = api.saldo_total(dia_vg_str)
        except Exception as e:
            st.error(f"Erro a consultar saldo total: {e}")
            totais = None
        try:
            serie_total = api.saldo_serie_total()
        except Exception as e:
            st.error(f"Erro a consultar a evolução do saldo total: {e}")
            serie_total = []
        serie_total_ate_dia = [p for p in serie_total if p["dia"] <= dia_vg_str]

        # --- KPIs
        with st.container(horizontal=True):
            st.metric(
                "Recebimentos do dia", f"{_n(total_recebimentos)} €", f"{len(recebimentos_vg)} movimento(s)",
                delta_color="off", delta_arrow="off", border=True,
                chart_data=[r.get("recebimentos_externos", r["recebimentos"]) for r in resumo_ate_dia] or None, chart_type="bar",
            )
            st.metric(
                "Pagamentos do dia", f"{_n(total_pagamentos)} €", f"{len(pagamentos_vg)} movimento(s)",
                delta_color="off", delta_arrow="off", border=True,
                chart_data=[r.get("pagamentos_externos", r["pagamentos"]) for r in resumo_ate_dia] or None, chart_type="bar",
            )
            st.metric(
                f"Balanço de {dia_vg:%m/%Y} até ao dia", f"{_n(balanco_ate_ao_dia)} €", border=True,
                help="Recebimentos menos pagamentos do extrato bancário, do dia 1 até ao dia escolhido.",
            )
            if totais:
                st.metric(
                    "Saldo contabilístico total", f"{_n(totais['saldo_contabilistico_total'])} €",
                    (f"{_n(totais['saldo_contabilistico_total'] - serie_total_ate_dia[-31]['saldo_contabilistico_total'], sinal=True)} € em 30 dias"
                     if len(serie_total_ate_dia) > 30 else None),
                    border=True,
                    chart_data=[p["saldo_contabilistico_total"] for p in serie_total_ate_dia[-30:]] or None,
                    chart_type="area",
                )
                st.metric("Saldo disponível total", f"{_n(totais['saldo_disponivel_total'])} €", border=True)
                st.metric("Contas incluídas", totais["entidades"], border=True)

        # --- saldo total + top contas
        col_evolucao, col_top = st.columns([3, 2])
        with col_evolucao, st.container(border=True):
            st.markdown("**Saldo bancário de todas as contas juntas**")
            if serie_total:
                df_serie_total_longo = pd.DataFrame(serie_total).melt(
                    id_vars=["dia"], value_vars=["saldo_contabilistico_total", "saldo_disponivel_total"],
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
                st.altair_chart(
                    alt.Chart(df_serie_total_longo).mark_line(strokeWidth=2).encode(
                        x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
                        y=alt.Y("valor:Q", title="EUR", axis=alt.Axis(format=",.0f"), scale=alt.Scale(zero=False)),
                        color=alt.Color(
                            "tipo:N",
                            scale=alt.Scale(domain=list(cores_serie_total), range=list(cores_serie_total.values())),
                            legend=alt.Legend(title=None, orient="top"),
                        ),
                        tooltip=["dia:T", "tipo:N", alt.Tooltip("valor:Q", format=",.2f")],
                    ).properties(height=320),
                    width="stretch",
                )
            else:
                st.info("Sem histórico de saldos suficiente para desenhar a evolução.")
        with col_top, st.container(border=True):
            st.markdown("**Contas com mais saldo**")
            try:
                saldos_atuais = api.listar_saldos_atuais(dia_vg_str)
            except Exception:
                saldos_atuais = []
            if saldos_atuais:
                df_ranking = pd.DataFrame(saldos_atuais).sort_values("saldo_contabilistico", ascending=False).head(10)
                df_ranking["entidade"] = [rotulo_conta(e, None, None) for e in df_ranking["entidade"]]
                st.altair_chart(
                    alt.Chart(df_ranking).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
                        y=alt.Y("entidade:N", title=None, sort="-x", axis=alt.Axis(labelLimit=180)),
                        x=alt.X("saldo_contabilistico:Q", title="Saldo contabilístico (EUR)", axis=alt.Axis(format=",.0s")),
                        color=alt.value(COR_RANKING_SALDO),
                        tooltip=["entidade:N", alt.Tooltip("saldo_contabilistico:Q", format=",.2f")],
                    ).properties(height=320),
                    width="stretch",
                )
            else:
                st.info("Sem saldos para este dia.")

        # --- mês
        with st.container(border=True):
            with st.container(horizontal=True, vertical_alignment="center"):
                st.markdown(f"**Liquidez por dia (recebimentos − pagamentos) · {dia_vg:%m/%Y}**")
                incluir_internas_vg = st.toggle(
                    "Incluir transferências entre empresas do grupo", value=False, persist_state="page", key="vg_incluir_internas",
                    help="Mútuos e transferências entre contas do grupo: saem de uma empresa e entram noutra, "
                         "por isso contam duas vezes e anulam-se no total do grupo.",
                )
            if resumo_mensal:
                sufixo = "" if incluir_internas_vg else "_externos"
                df_mensal = pd.DataFrame(resumo_mensal)
                df_mensal["dia"] = pd.to_datetime(df_mensal["dia"])
                df_mensal["Recebimentos"] = df_mensal[f"recebimentos{sufixo}"]
                df_mensal["Pagamentos"] = df_mensal[f"pagamentos{sufixo}"]
                df_mensal["Líquido"] = df_mensal["Recebimentos"] - df_mensal["Pagamentos"]
                df_mensal["sinal"] = df_mensal["Líquido"].map(lambda v: "Entrou mais" if v >= 0 else "Saiu mais")
                # uma barra por dia: verde se entrou mais do que saiu, vermelha se saiu mais
                cores_liquidez = {"Entrou mais": COR_RECEBIMENTOS_MES, "Saiu mais": COR_PAGAMENTOS_MES}
                # eixo por dia (ordinal): num eixo de tempo as barras do 1.º e do último
                # dia ficavam cortadas a meio nas margens
                eixo_dia = alt.X("yearmonthdate(dia):O", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45))
                barras_mes = alt.Chart(df_mensal).mark_bar(cornerRadiusEnd=4).encode(
                    x=eixo_dia,
                    y=alt.Y("Líquido:Q", title="Liquidez do dia (EUR)", axis=alt.Axis(format=",.0f")),
                    color=alt.Color("sinal:N", scale=alt.Scale(domain=list(cores_liquidez), range=list(cores_liquidez.values())),
                                    legend=alt.Legend(title=None, orient="top")),
                    tooltip=[
                        alt.Tooltip("dia:T", title="Dia", format="%d/%m"),
                        alt.Tooltip("Líquido:Q", title="Liquidez", format=",.2f"),
                        alt.Tooltip("Recebimentos:Q", format=",.2f"),
                        alt.Tooltip("Pagamentos:Q", format=",.2f"),
                    ],
                )
                zero_mes = alt.Chart(pd.DataFrame({"y": [0]})).mark_rule(color="#5c6370", strokeWidth=1).encode(y="y:Q")
                st.altair_chart((barras_mes + zero_mes).properties(height=280), width="stretch")
                internas_mes = (df_mensal["recebimentos"] - df_mensal["recebimentos_externos"]).sum()
                st.caption(
                    ("Só o que entrou e saiu de fora do grupo: "
                     f"{_n(internas_mes)} € de transferências entre empresas do grupo ficam de fora este mês. "
                     if not incluir_internas_vg else
                     "Inclui as transferências entre empresas do grupo (contam como recebimento numa e pagamento noutra). ")
                    + "Somas do extrato bancário (CGD), não do Mapa - podem divergir num dia ainda não "
                    "conciliado (ver Monitorização > Auditoria)."
                )
            else:
                st.info(f"Sem movimentos importados em {dia_vg:%m/%Y}.")

        # --- movimentos do dia
        col_tab_receb, col_tab_pag = st.columns(2)
        for coluna, titulo, linhas, vazio in (
            (col_tab_receb, f"Recebimentos de {dia_vg:%d/%m}", recebimentos_vg, "Sem recebimentos neste dia."),
            (col_tab_pag, f"Pagamentos de {dia_vg:%d/%m}", pagamentos_vg, "Sem pagamentos neste dia."),
        ):
            with coluna, st.container(border=True):
                st.markdown(f"**{titulo}**")
                if linhas:
                    st.dataframe(
                        pd.DataFrame(linhas)[["empresa", "descricao", "valor"]],
                        width="stretch", hide_index=True, height=min(320, 38 + 35 * len(linhas)),
                        column_config={**COLUNA_VALOR_EUR, "empresa": "Empresa", "descricao": "Descrição"},
                    )
                else:
                    st.info(vazio)

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


def _hora_local(valor) -> str:
    """"2026-10-06T15:20:28Z" (UTC, como a API devolve) -> "06/10 16:20" na
    hora de Lisboa; o que não for uma data fica como veio."""
    if not valor or valor == "-":
        return valor or ""
    try:
        return pd.Timestamp(valor).tz_convert("Europe/Lisbon").strftime("%d/%m %H:%M")
    except (ValueError, TypeError):
        try:
            return pd.Timestamp(valor).tz_localize("UTC").tz_convert("Europe/Lisbon").strftime("%d/%m %H:%M")
        except (ValueError, TypeError):
            return str(valor)


def _linha_log(timestamp, titulo, nivel, mensagem, cor):
    timestamp = _hora_local(timestamp)
    st.markdown(
        f"<div style='margin-bottom:8px;padding:8px 10px;border-left:4px solid {cor};"
        f"background:{cor}0f;border-radius:4px;white-space:pre-wrap;'>"
        f"<strong>{escape(timestamp)}</strong> · <strong>{escape(titulo)}</strong> · "
        f"<span style='color:{cor};font-weight:600'>{escape(nivel.upper())}</span><br/>{escape(mensagem)}</div>",
        unsafe_allow_html=True,
    )


# Scripts com botão "Correr" sempre visível na Monitorização (os outros só
# aparecem quando estão em erro/atrasados).
SCRIPTS_SEMPRE_CORRIVEIS = ["preencher_mapa", "enviar_mapa_smtp"]
ESTADO_PEDIDO = {"pendente": "⏳ à espera do agente do Windows", "iniciado": "▶ lançado", "erro": "❌ não lançado"}
ESTADO_DEPLOY = {"pendente": "⏳ À espera do agente do Windows", "a_correr": "🔄 A instalar",
                 "ok": "✅ Concluído", "erro": "❌ Falhou"}


def _barra_deploy(deploy: dict) -> None:
    total = deploy.get("total_passos") or 0
    passo = deploy.get("passo") or 0
    fracao = 1.0 if deploy["estado"] == "ok" else (passo / total if total else 0.0)
    texto = f"{ESTADO_DEPLOY.get(deploy['estado'], deploy['estado'])} · {deploy.get('mensagem') or ''}"
    st.progress(min(max(fracao, 0.0), 1.0), text=texto)
    if deploy.get("erro"):
        st.error(deploy["erro"])
    st.caption(
        f"Pedido {_hora_local(deploy['pedido_em'])}"
        + (f" · terminado {_hora_local(deploy['terminado_em'])}" if deploy.get("terminado_em") else
           f" · última notícia {_hora_local(deploy['atualizado_em'])}")
    )
    if deploy.get("log"):
        with st.expander("Log do deploy"):
            st.code("\n".join(deploy["log"][-150:]), language=None)


@st.fragment(run_every=3)
def _progresso_deploys() -> None:
    """Atualiza sozinho de 3 em 3 s (só este bloco, não a página toda),
    para a barra andar enquanto o agente do Windows instala."""
    try:
        deploys = api.listar_deploys(limit=10)
    except Exception as e:
        st.warning(f"Não foi possível carregar os deploys: {e}")
        return
    ultimos = {}
    for deploy in deploys:  # mais recentes primeiro
        ultimos.setdefault(deploy["alvo"], deploy)
    if not ultimos:
        st.caption("Ainda não houve deploys a partir do dashboard.")
    for deploy in ultimos.values():
        st.markdown(f"**{deploy['descricao']}**")
        _barra_deploy(deploy)


def _seccao_deploy_scripts() -> None:
    """Deploy dos scripts do Windows (máquina de produção) a partir do
    dashboard, que pode estar noutra máquina: a API grava o pedido e o agente
    do Windows (scripts/agente_pedidos.py) corre os passos de
    scripts/deploy_windows.py, reportando cada um para a barra."""
    with st.container(border=True):
        st.markdown("**Deploy dos scripts do Windows**")
        try:
            alvos = api.listar_alvos_deploy()
        except Exception as e:
            st.warning(f"Não foi possível carregar os alvos de deploy: {e}")
            return
        descricoes = {a["alvo"]: a["descricao"] for a in alvos}
        with st.container(horizontal=True, vertical_alignment="bottom"):
            alvo = st.selectbox("O que instalar", list(descricoes), format_func=descricoes.get,
                                key="alvo_deploy_scripts", width=480)
            with st.popover("🚀 Deploy"):
                st.markdown(
                    f"Instala **{descricoes[alvo]}** na máquina de produção. Os scripts CGD só são "
                    "trocados quando nenhuma extração está a usar o banco, e a extração anual é "
                    "reiniciada no fim."
                )
                if st.button("Fazer deploy agora", key="botao_confirmar_deploy", type="primary"):
                    try:
                        api.pedir_deploy(alvo)
                        st.success("Deploy pedido - o agente do Windows começa dentro de ~15 s.")
                    except Exception as e:
                        st.error(f"Não foi possível pedir o deploy: {e}")
        _progresso_deploys()


def _pedir_corrida(nome_script: str):
    try:
        resposta = api.correr_script(nome_script)
    except Exception as e:
        st.error(f"Não foi possível pedir '{nome_script}': {e}")
        return
    if resposta.get("status") == "pedido":
        st.success(f"Pedido para correr '{nome_script}' registado - o agente do Windows lança-o dentro de "
                   "~15 s. O estado em cima atualiza quando a corrida terminar.")
    else:
        st.success(f"Execução de '{nome_script}' iniciada. O estado atualiza quando terminar.")


def _botoes_correr_scripts(nomes: list):
    """Botões "Correr" da Monitorização. A API corre em Docker e os scripts
    no Windows: o botão grava um pedido e o agente do Windows
    (scripts/agente_pedidos.py) lança o script como a tarefa agendada o
    lança. O envio do Mapa manda o email a sério, por isso pede confirmação."""
    st.caption("Correr agora (no Windows, como a tarefa agendada):")
    with st.container(horizontal=True):
        for nome_script in nomes:
            if nome_script == "enviar_mapa_smtp":
                with st.popover(f"▶ Correr {nome_script}"):
                    st.markdown("Isto **envia o Mapa por email** aos destinatários de sempre, agora.")
                    if st.button("Enviar agora", key=f"botao_correr_{nome_script}", type="primary"):
                        _pedir_corrida(nome_script)
            elif st.button(f"▶ Correr {nome_script}", key=f"botao_correr_{nome_script}"):
                _pedir_corrida(nome_script)

    try:
        pedidos = api.listar_pedidos_corrida(limit=20).get("pedidos", [])
    except Exception:
        pedidos = []
    ultimos = {}
    for pedido in pedidos:  # mais recentes primeiro
        ultimos.setdefault(pedido["script"], pedido)
    linhas = [
        f"`{p['script']}` pedido {_hora_local(p['pedido_em'])}: {ESTADO_PEDIDO.get(p['estado'], p['estado'])}"
        + (f" ({_hora_local(p['iniciado_em'])})" if p.get("iniciado_em") and p["estado"] == "iniciado" else "")
        + (f" - {p['erro']}" if p.get("erro") else "")
        for p in ultimos.values() if p["script"] in nomes
    ]
    if linhas:
        st.caption("Últimos pedidos: " + " · ".join(linhas))


if aba_monitorizacao.open:
    with aba_monitorizacao:
        try:
            scripts = api.listar_monitorizacao_scripts().get("scripts", [])
            erro_scripts = None
        except Exception as e:
            scripts, erro_scripts = [], e
        try:
            logs_recentes_kpi = api.listar_monitorizacao_logs(limit=100).get("logs", [])
        except Exception:
            logs_recentes_kpi = []

        n_ok = sum(1 for s in scripts if s.get("status") == "ok" and not s.get("atrasado"))
        n_erro = sum(1 for s in scripts if s.get("status") == "erro")
        n_atrasado = sum(1 for s in scripts if s.get("atrasado"))
        n_execucoes_erro = sum(1 for l in logs_recentes_kpi if str(l.get("nivel", "")).lower() == "erro")
        n_execucoes = len(logs_recentes_kpi)

        # --- KPIs
        with st.container(horizontal=True, vertical_alignment="center"):
            st.metric("Scripts OK", n_ok, border=True)
            st.metric("Scripts com erro", n_erro, "precisa de atenção" if n_erro else None, delta_color="inverse", border=True)
            st.metric("Atrasados", n_atrasado, "sem execução à hora esperada" if n_atrasado else None, delta_color="inverse", border=True)
            if n_execucoes:
                taxa_sucesso = 100 * (n_execucoes - n_execucoes_erro) / n_execucoes
                st.metric(f"Sucesso (últimas {n_execucoes} execuções)", f"{taxa_sucesso:.0f}%", border=True)
            if st.button("🔄 Atualizar", key="botao_atualizar_estado_scripts"):
                st.rerun()

        # --- estado dos scripts
        with st.container(border=True):
            st.markdown("**Estado dos scripts**")
            if erro_scripts:
                st.warning(f"Não foi possível carregar a monitorização: {erro_scripts}")
            elif scripts:
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
                df_scripts["ultima_execucao"] = df_scripts["ultima_execucao"].map(_hora_local)
                df_scripts["ultima_erro"] = df_scripts["ultima_erro"].fillna("")
                st.dataframe(
                    df_scripts[["nome", "descricao", "hora_execucao", "status_badge", "ultima_execucao", "ultima_erro"]],
                    width="stretch",
                    hide_index=True,
                    column_config={
                        "nome": st.column_config.TextColumn("Script"),
                        "descricao": st.column_config.TextColumn("Descrição"),
                        "hora_execucao": st.column_config.TextColumn("Horas"),
                        "ultima_execucao": st.column_config.TextColumn("Última execução"),
                        "ultima_erro": st.column_config.TextColumn("Último erro"),
                        "status_badge": st.column_config.TextColumn("Estado"),
                    },
                )

                # Scripts sem botão "Correr" (ex.: os da CGD, avaliacao_online)
                # não entram - a API recusaria o pedido.
                corriveis_mask = df_scripts["corrivel"].fillna(True).astype(bool) if "corrivel" in df_scripts.columns else True
                scripts_em_falha = df_scripts.loc[((df_scripts["status"] == "erro") | atrasados_mask) & corriveis_mask, "nome"].tolist()
                _botoes_correr_scripts(SCRIPTS_SEMPRE_CORRIVEIS + [s for s in scripts_em_falha if s not in SCRIPTS_SEMPRE_CORRIVEIS])
            else:
                st.info("Sem dados de execução dos scripts.")

        _seccao_deploy_scripts()

        sep_tempo_real, sep_logs, sep_script, sep_auditoria, sep_ia = st.tabs(
            ["⚡ Erros em tempo real", "📜 Histórico de logs", "🔎 Detalhe por script", "🧾 Auditoria", "🤖 Qualidade da IA"]
        )

        with sep_tempo_real:
            with st.container(horizontal=True, vertical_alignment="center"):
                st.caption(
                    "Eventos [ERRO]/[AVISO] reportados assim que acontecem durante uma corrida "
                    "ainda a decorrer - não é preciso esperar o script terminar para os ver aqui."
                )
                if st.button("Atualizar", key="botao_atualizar_eventos_tempo_real"):
                    st.rerun()
            try:
                eventos = api.listar_monitorizacao_eventos(limit=30).get("eventos", [])
                if eventos:
                    with st.container(height=420):
                        for evento in reversed(eventos):
                            nivel = str(evento.get("nivel", "info")).lower()
                            _linha_log(
                                evento.get("timestamp") or "-", evento.get("script") or "script", nivel,
                                evento.get("mensagem") or "Sem mensagem", COR_ERRO if nivel == "erro" else COR_AMBIGUOS,
                            )
                else:
                    st.info("Sem eventos em tempo real reportados ainda.")
            except Exception as e:
                st.warning(f"Não foi possível carregar os eventos em tempo real: {e}")

        with sep_logs:
            with st.container(horizontal=True, vertical_alignment="bottom"):
                filtrar_por_dia = st.toggle("Filtrar por dia", value=False, persist_state="page", key="filtrar_dia_monitorizacao_logs")
                dia_logs = st.date_input("Dia", value=date.today(), persist_state="page", key="dia_monitorizacao_logs", width=200) if filtrar_por_dia else None
            try:
                logs_lista = api.listar_monitorizacao_logs(
                    limit=20, dia=dia_logs.isoformat() if dia_logs else None,
                ).get("logs", [])
                if logs_lista:
                    with st.container(height=520):
                        for item in reversed(logs_lista):
                            nivel = str(item.get("nivel", "info")).lower()
                            _linha_log(
                                item.get("timestamp") or "-", item.get("script") or "script", nivel,
                                item.get("mensagem") or "Sem mensagem", COR_ERRO if nivel == "erro" else COR_CASADOS,
                            )
                            if item.get("detalhe"):
                                _renderizar_detalhe_tarefas(item["detalhe"])
                else:
                    st.info("Sem logs de execução neste dia." if filtrar_por_dia else "Sem logs de execução.")
            except Exception as e:
                st.warning(f"Não foi possível carregar os logs: {e}")

        with sep_script:
            nomes_scripts = [item.get("nome") for item in scripts if item.get("nome")]
            if not nomes_scripts:
                st.info("Ainda não há scripts registados.")
            else:
                script_escolhido = st.selectbox("Script", nomes_scripts, index=0, persist_state="page", key="script_monitorizacao_detalhe", width=380)
                script_info = next((item for item in scripts if item.get("nome") == script_escolhido), None)
                if script_info:
                    with st.container(horizontal=True):
                        st.metric("Estado", script_info.get("status", "ok").upper(), border=True)
                        st.metric("Última execução", _hora_local(script_info.get("ultima_execucao")) or "Nunca", border=True)
                        st.metric("Hora esperada", script_info.get("hora_execucao") or "—", border=True)
                    if script_info.get("ultima_erro"):
                        st.error(f"**Erro da última execução:**\n\n{script_info['ultima_erro']}")
                    else:
                        st.success("Sem erros na última execução.")

                logs_filtrados = [
                    item for item in logs_recentes_kpi
                    if str(item.get("script", "")).lower() == script_escolhido.lower()
                ]
                if logs_filtrados:
                    st.markdown("**Execuções registadas para este script**")
                    with st.container(height=480):
                        for item in reversed(logs_filtrados):
                            nivel = str(item.get("nivel", "info")).lower()
                            _linha_log(
                                item.get("timestamp") or "-", script_escolhido, nivel,
                                item.get("mensagem") or "Sem mensagem", COR_ERRO if nivel == "erro" else COR_CASADOS,
                            )
                            if item.get("detalhe"):
                                _renderizar_detalhe_tarefas(item["detalhe"])
                else:
                    st.info(f"O script '{script_escolhido}' ainda não tem execuções registadas.")

        with sep_auditoria:
            st.caption(
                "Compara os extratos bancários reais desse dia com o que está no Mapa, "
                "nos dois sentidos - o mesmo que 'python preencher_mapa.py auditoria', "
                "aqui já com a fonte (ficheiro de extrato) de cada movimento."
            )
            col_aud_dia, col_aud_geral = st.columns(2)
            with col_aud_dia, st.container(border=True):
                st.markdown("**Auditar um dia**")
                with st.container(horizontal=True, vertical_alignment="bottom"):
                    dia_auditoria = st.date_input("Dia a auditar", value=date.today(), persist_state="page", key="dia_monitorizacao_auditoria", width=200)
                    auditar_dia = st.button("Auditar (regista no histórico)", key="botao_auditoria_monitorizacao", type="primary")
            with col_aud_geral, st.container(border=True):
                st.markdown("**Auditoria geral**")
                with st.container(horizontal=True, vertical_alignment="bottom"):
                    dias_atras_geral = st.number_input(
                        "Últimos N dias", min_value=1, max_value=90, value=31, persist_state="page", key="dias_atras_auditoria_geral", width=160,
                    )
                    auditar_geral = st.button("Sincronizar e auditar todos", key="botao_auditoria_geral")

            if auditar_dia:
                try:
                    resultado = api.registar_auditoria(dia_auditoria.isoformat())  # já sincroniza o dia antes de auditar
                except Exception as e:
                    st.error(f"Erro a consultar a auditoria: {e}")
                else:
                    movimentos_sem_match = resultado.get("movimentos_sem_match", [])
                    linhas_sem_match = resultado.get("linhas_sem_match", [])
                    movimentos_dia = resultado.get("movimentos_dia", [])
                    soma_extrato = resultado.get("soma_extrato", 0.0)
                    soma_mapa = resultado.get("soma_mapa", 0.0)
                    diferenca = resultado.get("diferenca_extrato_mapa", soma_extrato - soma_mapa)
                    bate_certo = abs(diferenca) <= TOLERANCIA_AUDITORIA

                    with st.container(horizontal=True):
                        st.metric("Soma do extrato bancário", f"{_n(soma_extrato, 2)} €", border=True)
                        st.metric("Soma no Mapa (confirmado)", f"{_n(soma_mapa, 2)} €", border=True)
                        st.metric(
                            "Diferença", f"{_n(diferenca, 2)} €", "bate certo" if bate_certo else "não bate certo",
                            delta_color="normal" if bate_certo else "inverse", border=True,
                        )
                        st.metric("Extrato sem linha no Mapa", resultado["sem_match_fwd"], border=True)
                        st.metric("Mapa sem movimento no extrato", resultado["sem_match_rev"], border=True)
                    if not bate_certo:
                        st.warning(
                            "As somas não batem certo - há movimento(s) do extrato ainda não refletido(s) no Mapa "
                            "(ou vice-versa). Ver as discrepâncias abaixo."
                        )

                    colunas_extrato = {
                        **COLUNA_VALOR_EUR,
                        "ficheiro_origem": st.column_config.TextColumn("fonte (ficheiro de extrato)"),
                    }
                    sep_sem_mapa, sep_sem_extrato, sep_extrato = st.tabs([
                        f"Extrato sem linha no Mapa ({len(movimentos_sem_match)})",
                        f"Mapa sem movimento no extrato ({len(linhas_sem_match)})",
                        f"Extrato do dia ({len(movimentos_dia)})",
                    ])
                    with sep_sem_mapa:
                        if movimentos_sem_match:
                            st.dataframe(
                                pd.DataFrame(movimentos_sem_match)[["empresa", "descricao", "valor", "ficheiro_origem"]],
                                width="stretch", hide_index=True, column_config=colunas_extrato,
                            )
                        else:
                            st.success("Todos os movimentos bancários deste dia já têm linha correspondente no Mapa.")
                    with sep_sem_extrato:
                        if linhas_sem_match:
                            st.dataframe(
                                pd.DataFrame(linhas_sem_match)[["linha", "empresa", "previsto", "imputacao"]],
                                width="stretch", hide_index=True,
                                column_config={"previsto": st.column_config.NumberColumn("previsto", format="euro")},
                            )
                        else:
                            st.success("Todas as linhas por confirmar deste dia têm movimento correspondente no extrato.")
                    with sep_extrato:
                        if movimentos_dia:
                            st.dataframe(
                                pd.DataFrame(movimentos_dia)[["empresa", "descricao", "valor", "ficheiro_origem"]],
                                width="stretch", hide_index=True, column_config=colunas_extrato,
                            )
                        else:
                            st.info("Sem movimentos de extrato importados para este dia.")

            if auditar_geral:
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
                            st.dataframe(pd.DataFrame.from_dict(dias_com_diferenca, orient="index"), width="stretch")
                        else:
                            st.success("Todos os dias auditados batem certo (extrato = Mapa, sem movimentos por confirmar).")

            with st.container(border=True):
                st.markdown("**Histórico de auditorias registadas**")
                try:
                    historico = api.historico_auditorias(limit=100).get("historico", [])
                    if historico:
                        st.dataframe(
                            pd.DataFrame(historico)[["dia", "timestamp", "sem_match_fwd", "sem_match_rev", "soma_extrato", "soma_mapa", "diferenca"]],
                            width="stretch",
                            hide_index=True,
                            column_config={
                                "sem_match_fwd": st.column_config.NumberColumn("extrato sem Mapa"),
                                "sem_match_rev": st.column_config.NumberColumn("Mapa sem extrato"),
                                "soma_extrato": st.column_config.NumberColumn("soma extrato", format="euro"),
                                "soma_mapa": st.column_config.NumberColumn("soma mapa", format="euro"),
                                "diferenca": st.column_config.NumberColumn("diferença", format="euro"),
                            },
                        )
                    else:
                        st.info("Ainda não há nenhuma auditoria registada - usa 'Auditar' ou 'Auditoria geral' acima.")
                except Exception as e:
                    st.warning(f"Não foi possível carregar o histórico de auditorias: {e}")

        with sep_ia:
            st.caption(
                "Qualidade da IA em produção, a partir do feedback que já existe: 👍/👎 e erros das perguntas ao "
                "Assistente, e sugestões para casos ambíguos aceites ou rejeitadas por quem resolveu o caso."
            )
            dias_ia = st.segmented_control(
                "Período", [7, 30, 90], default=30, format_func=lambda d: f"{d} dias", persist_state="page", key="dias_metricas_ia",
            ) or 30
            try:
                metricas = api.metricas_ia(dias_ia)
            except Exception as e:
                st.warning(f"Não foi possível carregar as métricas da IA: {e}")
            else:
                m_ass, m_amb = metricas["assistente"], metricas["ambiguos"]
                phoenix_url = (metricas.get("phoenix_url") or "").rstrip("/")

                def _pct(valor):
                    return f"{100 * valor:.0f}%" if valor is not None else "—"

                def _link_trace(item):
                    if phoenix_url and item.get("trace_id"):
                        st.markdown(f"<div style='margin:-4px 0 10px 4px;font-size:0.85em;'>"
                                    f"<a href='{escape(phoenix_url)}/redirects/traces/{escape(item['trace_id'])}' "
                                    f"target='_blank'>ver trace no Phoenix ↗</a></div>", unsafe_allow_html=True)

                if phoenix_url:
                    st.link_button("Abrir o Phoenix ↗", phoenix_url,
                                   help="Traces de cada chamada ao LLM: prompt, ferramentas, resultados, tokens e tempos.")
                else:
                    st.caption("Define PHOENIX_URL_PUBLICA no .env para ligar estas métricas aos traces no Phoenix.")

                with st.container(border=True):
                    st.markdown("**Assistente**")
                    with st.container(horizontal=True):
                        st.metric("Perguntas", m_ass["perguntas"], border=True)
                        st.metric(
                            "Terminadas em erro", _pct(m_ass["taxa_erro"]),
                            f"{m_ass['erros']} conversa(s)" if m_ass["erros"] else None, delta_color="inverse", border=True,
                            help="Ollama em baixo, ocupado ou sem resposta - a pessoa ficou sem resposta.",
                        )
                        st.metric(
                            "Satisfação (👍)", _pct(m_ass["taxa_satisfacao"]),
                            f"{m_ass['feedback_positivo']} 👍 · {m_ass['feedback_negativo']} 👎", delta_color="off", delta_arrow="off", border=True,
                            help=f"Só conta as respostas avaliadas ({_pct(m_ass['taxa_com_feedback'])} das perguntas).",
                        )
                        st.metric(
                            "Sem consultar dados", m_ass["sem_ferramentas"], border=True,
                            help="Respostas dadas sem chamar nenhuma ferramenta - candidatas a resposta inventada.",
                        )
                        st.metric(
                            "Números não verificados", m_ass["com_numeros_nao_verificados"], border=True,
                            help="Respostas com valores que não aparecem nos resultados das ferramentas (guardrail de números).",
                        )
                        st.metric(
                            "Tempo de resposta", f"{m_ass['duracao_media_s']:.0f} s" if m_ass["duracao_media_s"] else "—",
                            f"p95 {m_ass['duracao_p95_s']:.0f} s" if m_ass["duracao_p95_s"] else None,
                            delta_color="off", delta_arrow="off", border=True,
                        )

                    if m_ass["por_dia"]:
                        df_uso = pd.DataFrame(m_ass["por_dia"])
                        df_uso["dia"] = pd.to_datetime(df_uso["dia"])
                        df_uso["Respondidas"] = df_uso["perguntas"] - df_uso["erros"]
                        df_uso["Com erro"] = df_uso["erros"]
                        df_uso = df_uso.melt(id_vars=["dia"], value_vars=["Respondidas", "Com erro"], var_name="estado", value_name="n")
                        cores_uso = {"Respondidas": COR_CASADOS, "Com erro": COR_ERRO}
                        st.altair_chart(
                            alt.Chart(df_uso).mark_bar(size=12).encode(
                                x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
                                y=alt.Y("n:Q", title="perguntas", axis=alt.Axis(tickMinStep=1)),
                                color=alt.Color("estado:N", scale=alt.Scale(domain=list(cores_uso), range=list(cores_uso.values())),
                                                legend=alt.Legend(title=None, orient="top")),
                                tooltip=[alt.Tooltip("dia:T", format="%d/%m"), "estado:N", "n:Q"],
                            ).properties(height=220),
                            width="stretch",
                        )

                    col_neg, col_err = st.columns(2)
                    with col_neg:
                        st.markdown(f"**Respostas com 👎** ({m_ass['feedback_negativo']})")
                        if m_ass["respostas_negativas"]:
                            with st.container(height=320):
                                for item in m_ass["respostas_negativas"]:
                                    _linha_log(item["criado_em"], item["pergunta"], "👎", item["resposta"] or "—", COR_AMBIGUOS)
                                    _link_trace(item)
                        else:
                            st.caption("Nenhuma neste período.")
                    with col_err:
                        st.markdown(f"**Conversas terminadas em erro** ({m_ass['erros']})")
                        if m_ass["erros_recentes"]:
                            with st.container(height=320):
                                for item in m_ass["erros_recentes"]:
                                    _linha_log(item["criado_em"], item["pergunta"], "erro", item["erro"] or "—", COR_ERRO)
                                    _link_trace(item)
                        else:
                            st.caption("Nenhuma neste período.")

                    if m_ass["respostas_nao_verificadas"]:
                        with st.expander(f"Respostas com números não verificados ({m_ass['com_numeros_nao_verificados']})"):
                            for item in m_ass["respostas_nao_verificadas"]:
                                _linha_log(item["criado_em"], item["pergunta"], "por verificar",
                                           f"{item['resposta'] or '—'}\n\nNão verificados: {', '.join(item['numeros_nao_verificados'])}",
                                           COR_AMBIGUOS)
                                _link_trace(item)

                with st.container(border=True):
                    st.markdown("**Para rever**")
                    st.caption(
                        "Respostas com 👎, com números não verificados ou chumbadas pelo juiz das avaliações online. "
                        "Uma anotação com a resposta esperada entra no golden dataset do Assistente "
                        "(python -m app.evals.promover_golden) e passa a ser um caso de teste."
                    )
                    try:
                        para_rever = api.listar_para_rever(limit=20)
                    except Exception as e:
                        para_rever = []
                        st.warning(f"Não foi possível carregar a fila de revisão: {e}")
                    if not para_rever:
                        st.caption("Nada para rever.")
                    for item in para_rever:
                        with st.expander(f"{item['criado_em']} · {item['pergunta'][:90]}"):
                            st.markdown(f"**Resposta:** {item['resposta'] or '—'}")
                            st.caption("Motivo: " + " · ".join(item["motivos"]))
                            _link_trace(item)
                            with st.form(key=f"form_anotacao_{item['id']}"):
                                label = st.segmented_control(
                                    "Avaliação", ["correta", "incorreta", "alucinada", "incompleta"],
                                    default="incorreta", persist_state="page", key=f"label_anotacao_{item['id']}",
                                )
                                score = st.slider("Score", 0.0, 1.0, 0.0, 0.1, persist_state="page", key=f"score_anotacao_{item['id']}")
                                esperada = st.text_area("Resposta esperada (vai para o golden dataset)",
                                                        persist_state="page", key=f"esperada_anotacao_{item['id']}")
                                notas = st.text_input("Notas", persist_state="page", key=f"notas_anotacao_{item['id']}")
                                if st.form_submit_button("Guardar anotação"):
                                    try:
                                        api.anotar_interacao(item["id"], label or "incorreta", score, notas, esperada)
                                        st.success("Anotação guardada.")
                                    except Exception as e:
                                        st.error(f"Não foi possível guardar: {e}")

                with st.container(border=True):
                    st.markdown("**Calibração do juiz**")
                    try:
                        calibracao = api.calibracao_juiz()
                    except Exception as e:
                        calibracao = None
                        st.warning(f"Não foi possível carregar a calibração: {e}")
                    if calibracao is not None:
                        st.caption(
                            "Concordância entre o juiz das avaliações online e as pessoas (anotações ou 👍/👎), nas "
                            f"respostas que têm os dois. Com menos de {calibracao['min_pares']} pares, os scores do juiz "
                            "são só indicativos. Kappa: 0 = concordância de acaso, acima de 0,6 = boa."
                        )
                        linhas_calibracao = [
                            {"avaliador": nome, "pares": v["pares"], "concordância": v["concordancia"], "kappa": v["kappa"],
                             "apanha respostas más": v["apanha_mas"], "falsos alarmes": v["matriz"]["falso_alarme"],
                             "deixou passar": v["matriz"]["deixou_passar"],
                             "estado": "calibrado" if v["calibrado"] else "por calibrar"}
                            for nome, v in calibracao["avaliadores"].items()
                        ]
                        if linhas_calibracao:
                            st.dataframe(
                                pd.DataFrame(linhas_calibracao), hide_index=True, width="stretch",
                                column_config={
                                    "concordância": st.column_config.NumberColumn(format="percent"),
                                    "apanha respostas más": st.column_config.NumberColumn(format="percent"),
                                },
                            )
                        else:
                            st.caption("O juiz ainda não avaliou nenhuma resposta.")

                with st.container(border=True):
                    st.markdown("**Sugestões para casos ambíguos**")
                    with st.container(horizontal=True):
                        st.metric("Casos", m_amb["casos"], f"{m_amb['pendentes']} pendente(s)" if m_amb["pendentes"] else None,
                                  delta_color="off", delta_arrow="off", border=True)
                        st.metric(
                            "Sugestões aceites", _pct(m_amb["taxa_aceitacao"]),
                            f"{m_amb['aceites']} aceites · {m_amb['rejeitadas']} rejeitadas", delta_color="off", delta_arrow="off", border=True,
                            help="Casos resolvidos em que a decisão humana foi igual à sugestão.",
                        )
                        st.metric(
                            "Decididas sem LLM", _pct(m_amb["taxa_sem_llm"]),
                            f"{m_amb['sugestoes_por_regras']} regras · {m_amb['sugestoes_por_llm']} LLM", delta_color="off", delta_arrow="off", border=True,
                            help="Sugestões dadas pelas regras (triagem pelo texto/histórico), sem chamar o modelo.",
                        )
                        st.metric(
                            "Resolvidos sem sugestão", _pct(m_amb["taxa_passados_a_humano"]),
                            f"{m_amb['resolvidos_sem_sugestao']} caso(s)" if m_amb["resolvidos_sem_sugestao"] else None,
                            delta_color="inverse", border=True,
                            help="Casos que alguém resolveu sem ter nenhuma sugestão utilizável - passados a humano.",
                        )
                        st.metric(
                            "Respostas inválidas do LLM", m_amb["respostas_invalidas_llm"], border=True,
                            help="O modelo não devolveu JSON válido - a sugestão perdeu-se.",
                        )
                    st.caption(
                        f"Aceitação por origem: regras {_pct(m_amb['taxa_aceitacao_regras'])} · "
                        f"LLM {_pct(m_amb['taxa_aceitacao_llm'])} · {m_amb['dossiers']} dossier(s) do agente preparados. "
                        "Período pelo dia do movimento."
                    )

if aba_faturas.open:
    with aba_faturas:
        # --- filtros
        with st.container(border=True):
            pesquisa_livre = st.text_input(
                "🔍 Pesquisar em qualquer dia",
                placeholder="empresa, fornecedor, NIF, assunto, remetente ou valor",
                persist_state="page", key="faturas_pesquisa",
            )
            dia_faturas_str = desde_faturas_str = ate_faturas_str = None
            with st.container(horizontal=True, vertical_alignment="bottom"):
                filtrar_por_dia = st.toggle(
                    "Um único dia", value=False, persist_state="page", key="faturas_filtrar_dia", disabled=bool(pesquisa_livre)
                )
                if filtrar_por_dia and not pesquisa_livre:
                    dia_faturas_str = st.date_input("Dia", value=date.today(), persist_state="page", key="dia_faturas", width=200).isoformat()
                elif not pesquisa_livre:
                    # por omissão mostra todas as faturas desde o dia 1 do mês corrente
                    periodo_faturas = st.date_input(
                        "Período", value=(date.today().replace(day=1), date.today()), persist_state="page", key="faturas_periodo", width=260,
                    )
                    if isinstance(periodo_faturas, tuple) and len(periodo_faturas) == 2:
                        desde_faturas_str = periodo_faturas[0].isoformat()
                        ate_faturas_str = periodo_faturas[1].isoformat()
                limite_faturas = st.number_input(
                    "Máximo de linhas", min_value=50, max_value=5000, value=1000, step=50, persist_state="page", key="faturas_limit", width=160,
                )
            if pesquisa_livre:
                st.caption("A pesquisar em todos os dias - o filtro por período fica desligado enquanto houver texto na pesquisa.")

        try:
            faturas = api.listar_faturas_recebidas(
                dia_faturas_str,
                desde=desde_faturas_str,
                ate=ate_faturas_str,
                pesquisa=pesquisa_livre or None,
                limit=int(limite_faturas),
            )
        except Exception as e:
            st.warning(f"Não foi possível carregar as faturas recebidas: {e}")
            faturas = None

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

            # o fornecedor extraído do PDF/email vem muitas vezes vazio ou com lixo
            # (moradas, "do titular IBAN", o próprio cliente) - a API devolve o
            # melhor nome em fornecedor_normalizado (ver services/faturas.py)
            SEM_FORNECEDOR = ("(encaminhado internamente)", "(desconhecido)")
            if "fornecedor_normalizado" in df_faturas.columns:
                df_faturas["fornecedor_original"] = df_faturas["fornecedor"]
                df_faturas["fornecedor"] = df_faturas["fornecedor_normalizado"].fillna("(desconhecido)")
                df_faturas["fonte_fornecedor"] = df_faturas["fonte_fornecedor"].map(
                    {"extraido": "documento", "nif": "NIF", "remetente": "email do remetente"}
                ).fillna("—")

            with st.container(horizontal=True, vertical_alignment="bottom"):
                empresas_disponiveis = sorted(e for e in df_faturas["empresa"].dropna().unique() if e)
                empresa_filtro = st.selectbox(
                    "Empresa", ["Todas"] + empresas_disponiveis, persist_state="page", key="faturas_filtro_empresa", width=320,
                )
                fornecedor_filtro = st.text_input("Fornecedor contém", persist_state="page", key="faturas_filtro_fornecedor", width=240)
                valor_filtro = st.text_input("Valor fatura contém", persist_state="page", key="faturas_filtro_valor", width=180)

            if empresa_filtro != "Todas":
                df_faturas = df_faturas[df_faturas["empresa"] == empresa_filtro]
            if fornecedor_filtro:
                df_faturas = df_faturas[
                    df_faturas["fornecedor"].fillna("").str.contains(fornecedor_filtro, case=False, na=False)
                    | df_faturas.get("fornecedor_original", df_faturas["fornecedor"]).fillna("").str.contains(
                        fornecedor_filtro, case=False, na=False)
                ]
            if valor_filtro:
                df_faturas = df_faturas[
                    df_faturas["valor_fatura"].fillna("").str.contains(valor_filtro, case=False, na=False)
                ]

            # --- KPIs
            with st.container(horizontal=True):
                st.metric("Faturas", len(df_faturas), border=True)
                st.metric("Empresas", df_faturas["empresa"].nunique(), border=True)
                identificados = df_faturas[~df_faturas["fornecedor"].isin(SEM_FORNECEDOR)] if "fornecedor" in df_faturas else df_faturas
                st.metric("Fornecedores", identificados["fornecedor"].nunique() if "fornecedor" in df_faturas else "—",
                          f"{len(df_faturas) - len(identificados)} faturas sem fornecedor identificado"
                          if len(df_faturas) > len(identificados) else None,
                          delta_color="off", delta_arrow="off", border=True)
                if "pdf" in df_faturas.columns:
                    st.metric("Com PDF", int(df_faturas["pdf"].notna().sum()), border=True)
                if "dia" in df_faturas.columns and not df_faturas.empty:
                    st.metric("Mais recente", f"{pd.to_datetime(df_faturas['dia'].max()):%d/%m/%Y}", border=True)

            col_tabela_fat, col_top_fat = st.columns([3, 1])
            with col_tabela_fat, st.container(border=True):
                colunas = [
                    "dia", "hora", "empresa", "fornecedor", "fonte_fornecedor", "nif_fornecedor",
                    "valor_fatura", "debito", "credito", "saldo",
                    "n_anexos_pdf", "pdf", "assunto", "remetente", "fornecedor_original",
                ]
                colunas_existentes = [c for c in colunas if c in df_faturas.columns]
                st.markdown(f"**Faturas recebidas (faturas@vidor.pt)** · {len(df_faturas)}")
                if df_faturas.empty:
                    st.info("Nenhuma fatura corresponde aos filtros escolhidos.")
                else:
                    # valores em texto já formatado e células vazias em branco - o
                    # Streamlit mostrava "None" em quase todas as colunas de valores
                    tabela_fat = df_faturas[colunas_existentes].copy()
                    for coluna in ("valor_fatura", "debito", "credito", "saldo"):
                        if coluna in tabela_fat:
                            numeros = pd.to_numeric(tabela_fat[coluna], errors="coerce")
                            tabela_fat[coluna] = [f"{_n(v, 2)} €" if pd.notna(v) else "" for v in numeros]
                    texto = [c for c in tabela_fat.columns if c not in ("pdf", "n_anexos_pdf")]  # o link e o nº ficam
                    tabela_fat[texto] = tabela_fat[texto].astype(object).where(tabela_fat[texto].notna(), "")
                    if "dia" in tabela_fat:
                        tabela_fat["dia"] = pd.to_datetime(tabela_fat["dia"]).dt.strftime("%d/%m/%Y")
                    st.dataframe(
                        tabela_fat,
                        width="stretch",
                        hide_index=True,
                        height=520,
                        column_config={
                            "dia": "Dia", "hora": "Hora", "empresa": "Empresa", "fornecedor": "Fornecedor",
                            "debito": "Débito", "credito": "Crédito", "saldo": "Saldo", "assunto": "Assunto",
                            "remetente": "Remetente",
                            "fonte_fornecedor": st.column_config.TextColumn(
                                "Fornecedor vem de", help="documento (texto extraído do PDF/email), NIF (nome já conhecido "
                                                          "para o mesmo NIF) ou email do remetente"),
                            "fornecedor_original": st.column_config.TextColumn(
                                "Texto extraído", help="o que o recolher_faturas_recebidas.py extraiu, sem limpeza"),
                            "nif_fornecedor": st.column_config.TextColumn("NIF fornecedor"),
                            "valor_fatura": st.column_config.TextColumn("Valor fatura"),
                            "n_anexos_pdf": st.column_config.NumberColumn("Anexos PDF"),
                            "pdf": st.column_config.LinkColumn("PDF", display_text="Abrir PDF"),
                        },
                    )
                st.caption(
                    "Alimentado pelo recolher_faturas_recebidas.py (de hora a hora) - os mesmos dados "
                    "do Excel mensal em Documentos a Tratar/AFaturas. Aumenta o limite se faltarem dias."
                )
            with col_top_fat, st.container(border=True):
                st.markdown("**Fornecedores mais frequentes**")
                if "fornecedor" in df_faturas.columns and not identificados.empty:
                    top_fornecedores = (
                        identificados["fornecedor"].value_counts().head(10)
                        .rename_axis("fornecedor").reset_index(name="faturas")
                    )
                    st.altair_chart(
                        alt.Chart(top_fornecedores).mark_bar(cornerRadiusTopRight=4, cornerRadiusBottomRight=4).encode(
                            y=alt.Y("fornecedor:N", sort="-x", title=None, axis=alt.Axis(labelLimit=140)),
                            x=alt.X("faturas:Q", title="Faturas", axis=alt.Axis(tickMinStep=1)),
                            color=alt.value(COR_RANKING_SALDO),
                            tooltip=["fornecedor:N", "faturas:Q"],
                        ).properties(height=480),
                        width="stretch",
                    )
                    sem = len(df_faturas) - len(identificados)
                    if sem:
                        st.caption(
                            f"{sem} fatura(s) sem fornecedor identificado - a maioria reencaminhada por um "
                            "email interno (@vidor.pt) sem o nome no texto extraído."
                        )
                else:
                    st.info("Sem fornecedores identificados.")
        elif faturas is not None:
            st.info("Ainda não há faturas recebidas registadas para este filtro.")

if aba_saldos.open:
    with aba_saldos:
        with st.container(border=True, horizontal=True, vertical_alignment="bottom"):
            dia_mapa = st.date_input("Dia do mapa (opcional)", value=None, persist_state="page", key="dia_mapa_saldos", width=220)
            st.caption(
                "Última leitura de cada conta até ao dia escolhido (hoje, se vazio). Variação face à leitura "
                "anterior de cada conta - nem todas têm leitura todos os dias. ⚠️ = última leitura com mais de "
                f"{DIAS_LEITURA_ANTIGA} dias (continua a contar no total) ou extrato sem o nome da empresa."
            )

        try:
            mapa = api.mapa_saldos(dia_mapa.isoformat() if dia_mapa else None)
        except Exception as e:
            st.error(f"Erro a consultar o mapa de saldos: {e}")
            mapa = []

        if mapa:
            df_mapa = pd.DataFrame(mapa).sort_values("entidade")
            n_subiram = int((df_mapa["variacao_contabilistico"] > 0).sum())
            n_desceram = int((df_mapa["variacao_contabilistico"] < 0).sum())
            n_negativos = int((df_mapa["saldo_contabilistico"] < 0).sum())
            # recebimentos/pagamentos do dia do mapa (o escolhido, ou o da leitura mais recente)
            dia_fluxo = dia_mapa.isoformat() if dia_mapa else str(df_mapa["dia"].max())
            try:
                fluxo_dia = next((r for r in api.resumo_diario() if r["dia"] == dia_fluxo), None)
            except Exception as e:
                st.error(f"Erro a consultar recebimentos/pagamentos: {e}")
                fluxo_dia = None
            rotulo_dia_fluxo = f"{pd.to_datetime(dia_fluxo):%d/%m/%Y}"
            with st.container(horizontal=True):
                st.metric("Saldo contabilístico total", f"{_n(df_mapa['saldo_contabilistico'].sum())} €", border=True)
                st.metric("Saldo disponível total", f"{_n(df_mapa['saldo_disponivel'].sum())} €", border=True)
                st.metric(
                    f"Recebimentos ({rotulo_dia_fluxo})", f"{_n(fluxo_dia['recebimentos'] if fluxo_dia else 0)} €",
                    f"{_n(fluxo_dia['recebimentos_externos'])} € fora do grupo" if fluxo_dia else None,
                    delta_color="off", delta_arrow="off", border=True,
                    help="Soma dos movimentos a crédito de todas as contas nesse dia. O valor pequeno exclui "
                         "as transferências entre empresas do grupo.",
                )
                st.metric(
                    f"Pagamentos ({rotulo_dia_fluxo})", f"{_n(fluxo_dia['pagamentos'] if fluxo_dia else 0)} €",
                    f"{_n(fluxo_dia['pagamentos_externos'])} € fora do grupo" if fluxo_dia else None,
                    delta_color="off", delta_arrow="off", border=True,
                    help="Soma dos movimentos a débito de todas as contas nesse dia. O valor pequeno exclui "
                         "as transferências entre empresas do grupo.",
                )
                st.metric("Contas", len(df_mapa), border=True)
                st.metric("Subiram / desceram", f"{n_subiram} / {n_desceram}", border=True,
                          help="Face à leitura anterior de cada conta.")
                st.metric("Contas negativas", n_negativos, "atenção" if n_negativos else None,
                          delta_color="inverse", border=True)

        try:
            empresas_saldo = api.listar_empresas()
        except Exception as e:
            st.error(f"Erro a consultar empresas: {e}")
            empresas_saldo = []

        col_mapa, col_empresa = st.columns([3, 2])
        with col_mapa, st.container(border=True):
            st.markdown("**Mapa de saldos de todas as empresas**")
            if not mapa:
                st.info("Sem saldos guardados (precisas de correr /saldos/atualizar primeiro).")
            else:
                df_mapa["Var. (contab.)"] = [variacao_saldo(v, p) for v, p in
                                             zip(df_mapa["variacao_contabilistico"], df_mapa["variacao_pct_contabilistico"])]
                df_mapa["Var. (disp.)"] = [variacao_saldo(v, p) for v, p in
                                           zip(df_mapa["variacao_disponivel"], df_mapa["variacao_pct_disponivel"])]
                dia_mais_recente = df_mapa["dia"].max()
                df_mapa["Empresa"] = [rotulo_conta(e, d, dia_mais_recente) for e, d in zip(df_mapa["entidade"], df_mapa["dia"])]
                df_mapa["Dia"] = pd.to_datetime(df_mapa["dia"]).dt.strftime("%d/%m/%Y")

                tabela_mapa = df_mapa[[
                    "Empresa", "Dia", "saldo_contabilistico", "Var. (contab.)", "saldo_disponivel", "Var. (disp.)",
                ]].rename(columns={
                    "saldo_contabilistico": "Saldo contabilístico",
                    "saldo_disponivel": "Saldo disponível",
                })

                linha_total = pd.DataFrame([{
                    "Empresa": "Total", "Dia": "",
                    "Saldo contabilístico": df_mapa["saldo_contabilistico"].sum(), "Var. (contab.)": "",
                    "Saldo disponível": df_mapa["saldo_disponivel"].sum(), "Var. (disp.)": "",
                }])
                tabela_mapa_com_total = pd.concat([tabela_mapa, linha_total], ignore_index=True)

                def _destacar_total(linha):
                    estilo = "font-weight: bold; border-top: 2px solid currentColor" if linha["Empresa"] == "Total" else ""
                    return [estilo] * len(linha)

                st.dataframe(
                    tabela_mapa_com_total.style.apply(_destacar_total, axis=1),
                    width="stretch",
                    hide_index=True,
                    # altura calculada para caber todas as linhas sem scroll interno
                    # (linha ~35px + cabeçalho ~38px + margem) - por omissão o
                    # st.dataframe só mostra ~10 linhas e obriga a fazer scroll.
                    height=int(35 * len(tabela_mapa_com_total) + 38 + 3),
                    # colunas estreitas onde o conteúdo é curto, para caber tudo sem
                    # scroll horizontal (o "Saldo disponível" ficava cortado)
                    column_config={
                        "Empresa": st.column_config.TextColumn(width="large"),
                        "Dia": st.column_config.TextColumn(width="small"),
                        "Saldo contabilístico": st.column_config.NumberColumn(format="euro", width="small"),
                        "Var. (contab.)": st.column_config.TextColumn(width="small"),
                        "Saldo disponível": st.column_config.NumberColumn(format="euro", width="small"),
                        "Var. (disp.)": st.column_config.TextColumn(width="small"),
                    },
                )

        with col_empresa, st.container(border=True):
            st.markdown("**Consultar uma empresa**")
            if not empresas_saldo:
                st.info("Ainda não há empresas com movimentos importados.")
            else:
                with st.container(horizontal=True, vertical_alignment="bottom"):
                    empresa = st.selectbox("Empresa", empresas_saldo, persist_state="page", key="empresa_consultar_saldo")
                    dia_saldo = st.date_input("Dia (opcional)", value=None, persist_state="page", key="dia_saldo", width=160)
                try:
                    saldos_mes = api.consultar_saldo(empresa)
                except Exception as e:
                    st.error(f"Erro a consultar o histórico: {e}")
                    saldos_mes = []
                try:
                    resultado = api.consultar_saldo(empresa, dia_saldo.isoformat() if dia_saldo else None)
                except Exception as e:
                    st.error(f"Erro: {e}")
                    resultado = []

                if resultado:
                    ultimo = sorted(resultado, key=lambda r: r["dia"])[-1]
                    with st.container(horizontal=True):
                        st.metric(f"Contabilístico ({pd.to_datetime(ultimo['dia']):%d/%m/%Y})", f"{_n(ultimo['saldo_contabilistico'], 2)} €", border=True)
                        st.metric("Disponível", f"{_n(ultimo['saldo_disponivel'], 2)} €", border=True)
                else:
                    st.warning("Nenhum saldo encontrado para essa empresa/dia.")

                if saldos_mes:
                    df_saldos_mes_longo = pd.DataFrame(saldos_mes).sort_values("dia").melt(
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
                    st.altair_chart(
                        alt.Chart(df_saldos_mes_longo).mark_line(strokeWidth=2).encode(
                            x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
                            y=alt.Y("valor:Q", title="EUR", axis=alt.Axis(format=",.0f")),
                            color=alt.Color(
                                "tipo:N",
                                scale=alt.Scale(domain=list(cores_saldo_mes), range=list(cores_saldo_mes.values())),
                                legend=alt.Legend(title=None, orient="top"),
                            ),
                            tooltip=["dia:T", "tipo:N", alt.Tooltip("valor:Q", format=",.2f")],
                        ).properties(height=300),
                        width="stretch",
                    )
                    with st.expander("Ver leituras"):
                        st.dataframe(resultado or saldos_mes, width="stretch", column_config=COLUNAS_SALDO_EUR)
                else:
                    st.info("Sem histórico para esta empresa.")

ESTADOS_NEGOCIO = {"concluído": "✅ concluído", "em dia": "🟢 em dia", "por confirmar": "⚠️ por confirmar"}
ESTADOS_PAGAMENTO = {
    "recebido": "✅ recebido (Mapa)", "recebido (índice)": "✅ recebido (índice)",
    "por confirmar": "⚠️ por confirmar", "agendado": "🗓 agendado",
}
SERIES_VENDAS_MES = {"Recebido (Mapa)": CORES_PREVISAO["Histórico"], "Agendado (índice)": COR_RITMO_FC}


def _secao_vendas_indice(empresa, periodo: dict):
    """Vendas do índice do Departamento Comercial em detalhe: cada negócio
    com a agenda (sinal, reforços, escritura), o que já entrou segundo o
    Mapa, o que falta e o próximo pagamento - ver app/services/vendas.py."""
    with st.container(horizontal=True, vertical_alignment="center"):
        st.caption(
            "Do Índice do Departamento Comercial, cruzado com o Mapa: cada pagamento da agenda casa com um "
            "recebimento com a mesma Ref. na descrição, de valor e data parecidos. \"Por confirmar\" = a data "
            "já passou e não há entrada identificada - pode estar em atraso ou ter entrado numa linha sem a Ref."
        )
        so_periodo = st.toggle("Só negócios com datas no período", value=False, persist_state="page", key="vendas_so_periodo")
    try:
        dados = api.negocios_comerciais(empresa, **(periodo if so_periodo else {}))
    except Exception as e:
        st.error(f"Erro a consultar o índice comercial: {e}")
        return
    negocios, resumo = dados["negocios"], dados["resumo"]
    if not negocios:
        st.info("Sem negócios no índice comercial para este filtro (ou o índice não está acessível nesta máquina).")
        return

    with st.container(horizontal=True):
        st.metric("Negócios", resumo["negocios"], border=True)
        st.metric("Valor dos negócios", f"{_n(resumo['valor_proposto'])} €", border=True)
        st.metric("Recebido", f"{_n(resumo['recebido'])} €", border=True,
                  help="Pagamentos da agenda casados com o Mapa, mais o sinal que o índice regista como recebido.")
        st.metric("Por receber", f"{_n(resumo['por_receber'])} €", border=True)
        st.metric("Por confirmar", f"{_n(resumo['por_confirmar'])} €",
                  f"{resumo['n_por_confirmar']} negócio(s)" if resumo["n_por_confirmar"] else None,
                  delta_color="off", delta_arrow="off", border=True,
                  help="Reforços/escrituras com data já passada sem entrada identificada no Mapa.")
        st.metric("Agendado · 30 dias", f"{_n(resumo['proximos_30_dias'])} €", border=True,
                  help="Sinais, reforços e escrituras marcados no índice para os próximos 30 dias.")

    if dados.get("por_mes"):
        df_mes = pd.DataFrame(dados["por_mes"])
        df_mes["mes"] = pd.to_datetime(df_mes["mes"] + "-01")
        st.altair_chart(
            alt.Chart(df_mes).mark_bar(size=22, cornerRadiusTopLeft=4, cornerRadiusTopRight=4).encode(
                x=alt.X("yearmonth(mes):T", title=None, axis=alt.Axis(format="%m/%Y", labelAngle=-45)),
                y=alt.Y("valor:Q", title="EUR por mês", axis=alt.Axis(format=",.0f")),
                color=alt.Color("serie:N", scale=alt.Scale(domain=list(SERIES_VENDAS_MES),
                                                           range=list(SERIES_VENDAS_MES.values())),
                                legend=alt.Legend(title=None, orient="top")),
                tooltip=[alt.Tooltip("yearmonth(mes):T", title="Mês", format="%m/%Y"), "serie:N",
                         alt.Tooltip("valor:Q", title="Valor", format=",.0f")],
            ).properties(height=240, title="Vendas por mês: recebidas (Mapa) e agendadas (índice)"),
            width="stretch",
        )

    def _proximo(n):
        p = n["proximo_pagamento"]
        return f"{p['tipo']} · {pd.to_datetime(p['dia']):%d/%m/%Y} · {_n(p['valor'])} €" if p else "—"

    df_neg = pd.DataFrame([{
        "Estado": ESTADOS_NEGOCIO.get(n["estado"], n["estado"]), "Ref.": n["ref"],
        "Empreendimento": n["empreendimento"], "Fração": n["fracao"], "Cliente": n["cliente"],
        "Empresa": n["empresa"], "Valor do negócio": n["valor_proposto"],
        "Data CPCV": pd.to_datetime(n["data_cpcv"]) if n["data_cpcv"] else None,
        "Recebido": n["recebido"], "Por receber": n["por_receber"], "Por confirmar": n["por_confirmar"],
        "Próximo pagamento": _proximo(n),
    } for n in negocios])
    selecao = st.dataframe(
        df_neg, width="stretch", hide_index=True, key="tabela_vendas_indice",
        on_select="rerun", selection_mode="single-row",
        column_config={
            **{c: st.column_config.NumberColumn(format="euro")
               for c in ("Valor do negócio", "Recebido", "Por receber", "Por confirmar")},
            "Data CPCV": st.column_config.DateColumn(format="DD/MM/YYYY"),
        },
    )
    linhas_sel = selecao.selection.rows if selecao else []
    if not linhas_sel:
        st.caption("Clica numa linha para ver a agenda de pagamentos desse negócio.")
        return
    n = negocios[linhas_sel[0]]
    st.markdown(f"**Agenda · {n['ref']} · {n['empreendimento'] or ''} {n['fracao'] or ''} · {n['cliente'] or ''}**")
    st.dataframe(
        pd.DataFrame([{
            "Pagamento": p["tipo"], "Data marcada": pd.to_datetime(p["dia"]), "Valor": p["valor"],
            "Estado": ESTADOS_PAGAMENTO.get(p["estado"], p["estado"]),
            "Recebido em": pd.to_datetime(p["recebido_em"]) if p.get("recebido_em") else None,
        } for p in n["pagamentos"]]),
        width="stretch", hide_index=True,
        column_config={
            "Valor": st.column_config.NumberColumn(format="euro"),
            "Data marcada": st.column_config.DateColumn(format="DD/MM/YYYY"),
            "Recebido em": st.column_config.DateColumn(format="DD/MM/YYYY"),
        },
    )


ESTADOS_RENDA = {
    "pago": "✅", "por registar": "📝", "registado no Mapa": "☑️", "em falta": "❌",
    "por receber": "⏳", "sem contrato": "–", "sem extrato": "❔",
}
MESES_ABREV = ["Jan", "Fev", "Mar", "Abr", "Mai", "Jun", "Jul", "Ago", "Set", "Out", "Nov", "Dez"]


def _secao_rendas(empresa, periodo: dict):
    """Mapa de Rendas cruzado com os extratos: cada recebimento "TRF/TFI
    <nome>" é atribuído a um contrato pelo nome do arrendatário e pelo valor,
    e cada mês do ano fica pago, por registar no Mapa ou em falta - ver
    app/services/rendas.py."""
    try:
        dados = api.rendas(empresa, periodo["dia_fim"])
    except Exception as e:
        st.error(f"Erro a consultar as rendas: {e}")
        return
    contratos, resumo = dados["contratos"], dados["resumo"]
    if not contratos:
        st.info(dados.get("erro") or "Sem contratos de renda para este filtro.")
        return

    st.caption(
        f"Do {dados['mapa']} (folha RENDAS), cruzado com os extratos de {dados['ano']}: cada recebimento "
        "\"TRF/TFI <nome>\" conta para o contrato do arrendatário com esse nome quando o valor é a renda "
        "(ou várias rendas - pagam vários meses, primeiro o do recebimento, depois os em atraso). "
        "✅ pago e marcado no Mapa · 📝 pago no extrato mas ainda sem X no Mapa · ☑️ X no Mapa sem "
        "recebimento identificado (outra via ou outro nome) · ❌ em falta · ⏳ mês em curso · – sem contrato · "
        "❔ extratos do mês incompletos na base de dados (não dá para dizer se foi pago)."
    )
    if dados.get("meses_sem_extrato"):
        st.warning("Extratos incompletos em " + ", ".join(MESES_ABREV[m - 1] for m in dados["meses_sem_extrato"])
                   + ": os meses sem X no Mapa ficam ❔ em vez de ❌.")
    with st.container(horizontal=True):
        st.metric("Contratos", resumo["contratos"], border=True)
        st.metric("Renda mensal", f"{_n(resumo['renda_mensal'])} €", border=True)
        st.metric(f"Recebido em {dados['ano']}", f"{_n(resumo['recebido'])} €", border=True,
                  help="Recebimentos dos extratos identificados como renda (nome do arrendatário + valor).")
        st.metric("Em falta", f"{_n(resumo['valor_em_falta'])} €",
                  f"{resumo['meses_em_falta']} mês(es) · {resumo['contratos_em_falta']} contrato(s)",
                  delta_color="off", delta_arrow="off", border=True,
                  help="Meses já fechados sem recebimento identificado nem X no Mapa de Rendas.")
        st.metric("Por registar no Mapa", resumo["meses_por_registar"], border=True,
                  help="Meses pagos segundo o extrato que ainda não têm X na folha RENDAS.")

    meses_mostrar = range(1, dados["mes_atual"] + 1)
    df = pd.DataFrame([{
        "Empresa": c["empresa"], "Cliente": c["cliente"], "Espaço": c["espaco"], "Fração": c["fracao"],
        "Renda": c["renda"],
        **{MESES_ABREV[m - 1]: ESTADOS_RENDA.get(c["meses"][m - 1]["estado"], "") for m in meses_mostrar},
        "Recebido": c["recebido"], "Em falta": c["valor_em_falta"],
    } for c in contratos])
    selecao = st.dataframe(
        df, width="stretch", hide_index=True, key="tabela_rendas",
        on_select="rerun", selection_mode="single-row",
        column_config={
            **{c: st.column_config.NumberColumn(format="euro") for c in ("Renda", "Recebido", "Em falta")},
            **{MESES_ABREV[m - 1]: st.column_config.TextColumn(width=40) for m in meses_mostrar},
        },
    )

    por_registar = [
        {"Empresa": c["empresa"], "Cliente": c["cliente"], "Espaço": c["espaco"], "Fração": c["fracao"],
         "Mês": MESES_ABREV[m["mes"] - 1], "Recebido em": pd.to_datetime(m["dia"]), "Valor": m["valor"]}
        for c in contratos for m in c["meses"] if m["estado"] == "por registar"
    ]
    if por_registar:
        with st.expander(f"📝 Por registar no Mapa de Rendas ({len(por_registar)})"):
            st.dataframe(
                pd.DataFrame(por_registar), width="stretch", hide_index=True,
                column_config={"Valor": st.column_config.NumberColumn(format="euro"),
                               "Recebido em": st.column_config.DateColumn(format="DD/MM/YYYY")},
            )

    linhas_sel = selecao.selection.rows if selecao else []
    if not linhas_sel:
        st.caption("Clica numa linha para ver os recebimentos desse contrato.")
        return
    c = contratos[linhas_sel[0]]
    st.markdown(f"**Recebimentos · {c['cliente']} · {c['espaco']} {c['fracao']}**")
    if not c["pagamentos"]:
        st.info("Nenhum recebimento identificado nos extratos este ano.")
        return
    st.dataframe(
        pd.DataFrame([{
            "Dia": pd.to_datetime(p["dia"]), "Descrição": p["descricao"], "Valor": p["valor"],
            "Rendas": p["n_meses"],
        } for p in c["pagamentos"]]),
        width="stretch", hide_index=True,
        column_config={"Valor": st.column_config.NumberColumn(format="euro"),
                       "Dia": st.column_config.DateColumn(format="DD/MM/YYYY")},
    )


if aba_analise_extratos.open:
    with aba_analise_extratos:
        try:
            empresas_extratos = api.listar_empresas()
        except Exception as e:
            st.error(f"Erro a consultar empresas: {e}")
            empresas_extratos = []

        hoje = date.today()
        with st.container(border=True, horizontal=True, vertical_alignment="bottom"):
            dia_inicio_extratos = st.date_input("De", value=hoje.replace(day=1), persist_state="page", key="dia_inicio_extratos", width=180)
            dia_fim_extratos = st.date_input("Até", value=hoje, persist_state="page", key="dia_fim_extratos", width=180)
            empresa_extratos_sel = st.selectbox(
                "Empresa", ["Todas as empresas"] + empresas_extratos, persist_state="page", key="empresa_analise_extratos", width=380,
            )
            pesquisa_extratos = st.text_input(
                "Extratos", placeholder="Procurar extratos...", persist_state="page", key="pesquisa_analise_extratos", width=320,
                help="Filtra as tabelas por descrição, empresa, imputação ou valor (todas as palavras, sem "
                     "acentos). Os gráficos e os totais continuam a contar o período todo.",
            )
        empresa_extratos = None if empresa_extratos_sel == "Todas as empresas" else empresa_extratos_sel
        periodo_extratos = {"dia_inicio": dia_inicio_extratos.isoformat(), "dia_fim": dia_fim_extratos.isoformat()}
        filtro_empresa = {"empresa": empresa_extratos} if empresa_extratos else {}

        try:
            analise = api.analise_imputacoes(**filtro_empresa, **periodo_extratos)
        except Exception as e:
            st.error(f"Erro a consultar a análise de extratos: {e}")
            analise = {"recebimentos": [], "pagamentos": []}
        try:
            linhas_extratos = api.listar_linhas_imputacao(**filtro_empresa, **periodo_extratos)
        except Exception as e:
            st.error(f"Erro a consultar os extratos: {e}")
            linhas_extratos = []
        linhas_encontradas = procurar_linhas_extratos(linhas_extratos, pesquisa_extratos)
        linhas_receb = [l for l in linhas_encontradas if l["tipo"] == "recebimento"]
        linhas_pag = [l for l in linhas_encontradas if l["tipo"] == "pagamento"]

        total_recebido = sum(l["valor"] for l in analise.get("recebimentos", []))
        total_pago = sum(l["valor"] for l in analise.get("pagamentos", []))
        n_pendentes = sum(1 for l in linhas_extratos if not l.get("confirmado"))
        n_cpcv_escritura = sum(1 for l in linhas_extratos if l["tipo"] == "recebimento" and l.get("e_cpcv_escritura"))
        linhas_cpcv_escritura = [l for l in linhas_receb if l.get("e_cpcv_escritura")]

        # --- KPIs
        with st.container(horizontal=True):
            st.metric("Recebido (confirmado)", f"{_n(total_recebido)} €", border=True)
            st.metric("Pago (confirmado)", f"{_n(total_pago)} €", border=True)
            st.metric("Líquido", f"{_n(total_recebido - total_pago, sinal=True)} €", border=True)
            st.metric("Linhas pendentes", n_pendentes, border=True,
                      help="Linhas do Mapa ainda sem movimento correspondente no extrato.")
            st.metric("CPCVs / escrituras", n_cpcv_escritura, border=True)

        # --- distribuição por imputação
        col_receb_pizza, col_pag_pizza = st.columns(2)
        sufixo = f" - {empresa_extratos}" if empresa_extratos else ""
        with col_receb_pizza, st.container(border=True):
            grafico_receb, cores_receb = grafico_pizza_imputacoes(analise.get("recebimentos", []), f"Recebimentos por imputação{sufixo}")
            if grafico_receb is not None:
                st.altair_chart(grafico_receb, width="stretch")
            else:
                st.info("Sem recebimentos registados no período.")
        with col_pag_pizza, st.container(border=True):
            grafico_pag, cores_pag = grafico_pizza_imputacoes(analise.get("pagamentos", []), f"Pagamentos por imputação{sufixo}")
            if grafico_pag is not None:
                st.altair_chart(grafico_pag, width="stretch")
            else:
                st.info("Sem pagamentos registados no período.")

        if pesquisa_extratos.strip():
            st.caption(f"🔎 {len(linhas_encontradas)} de {len(linhas_extratos)} linhas com \"{pesquisa_extratos.strip()}\".")
        sep_receb, sep_pag, sep_cpcv_mapa, sep_cpcv_indice, sep_rendas = st.tabs([
            f"Recebimentos ({len(linhas_receb)})", f"Pagamentos ({len(linhas_pag)})",
            f"CPCVs / Escrituras - Mapa ({len(linhas_cpcv_escritura)})", "Vendas - Índice comercial", "Rendas",
        ])
        with sep_receb:
            tabela_extratos_colorida(linhas_receb, cores_receb, "Sem recebimentos registados no período.")
        with sep_pag:
            tabela_extratos_colorida(linhas_pag, cores_pag, "Sem pagamentos registados no período.")

        with sep_cpcv_mapa:
            st.caption(
                "Do Mapa de Pagamentos e Recebimentos: conta bancária e dia do recebimento "
                "(imputação CPCV/Escritura, ou outra imputação - ex. \"DEPOSITO\" - cuja descrição "
                "refira o código de uma fração). Inclui escrituras ainda pendentes de confirmação no "
                "extrato (coluna Estado) e, quando o código bate com o Índice Comercial, o espaço "
                "físico e a fração da venda."
            )
            if linhas_cpcv_escritura:
                try:
                    mapa_ref = api.espaco_fracao_por_ref()
                except Exception:
                    mapa_ref = {}

                for l in linhas_cpcv_escritura:
                    refs = l.get("refs_fracao") or []
                    l["ref"] = ", ".join(refs) if refs else "-"
                    info_ref = next((mapa_ref[r] for r in refs if r in mapa_ref), None)
                    l["espaco_fisico"] = (info_ref or {}).get("espaco_fisico") or "-"
                    l["fracao"] = (info_ref or {}).get("fracao") or "-"

                df_cpcv = pd.DataFrame(linhas_cpcv_escritura)[[
                    "dia", "empresa", "imputacao", "ref", "espaco_fisico", "fracao",
                    "previsto", "valor", "confirmado",
                ]]
                df_cpcv["confirmado"] = df_cpcv["confirmado"].map({True: "Confirmado", False: "Pendente"})
                df_cpcv = df_cpcv.sort_values("dia", ascending=False).rename(columns={
                    "dia": "Dia", "empresa": "Empresa", "imputacao": "Tipo", "ref": "Ref.",
                    "espaco_fisico": "Espaço Físico", "fracao": "Fração",
                    "previsto": "Valor tabelado/proposto", "valor": "Valor recebido",
                    "confirmado": "Estado",
                })
                st.dataframe(
                    df_cpcv, width="stretch", hide_index=True,
                    column_config={
                        "Valor tabelado/proposto": st.column_config.NumberColumn(format="euro"),
                        "Valor recebido": st.column_config.NumberColumn(format="euro"),
                    },
                )
            else:
                st.info("Sem CPCVs/Escrituras recebidos registados no período.")

        with sep_cpcv_indice:
            _secao_vendas_indice(empresa_extratos, periodo_extratos)

        with sep_rendas:
            _secao_rendas(empresa_extratos, periodo_extratos)

        with st.container(border=True):
            grafico_ranking_pag = grafico_ranking_imputacoes(analise.get("pagamentos", []), "Imputações que mais gastam (ranking completo)")
            if grafico_ranking_pag is not None:
                st.altair_chart(grafico_ranking_pag, width="stretch")
            else:
                st.info("Sem pagamentos registados no período.")

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
