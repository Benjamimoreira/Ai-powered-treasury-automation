from datetime import date

from app.db.models import LinhaMapa, MovimentoBancario, SaldoDiario

DIA = "2026-07-21"
DIA_DATE = date(2026, 7, 21)


def test_raiz_responde_ok(client):
    resposta = client.get("/")
    assert resposta.status_code == 200
    assert resposta.json() == {"status": "ok"}


def test_reconciliar_endpoint_casa_movimento(client, db_session):
    db_session.add(MovimentoBancario(
        dia=DIA_DATE, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-100.0,
        ficheiro_origem="x.xlsx",
    ))
    db_session.add(LinhaMapa(dia=DIA_DATE, tipo="pagamento", linha=5, empresa="Ancora Apogeu", previsto=-100.0))
    db_session.commit()

    resposta = client.post(f"/reconciliar/{DIA}")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["casados"] == 1
    assert corpo["novos"] == 0
    assert corpo["ambiguos"] == 0


def test_auditoria_endpoint(client, db_session):
    db_session.add(MovimentoBancario(
        dia=DIA_DATE, empresa="SEM MAPA,LDA", descricao="TRANSF", valor=-50.0, ficheiro_origem="x.xlsx",
    ))
    db_session.commit()

    resposta = client.get(f"/auditoria/{DIA}")

    assert resposta.status_code == 200
    assert resposta.json()["sem_match_fwd"] == 1


def test_ambiguos_listar_e_resolver(client, db_session):
    db_session.add(MovimentoBancario(
        dia=DIA_DATE, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-100.0,
        ficheiro_origem="x.xlsx",
    ))
    db_session.add(LinhaMapa(dia=DIA_DATE, tipo="pagamento", linha=5, empresa="Ancora Apogeu", previsto=-100.0))
    db_session.add(LinhaMapa(dia=DIA_DATE, tipo="pagamento", linha=9, empresa="Ancora Apogeu", previsto=-100.0))
    db_session.commit()
    client.post(f"/reconciliar/{DIA}")

    lista = client.get("/ambiguos")
    assert lista.status_code == 200
    casos = lista.json()
    assert len(casos) == 1
    caso_id = casos[0]["id"]
    linha_escolhida = casos[0]["candidatos"][0]

    resolvido = client.post(
        f"/ambiguos/{caso_id}/resolver",
        json={"linha_id": linha_escolhida, "resolvido_por": "benjamim"},
    )
    assert resolvido.status_code == 200
    assert resolvido.json()["resolvido_por"] == "benjamim"

    lista_depois = client.get("/ambiguos")
    assert lista_depois.json() == []


def test_saldos_consulta_por_empresa(client, db_session):
    db_session.add(SaldoDiario(
        dia=DIA_DATE, entidade="Ancora Apogeu",
        saldo_contabilistico=1234.56, saldo_disponivel=1200.0,
    ))
    db_session.commit()

    resposta = client.get("/saldos/ANCORA APOGEU,LDA")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert len(corpo) == 1
    assert corpo[0]["saldo_contabilistico"] == 1234.56
