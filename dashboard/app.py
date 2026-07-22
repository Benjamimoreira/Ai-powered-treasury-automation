from datetime import date

import streamlit as st

import api_client as api

st.set_page_config(page_title="Tesouraria - Dashboard", layout="wide")
st.title("Dashboard de Tesouraria")

aba_reconciliacao, aba_saldos, aba_ambiguos = st.tabs(["Reconciliação", "Saldos", "Ambíguos"])

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
