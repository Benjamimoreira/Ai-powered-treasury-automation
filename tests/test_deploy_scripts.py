from datetime import datetime, timedelta, timezone

from scripts import agente_pedidos, deploy_windows


# ------------------------------------------------------------------ API

def test_deploy_pedido_progresso_e_fim(client):
    alvos = client.get("/monitorizacao/deploys/alvos").json()["alvos"]
    assert {a["alvo"] for a in alvos} == set(deploy_windows.ALVOS)

    primeiro = client.post("/monitorizacao/deploys", json={"alvo": "scripts_cgd"})
    assert primeiro.status_code == 200
    deploy_id = primeiro.json()["id"]
    assert primeiro.json()["estado"] == "pendente"
    # dois cliques seguidos = um só deploy
    assert client.post("/monitorizacao/deploys", json={"alvo": "scripts_cgd"}).json()["id"] == deploy_id

    pendentes = client.get("/monitorizacao/deploys", params={"estado": "pendente"}).json()["deploys"]
    assert [d["id"] for d in pendentes] == [deploy_id]

    a_correr = client.post(f"/monitorizacao/deploys/{deploy_id}/progresso", json={
        "estado": "a_correr", "passo": 2, "total_passos": 10, "mensagem": "3/10 · Compilar", "linhas": ["a", "b"]})
    assert a_correr.status_code == 200
    assert (a_correr.json()["passo"], a_correr.json()["total_passos"]) == (2, 10)
    # ainda a correr: continua a ser o deploy ativo
    assert client.post("/monitorizacao/deploys", json={"alvo": "scripts_cgd"}).json()["id"] == deploy_id

    client.post(f"/monitorizacao/deploys/{deploy_id}/progresso", json={"estado": "a_correr", "linhas": ["c"]})
    fim = client.post(f"/monitorizacao/deploys/{deploy_id}/progresso", json={"estado": "ok", "mensagem": "Concluído"})
    corpo = fim.json()
    assert corpo["estado"] == "ok" and corpo["passo"] == 10 and corpo["terminado_em"]
    assert corpo["log"] == ["a", "b", "c"]

    # terminado não volta atrás, e o próximo clique é um deploy novo
    assert client.post(f"/monitorizacao/deploys/{deploy_id}/progresso", json={"estado": "a_correr"}).status_code == 409
    assert client.post("/monitorizacao/deploys", json={"alvo": "scripts_cgd"}).json()["id"] != deploy_id


def test_deploy_alvo_desconhecido_e_estado_invalido(client):
    assert client.post("/monitorizacao/deploys", json={"alvo": "nao_existe"}).status_code == 404
    deploy_id = client.post("/monitorizacao/deploys", json={"alvo": "tesouraria_preenchimento"}).json()["id"]
    assert client.post(f"/monitorizacao/deploys/{deploy_id}/progresso", json={"estado": "pendente"}).status_code == 422
    assert client.get("/monitorizacao/deploys/999999").status_code == 404


# ---------------------------------------------------------------- agente

def _deploy(id_, alvo, ha_minutos):
    pedido_em = datetime.now(timezone.utc) - timedelta(minutes=ha_minutos)
    return {"id": id_, "alvo": alvo, "estado": "pendente",
            "pedido_em": pedido_em.replace(tzinfo=None).isoformat(timespec="seconds") + "Z"}


def test_agente_corre_um_deploy_de_cada_vez_e_expira_os_antigos(monkeypatch):
    deploys = [_deploy(3, "tesouraria_preenchimento", 1), _deploy(2, "scripts_cgd", 2), _deploy(1, "scripts_cgd", 90)]
    enviados, corridos = [], []

    def api_falsa(metodo, caminho, corpo=None):
        if metodo == "GET":
            return {"deploys": deploys}
        enviados.append((caminho, corpo))

    def correr_falso(alvo, reportar):
        corridos.append(alvo)
        reportar(estado="ok")
        return True

    monkeypatch.setattr(agente_pedidos, "_api", api_falsa)
    monkeypatch.setattr(agente_pedidos, "_deploy_em_curso", None)
    monkeypatch.setattr(agente_pedidos.deploy_windows, "correr", correr_falso)

    assert agente_pedidos.tratar_deploys() == 3
    agente_pedidos._deploy_em_curso.join()

    # o de há 90 min expira; dos outros, só o mais antigo corre agora
    assert corridos == ["scripts_cgd"]
    assert enviados[0][0] == "/monitorizacao/deploys/1/progresso"
    assert enviados[0][1]["estado"] == "erro" and enviados[0][1]["erro"].startswith("Expirado")
    assert enviados[1] == ("/monitorizacao/deploys/2/progresso",
                           {"estado": "a_correr", "passo": 0, "mensagem": "Iniciado pelo agente do Windows"})
    assert enviados[2] == ("/monitorizacao/deploys/2/progresso", {"estado": "ok"})


# ------------------------------------------------------- passos do deploy

def test_passos_reportam_progresso_e_param_no_primeiro_erro(monkeypatch):
    feitos = []

    def passo_ok(ctx):
        feitos.append("ok")
        ctx.linha("linha do passo")

    def passo_falha(ctx):
        raise deploy_windows.FalhaDeploy("compilação falhou")

    monkeypatch.setitem(deploy_windows.ALVOS, "teste", [("Um", passo_ok), ("Dois", passo_falha), ("Três", passo_ok)])
    reportes = []
    assert deploy_windows.correr("teste", lambda **campos: reportes.append(campos)) is False

    assert feitos == ["ok"]
    assert [r.get("passo") for r in reportes if "passo" in r and r["estado"] == "a_correr"] == [0, 1]
    final = reportes[-1]
    assert final["estado"] == "erro" and final["erro"] == "compilação falhou"
    assert final["mensagem"] == "Falhou em: Dois"
    linhas = [l for r in reportes for l in r.get("linhas", [])]
    assert "linha do passo" in linhas and "[ERRO] compilação falhou" in linhas


def test_passos_todos_ok_terminam_com_a_barra_cheia(monkeypatch):
    monkeypatch.setitem(deploy_windows.ALVOS, "teste", [("Um", lambda ctx: None), ("Dois", lambda ctx: None)])
    reportes = []
    assert deploy_windows.correr("teste", lambda **campos: reportes.append(campos)) is True
    assert reportes[-1]["estado"] == "ok"
    assert (reportes[-1]["passo"], reportes[-1]["total_passos"]) == (2, 2)


def test_instalar_exes_guarda_o_anterior_como_backup(tmp_path, monkeypatch):
    origem = tmp_path / "dev"
    (origem / "dist").mkdir(parents=True)
    instalada = tmp_path / "Scripts CGD"
    monkeypatch.setattr(deploy_windows, "PASTA_CGD", origem)
    monkeypatch.setattr(deploy_windows, "PASTA_CGD_INSTALADA", instalada)
    monkeypatch.setattr(deploy_windows, "EXES_CGD", [("MovimentosCGD", "Movimentos CGD")])
    (origem / "dist" / "MovimentosCGD.exe").write_bytes(b"novo")
    (instalada / "Movimentos CGD").mkdir(parents=True)
    (instalada / "Movimentos CGD" / "MovimentosCGD.exe").write_bytes(b"antigo")

    deploy_windows._cgd_instalar(deploy_windows.Contexto(lambda **campos: None))

    pasta = instalada / "Movimentos CGD"
    assert (pasta / "MovimentosCGD.exe").read_bytes() == b"novo"
    backups = list(pasta.glob("MovimentosCGD.exe.bak_*"))
    assert len(backups) == 1 and backups[0].read_bytes() == b"antigo"
