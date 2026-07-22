from datetime import date, timedelta

from app.db.models import MovimentoBancario
from app.services.anomalias import detetar_anomalias_do_dia

DIA = date(2026, 7, 21)


def _adicionar_movimento(db_session, dia, empresa, valor, descricao="TRANSF"):
    db_session.add(MovimentoBancario(
        dia=dia, empresa=empresa, descricao=descricao, valor=valor, ficheiro_origem="x.xlsx",
    ))


def test_detetar_anomalias_assinala_valor_muito_fora_do_padrao(db_session):
    valores_normais = [-48.0, -50.0, -52.0, -49.0, -51.0, -47.0, -53.0, -50.0, -49.5, -50.5]
    for i, valor in enumerate(valores_normais):
        _adicionar_movimento(db_session, DIA - timedelta(days=i + 1), "EMPRESA TESTE,LDA", valor)

    _adicionar_movimento(db_session, DIA, "EMPRESA TESTE,LDA", -50.0, descricao="NORMAL")
    _adicionar_movimento(db_session, DIA, "EMPRESA TESTE,LDA", -8000.0, descricao="MUITO FORA DO PADRAO")
    db_session.commit()

    resultado = detetar_anomalias_do_dia(db_session, DIA)

    descricoes_assinaladas = {r["descricao"] for r in resultado}
    assert "MUITO FORA DO PADRAO" in descricoes_assinaladas
    assert "NORMAL" not in descricoes_assinaladas


def test_detetar_anomalias_ignora_empresa_com_pouco_historico(db_session):
    for i in range(3):
        _adicionar_movimento(db_session, DIA - timedelta(days=i + 1), "EMPRESA NOVA,LDA", -50.0)
    _adicionar_movimento(db_session, DIA, "EMPRESA NOVA,LDA", -9999.0)
    db_session.commit()

    resultado = detetar_anomalias_do_dia(db_session, DIA)

    assert resultado == []


def test_detetar_anomalias_sem_movimentos_no_dia_devolve_vazio(db_session):
    resultado = detetar_anomalias_do_dia(db_session, DIA)
    assert resultado == []
