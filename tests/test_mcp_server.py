from datetime import date, timedelta

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


def test_listar_empresas_tool(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-10.0, ficheiro_origem="x.xlsx",
    ))
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="PALAVRADICIONAL,LDA", descricao="TRANSF", valor=-20.0, ficheiro_origem="x.xlsx",
    ))
    db_session.commit()

    resultado = mcp_server.listar_empresas_tool()

    assert set(resultado) == {"ANCORA APOGEU,LDA", "PALAVRADICIONAL,LDA"}


def test_saldo_total_tool(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    db_session.add(SaldoDiario(
        dia=DIA, entidade="Ancora Apogeu", saldo_contabilistico=100.0, saldo_disponivel=90.0,
    ))
    db_session.add(SaldoDiario(
        dia=DIA, entidade="Palavradicional", saldo_contabilistico=50.0, saldo_disponivel=40.0,
    ))
    db_session.commit()

    resultado = mcp_server.saldo_total_tool()

    assert resultado["entidades"] == 2
    assert resultado["saldo_contabilistico_total"] == 150.0
    assert resultado["saldo_disponivel_total"] == 130.0


def test_listar_saldos_tool(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    db_session.add(SaldoDiario(
        dia=DIA, entidade="Ancora Apogeu", saldo_contabilistico=100.0, saldo_disponivel=90.0,
    ))
    db_session.commit()

    resultado = mcp_server.listar_saldos_tool()

    assert len(resultado) == 1
    assert resultado[0]["entidade"] == "Ancora Apogeu"
    assert resultado[0]["dia"] == DIA_STR


def _sem_fontes_externas(monkeypatch):
    """Rendas (OneDrive) e índice comercial ficam de fora dos testes."""
    monkeypatch.setattr("app.services.previsao_ancorada.recebimentos_comerciais_por_empresa", lambda db: [])
    monkeypatch.setattr("app.services.previsao._onedrive_raiz", lambda: "/nao/existe")


def test_previsao_saldo_tool(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    _sem_fontes_externas(monkeypatch)
    for i in range(10):
        db_session.add(SaldoDiario(
            dia=DIA + timedelta(days=i), entidade="Ancora Apogeu",
            saldo_contabilistico=100.0 + 10 * i, saldo_disponivel=100.0,
        ))
    db_session.commit()

    resultado = mcp_server.previsao_saldo_tool("Ancora Apogeu", dias=14)

    assert resultado["saldo_atual"] == 190.0
    assert resultado["dias"] == 14
    # sem fluxos conhecidos a previsão não extrapola a subida do histórico
    assert resultado["saldo_previsto"] == 190.0
    assert resultado["previsao_por_semana"][-1]["dia"] == (DIA + timedelta(days=9 + 14)).isoformat()
    assert resultado["maiores_fluxos_conhecidos"] == []
    assert resultado["fluxos_conhecidos_por_tipo"] == {}


def test_avaliar_previsao_tool(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    _sem_fontes_externas(monkeypatch)
    for i in range(40):
        db_session.add(SaldoDiario(
            dia=DIA + timedelta(days=i), entidade="Ancora Apogeu",
            saldo_contabilistico=100.0 + 10 * i, saldo_disponivel=100.0,
        ))
    db_session.commit()

    resultado = mcp_server.avaliar_previsao_tool("Ancora Apogeu", dias=7)

    assert resultado["cortes_avaliados"] > 0
    assert resultado["erro_medio_previsao"] == resultado["erro_medio_saldo_fica_igual"]


def test_faturas_recebidas_tool_pesquisa(db_session, session_factory, monkeypatch):
    from app.db.models import FaturaRecebida

    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    for i, fornecedor in enumerate(("EDP Comercial", "Aguas do Porto")):
        db_session.add(FaturaRecebida(
            outlook_id=f"id-{i}", dia=DIA, hora="10:00", fornecedor=fornecedor, empresa="Ancora Apogeu",
        ))
    db_session.commit()

    resultado = mcp_server.faturas_recebidas_tool(pesquisa="edp")

    assert [f["fornecedor"] for f in resultado] == ["EDP Comercial"]
    assert resultado[0]["tem_pdf"] is False


def test_anomalias_do_dia_tool(db_session, session_factory, monkeypatch):
    monkeypatch.setattr(mcp_server, "SessionLocal", session_factory)
    valores_normais = [-48.0, -50.0, -52.0, -49.0, -51.0, -47.0, -53.0, -50.0, -49.5, -50.5]
    for i, valor in enumerate(valores_normais):
        db_session.add(MovimentoBancario(
            dia=DIA - timedelta(days=i + 1), empresa="EMPRESA TESTE,LDA", descricao="TRANSF",
            valor=valor, ficheiro_origem="x.xlsx",
        ))
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="EMPRESA TESTE,LDA", descricao="MUITO FORA DO PADRAO",
        valor=-8000.0, ficheiro_origem="x.xlsx",
    ))
    db_session.commit()

    resultado = mcp_server.anomalias_do_dia_tool(DIA_STR)

    assert len(resultado) == 1
    assert resultado[0]["descricao"] == "MUITO FORA DO PADRAO"
    assert resultado[0]["dia"] == DIA_STR
