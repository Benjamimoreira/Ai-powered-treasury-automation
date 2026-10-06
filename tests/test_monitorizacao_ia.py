from datetime import date

import pytest

from app.db.models import CasoAmbiguo, InteracaoAssistente
from app.main import app
from app.routers import chat as chat_router


@pytest.fixture()
def agent_falso():
    app.state.agent = object()  # evita criar o Agent real (subprocesso MCP + Ollama)
    yield
    app.state.agent = None


def test_chat_regista_interacao_e_aceita_feedback(client, db_session, agent_falso, monkeypatch):
    async def perguntar_falso(agent, pergunta, rastreio):
        rastreio["trace_id"] = "ab" * 16
        return {"resposta": "O saldo total é 10 €.", "ferramentas_usadas": ["saldo_total"]}

    monkeypatch.setattr(chat_router, "perguntar", perguntar_falso)

    resposta = client.post("/chat", json={"pergunta": "Qual é o saldo?"})

    assert resposta.status_code == 200
    interacao_id = resposta.json()["id"]
    interacao = db_session.get(InteracaoAssistente, interacao_id)
    assert interacao.status == "ok"
    assert interacao.ferramentas_usadas == ["saldo_total"]
    assert interacao.trace_id == "ab" * 16

    assert client.post(f"/chat/{interacao_id}/feedback", json={"util": False}).status_code == 200
    db_session.refresh(interacao)
    assert interacao.feedback == 0


def test_chat_regista_conversa_terminada_em_erro(client, db_session, agent_falso, monkeypatch):
    async def perguntar_falha(agent, pergunta, rastreio):
        rastreio["trace_id"] = "cd" * 16
        raise ConnectionRefusedError("ollama em baixo")

    monkeypatch.setattr(chat_router, "perguntar", perguntar_falha)

    resposta = client.post("/chat", json={"pergunta": "Qual é o saldo?"})

    assert resposta.status_code == 503
    interacao = db_session.query(InteracaoAssistente).one()
    assert interacao.status == "erro"
    assert "Ollama" in interacao.erro
    assert interacao.trace_id == "cd" * 16  # o trace da conversa falhada também fica ligado


def test_feedback_interacao_inexistente_da_404(client):
    assert client.post("/chat/999/feedback", json={"util": True}).status_code == 404


def test_metricas_ia_assistente_e_ambiguos(client, db_session):
    db_session.add_all([
        InteracaoAssistente(pergunta="a", resposta="r", status="ok", ferramentas_usadas=["x"], feedback=1, duracao_segundos=10),
        InteracaoAssistente(pergunta="b", resposta="r", status="ok", ferramentas_usadas=[], feedback=0, duracao_segundos=20),
        InteracaoAssistente(pergunta="c", status="erro", erro="Ollama ocupado"),
    ])
    hoje = date.today()
    base = dict(movimento_id=1, dia=hoje, empresa="X", valor=1.0)
    db_session.add_all([
        # sugestão por regras, aceite
        CasoAmbiguo(**base, resolucao_sugerida="linha_id=1", justificacao_sugerida="[regra: texto, sem LLM] ok",
                    resolvido_por="ana", resolucao="linha_id=1"),
        # sugestão do LLM, rejeitada
        CasoAmbiguo(**base, resolucao_sugerida="linha_id=2", justificacao_sugerida="parece",
                    resolvido_por="ana", resolucao="linha_id=3"),
        # resolvido sem sugestão (passado a humano)
        CasoAmbiguo(**base, resolvido_por="ana", resolucao="novo"),
        # pendente, LLM respondeu lixo
        CasoAmbiguo(**base, justificacao_sugerida="[resposta do LLM não veio em JSON válido] ???"),
    ])
    db_session.commit()

    corpo = client.get("/monitorizacao/ia", params={"dias": 7}).json()

    assistente = corpo["assistente"]
    assert assistente["perguntas"] == 3
    assert assistente["erros"] == 1
    assert assistente["taxa_satisfacao"] == 0.5
    assert assistente["sem_ferramentas"] == 1
    assert assistente["duracao_media_s"] == 15
    assert [r["pergunta"] for r in assistente["respostas_negativas"]] == ["b"]

    ambiguos = corpo["ambiguos"]
    assert ambiguos["casos"] == 4
    assert ambiguos["pendentes"] == 1
    assert ambiguos["sugestoes_por_regras"] == 1
    assert ambiguos["sugestoes_por_llm"] == 1
    assert ambiguos["respostas_invalidas_llm"] == 1
    assert ambiguos["aceites"] == 1
    assert ambiguos["rejeitadas"] == 1
    assert ambiguos["taxa_aceitacao_regras"] == 1.0
    assert ambiguos["taxa_aceitacao_llm"] == 0.0
    assert ambiguos["resolvidos_sem_sugestao"] == 1


def test_run_cadeia_no_phoenix_da_trace_id_e_passos_filhos(monkeypatch):
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import SimpleSpanProcessor
    from opentelemetry.sdk.trace.export.in_memory_span_exporter import InMemorySpanExporter

    from app.services import llm_tracing

    exporter = InMemorySpanExporter()
    provider = TracerProvider()
    provider.add_span_processor(SimpleSpanProcessor(exporter))
    monkeypatch.setattr(llm_tracing, "_tracer", provider.get_tracer("teste"))
    monkeypatch.setattr(llm_tracing, "_iniciado", True)
    monkeypatch.setenv("LANGSMITH_TRACING", "false")

    with llm_tracing.run_cadeia("assistente", {"pergunta": "x"}, modelo="qwen") as cadeia:
        trace_id = cadeia.trace_id
        llm_tracing.registar_passo(cadeia, "saldo_total_tool", "tool", {"argumentos": "{}"}, {"resultado": "10"})
        cadeia.terminar({"resposta": "10 €"})

    spans = {s.name: s for s in exporter.get_finished_spans()}
    assert len(trace_id) == 32
    assert format(spans["assistente"].context.trace_id, "032x") == trace_id
    assert spans["assistente"].attributes["openinference.span.kind"] == "CHAIN"
    assert spans["saldo_total_tool"].parent.span_id == spans["assistente"].context.span_id
    assert spans["saldo_total_tool"].attributes["openinference.span.kind"] == "TOOL"


def test_metricas_ia_devolvem_url_do_phoenix(client, monkeypatch):
    monkeypatch.setenv("PHOENIX_URL_PUBLICA", "http://localhost:6006")
    assert client.get("/monitorizacao/ia").json()["phoenix_url"] == "http://localhost:6006"
