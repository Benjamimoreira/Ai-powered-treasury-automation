import json

import httpx
import pytest

from app.evals import avaliar as avaliador
from app.evals.pseudonimizar import Pseudonimizador
from app.services import llm_resolver
from app.services.llm_resolver import RespostaLLM, interpretar_resposta, montar_prompt_de_dados

CASO = {
    "id": "caso_teste",
    "categoria": "linha_correta",
    "movimento": {"dia": "2026-07-14", "empresa": "EMPRESA TESTE,LDA", "descricao": "EDP COMERCIAL", "valor": -120.5},
    "candidatos": [
        {"id": 11, "linha": 5, "tipo": "pagamento", "empresa": "Teste", "previsto": -120.5, "imputacao": "Eletricidade", "descricao": "EDP"},
        {"id": 22, "linha": 9, "tipo": "pagamento", "empresa": "Teste", "previsto": -120.5, "imputacao": "Agua", "descricao": None},
    ],
    "esperado": 11,
    "historico_entidade": [
        {"dia": "2026-06-14", "descricao": "EDP COMERCIAL", "valor": -118.0, "imputacao_no_mapa": "Eletricidade"},
    ],
    "movimentos_parecidos": [
        {"dia": "2026-06-14", "descricao": "EDP COMERCIAL", "valor": -118.0, "imputacao_no_mapa": "Eletricidade"},
    ],
}


def _resposta(texto, tokens=(100, 20)):
    return RespostaLLM(texto=texto, fornecedor="falso", modelo="falso", tokens_entrada=tokens[0],
                       tokens_saida=tokens[1], latencia_s=0.01)


# --- pseudonimização -------------------------------------------------------

def test_pseudonimiza_pessoa_depois_de_trf_mesmo_fora_da_lista_de_nomes():
    p = Pseudonimizador()
    assert p.texto("TRF FULGENCIO MISSUA") == "TRF PESSOA_01"
    # a mesma pessoa noutro texto do caso fica com o mesmo pseudónimo
    assert p.texto("TFI Fulgencio Missua 2026") == "TFI PESSOA_01"


def test_mantem_entidades_depois_de_trf():
    p = Pseudonimizador()
    assert p.texto("TRF IFTHENPAY LDA") == "TRF IFTHENPAY LDA"
    assert p.texto("TRF Caixadirecta EMP") == "TRF Caixadirecta EMP"


def test_pseudonimiza_nome_proprio_no_meio_do_texto_e_mascara_contactos():
    p = Pseudonimizador()
    assert p.texto("Renda Maria Silva Costa junho") == "Renda PESSOA_01 junho"
    assert p.texto("Trf Mbway 922XXX491") == "Trf Mbway 9XXXXXXXX"
    assert "[NIF]" in p.texto("FT fornecedor 501234567")


# --- interpretação da resposta ---------------------------------------------

def test_interpretar_resposta_aceita_id_candidato_e_null():
    assert interpretar_resposta('{"linha_id": 11, "justificacao": "x"}', [11, 22])["linha_id"] == 11
    assert interpretar_resposta('{"linha_id": "22"}', [11, 22])["linha_id"] == 22
    nulo = interpretar_resposta('{"linha_id": null, "justificacao": "nenhuma"}', [11, 22])
    assert nulo["valida"] and nulo["linha_id"] is None


def test_interpretar_resposta_rejeita_id_inventado_e_texto_sem_json():
    assert interpretar_resposta('{"linha_id": 999}', [11, 22])["valida"] is False
    assert interpretar_resposta("não sei", [11, 22])["valida"] is False


# --- prompt ----------------------------------------------------------------

def test_prompt_v1_nao_tem_descritivo_e_v2_tem_descritivo_e_historico():
    v1 = montar_prompt_de_dados(CASO, "v1")
    v2 = montar_prompt_de_dados(CASO, "v2")
    assert "EDP COMERCIAL" not in v1
    assert 'descritivo do banco: "EDP COMERCIAL"' in v2
    assert 'imputação "Eletricidade"' in v2
    assert "[id 11]" in v1 and "[id 11]" in v2


# --- chamada ao LLM --------------------------------------------------------

def test_chamar_llm_detalhado_repete_no_429_e_devolve_tokens(monkeypatch):
    monkeypatch.setattr(llm_resolver.time, "sleep", lambda s: None)
    pedidos = []

    def post_falso(url, headers, json, timeout):
        pedidos.append(json)
        pedido = httpx.Request("POST", url)
        if len(pedidos) == 1:
            return httpx.Response(429, headers={"retry-after": "0"}, request=pedido)
        return httpx.Response(200, request=pedido, json={
            "choices": [{"message": {"content": '{"linha_id": 11}'}}],
            "usage": {"prompt_tokens": 321, "completion_tokens": 12},
        })

    monkeypatch.setattr(llm_resolver.httpx, "post", post_falso)

    resposta = llm_resolver.chamar_llm_detalhado("prompt", fornecedor="ollama")

    assert len(pedidos) == 2
    assert pedidos[0]["response_format"] == {"type": "json_object"}
    assert (resposta.tokens_entrada, resposta.tokens_saida) == (321, 12)
    assert resposta.custo_usd == 0  # modelo local: sem custo de API


# --- avaliador -------------------------------------------------------------

def test_avaliar_mede_exatidao_e_ids_inventados(monkeypatch):
    respostas = iter(['{"linha_id": 11}', '{"linha_id": 999}'])
    monkeypatch.setattr(avaliador, "chamar_llm_detalhado", lambda *a, **k: _resposta(next(respostas)))
    casos = [CASO, {**CASO, "id": "caso_2"}]

    relatorio = avaliador.avaliar(casos, "falso")

    m = relatorio["metricas"]
    assert m["exatidao"] == 0.5
    assert m["respostas_validas"] == 0.5  # o 999 não existe entre os candidatos
    assert relatorio["resultados"][1]["certa"] is False


def test_main_falha_abaixo_do_limiar(monkeypatch, tmp_path):
    conjunto = tmp_path / "conjunto.json"
    conjunto.write_text(json.dumps({"casos": [CASO]}), encoding="utf-8")
    monkeypatch.setattr(avaliador, "PASTA_RESULTADOS", str(tmp_path / "resultados"))
    monkeypatch.setattr(avaliador, "chamar_llm_detalhado", lambda *a, **k: _resposta('{"linha_id": 22}'))

    codigo = avaliador.main(["--fornecedor", "falso", "--limiar", "0.8", "--conjunto", str(conjunto)])

    assert codigo == 1


# --- agente ----------------------------------------------------------------

def test_agente_pede_mais_historico_e_depois_recomenda(monkeypatch):
    from app.services import agente_ambiguos
    from app.services.agente_ambiguos import ContextoCaso, investigar

    respostas = iter([
        '{"linha_id": null, "confianca": "baixa", "justificacao": "pouco histórico", "precisa_de": "historico_alargado"}',
        '{"linha_id": 11, "confianca": "alta", "justificacao": "EDP imputada a Eletricidade no histórico"}',
    ])
    monkeypatch.setattr(agente_ambiguos, "chamar_llm_detalhado", lambda *a, **k: _resposta(next(respostas)))

    dossier = investigar(ContextoCaso.de_caso_avaliacao(CASO), fornecedor="falso", usar_regras=False)

    assert dossier["linha_id_recomendada"] == 11
    assert dossier["confianca"] == "alta"
    assert [p.split(":")[0].split(" (")[0] for p in dossier["passos"]] == [
        "recolher_provas", "analisar", "alargar_historico", "analisar", "verificar",
    ]
    assert dossier["decisao"] == "pendente de aprovação humana"
    assert dossier["uso"]["tokens_entrada"] == 200


def test_agente_descarta_id_inventado_e_obriga_revisao(monkeypatch):
    from app.services import agente_ambiguos
    from app.services.agente_ambiguos import ContextoCaso, investigar

    monkeypatch.setattr(agente_ambiguos, "chamar_llm_detalhado",
                        lambda *a, **k: _resposta('{"linha_id": 999, "confianca": "alta", "justificacao": "x"}'))

    dossier = investigar(ContextoCaso.de_caso_avaliacao(CASO), fornecedor="falso", usar_regras=False)

    assert dossier["linha_id_recomendada"] is None
    assert dossier["confianca"] == "baixa"
    assert any("não está entre os candidatos" in a for a in dossier["alertas"])
    assert any("revisão humana obrigatória" in a for a in dossier["alertas"])


# --- decisão sem LLM ---------------------------------------------------

def test_regras_decidem_pela_triagem_sem_ler_o_historico():
    from app.services.resolucao_regras import decidir_sem_llm

    lido = []
    decisao = decidir_sem_llm(CASO["movimento"], CASO["candidatos"], lambda: lido.append(1) or [])

    assert decisao.decidiu and decisao.linha_id == 11 and decisao.fonte == "triagem_texto"
    assert lido == []  # recolha condicional: o histórico nem foi pedido


def test_regras_usam_o_historico_quando_o_texto_nao_chega():
    from app.services.resolucao_regras import decidir_sem_llm

    movimento = {**CASO["movimento"], "descricao": "INSTITUTO REGISTOS NO"}
    candidatos = [{"id": 1, "imputacao": "IRN", "descricao": None}, {"id": 2, "imputacao": "Portagens", "descricao": None}]
    historico = [{"descricao": "INSTITUTO REGISTOS NO", "imputacao_no_mapa": "IRN"}]

    decisao = decidir_sem_llm(movimento, candidatos, lambda: historico)

    assert (decisao.linha_id, decisao.fonte) == (1, "historico")


def test_regras_passam_ao_llm_em_empate_ou_sem_sinal():
    from app.services.resolucao_regras import decidir_sem_llm

    empate = [{"id": 1, "imputacao": "EDP casa A"}, {"id": 2, "imputacao": "EDP casa B"}]
    assert not decidir_sem_llm(CASO["movimento"], empate, lambda: []).decidiu
    sem_sinal = [{"id": 1, "imputacao": "Agua"}, {"id": 2, "imputacao": "Portagens"}]
    assert not decidir_sem_llm(CASO["movimento"], sem_sinal, lambda: []).decidiu


def test_agente_com_regras_nao_chama_o_llm_quando_elas_decidem(monkeypatch):
    from app.services import agente_ambiguos
    from app.services.agente_ambiguos import ContextoCaso, investigar

    def nao_chamar(*a, **k):
        raise AssertionError("o LLM não devia ser chamado")

    monkeypatch.setattr(agente_ambiguos, "chamar_llm_detalhado", nao_chamar)

    dossier = investigar(ContextoCaso.de_caso_avaliacao(CASO), fornecedor="falso")

    assert dossier["linha_id_recomendada"] == 11
    assert dossier["decidido_por"] == "regras (triagem_texto)"
    assert dossier["uso"]["chamadas_llm"] == 0
    assert [p.split(":")[0] for p in dossier["passos"]] == ["decidir_sem_llm", "verificar"]


def test_estrategia_hibrida_so_chama_o_llm_no_que_as_regras_nao_decidem(monkeypatch):
    chamadas = []
    monkeypatch.setattr(avaliador, "chamar_llm_detalhado",
                        lambda *a, **k: chamadas.append(1) or _resposta('{"linha_id": null}'))
    sem_sinal = {**CASO, "id": "sem_sinal", "esperado": None, "historico_entidade": [], "candidatos": [
        {**CASO["candidatos"][1], "id": 33}, {**CASO["candidatos"][1], "id": 44, "imputacao": "Portagens"}]}

    relatorio = avaliador.avaliar([CASO, sem_sinal], "falso", estrategia="hibrida")

    assert len(chamadas) == 1
    m = relatorio["metricas"]
    assert m["exatidao"] == 1.0 and m["chamadas_llm"] == 1 and m["decididos_sem_llm"] == 0.5


def test_investigar_caso_grava_dossier_sem_resolver(db_session, monkeypatch):
    from datetime import date

    from app.db.models import CasoAmbiguo, DossierAmbiguo, LinhaMapa, MovimentoBancario
    from app.services import agente_ambiguos

    mov = MovimentoBancario(dia=date(2026, 7, 21), empresa="Ancora Apogeu", descricao="EDP", valor=-50.0,
                            ficheiro_origem="x.xlsx")
    db_session.add(mov)
    db_session.flush()
    linhas = [LinhaMapa(dia=date(2026, 7, 21), tipo="pagamento", linha=n, empresa="Ancora Apogeu", previsto=-50.0,
                        imputacao=imp) for n, imp in ((5, "Eletricidade"), (9, "Agua"))]
    db_session.add_all(linhas)
    db_session.flush()
    caso = CasoAmbiguo(movimento_id=mov.id, dia=mov.dia, empresa=mov.empresa, valor=mov.valor,
                       candidatos=[l.id for l in linhas])
    db_session.add(caso)
    db_session.commit()
    monkeypatch.setattr(agente_ambiguos, "chamar_llm_detalhado", lambda *a, **k: _resposta(
        json.dumps({"linha_id": linhas[0].id, "confianca": "media", "justificacao": "EDP = eletricidade"})))

    registo = agente_ambiguos.investigar_caso(db_session, caso.id, fornecedor="falso")

    assert db_session.query(DossierAmbiguo).count() == 1
    assert registo.linha_id_recomendada == linhas[0].id
    db_session.refresh(caso)
    assert caso.resolvido_por is None  # o agente nunca resolve


# --- tracing (LangSmith) ----------------------------------------------------

def test_span_llm_envia_run_llm_com_tokens_para_o_langsmith(monkeypatch):
    import contextlib

    import langsmith.run_helpers

    from app.services import llm_tracing

    runs = []

    class RunFalso:
        def __init__(self, nome, run_type, inputs, metadata, project_name):
            self.nome, self.run_type, self.metadata, self.saida = nome, run_type, metadata, None

        def end(self, outputs=None, error=None):
            self.saida = outputs or {"erro": error}

    @contextlib.contextmanager
    def trace_falso(nome, run_type="chain", *, inputs=None, metadata=None, project_name=None, **k):
        run = RunFalso(nome, run_type, inputs, metadata, project_name)
        runs.append(run)
        yield run

    monkeypatch.setenv("LANGSMITH_TRACING", "true")
    monkeypatch.setenv("LANGSMITH_API_KEY", "lsv2_falsa")
    monkeypatch.delenv("PHOENIX_COLLECTOR_ENDPOINT", raising=False)
    monkeypatch.setattr(langsmith.run_helpers, "trace", trace_falso)

    with llm_tracing.span_llm("sugestao_ambiguo", "groq", "openai/gpt-oss-20b", [{"role": "user", "content": "x"}],
                              caso="caso_1") as registo:
        registo.registar_resposta('{"linha_id": 1}', 300, 20)

    [run] = runs
    assert run.run_type == "llm"
    assert run.metadata["ls_model_name"] == "openai/gpt-oss-20b" and run.metadata["caso"] == "caso_1"
    assert run.saida["usage_metadata"] == {"input_tokens": 300, "output_tokens": 20, "total_tokens": 320}
