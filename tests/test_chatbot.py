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
        "anomalias_do_dia_tool",
    }
    assert set(chatbot.FERRAMENTAS_PERMITIDAS) == esperadas


def test_criar_agent_falha_sem_token(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        chatbot.criar_agent()
