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


def test_auditoria_historico_endpoint(client, db_session):
    db_session.add(MovimentoBancario(
        dia=DIA_DATE, empresa="SEM MAPA,LDA", descricao="TRANSF", valor=-50.0, ficheiro_origem="x.xlsx",
    ))
    db_session.commit()

    from app.services.reconciliador import registar_auditoria_dia
    registar_auditoria_dia(db_session, DIA_DATE)

    resposta = client.get("/auditoria/historico")

    assert resposta.status_code == 200
    historico = resposta.json()["historico"]
    assert len(historico) == 1
    assert historico[0]["dia"] == DIA


def test_auditoria_geral_endpoint_recupera_sessao_apos_erro_num_dia(client, db_session, monkeypatch):
    """Regressão: se registar_auditoria_dia falhar (ex.: erro de BD) a
    meio do loop de dias, a sessão SQLAlchemy fica "suja" até se chamar
    rollback() - sem isso, todos os dias seguintes falhavam em cascata,
    mesmo sem nada de errado com eles."""
    from app.services import onedrive_sync
    from app.db.models import AuditoriaDia
    import app.routers.reconciliacao as reconciliacao_router

    monkeypatch.setattr(onedrive_sync, "atualizar_dados_recentes", lambda db, dias_atras: None)

    dia1, dia2 = date(2026, 7, 20), date(2026, 7, 21)
    db_session.add(MovimentoBancario(
        dia=dia1, empresa="A,LDA", descricao="TRANSF", valor=-10.0, ficheiro_origem="x.xlsx",
    ))
    db_session.add(MovimentoBancario(
        dia=dia2, empresa="B,LDA", descricao="TRANSF", valor=-20.0, ficheiro_origem="x.xlsx",
    ))
    db_session.commit()

    original = reconciliacao_router.registar_auditoria_dia
    estado = {"chamadas": 0}

    def registar_com_falha_no_primeiro_dia(db, dia):
        estado["chamadas"] += 1
        if estado["chamadas"] == 1:
            # simula uma falha real de BD a meio do commit (viola a
            # coluna NOT NULL "dia" de auditorias_dia via um INSERT feito
            # pelo ORM, tal como o próprio registar_auditoria_dia faz) -
            # deixa a sessão "suja" (PendingRollbackError) tal como
            # aconteceria com um erro de integridade genuíno
            db.add(AuditoriaDia(
                dia=None, sem_match_fwd=0, sem_match_rev=0, soma_extrato=0, soma_mapa=0, diferenca=0,
            ))
            db.commit()
        return original(db, dia)

    monkeypatch.setattr(reconciliacao_router, "registar_auditoria_dia", registar_com_falha_no_primeiro_dia)

    resposta = client.post("/auditoria/geral")

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["dias_auditados"] == 2
    assert "erro" in corpo["resultados"][dia1.isoformat()]
    assert corpo["resultados"][dia2.isoformat()]["sem_match_fwd"] == 1


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


def test_monitorizacao_regista_e_lista_evento_em_tempo_real(client):
    resposta = client.post(
        "/monitorizacao/scripts/preencher_mapa/eventos",
        json={"nivel": "erro", "mensagem": "Falha a meio da corrida"},
    )

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["script"] == "preencher_mapa"
    assert corpo["nivel"] == "erro"
    assert corpo["mensagem"] == "Falha a meio da corrida"

    eventos = client.get("/monitorizacao/eventos")
    assert eventos.status_code == 200
    assert any(
        e["script"] == "preencher_mapa" and e["nivel"] == "erro" and e["mensagem"] == "Falha a meio da corrida"
        for e in eventos.json()["eventos"]
    )


def test_monitorizacao_lista_eventos_filtra_por_script(client):
    client.post("/monitorizacao/scripts/preencher_mapa/eventos", json={"nivel": "erro", "mensagem": "a"})
    client.post("/monitorizacao/scripts/enviar_mapa_smtp/eventos", json={"nivel": "info", "mensagem": "b"})

    eventos = client.get("/monitorizacao/eventos", params={"script": "enviar_mapa_smtp"})

    assert eventos.status_code == 200
    scripts = {e["script"] for e in eventos.json()["eventos"]}
    assert scripts == {"enviar_mapa_smtp"}


def test_correr_script_em_docker_grava_pedido_para_o_agente(client, monkeypatch):
    # API em Docker: a pasta dos scripts não existe aqui
    monkeypatch.delenv("SCRIPTS_PREENCHIMENTO_RAIZ", raising=False)
    monkeypatch.delenv("SCRIPTS_LOG_DIR", raising=False)

    primeiro = client.post("/monitorizacao/scripts/enviar_mapa_smtp/correr")
    segundo = client.post("/monitorizacao/scripts/enviar_mapa_smtp/correr")

    assert primeiro.status_code == 200
    assert primeiro.json()["status"] == "pedido"
    # dois cliques seguidos = um só pedido
    assert segundo.json()["pedido"]["id"] == primeiro.json()["pedido"]["id"]
    pendentes = client.get("/monitorizacao/pedidos", params={"estado": "pendente"}).json()["pedidos"]
    assert [p["script"] for p in pendentes] == ["enviar_mapa_smtp"]

    pedido_id = pendentes[0]["id"]
    marcado = client.post(f"/monitorizacao/pedidos/{pedido_id}/estado", json={"estado": "iniciado"})
    assert marcado.status_code == 200
    assert marcado.json()["estado"] == "iniciado"
    assert marcado.json()["iniciado_em"]
    # já tratado: outro agente não o volta a lançar
    assert client.post(f"/monitorizacao/pedidos/{pedido_id}/estado", json={"estado": "iniciado"}).status_code == 409
    assert client.get("/monitorizacao/pedidos", params={"estado": "pendente"}).json()["pedidos"] == []


def test_extratos_prontos_pede_preencher_mapa_e_sincroniza_dia_e_anterior(client, monkeypatch):
    from app.routers import sync

    monkeypatch.delenv("SCRIPTS_PREENCHIMENTO_RAIZ", raising=False)
    monkeypatch.delenv("SCRIPTS_LOG_DIR", raising=False)
    sincronizados = []

    def sincronizar_falso(db, dia):
        sincronizados.append(dia.isoformat())
        return {"dias_com_movimentos_novos": [dia.isoformat()], "dias_com_saldos_novos": [],
                "dias_com_mapa_novo": [], "erros": []}

    monkeypatch.setattr(sync, "atualizar_dados_do_dia", sincronizar_falso)

    resposta = client.post("/extratos/prontos", params={"dia": "2026-10-08"})

    assert resposta.status_code == 200
    corpo = resposta.json()
    assert corpo["preencher_mapa"]["status"] == "pedido"
    assert sincronizados == ["2026-10-07", "2026-10-08"]
    assert corpo["sincronizacao"]["dias_com_movimentos_novos"] == ["2026-10-07", "2026-10-08"]
    # o envio do Mapa não entra na cadeia (fica à hora fixa)
    pendentes = client.get("/monitorizacao/pedidos", params={"estado": "pendente"}).json()["pedidos"]
    assert [p["script"] for p in pendentes] == ["preencher_mapa"]


def test_correr_script_desconhecido_da_404(client):
    assert client.post("/monitorizacao/scripts/avaliacao_online/correr").status_code == 404
    assert client.post("/monitorizacao/scripts/nao_existe/correr").status_code == 404


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
