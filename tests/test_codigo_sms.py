"""Código SMS da CGD introduzido no dashboard (app/services/codigo_sms.py):
o exe abre o pedido e vai buscar o código sem chave; listar e entregar
pedem a chave da API."""
import pytest

from app.services import codigo_sms

CHAVE = "chave-de-teste"
ID = "0123456789abcdef0123456789abcdef"
COM_CHAVE = {"X-API-Key": CHAVE}


@pytest.fixture(autouse=True)
def _limpo(monkeypatch):
    monkeypatch.setenv("API_TOKEN", CHAVE)
    codigo_sms.limpar_tudo()
    yield
    codigo_sms.limpar_tudo()


def test_exe_abre_dashboard_responde_exe_levanta(client):
    r = client.post("/monitorizacao/sms/pedidos", json={"id": ID, "script": "extratos_cgd_anual",
                                                        "descricao": "Extração anual"})
    assert r.status_code == 200
    assert client.get(f"/monitorizacao/sms/pedidos/{ID}/codigo").json() == {"codigo": None}

    pedidos = client.get("/monitorizacao/sms/pedidos", headers=COM_CHAVE).json()["pedidos"]
    assert [p["id"] for p in pedidos] == [ID]
    assert pedidos[0]["descricao"] == "Extração anual"

    r = client.post(f"/monitorizacao/sms/pedidos/{ID}/codigo", json={"codigo": "123456"}, headers=COM_CHAVE)
    assert r.status_code == 200
    # respondido: sai da lista do dashboard e não aceita um segundo código
    assert client.get("/monitorizacao/sms/pedidos", headers=COM_CHAVE).json()["pedidos"] == []
    r = client.post(f"/monitorizacao/sms/pedidos/{ID}/codigo", json={"codigo": "654321"}, headers=COM_CHAVE)
    assert r.status_code == 409

    assert client.get(f"/monitorizacao/sms/pedidos/{ID}/codigo").json() == {"codigo": "123456"}
    # só se levanta uma vez
    assert client.get(f"/monitorizacao/sms/pedidos/{ID}/codigo").status_code == 404


def test_listar_e_entregar_pedem_a_chave(client):
    client.post("/monitorizacao/sms/pedidos", json={"id": ID, "script": "extratos_cgd"})
    assert client.get("/monitorizacao/sms/pedidos").status_code == 401
    assert client.post(f"/monitorizacao/sms/pedidos/{ID}/codigo", json={"codigo": "1"}).status_code == 401
    # o exe fecha sem chave (respondido no popup)
    assert client.delete(f"/monitorizacao/sms/pedidos/{ID}").status_code == 200
    assert client.get("/monitorizacao/sms/pedidos", headers=COM_CHAVE).json()["pedidos"] == []


def test_id_e_codigo_invalidos(client):
    assert client.post("/monitorizacao/sms/pedidos", json={"id": "curto", "script": "x"}).status_code == 422
    client.post("/monitorizacao/sms/pedidos", json={"id": ID, "script": "x"})
    r = client.post(f"/monitorizacao/sms/pedidos/{ID}/codigo", json={"codigo": "12ab"}, headers=COM_CHAVE)
    assert r.status_code == 422


def test_pedido_expira():
    codigo_sms.abrir_pedido(ID, "extratos_cgd", agora=1000)
    assert len(codigo_sms.listar_pendentes(agora=1000 + codigo_sms.VALIDADE_PEDIDO_S - 1)) == 1
    assert codigo_sms.listar_pendentes(agora=1000 + codigo_sms.VALIDADE_PEDIDO_S + 1) == []
    with pytest.raises(KeyError):
        codigo_sms.levantar_codigo(ID, agora=1000 + codigo_sms.VALIDADE_PEDIDO_S + 1)
