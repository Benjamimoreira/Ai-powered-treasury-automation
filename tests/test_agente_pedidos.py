from datetime import datetime, timedelta, timezone

from scripts import agente_pedidos


def _pedido(id_, script, ha_minutos):
    pedido_em = datetime.now(timezone.utc) - timedelta(minutes=ha_minutos)
    return {"id": id_, "script": script, "estado": "pendente",
            "pedido_em": pedido_em.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"}


def test_agente_lanca_pendentes_e_expira_os_antigos(monkeypatch):
    pedidos = [_pedido(2, "enviar_mapa_smtp", ha_minutos=60), _pedido(1, "preencher_mapa", ha_minutos=1)]
    lancados, marcados = [], []

    def api_falsa(metodo, caminho, corpo=None):
        if metodo == "GET":
            return {"pedidos": pedidos}
        marcados.append((caminho, corpo))

    monkeypatch.setattr(agente_pedidos, "_api", api_falsa)
    monkeypatch.setattr(agente_pedidos, "lancar", lancados.append)

    assert agente_pedidos.tratar_pendentes() == 2

    # o de há uma hora não corre (um envio do Mapa fora de horas seria pior que nada)
    assert lancados == ["preencher_mapa"]
    assert marcados[0] == ("/monitorizacao/pedidos/1/estado", {"estado": "iniciado", "erro": None})
    assert marcados[1][0] == "/monitorizacao/pedidos/2/estado"
    assert marcados[1][1]["estado"] == "erro" and marcados[1][1]["erro"].startswith("Expirado")


def test_agente_marca_erro_quando_nao_consegue_lancar(monkeypatch):
    marcados = []

    def api_falsa(metodo, caminho, corpo=None):
        if metodo == "GET":
            return {"pedidos": [_pedido(3, "preencher_mapa", ha_minutos=0)]}
        marcados.append(corpo)

    def lancar_falha(script):
        raise RuntimeError("Pasta dos scripts não encontrada")

    monkeypatch.setattr(agente_pedidos, "_api", api_falsa)
    monkeypatch.setattr(agente_pedidos, "lancar", lancar_falha)

    agente_pedidos.tratar_pendentes()

    assert marcados == [{"estado": "erro", "erro": "Pasta dos scripts não encontrada"}]


def test_usa_o_python_do_venv_da_pasta_dos_scripts(tmp_path, monkeypatch):
    monkeypatch.setattr(agente_pedidos, "PASTA_SCRIPTS", tmp_path)
    monkeypatch.setattr(agente_pedidos, "_lancador", lambda nome: f"{nome}.exe")
    # sem .venv: Python Launcher, como antes
    assert agente_pedidos._executavel("pyw") == ["pyw.exe", "-3"]

    scripts = tmp_path / ".venv" / "Scripts"
    scripts.mkdir(parents=True)
    (scripts / "pythonw.exe").write_bytes(b"")
    (scripts / "python.exe").write_bytes(b"")
    assert agente_pedidos._executavel("pyw") == [str(scripts / "pythonw.exe")]
    assert agente_pedidos._executavel("py") == [str(scripts / "python.exe")]
