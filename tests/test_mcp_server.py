from datetime import date

import mcp_server
from app.db.models import LinhaMapa, MovimentoBancario, SaldoDiario

DIA = date(2026, 7, 21)
DIA_STR = "2026-07-21"


def test_reconciliar_dia_tool_casa_movimento(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-100.0,
        ficheiro_origem="x.xlsx",
    ))
    db_session.add(LinhaMapa(dia=DIA, tipo="pagamento", linha=5, empresa="Ancora Apogeu", previsto=-100.0))
    db_session.commit()

    resultado = mcp_server.reconciliar_dia_tool(DIA_STR)

    assert resultado == {"casados": 1, "novos": 0, "ambiguos": 0}


def test_auditoria_dia_tool(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="SEM MAPA,LDA", descricao="TRANSF", valor=-50.0, ficheiro_origem="x.xlsx",
    ))
    db_session.commit()

    resultado = mcp_server.auditoria_dia_tool(DIA_STR)

    assert resultado["sem_match_fwd"] == 1


def test_movimentos_do_dia_tool(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-100.0,
        ficheiro_origem="x.xlsx",
    ))
    db_session.commit()

    resultado = mcp_server.movimentos_do_dia_tool(DIA_STR)

    assert len(resultado) == 1
    assert resultado[0]["tipo_match"] is None


def test_consultar_saldo_tool(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    db_session.add(SaldoDiario(
        dia=DIA, entidade="Ancora Apogeu", saldo_contabilistico=1234.56, saldo_disponivel=1200.0,
    ))
    db_session.commit()

    resultado = mcp_server.consultar_saldo_tool("ANCORA APOGEU,LDA")

    assert len(resultado) == 1
    assert resultado[0]["saldo_contabilistico"] == 1234.56
    assert resultado[0]["dia"] == DIA_STR


def test_listar_e_resolver_ambiguo_tool(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-100.0,
        ficheiro_origem="x.xlsx",
    ))
    linha_a = LinhaMapa(dia=DIA, tipo="pagamento", linha=5, empresa="Ancora Apogeu", previsto=-100.0)
    linha_b = LinhaMapa(dia=DIA, tipo="pagamento", linha=9, empresa="Ancora Apogeu", previsto=-100.0)
    db_session.add(linha_a)
    db_session.add(linha_b)
    db_session.commit()

    mcp_server.reconciliar_dia_tool(DIA_STR)
    ambiguos = mcp_server.listar_ambiguos_tool()
    assert len(ambiguos) == 1
    caso_id = ambiguos[0]["id"]
    linha_escolhida = ambiguos[0]["candidatos"][0]

    resultado = mcp_server.resolver_ambiguo_tool(caso_id, linha_escolhida, "benjamim")

    assert resultado["resolvido_por"] == "benjamim"
    assert mcp_server.listar_ambiguos_tool() == []
