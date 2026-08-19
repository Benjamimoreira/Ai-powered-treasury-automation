from datetime import date

from app.db.models import LinhaMapa, MovimentoBancario, SaldoDiario
from app.services import llm_resolver

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
    assert len(casos[0]["candidatos_detalhe"]) == 2
    assert casos[0]["candidatos_detalhe"][0]["empresa"] == "Ancora Apogeu"

    resolvido = client.post(
        f"/ambiguos/{caso_id}/resolver",
        json={"linha_id": linha_escolhida, "resolvido_por": "benjamim"},
    )
    assert resolvido.status_code == 200
    assert resolvido.json()["resolvido_por"] == "benjamim"

    lista_depois = client.get("/ambiguos")
    assert lista_depois.json() == []


def test_ambiguos_sugerir_usa_llm_mockado_e_nao_resolve_sozinho(client, db_session, monkeypatch):
    db_session.add(MovimentoBancario(
        dia=DIA_DATE, empresa="ANCORA APOGEU,LDA", descricao="TRANSF", valor=-100.0,
        ficheiro_origem="x.xlsx",
    ))
    db_session.add(LinhaMapa(dia=DIA_DATE, tipo="pagamento", linha=5, empresa="Ancora Apogeu", previsto=-100.0))
    db_session.add(LinhaMapa(dia=DIA_DATE, tipo="pagamento", linha=9, empresa="Ancora Apogeu", previsto=-100.0))
    db_session.commit()
    client.post(f"/reconciliar/{DIA}")
    caso_id = client.get("/ambiguos").json()[0]["id"]

    monkeypatch.setattr(
        llm_resolver, "chamar_llm",
        lambda prompt: '{"linha_id": null, "justificacao": "nenhuma linha bate com o padrão histórico"}',
    )

    resposta = client.post(f"/ambiguos/{caso_id}/sugerir")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["resolucao_sugerida"] == "novo"
    assert corpo["justificacao_sugerida"] == "nenhuma linha bate com o padrão histórico"
    assert corpo["resolvido_por"] is None


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


def test_monitorizacao_lista_scripts_e_horas(client):
    resposta = client.get("/monitorizacao/scripts")

    assert resposta.status_code == 200
    corpo = resposta.json()
    nomes = {item["nome"] for item in corpo["scripts"]}
    assert {
        "preencher_mapa",
        "atualizar_mapa_saldos",
        "enviar_mapa_smtp",
    }.issubset(nomes)
    assert all("hora_execucao" in item for item in corpo["scripts"])


def test_monitorizacao_regista_execucao_e_log_erro(client):
    resposta = client.post(
        "/monitorizacao/scripts/preencher_mapa/executar",
        json={
            "status": "erro",
            "erro": "Falha ao preencher mapa",
            "log": ["iniciou", "erro ao abrir XLSX"],
            "duracao_segundos": 12.5,
        },
    )

    assert resposta.status_code == 200
    assert resposta.json()["status"] == "erro"
    assert resposta.json()["ultima_erro"] == "Falha ao preencher mapa"

    logs = client.get("/monitorizacao/logs")
    assert logs.status_code == 200
    assert any(
        item["script"] == "preencher_mapa" and item["nivel"] == "erro"
        for item in logs.json()["logs"]
    )


def test_monitorizacao_sinaliza_script_atrasado():
    from datetime import datetime
    from zoneinfo import ZoneInfo

    from app.services.monitorizacao import _verificar_atraso

    # 15 min depois das 12:50 - dentro da tolerância (20 min), ainda não conta como atrasado
    agora_dentro_tolerancia = datetime(2026, 8, 19, 13, 5, tzinfo=ZoneInfo("Europe/Lisbon"))
    atraso = _verificar_atraso("08:50, 12:50, 14:10", "2026-08-19T07:55:00Z", agora=agora_dentro_tolerancia)
    assert atraso == {"atrasado": False, "hora_em_falta": None}

    agora = datetime(2026, 8, 19, 13, 15, tzinfo=ZoneInfo("Europe/Lisbon"))

    # 12:50 já passou há mais do que a tolerância e não há execução -> atrasado
    atraso = _verificar_atraso("08:50, 12:50, 14:10", None, agora=agora)
    assert atraso == {"atrasado": True, "hora_em_falta": "12:50"}

    # execução registada depois do último horário devido -> não atrasado
    atraso = _verificar_atraso("08:50, 12:50, 14:10", "2026-08-19T11:55:00Z", agora=agora)
    assert atraso == {"atrasado": False, "hora_em_falta": None}

    # nenhum horário de hoje ainda passou (tolerância incluída) -> não atrasado
    cedo = datetime(2026, 8, 19, 8, 0, tzinfo=ZoneInfo("Europe/Lisbon"))
    atraso = _verificar_atraso("08:50, 12:50, 14:10", None, agora=cedo)
    assert atraso == {"atrasado": False, "hora_em_falta": None}
