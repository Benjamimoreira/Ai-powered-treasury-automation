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

    def correr_falso(alvo, reportar, extracao=None, api=None, lancar=None):
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


def test_deploy_guarda_a_extracao_escolhida_e_valida(client):
    corpo = client.post("/monitorizacao/deploys", json={
        "alvo": "scripts_cgd", "extracao": {"modo": "periodo", "inicio": "2026-10-09", "fim": "2026-10-12"}}).json()
    assert corpo["extracao"] == {"modo": "periodo", "inicio": "2026-10-09", "fim": "2026-10-12"}
    pendente = client.get("/monitorizacao/deploys", params={"estado": "pendente"}).json()["deploys"][0]
    assert pendente["extracao"]["inicio"] == "2026-10-09"

    assert client.post("/monitorizacao/deploys", json={
        "alvo": "tesouraria_preenchimento", "extracao": {"modo": "periodo", "inicio": "2026-10-12", "fim": "2026-10-01"}}
    ).status_code == 422
    assert client.post("/monitorizacao/deploys", json={
        "alvo": "tesouraria_preenchimento", "extracao": {"modo": "qualquer"}}).status_code == 422


def test_reiniciar_anual_escreve_a_extracao_pedida(tmp_path, monkeypatch):
    import json

    ficheiro = tmp_path / "proxima_extracao.json"
    monkeypatch.setattr(deploy_windows, "FICHEIRO_PROXIMA_EXTRACAO", ficheiro)
    comandos = []
    ctx = deploy_windows.Contexto(lambda **campos: None)
    monkeypatch.setattr(ctx, "correr", lambda comando, **kw: comandos.append(comando))

    ctx.estado["extracao"] = {"modo": "nenhuma"}
    deploy_windows._cgd_reiniciar_anual(ctx)
    assert json.loads(ficheiro.read_text(encoding="utf-8")) == {"modo": "nenhuma"}
    assert comandos[0][:3] == ["schtasks", "/Run", "/TN"]

    ctx.estado["extracao"] = None  # deploy sem escolha = ano inteiro, como antes
    deploy_windows._cgd_reiniciar_anual(ctx)
    assert json.loads(ficheiro.read_text(encoding="utf-8")) == {"modo": "ano"}


def test_deploy_com_periodo_extrai_e_acompanha_mapas_e_dashboard(monkeypatch):
    """Hoje/Intervalo: depois de instalar, extrair os dias, esperar pelo
    Atualizar Mapa Tesouraria e pelo dashboard; a extração anual não extrai."""
    nomes = []
    for alvo in ("scripts_cgd", "cgd_MovimentosDiariosCGD"):
        reportes = []
        monkeypatch.setitem(deploy_windows.ALVOS, alvo, [("Instalar", lambda ctx: None)])
        chamados = []
        monkeypatch.setattr(deploy_windows, "passos_atualizar", lambda extracao: [
            ("Extrair", lambda ctx: chamados.append("extrair")),
            ("Mapas", lambda ctx: chamados.append("mapas")),
            ("Dashboard", lambda ctx: chamados.append("dashboard")),
        ] if extracao and extracao.get("modo") == "periodo" else [])
        assert deploy_windows.correr(alvo, lambda **c: reportes.append(c),
                                     extracao={"modo": "periodo", "inicio": "2026-10-12", "fim": "2026-10-12"})
        assert chamados == ["extrair", "mapas", "dashboard"]
        assert reportes[-1]["total_passos"] == 4
        nomes.append(alvo)
    # só instalar: nada a seguir
    reportes = []
    monkeypatch.setitem(deploy_windows.ALVOS, "tesouraria_preenchimento", [("Instalar", lambda ctx: None)])
    deploy_windows.correr("tesouraria_preenchimento", lambda **c: reportes.append(c),
                          extracao={"modo": "periodo", "inicio": "2026-10-12", "fim": "2026-10-12"})
    assert reportes[-1]["total_passos"] == 1  # a tesouraria não extrai


def test_passos_atualizar_e_anual_com_periodo(tmp_path, monkeypatch):
    import json

    assert [p[0] for p in deploy_windows.passos_atualizar({"modo": "periodo", "inicio": "2026-10-09", "fim": "2026-10-12"})] == [
        "Extrair extratos de 2026-10-09 a 2026-10-12",
        "Atualizar Mapa de Saldos e Mapa de Pagamentos e Recebimentos",
        "Atualizar o dashboard",
    ]
    assert deploy_windows.passos_atualizar({"modo": "nenhuma"}) == []
    ficheiro = tmp_path / "proxima_extracao.json"
    monkeypatch.setattr(deploy_windows, "FICHEIRO_PROXIMA_EXTRACAO", ficheiro)
    ctx = deploy_windows.Contexto(lambda **campos: None)
    monkeypatch.setattr(ctx, "correr", lambda comando, **kw: None)
    ctx.estado["extracao"] = {"modo": "periodo", "inicio": "2026-10-12", "fim": "2026-10-12"}
    deploy_windows._cgd_reiniciar_anual(ctx)
    assert json.loads(ficheiro.read_text(encoding="utf-8")) == {"modo": "nenhuma"}  # quem extrai é o passo seguinte


def test_dias_por_mes_para_o_preencher():
    assert deploy_windows._dias_por_mes({"inicio": "2026-10-09", "fim": "2026-10-09"}) == [("2026-10", "9")]
    assert deploy_windows._dias_por_mes({"inicio": "2026-09-29", "fim": "2026-10-02"}) == [
        ("2026-09", "29-30"), ("2026-10", "1-2")]
    passos = deploy_windows.passos_atualizar({"modo": "periodo", "inicio": "2026-10-09", "fim": "2026-10-09",
                                              "empresa": "J PINTO CONSTRUCOES,LDA"})
    assert [p[0] for p in passos][-1] == "Verificar a discrepância"
