from datetime import date

from app.db.models import LinhaMapa, PedidoCorrida

LINHA = {"dia": 27, "mes": 10, "ano": 2026, "tipo": "pagamento", "descricao": "IMI 2.ª prestação",
         "valor": 1234.5, "imputacao": "Impostos"}


def test_guarda_a_previsao_e_pede_ao_agente(client, db_session):
    r = client.post("/previsoes-manuais", json=LINHA)
    assert r.status_code == 200
    corpo = r.json()
    assert (corpo["dia"], corpo["tipo"], corpo["valor"], corpo["estado"]) == ("2026-10-27", "pagamento", 1234.5, "pendente")
    assert corpo["mapa"] == "pedido"
    assert db_session.query(PedidoCorrida).filter_by(script="aplicar_previsoes_manuais").count() == 1

    # o script vai buscar as por aplicar, marca e deixam de aparecer
    pendentes = client.get("/previsoes-manuais", params={"por_aplicar": True}).json()["previsoes"]
    assert [p["id"] for p in pendentes] == [corpo["id"]]
    assert client.post(f"/previsoes-manuais/{corpo['id']}/estado", json={"estado": "aplicada"}).json()["estado"] == "aplicada"
    assert client.get("/previsoes-manuais", params={"por_aplicar": True}).json()["previsoes"] == []
    # aplicada já não muda nem se apaga pela API
    assert client.post(f"/previsoes-manuais/{corpo['id']}/estado", json={"estado": "erro"}).status_code == 409
    assert client.delete(f"/previsoes-manuais/{corpo['id']}").status_code == 409


def test_erro_volta_a_ser_tentado_e_pode_apagar(client):
    pid = client.post("/previsoes-manuais", json=LINHA).json()["id"]
    r = client.post(f"/previsoes-manuais/{pid}/estado", json={"estado": "erro", "erro": "Mapa aberto no Excel"}).json()
    assert (r["estado"], r["erro"]) == ("erro", "Mapa aberto no Excel")
    assert [p["id"] for p in client.get("/previsoes-manuais", params={"por_aplicar": True}).json()["previsoes"]] == [pid]
    assert client.post("/previsoes-manuais/aplicar").json()["mapa"] == "pedido"
    assert client.delete(f"/previsoes-manuais/{pid}").status_code == 200
    assert client.get("/previsoes-manuais").json()["previsoes"] == []


def test_validacao(client):
    assert client.post("/previsoes-manuais", json={**LINHA, "dia": 31, "mes": 11}).status_code == 422  # 31/11
    assert client.post("/previsoes-manuais", json={**LINHA, "valor": 0}).status_code == 422
    assert client.post("/previsoes-manuais", json={**LINHA, "descricao": "  "}).status_code == 422
    assert client.post("/previsoes-manuais", json={**LINHA, "imputacao": ""}).status_code == 422
    assert client.post("/previsoes-manuais", json={**LINHA, "tipo": "outro"}).status_code == 422


def test_imputacoes_do_mapa_e_das_previsoes(client, db_session):
    db_session.add_all([
        LinhaMapa(dia=date(2026, 10, 2), tipo="pagamento", linha=5, empresa="H.I.I", descricao="x", imputacao="Ordenados"),
        LinhaMapa(dia=date(2026, 10, 3), tipo="pagamento", linha=5, empresa="H.I.I", descricao="y", imputacao="Ordenados"),
        LinhaMapa(dia=date(2026, 10, 2), tipo="recebimento", linha=5, empresa="H.I.I", descricao="z", imputacao="Rendas"),
    ])
    db_session.commit()
    client.post("/previsoes-manuais", json=LINHA)
    assert client.get("/previsoes-manuais/imputacoes").json()["imputacoes"] == ["Impostos", "Ordenados", "Rendas"]
    assert client.get("/previsoes-manuais/imputacoes", params={"tipo": "recebimento"}).json()["imputacoes"] == ["Rendas"]


def test_previsoes_entram_na_previsao_de_saldo_do_grupo(db_session):
    from datetime import timedelta

    from app.db.models import PrevisaoManual
    from app.services.previsao import _previsto_mapa_por_dia
    from app.services.previsao_ancorada import carregar_contexto, fluxos_conhecidos_no_horizonte
    from app.services.previsoes_manuais import linhas_previstas

    amanha, depois = date.today() + timedelta(days=1), date.today() + timedelta(days=5)
    db_session.add_all([
        PrevisaoManual(dia=amanha, tipo="pagamento", descricao="Flexinsstal", valor=17193.39, imputacao="Equipamentos",
                       estado="aplicada"),
        PrevisaoManual(dia=depois, tipo="recebimento", descricao="Sinal Loja A", valor=5000, imputacao="Sinais"),
        PrevisaoManual(dia=date.today() - timedelta(days=3), tipo="pagamento", descricao="Antiga", valor=1,
                       imputacao="x"),
        # a mesma linha já lida do Mapa (com empresa): não conta duas vezes
        LinhaMapa(dia=depois, tipo="recebimento", linha=7, empresa="H.I.I", descricao="Sinal  loja A", previsto=5000),
    ])
    db_session.commit()

    linhas = linhas_previstas(db_session, depois_de=date.today(), linhas_mapa=db_session.query(LinhaMapa).all())
    assert [(l.dia, l.previsto, l.empresa) for l in linhas] == [(amanha, -17193.39, "")]

    contexto = carregar_contexto(db_session, recebimentos_comerciais=[])
    dias = [date.today() + timedelta(days=i) for i in range(1, 31)]
    grupo = [f for f in fluxos_conhecidos_no_horizonte(contexto, dias) if f["fonte"] == "mapa"]
    assert sorted(f["valor"] for f in grupo) == [-17193.39, 5000.0]
    # numa empresa não entra (não tem empresa)
    assert all(f["descricao"] != "Flexinsstal"
               for f in fluxos_conhecidos_no_horizonte(contexto, dias, empresa="HABISERVE INVESTIMENTOS IMOBILIARIOS,LDA"))
    # no backtest (ate) não entra
    assert carregar_contexto(db_session, ate=date.today(), recebimentos_comerciais=[]).linhas_mapa_futuras == []
    # "Previsto (Mapa)" dos gráficos dos modelos
    assert _previsto_mapa_por_dia(db_session, [amanha, depois]) == {amanha: -17193.39, depois: 5000}
