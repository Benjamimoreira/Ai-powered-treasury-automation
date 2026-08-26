from datetime import date
from html import escape
from pathlib import Path
import sys

dashboard_dir = str(Path(__file__).resolve().parent)
if dashboard_dir not in sys.path:
    sys.path.insert(0, dashboard_dir)

import altair as alt
import pandas as pd
import streamlit as st

import api_client as api

# Paleta de estado validada (skill de dataviz - references/palette.md):
# bom/fechado = verde, precisa de decisão humana = amarelo, informativo = azul,
# ainda não processado = cinza neutro. Nunca escolhida "a olho".
COR_CASADOS = "#0ca30c"
COR_AMBIGUOS = "#fab219"
COR_NOVOS = "#2a78d6"
COR_POR_PROCESSAR = "#898781"

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
CORES_PREVISAO = {
    "Histórico": "#2a78d6",
    "Regressão linear": "#eb6834",
    "Média móvel": "#1baf7a",
    "Suavização exponencial": "#eda100",
    "ARIMA": "#e87ba4",
    "Markov-switching": "#008300",
    "Gradient Boosting": "#4a3aa7",
}
COR_IMPORTANCIA_FEATURES = "#4a3aa7"  # mesma cor do Gradient Boosting - a barra "pertence" a esse modelo

NOMES_MODELO = {
    "regressao_linear": "Regressão linear",
    "media_movel": "Média móvel",
    "suavizacao_exponencial": "Suavização exponencial",
    "arima": "ARIMA",
    "markov_switching": "Markov-switching",
    "gradient_boosting": "Gradient Boosting",
}

# Formatação partilhada para colunas EUR em tabelas cruas (st.dataframe)
# - separador de milhares, sem depender de cada chamada repetir a config.
COLUNA_VALOR_EUR = {"valor": st.column_config.NumberColumn("valor", format="euro")}
COLUNAS_SALDO_EUR = {
    "saldo_contabilistico": st.column_config.NumberColumn("saldo_contabilistico", format="euro"),
    "saldo_disponivel": st.column_config.NumberColumn("saldo_disponivel", format="euro"),
}

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


def grafico_previsao(historico_pontos, previsao_por_modelo, y_title):
    """Histórico (linha sólida) + previsão de cada modelo (tracejada),
    reaproveitado pela previsão de saldo e pela de cash-flow - mesma
    paleta e mesma convenção visual para não obrigar a reaprender o
    gráfico entre secções."""
    linhas = [{"dia": p["dia"], "valor": p["valor"], "serie": "Histórico"} for p in historico_pontos]
    for modelo, pontos in previsao_por_modelo.items():
        nome = NOMES_MODELO.get(modelo, modelo)
        linhas.extend({"dia": p["dia"], "valor": p["valor"], "serie": nome} for p in pontos)

    df_previsao = pd.DataFrame(linhas)
    df_previsao["tipo_linha"] = df_previsao["serie"].apply(
        lambda s: "Histórico" if s == "Histórico" else "Previsão"
    )

    return alt.Chart(df_previsao).mark_line(point=True, strokeWidth=2).encode(
        x=alt.X("dia:T", title=None, axis=alt.Axis(format="%d/%m", labelAngle=-45)),
        y=alt.Y("valor:Q", title=y_title, axis=alt.Axis(format=",.0f")),
        color=alt.Color(
            "serie:N",
            scale=alt.Scale(domain=list(CORES_PREVISAO.keys()), range=list(CORES_PREVISAO.values())),
            legend=alt.Legend(title=None),
        ),
        strokeDash=alt.StrokeDash(
            "tipo_linha:N",
            scale=alt.Scale(domain=["Histórico", "Previsão"], range=[[1, 0], [6, 3]]),
            legend=None,
        ),
        tooltip=["dia:T", "serie:N", alt.Tooltip("valor:Q", format=",.2f")],
    ).properties(height=320)


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
st.title("Plataforma de Análise de Tesouraria")

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
    aba_visao_geral, aba_monitorizacao, aba_faturas, aba_reconciliacao,
    aba_saldos, aba_contas, aba_ambiguos, aba_assistente,
) = st.tabs(
    ["Visão Geral", "Monitorização", "Faturas", "Reconciliação", "Saldos", "Análise de Contas", "Ambíguos", "Assistente"]
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

    try:
        ambiguos_globais = api.listar_ambiguos()
    except Exception:
        ambiguos_globais = []

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

    st.markdown(f"**Recebimentos vs Pagamentos - Mapa de {dia_vg.strftime('%m/%Y')}**")
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
            y=alt.Y("valor:Q", title="EUR", axis=alt.Axis(format=",.0f")),
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

    st.markdown("**Previsão de cash-flow (carteira, próximos dias)**")
    st.caption(
        "Cash-flow líquido diário (recebimentos - pagamentos) de todas as entidades, "
        "não o saldo acumulado - varia todos os dias (mesmo os dias sem movimento contam "
        "como 0), por isso a previsão é visível mesmo sem grande tendência."
    )
    dias_previsao_cf = st.slider("Dias a prever", min_value=3, max_value=30, value=7, key="dias_previsao_cf")
    try:
        previsao_cf = api.previsao_cashflow(dias=dias_previsao_cf)
    except Exception as e:
        previsao_cf = None
        st.info(f"Sem previsão disponível: {e}")

    if previsao_cf:
        historico_liquido = [
            {"dia": p["dia"], "valor": p["liquido"]} for p in previsao_cf["historico"]
        ]
        st.altair_chart(
            grafico_previsao(historico_liquido, previsao_cf["previsao"], "Cash-flow líquido (EUR)"),
            use_container_width=True,
        )
        st.caption(
            "Linhas tracejadas = previsão. Regressão linear, média móvel, suavização "
            "exponencial, ARIMA e Markov-switching são estatística clássica - só olham "
            "para a forma da própria série. **Gradient Boosting** é o único destes que é "
            "ML no sentido estrito: aprende de variáveis explícitas (dia da semana, dia "
            "do mês, valores recentes), não só extrapola uma curva."
        )
        if previsao_cf.get("importancia_features"):
            st.markdown("**O que pesou mais na previsão do Gradient Boosting**")
            st.altair_chart(
                grafico_importancia_features(previsao_cf["importancia_features"]),
                use_container_width=True,
            )
        secao_avaliacao_modelos(lambda dt: api.avaliar_previsao_cashflow(dias_teste=dt), "cf_agregado")

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

        st.divider()

    total = len(movimentos_vg)
    casados = sum(1 for m in movimentos_vg if m["tipo_match"] == "exato")
    ambiguos_dia = sum(1 for m in movimentos_vg if m["tipo_match"] == "ambiguo")
    novos = sum(1 for m in movimentos_vg if m["tipo_match"] == "novo")
    por_processar = sum(1 for m in movimentos_vg if m["tipo_match"] is None)

    col1, col2, col3, col4, col5 = st.columns(5)
    col1.metric("Movimentos do dia", total)
    col2.metric("Casados", casados)
    col3.metric("Novos", novos)
    col4.metric("Ambíguos (dia)", ambiguos_dia)
    col5.metric("Ambíguos por resolver (todos os dias)", len(ambiguos_globais))

    if total > 0:
        dados_grafico = pd.DataFrame({
            "estado": ["Casados", "Ambíguos", "Novos", "Por processar"],
            "quantidade": [casados, ambiguos_dia, novos, por_processar],
        })
        cores = {
            "Casados": COR_CASADOS,
            "Ambíguos": COR_AMBIGUOS,
            "Novos": COR_NOVOS,
            "Por processar": COR_POR_PROCESSAR,
        }
        grafico = alt.Chart(dados_grafico).mark_bar(
            cornerRadiusTopLeft=4, cornerRadiusTopRight=4,
        ).encode(
            x=alt.X("estado:N", title=None, sort=list(cores.keys())),
            y=alt.Y("quantidade:Q", title="Movimentos"),
            color=alt.Color(
                "estado:N",
                scale=alt.Scale(domain=list(cores.keys()), range=list(cores.values())),
                legend=None,
            ),
            tooltip=["estado", "quantidade"],
        ).properties(height=280)
        st.altair_chart(grafico, use_container_width=True)
    else:
        st.info("Sem movimentos importados para este dia.")

    st.markdown("**⚠️ Anomalias detetadas (ML - Isolation Forest)**")
    try:
        anomalias = api.listar_anomalias(dia_vg_str)
    except Exception as e:
        st.error(f"Erro a consultar anomalias: {e}")
        anomalias = []

    if anomalias:
        st.warning(f"{len(anomalias)} movimento(s) fora do padrão habitual da respetiva empresa.")
        st.dataframe(pd.DataFrame(anomalias), use_container_width=True, column_config=COLUNA_VALOR_EUR)
    else:
        st.info("Sem anomalias detetadas para este dia (ou histórico insuficiente por empresa).")

with aba_monitorizacao:
    st.markdown("### Estado da Monitorização")
    st.subheader("Estado dos scripts")
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
        else:
            st.info("Sem dados de execução dos scripts.")
    except Exception as e:
        st.warning(f"Não foi possível carregar a monitorização: {e}")

    st.subheader("Histórico de logs")
    try:
        logs = api.listar_monitorizacao_logs(limit=20)
        logs_lista = logs.get("logs", [])
        if logs_lista:
            for item in reversed(logs_lista):
                nivel = str(item.get("nivel", "info")).lower()
                mensagem = item.get("mensagem") or "Sem mensagem"
                script = item.get("script") or "script"
                timestamp = item.get("timestamp") or "-"
                cor = "#d32f2f" if nivel == "erro" else "#1f77b4"
                st.markdown(
                    f"<div style='margin-bottom: 8px; color: {cor}; white-space: pre-wrap;'>"
                    f"<strong>{escape(timestamp)}</strong> · <strong>{escape(script)}</strong> · {escape(nivel.upper())}<br/>"
                    f"{escape(mensagem)}</div>",
                    unsafe_allow_html=True,
                )
        else:
            st.info("Ainda não há logs de execução.")
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
                        f"<div style='color: #d32f2f; white-space: pre-wrap;'><strong>Erro da última execução:</strong><br/>{escape(ultima_erro)}</div>",
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
                    cor = "#d32f2f" if nivel == "erro" else "#1f77b4"
                    st.markdown(
                        f"<div style='margin-bottom: 10px; padding: 8px 10px; border-left: 4px solid {cor}; background: rgba(255,255,255,0.02); white-space: pre-wrap;'>"
                        f"<strong>{escape(timestamp)}</strong> · {escape(nivel.upper())}<br/>"
                        f"{escape(mensagem)}</div>",
                        unsafe_allow_html=True,
                    )
                    if detalhe:
                        st.code("\n".join(str(x) for x in detalhe), language="text")
            else:
                st.info(f"O script '{script_escolhido}' ainda não tem execuções registadas.")
    except Exception as e:
        st.warning(f"Não foi possível carregar o detalhe do script: {e}")

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
        "Filtrar por dia", value=False, key="faturas_filtrar_dia", disabled=bool(pesquisa_livre)
    )
    dia_faturas_str = None
    if filtrar_por_dia and not pesquisa_livre:
        dia_faturas = st.date_input("Dia", value=date.today(), key="dia_faturas")
        dia_faturas_str = dia_faturas.isoformat()
    if pesquisa_livre:
        st.caption("A pesquisar em todos os dias - o filtro por dia fica desligado enquanto houver texto na pesquisa.")
    limite_faturas = st.number_input(
        "Máximo de linhas", min_value=50, max_value=5000, value=1000, step=50, key="faturas_limit"
    )

    try:
        faturas = api.listar_faturas_recebidas(dia_faturas_str, pesquisa=pesquisa_livre or None, limit=int(limite_faturas))
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

with aba_reconciliacao:
    st.subheader("Reconciliação do dia")
    dia = st.date_input("Dia", value=date.today(), key="dia_reconciliacao")
    dia_str = dia.isoformat()

    col1, col2 = st.columns(2)
    with col1:
        if st.button("Reconciliar"):
            try:
                resultado = api.reconciliar_dia(dia_str)
                st.success(
                    f"Casados: {resultado['casados']} | "
                    f"Novos: {resultado['novos']} | "
                    f"Ambíguos: {resultado['ambiguos']}"
                )
            except Exception as e:
                st.error(f"Erro: {e}")
    with col2:
        if st.button("Consultar auditoria"):
            try:
                resultado = api.auditoria(dia_str)
                st.info(
                    f"Movimentos sem match: {resultado['sem_match_fwd']} | "
                    f"Linhas do mapa sem match: {resultado['sem_match_rev']}"
                )
            except Exception as e:
                st.error(f"Erro: {e}")

    if st.button("Ver movimentos do dia"):
        try:
            movimentos = api.listar_movimentos(dia_str)
            if movimentos:
                st.dataframe(movimentos, use_container_width=True, column_config=COLUNA_VALOR_EUR)
            else:
                st.warning("Sem movimentos importados para este dia.")
        except Exception as e:
            st.error(f"Erro: {e}")

with aba_saldos:
    st.subheader("Consultar saldos")
    empresa = st.text_input("Empresa (nome completo ou parcial)")
    dia_saldo = st.date_input("Dia (opcional)", value=None, key="dia_saldo")

    if st.button("Consultar saldo"):
        if not empresa:
            st.warning("Escreve o nome da empresa.")
        else:
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

with aba_contas:
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

        st.markdown("**Previsão de saldo (5 modelos de ML)**")
        st.caption(
            "O saldo só muda em dias com movimento - se os últimos dias estiverem "
            "parados, uma previsão \"sem alteração\" está correta, não avariada. "
            "Para ver a previsão reagir todos os dias, ver \"Previsão de cash-flow\" mais abaixo."
        )
        dias_previsao = st.slider("Dias a prever", min_value=3, max_value=30, value=7, key="dias_previsao")
        try:
            previsao = api.previsao_saldo(empresa_escolhida, dias_previsao)
        except Exception as e:
            previsao = None
            st.info(f"Sem previsão disponível: {e}")

        if previsao:
            st.altair_chart(
                grafico_previsao(previsao["historico"], previsao["previsao"], "Saldo contabilístico (EUR)"),
                use_container_width=True,
            )
            st.caption(
                "Linhas tracejadas = previsão. Regressão linear extrapola a tendência; "
                "média móvel repete o nível recente; suavização exponencial (Holt) reage "
                "depressa a mudanças recentes; ARIMA capta autocorrelação; Markov-switching "
                "tenta detetar mudanças de regime (útil se a conta teve uma quebra grande)."
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
            "Dias a prever", min_value=3, max_value=30, value=7, key="dias_previsao_cf_entidade",
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
                grafico_previsao(historico_liquido_ent, previsao_cf_ent["previsao"], "Cash-flow líquido (EUR)"),
                use_container_width=True,
            )
            st.caption(
                "Como esta entidade pode ter poucos dias com movimento real, a série "
                "inclui os dias sem movimento como 0 - reduz o risco de a previsão "
                "extrapolar picos isolados como se fossem tendência. Gradient Boosting "
                "precisa de mais histórico que os outros modelos (mínimo 15 dias) - pode "
                "não aparecer em entidades com pouco movimento."
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

with aba_ambiguos:
    st.subheader("Casos ambíguos por resolver")

    try:
        casos = api.listar_ambiguos()
    except Exception as e:
        st.error(f"Erro a ligar à API: {e}")
        casos = []

    if not casos:
        st.info("Sem casos ambíguos por resolver.")

    for caso in casos:
        with st.expander(f"Caso {caso['id']} — {caso['empresa']} — {caso['valor']:.2f} EUR"):
            st.write("Linhas candidatas:")
            st.table(caso["candidatos_detalhe"])

            if caso.get("resolucao_sugerida"):
                st.write(f"**Sugestão do LLM:** {caso['resolucao_sugerida']}")
                st.caption(caso.get("justificacao_sugerida") or "")

            if st.button("Pedir sugestão ao LLM", key=f"sugerir_{caso['id']}"):
                try:
                    api.sugerir_ambiguo(caso["id"])
                    st.rerun()
                except Exception as e:
                    st.error(f"Erro: {e}")

            opcoes = {
                f"Linha {c['linha']} — {c['imputacao'] or '(sem imputação)'}": c["id"]
                for c in caso["candidatos_detalhe"]
            }
            opcoes["Nenhuma (é um movimento novo)"] = None
            escolha = st.selectbox(
                "Escolhe a linha correta", list(opcoes.keys()), key=f"escolha_{caso['id']}"
            )
            resolvido_por = st.text_input("O teu nome", key=f"nome_{caso['id']}")

            if st.button("Confirmar resolução", key=f"resolver_{caso['id']}"):
                if not resolvido_por:
                    st.warning("Escreve o teu nome antes de confirmar.")
                else:
                    try:
                        api.resolver_ambiguo(caso["id"], opcoes[escolha], resolvido_por)
                        st.success("Resolvido!")
                        st.rerun()
                    except Exception as e:
                        st.error(f"Erro: {e}")

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
