from datetime import date

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

# Previsão de saldo: histórico + 3 modelos, cada um o seu hue categórico
# (evita reutilizar verde/vermelho, que aqui têm significado de estado).
CORES_PREVISAO = {
    "Histórico": "#2a78d6",
    "Regressão linear": "#eb6834",
    "Média móvel": "#eda100",
    "Suavização exponencial": "#4a3aa7",
}

st.set_page_config(page_title="Tesouraria - Dashboard", layout="wide")
st.title("Dashboard de Tesouraria")

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

aba_visao_geral, aba_reconciliacao, aba_saldos, aba_contas, aba_ambiguos = st.tabs(
    ["Visão Geral", "Reconciliação", "Saldos", "Análise de Contas", "Ambíguos"]
)

with aba_visao_geral:
    st.subheader("Visão geral do dia")
    dia_vg = st.date_input("Dia", value=date.today(), key="dia_visao_geral")
    dia_vg_str = dia_vg.isoformat()

    try:
        movimentos_vg = api.listar_movimentos(dia_vg_str)
    except Exception as e:
        st.error(f"Erro a ligar à API: {e}")
        movimentos_vg = []

    try:
        ambiguos_globais = api.listar_ambiguos()
    except Exception:
        ambiguos_globais = []

    try:
        totais = api.saldo_total()
    except Exception as e:
        st.error(f"Erro a consultar saldo total: {e}")
        totais = None

    if totais:
        st.markdown("**Saldo contabilístico geral** (última leitura conhecida de cada conta)")
        col_a, col_b, col_c = st.columns(3)
        col_a.metric("Saldo contabilístico total", f"{totais['saldo_contabilistico_total']:,.2f} €")
        col_b.metric("Saldo disponível total", f"{totais['saldo_disponivel_total']:,.2f} €")
        col_c.metric("Contas incluídas", totais["entidades"])

        try:
            saldos_atuais = api.listar_saldos_atuais()
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
                x=alt.X("saldo_contabilistico:Q", title="Saldo contabilístico (EUR)"),
                color=alt.value(COR_RANKING_SALDO),
                tooltip=["entidade:N", "saldo_contabilistico:Q"],
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
        st.dataframe(pd.DataFrame(anomalias), use_container_width=True)
    else:
        st.info("Sem anomalias detetadas para este dia (ou histórico insuficiente por empresa).")

with aba_reconciliacao:
    st.subheader("Reconciliação do dia")
    dia = st.date_input("Dia", value=date.today())
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
                st.dataframe(movimentos, use_container_width=True)
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
                    st.dataframe(resultado, use_container_width=True)
                else:
                    st.warning("Nenhum saldo encontrado para essa empresa/dia.")
            except Exception as e:
                st.error(f"Erro: {e}")

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
                x=alt.X("dia:T", title=None),
                y=alt.Y("valor:Q", title="EUR"),
                color=alt.Color(
                    "tipo:N",
                    scale=alt.Scale(domain=list(cores_saldo.keys()), range=list(cores_saldo.values())),
                    legend=alt.Legend(title=None),
                ),
                tooltip=["dia:T", "tipo:N", "valor:Q"],
            ).properties(height=300)
            st.altair_chart(grafico_saldo, use_container_width=True)
        else:
            st.info("Sem saldos guardados para esta empresa (precisas de correr /saldos/atualizar primeiro).")

        st.markdown("**Previsão de saldo (3 modelos de ML)**")
        dias_previsao = st.slider("Dias a prever", min_value=3, max_value=30, value=7, key="dias_previsao")
        try:
            previsao = api.previsao_saldo(empresa_escolhida, dias_previsao)
        except Exception as e:
            previsao = None
            st.info(f"Sem previsão disponível: {e}")

        if previsao:
            linhas = [
                {"dia": p["dia"], "valor": p["valor"], "serie": "Histórico"}
                for p in previsao["historico"]
            ]
            nomes_modelo = {
                "regressao_linear": "Regressão linear",
                "media_movel": "Média móvel",
                "suavizacao_exponencial": "Suavização exponencial",
            }
            for modelo, pontos in previsao["previsao"].items():
                nome = nomes_modelo[modelo]
                linhas.extend({"dia": p["dia"], "valor": p["valor"], "serie": nome} for p in pontos)

            df_previsao = pd.DataFrame(linhas)
            df_previsao["tipo_linha"] = df_previsao["serie"].apply(
                lambda s: "Histórico" if s == "Histórico" else "Previsão"
            )

            grafico_previsao = alt.Chart(df_previsao).mark_line(point=True, strokeWidth=2).encode(
                x=alt.X("dia:T", title=None),
                y=alt.Y("valor:Q", title="Saldo contabilístico (EUR)"),
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
                tooltip=["dia:T", "serie:N", "valor:Q"],
            ).properties(height=320)
            st.altair_chart(grafico_previsao, use_container_width=True)
            st.caption(
                "Linhas tracejadas = previsão. Regressão linear extrapola a tendência; "
                "média móvel repete o nível recente; suavização exponencial (Holt) "
                "reage mais depressa a mudanças recentes."
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
                x=alt.X("dia:T", title=None),
                y=alt.Y("valor:Q", title="EUR (líquido do dia)"),
                color=alt.Color(
                    "sinal:N",
                    scale=alt.Scale(domain=list(cores_fluxo.keys()), range=list(cores_fluxo.values())),
                    legend=alt.Legend(title=None),
                ),
                tooltip=["dia:T", "valor:Q"],
            ).properties(height=250)
            st.altair_chart(grafico_fluxo, use_container_width=True)

            with st.expander("Ver movimentos individuais"):
                st.dataframe(df_hist, use_container_width=True)
        else:
            st.info("Sem movimentos importados para esta empresa.")

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
