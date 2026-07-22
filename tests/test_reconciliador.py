from datetime import date

from app.db.models import CasoAmbiguo, LinhaMapa, MovimentoBancario, Reconciliacao
from app.services.reconciliador import auditoria_dia, chave_empresa, reconciliar_dia, resolver_ambiguo

DIA = date(2026, 7, 21)


def test_chave_empresa_ignora_forma_legal():
    assert chave_empresa("ANCORA APOGEU,LDA") == chave_empresa("Ancora Apogeu")


def test_reconciliar_dia_casa_movimento_exato(db_session):
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-100.0,
        ficheiro_origem="x.xlsx",
    ))
    db_session.add(LinhaMapa(
        dia=DIA, tipo="pagamento", linha=5, empresa="Ancora Apogeu", previsto=-100.0,
    ))
    db_session.commit()

    resultado = reconciliar_dia(db_session, DIA)

    assert resultado == {"casados": 1, "novos": 0, "ambiguos": 0}
    linha = db_session.query(LinhaMapa).one()
    assert linha.pago == -100.0
    reconciliacao = db_session.query(Reconciliacao).one()
    assert reconciliacao.tipo_match == "exato"
    assert reconciliacao.linha_id == linha.id


def test_reconciliar_dia_ignora_linha_ja_paga(db_session):
    """Uma linha com previsto e pago já preenchidos (resolvida antes de
    existir esta API, ex. diretamente no Excel) não deve voltar a ser
    candidata - senão um movimento novo "rouba" uma linha antiga já
    fechada em vez de ficar corretamente marcado como novo/ambíguo."""
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-100.0,
        ficheiro_origem="x.xlsx",
    ))
    db_session.add(LinhaMapa(
        dia=DIA, tipo="pagamento", linha=5, empresa="Ancora Apogeu",
        previsto=-100.0, pago=-100.0,
    ))
    db_session.commit()

    resultado = reconciliar_dia(db_session, DIA)

    assert resultado == {"casados": 0, "novos": 1, "ambiguos": 0}


def test_reconciliar_dia_marca_novo_sem_linha_correspondente(db_session):
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="EMPRESA SEM MAPA,LDA", descricao="TRANSF", valor=-50.0,
        ficheiro_origem="x.xlsx",
    ))
    db_session.commit()

    resultado = reconciliar_dia(db_session, DIA)

    assert resultado == {"casados": 0, "novos": 1, "ambiguos": 0}


def test_reconciliar_dia_marca_ambiguo_com_duas_linhas_candidatas(db_session):
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-100.0,
        ficheiro_origem="x.xlsx",
    ))
    db_session.add(LinhaMapa(dia=DIA, tipo="pagamento", linha=5, empresa="Ancora Apogeu", previsto=-100.0))
    db_session.add(LinhaMapa(dia=DIA, tipo="pagamento", linha=9, empresa="Ancora Apogeu", previsto=-100.0))
    db_session.commit()

    resultado = reconciliar_dia(db_session, DIA)

    assert resultado == {"casados": 0, "novos": 0, "ambiguos": 1}
    caso = db_session.query(CasoAmbiguo).one()
    assert len(caso.candidatos) == 2


def test_reconciliar_dia_e_idempotente(db_session):
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-100.0,
        ficheiro_origem="x.xlsx",
    ))
    db_session.add(LinhaMapa(dia=DIA, tipo="pagamento", linha=5, empresa="Ancora Apogeu", previsto=-100.0))
    db_session.commit()

    reconciliar_dia(db_session, DIA)
    segunda_corrida = reconciliar_dia(db_session, DIA)

    assert segunda_corrida == {"casados": 0, "novos": 0, "ambiguos": 0}
    assert db_session.query(Reconciliacao).count() == 1


def test_auditoria_dia_conta_sem_correspondencia_nos_dois_sentidos(db_session):
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="SEM MAPA,LDA", descricao="TRANSF", valor=-50.0, ficheiro_origem="x.xlsx",
    ))
    db_session.add(LinhaMapa(dia=DIA, tipo="pagamento", linha=5, empresa="Outra Empresa", previsto=-30.0))
    db_session.commit()

    resultado = auditoria_dia(db_session, DIA)

    assert resultado == {"sem_match_fwd": 1, "sem_match_rev": 1}


def test_resolver_ambiguo_associa_linha_escolhida(db_session):
    db_session.add(MovimentoBancario(
        dia=DIA, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-100.0,
        ficheiro_origem="x.xlsx",
    ))
    linha_a = LinhaMapa(dia=DIA, tipo="pagamento", linha=5, empresa="Ancora Apogeu", previsto=-100.0)
    linha_b = LinhaMapa(dia=DIA, tipo="pagamento", linha=9, empresa="Ancora Apogeu", previsto=-100.0)
    db_session.add(linha_a)
    db_session.add(linha_b)
    db_session.commit()

    reconciliar_dia(db_session, DIA)
    caso = db_session.query(CasoAmbiguo).one()

    resolver_ambiguo(db_session, caso.id, linha_a.id, resolvido_por="benjamim")

    db_session.refresh(caso)
    db_session.refresh(linha_a)
    assert caso.resolvido_por == "benjamim"
    assert linha_a.pago == -100.0
    reconciliacao = db_session.query(Reconciliacao).one()
    assert reconciliacao.tipo_match == "exato"
    assert reconciliacao.linha_id == linha_a.id
