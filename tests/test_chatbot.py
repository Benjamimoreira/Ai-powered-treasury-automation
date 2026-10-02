from types import SimpleNamespace

import pytest

from app.services import chatbot


def _chunk(content):
    return SimpleNamespace(choices=[SimpleNamespace(delta=SimpleNamespace(content=content))])


def _tool_message(nome):
    return SimpleNamespace(role="tool", name=nome, content="[resultado]")


def test_acumular_resposta_so_texto_sem_tools():
    eventos = [_chunk("Olá"), _chunk(" mundo")]

    resultado = chatbot.acumular_resposta(eventos)

    assert resultado == {"resposta": "Olá mundo", "ferramentas_usadas": []}


def test_acumular_resposta_com_uma_tool():
    # ronda em que o modelo só pede a tool não emite texto (content=""),
    # o texto real só aparece depois do resultado da tool ser devolvido
    eventos = [
        _chunk(""),
        _tool_message("saldo_total_tool"),
        _chunk("O saldo total "),
        _chunk("é 213 937,26 €."),
    ]

    resultado = chatbot.acumular_resposta(eventos)

    assert resultado["resposta"] == "O saldo total é 213 937,26 €."
    assert resultado["ferramentas_usadas"] == ["saldo_total_tool"]


def test_acumular_resposta_com_varias_tools_em_ordem():
    eventos = [
        _chunk(""),
        _tool_message("listar_empresas_tool"),
        _chunk(""),
        _tool_message("consultar_saldo_tool"),
        _chunk("Resposta final."),
    ]

    resultado = chatbot.acumular_resposta(eventos)

    assert resultado["ferramentas_usadas"] == ["listar_empresas_tool", "consultar_saldo_tool"]
    assert resultado["resposta"] == "Resposta final."


def test_ferramentas_permitidas_exclui_tools_de_escrita():
    assert "reconciliar_dia_tool" not in chatbot.FERRAMENTAS_PERMITIDAS
    assert "resolver_ambiguo_tool" not in chatbot.FERRAMENTAS_PERMITIDAS


def test_ferramentas_permitidas_inclui_as_tools_de_leitura_esperadas():
    esperadas = {
        "consultar_saldo_tool", "movimentos_do_dia_tool", "auditoria_dia_tool",
        "listar_ambiguos_tool", "saldo_total_tool", "listar_saldos_tool",
        "listar_empresas_tool", "previsao_saldo_tool", "avaliar_previsao_tool",
        "ranking_risco_tool", "estado_scripts_tool", "faturas_recebidas_tool", "movimentos_empresa_tool",
        "anomalias_do_dia_tool",
    }
    assert set(chatbot.FERRAMENTAS_PERMITIDAS) == esperadas


def test_criar_agent_usa_o_ollama_local(monkeypatch):
    capturado = {}

    class AgentFalso:
        def __init__(self, **kwargs):
            capturado.update(kwargs)

    monkeypatch.setattr(chatbot, "Agent", AgentFalso)
    monkeypatch.setenv("OLLAMA_URL", "http://host.docker.internal:11434/v1")
    monkeypatch.setenv("OLLAMA_MODEL_ID", "qwen2.5:3b")

    chatbot.criar_agent()

    assert capturado["base_url"] == "http://host.docker.internal:11434/v1"
    assert capturado["model"] == "qwen2.5:3b"
    assert capturado["servers"][0]["allowed_tools"] == chatbot.FERRAMENTAS_PERMITIDAS


def test_passos_ferramentas_junta_argumentos_e_resultado():
    mensagens = [
        {"role": "assistant", "content": None, "tool_calls": [
            {"id": "c1", "type": "function", "function": {"name": "saldo_total_tool", "arguments": "{}"}},
        ]},
        {"role": "tool", "tool_call_id": "c1", "name": "saldo_total_tool", "content": '{"entidades": 34}'},
        {"role": "assistant", "content": "O saldo total é 233 203,52 €."},
    ]

    assert chatbot._passos_ferramentas(mensagens) == [("saldo_total_tool", "{}", '{"entidades": 34}')]


def test_run_cadeia_nao_faz_nada_com_o_langsmith_desligado(monkeypatch):
    from app.services.llm_tracing import registar_passo, run_cadeia

    monkeypatch.setenv("LANGSMITH_TRACING", "false")
    with run_cadeia("assistente", {"pergunta": "x"}) as run:
        assert run is None
        registar_passo(run, "ferramenta", "tool", {}, {})  # não pode falhar


def test_chamada_em_texto_reconhece_ferramenta_permitida():
    resposta = ('</tool_call>\n{"name": "movimentos_empresa_tool", "arguments": '
                '{"empresa": "J. Pinto", "desde": "2026-08-01", "ate": "2026-08-31"}}\n</tool_call>')
    assert chatbot.chamada_em_texto(resposta) == (
        "movimentos_empresa_tool", {"empresa": "J. Pinto", "desde": "2026-08-01", "ate": "2026-08-31"})


def test_chamada_em_texto_ignora_ferramentas_de_escrita_e_texto_normal():
    # a rede de segurança nunca pode executar o que o Assistente não pode
    assert chatbot.chamada_em_texto('{"name": "reconciliar_dia_tool", "arguments": {"dia": "2026-08-01"}}') is None
    assert chatbot.chamada_em_texto("O saldo total é 233 203,52 €.") is None
