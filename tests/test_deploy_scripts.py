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

    def correr_falso(alvo, reportar, extracao=None, api=None, lancar=None, lancar_script=None):
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


def test_deploy_guarda_a_pesquisa_do_intervalo_no_banco(client):
    """A pesquisa pedida fica no pedido; sem nenhuma, o agente faz uma só
    pesquisa por empresa (ver test_extrair_dias_numa_so_pesquisa...)."""
    periodo = {"modo": "periodo", "inicio": "2026-10-01", "fim": "2026-10-05"}
    corpo = client.post("/monitorizacao/deploys", json={
        "alvo": "scripts_cgd", "extracao": {**periodo, "pesquisa": "intervalo"}}).json()
    assert corpo["extracao"] == {**periodo, "pesquisa": "intervalo"}

    corpo = client.post("/monitorizacao/deploys", json={
        "alvo": "tesouraria_preenchimento", "extracao": {**periodo, "pesquisa": "dia"}}).json()
    assert corpo["extracao"] == {**periodo, "pesquisa": "dia"}
    assert client.post("/monitorizacao/deploys", json={
        "alvo": "scripts_cgd", "extracao": {**periodo, "pesquisa": "semana"}}).status_code == 422


def test_extrair_dias_numa_so_pesquisa_passa_uma_pesquisa_ao_exe(tmp_path, monkeypatch):
    pasta = tmp_path / "Movimentos Diários CGD"
    pasta.mkdir()
    (pasta / "MovimentosEscolherDiaCGD.exe").write_bytes(b"")
    monkeypatch.setattr(deploy_windows, "PASTA_CGD_INSTALADA", tmp_path)
    comandos = []
    ctx = deploy_windows.Contexto(lambda **campos: None)
    monkeypatch.setattr(ctx, "correr", lambda comando, **kw: comandos.append([str(c) for c in comando]))

    periodo = {"modo": "periodo", "inicio": "2026-10-01", "fim": "2026-10-05"}
    ctx.estado["extracao"] = {**periodo, "pesquisa": "intervalo"}
    deploy_windows._cgd_extrair_dias(ctx)
    # sem "pesquisa" (corrigir discrepâncias, reextrair uma discrepância): também uma só
    ctx.estado["extracao"] = {**periodo, "discrepancias": [{"dia": "2026-10-03", "empresa": "X", "tipo": ""}]}
    deploy_windows._cgd_extrair_dias(ctx)
    ctx.estado["extracao"] = {**periodo, "empresa": "EMP", "dia": "2026-10-05"}
    deploy_windows._cgd_extrair_dias(ctx)
    ctx.estado["extracao"] = {**periodo, "pesquisa": "dia"}  # um dia de cada vez, como antes
    deploy_windows._cgd_extrair_dias(ctx)

    assert [c[1:] for c in comandos] == [["--dias", "2026-10-01:2026-10-05", "--uma-pesquisa"],
                                         ["--dias", "2026-10-01:2026-10-05", "--uma-pesquisa"],
                                         ["--dias", "2026-10-01:2026-10-05", "--empresa", "EMP", "--uma-pesquisa"],
                                         ["--dias", "2026-10-01:2026-10-05"]]


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


def test_codigo_de_erro_diz_o_motivo(tmp_path):
    import sys
    ctx = deploy_windows.Contexto(lambda **campos: None)
    casos = [
        ("[ERRO] Page.goto: net::ERR_INTERNET_DISCONNECTED at https://www.cgd.pt/", "falha de internet"),
        ("[ERRO] Falha no download: Download.save_as: Target page, context or browser has been closed\n"
         "[ERRO] Erro inesperado: Page.wait_for_timeout: Target page, context or browser has been closed",
         "o Chrome fechou-se"),
        ("[ERRO] URL inesperado: https://www.cgd.pt/\n[ERRO] Login incompleto.", "o login no banco falhou"),
        ("[ERRO] Algo estranho aconteceu", "Algo estranho aconteceu"),
    ]
    for saida, motivo in casos:
        script = tmp_path / "falha.py"
        script.write_text(f"print({saida!r}); raise SystemExit(1)", encoding="utf-8")
        try:
            ctx.correr([sys.executable, script])
        except deploy_windows.FalhaDeploy as e:
            assert "terminou com código 1: " + motivo in str(e), (saida, str(e))
        else:
            raise AssertionError("devia ter falhado")


def test_reextrair_discrepancia_extrai_a_semana_e_verifica_o_dia(client):
    extracao = {"modo": "periodo", "inicio": "2026-10-06", "fim": "2026-10-12", "dia": "2026-10-12",
                "empresa": "PALAVRADICIONAL", "tipo": "x"}
    corpo = client.post("/monitorizacao/deploys", json={"alvo": "reextrair_discrepancia", "extracao": extracao}).json()
    assert corpo["extracao"] == extracao
    fora = dict(extracao, dia="2026-10-20")
    assert client.post("/monitorizacao/deploys", json={"alvo": "reextrair_discrepancia", "extracao": fora}).status_code == 422
    assert deploy_windows._dia_discrepancia(extracao) == "2026-10-12"
    assert deploy_windows._dia_discrepancia({"inicio": "2026-10-12", "fim": "2026-10-12"}) == "2026-10-12"
    assert deploy_windows.passos_atualizar(extracao)[0][0] == "Reextrair PALAVRADICIONAL de 2026-10-06 a 2026-10-12"


def test_verificar_discrepancia_para_logo_se_o_valor_esta_noutro_dia(monkeypatch):
    pedidos = []

    def api_falsa(metodo, caminho, corpo=None):
        pedidos.append(caminho)
        if caminho.startswith("/auditoria/"):
            return {"diferencas_por_empresa": [{"empresa": "Palavra", "diferenca": 3294.52}]}
        return {"itens": [{"lado": "mapa", "estado": "noutro_dia",
                           "explicacao": "-374,50 € no Mapa a 12/10 — no extrato está a 10/10"}],
                "diferenca": 374.5, "so_noutro_dia": True}

    extrair = []
    monkeypatch.setattr(deploy_windows, "_cgd_extrair_dias", lambda ctx: extrair.append(1))
    ctx = deploy_windows.Contexto(lambda **campos: None)
    ctx.api = api_falsa
    ctx.estado["extracao"] = {"modo": "periodo", "inicio": "2026-10-06", "fim": "2026-10-12", "dia": "2026-10-12",
                              "empresa": "Palavra"}
    deploy_windows._verificar_discrepancia(ctx)  # termina "Concluído" com o resultado, sem erro
    assert ctx.estado["resultado"].startswith("Concluído - extratos extraídos, a discrepância continua")
    assert "no extrato está a 10/10" in ctx.estado["resultado"]
    assert extrair == []  # não volta a extrair à toa
    assert any("dia=2026-10-12" in p and "empresa=Palavra" in p for p in pedidos)


def test_deploy_parado_sem_noticias_passa_a_erro(client, db_session):
    from app.db.models import DeployScripts

    velho = datetime.now(timezone.utc).replace(tzinfo=None) - timedelta(minutes=40)
    db_session.add(DeployScripts(alvo="reextrair_discrepancia", estado="a_correr", log=[], atualizado_em=velho,
                                 pedido_em=velho))
    db_session.commit()
    deploy = client.get("/monitorizacao/deploys").json()["deploys"][0]
    assert deploy["estado"] == "erro" and "Sem notícias do agente" in deploy["erro"]


def test_atualizar_mapas_espera_pelo_que_ja_esta_a_correr(monkeypatch):
    estados = iter([True, True, False])  # a correr, a correr, livre
    monkeypatch.setattr(deploy_windows, "_tarefa_a_correr", lambda nome: next(estados, False))
    monkeypatch.setattr(deploy_windows.time, "sleep", lambda s: None)
    lancados = []
    ctx = deploy_windows.Contexto(lambda **campos: None)
    ctx.estado["extracao"] = {"inicio": "2026-10-06", "fim": "2026-10-12"}
    ctx.lancar = lancados.append
    ctx.api = lambda metodo, caminho, corpo=None: {"logs": [
        {"timestamp": "9999", "nivel": "info", "mensagem": "ok"}]}
    deploy_windows._atualizar_mapas(ctx)
    assert lancados == [["2026-10", "6-12"]]


def test_deploy_que_esperou_por_outro_nao_expira(monkeypatch):
    """Pedido há 60 min, mas o agente esteve ocupado até agora: não expira."""
    deploys = [_deploy(5, "scripts_cgd", 60)]
    enviados, corridos = [], []

    def api_falsa(metodo, caminho, corpo=None):
        if metodo == "GET":
            return {"deploys": deploys}
        enviados.append((caminho, corpo))

    def correr_falso(alvo, reportar, extracao=None, api=None, lancar=None, lancar_script=None):
        corridos.append(alvo)
        return True

    class Terminado:
        def is_alive(self):
            return False

    monkeypatch.setattr(agente_pedidos, "_api", api_falsa)
    monkeypatch.setattr(agente_pedidos, "_deploy_em_curso", Terminado())  # outro acabou de terminar
    monkeypatch.setattr(agente_pedidos, "_livre_desde", [None])
    monkeypatch.setattr(agente_pedidos.deploy_windows, "correr", correr_falso)
    agente_pedidos.tratar_deploys()
    agente_pedidos._deploy_em_curso.join()
    assert corridos == ["scripts_cgd"]
    assert not any((c or {}).get("estado") == "erro" for _, c in enviados)


def test_corrigir_discrepancias_numa_corrida(client):
    lista = [{"dia": "2026-10-14", "empresa": "HCC", "tipo": "Extrato ≠ Mapa de Pagamentos (auditoria)"},
             {"dia": "2026-10-12", "empresa": "Palavra", "tipo": "Extrato/Mapa ≠ Mapa de Saldos"}]
    extracao = {"modo": "periodo", "inicio": "2026-10-06", "fim": "2026-10-14", "discrepancias": lista}
    corpo = client.post("/monitorizacao/deploys", json={"alvo": "corrigir_discrepancias", "extracao": extracao}).json()
    assert corpo["extracao"] == extracao
    fora = dict(extracao, discrepancias=[dict(lista[0], dia="2026-10-20")])
    assert client.post("/monitorizacao/deploys", json={"alvo": "corrigir_discrepancias", "extracao": fora}).status_code == 422
    assert [p[0] for p in deploy_windows.passos_atualizar(extracao)] == [
        "Reauditar com o Mapa relido (sem ir ao banco)",
        "Extrair todas as empresas de 2026-10-06 a 2026-10-14 (uma sessão do banco)",
        "Atualizar Mapa de Saldos e Mapa de Pagamentos e Recebimentos desses dias",
        "Corrigir no Mapa o que é seguro (linhas noutro dia)",
        "Verificar as 2 discrepância(s)",
    ]


def test_verificar_discrepancias_resolve_as_que_batem_e_resume_as_outras():
    resolvidas = []

    def api_falsa(metodo, caminho, corpo=None):
        if caminho == "/auditoria/2026-10-14/registar":
            return {"diferencas_por_empresa": []}  # HCC já bate certo
        if caminho.startswith("/monitorizacao/discrepancias-saldos?"):
            return {"discrepancias": [{"dia": "2026-10-12", "empresa": "Palavra", "tipo": "Extrato/Mapa ≠ Mapa de Saldos",
                                       "visto_em": "2026-10-19T10:00:00Z", "mensagem": "continua"}]}
        if caminho.endswith("/resolvida"):
            resolvidas.append(corpo["empresa"])
            return {"ok": True}
        if caminho.startswith("/monitorizacao/discrepancias-saldos/destino"):
            return {"itens": [{"lado": "extrato", "estado": "em_falta", "explicacao": "falta lançar 80 €"}],
                    "diferenca": -80.0}
        raise AssertionError(caminho)

    ctx = deploy_windows.Contexto(lambda **campos: None)
    ctx.api = api_falsa
    extracao = {"modo": "periodo", "inicio": "2026-10-06", "fim": "2026-10-14", "discrepancias": [
        {"dia": "2026-10-14", "empresa": "HCC", "tipo": "Extrato ≠ Mapa de Pagamentos (auditoria)"},
        {"dia": "2026-10-12", "empresa": "Palavra", "tipo": "Extrato/Mapa ≠ Mapa de Saldos"}]}
    ctx.estado.update(extracao=extracao, mapas_inicio="2026-10-19T09:00:00Z", correcoes="[OK] 1 linha mudada de dia")
    deploy_windows._verificar_discrepancias(ctx)
    assert resolvidas == ["HCC"]
    assert ctx.estado["extracao"] == extracao
    assert ctx.estado["resultado"].startswith("Concluído - 1 de 2 discrepância(s) resolvida(s). Mapa: [OK] 1 linha")
    assert "Continuam (1, precisam de alguém): 12/10 Palavra Diferença -80.00 €: falta lançar 80 €" in ctx.estado["resultado"]


def test_corrigir_no_mapa_lanca_o_script_e_espera(monkeypatch):
    lancados = []
    monkeypatch.setattr(deploy_windows, "_tarefa_a_correr", lambda nome: False)
    monkeypatch.setattr(deploy_windows, "_agora_iso", lambda: "2026-10-19T09:00:00Z")
    ctx = deploy_windows.Contexto(lambda **campos: None)
    ctx.api = lambda metodo, caminho, corpo=None: {"logs": [
        {"timestamp": "2026-10-19T09:01:00Z", "nivel": "ok", "mensagem": "[OK] 2 linha(s) mudada(s) de dia"}]}
    ctx.lancar_script = lancados.append
    deploy_windows._corrigir_no_mapa(ctx)
    assert lancados == ["corrigir_discrepancias"]
    assert ctx.estado["correcoes"] == "[OK] 2 linha(s) mudada(s) de dia"
    # agente antigo, sem lancar_script: salta o passo sem falhar a corrida
    ctx = deploy_windows.Contexto(lambda **campos: None)
    ctx.api = lambda *a, **k: {}
    deploy_windows._corrigir_no_mapa(ctx)
    assert "correcoes" not in ctx.estado


def test_reauditar_primeiro_tira_as_que_ja_batem_e_encolhe_a_extracao():
    resolvidas = []

    def api_falsa(metodo, caminho, corpo=None):
        if caminho == "/auditoria/2026-09-03/registar":
            return {"diferencas_por_empresa": []}  # já batia: um preencher antigo corrigiu a folha
        if caminho == "/auditoria/2026-09-28/registar":
            return {"diferencas_por_empresa": [{"empresa": "HCC", "diferenca": 80}]}
        if caminho.endswith("/resolvida"):
            resolvidas.append(corpo["dia"])
            return {"ok": True}
        raise AssertionError(caminho)

    auditoria = "Extrato ≠ Mapa de Pagamentos (auditoria)"
    ctx = deploy_windows.Contexto(lambda **campos: None)
    ctx.api = api_falsa
    ctx.estado["extracao"] = {"modo": "periodo", "inicio": "2026-08-26", "fim": "2026-09-28", "discrepancias": [
        {"dia": "2026-09-03", "empresa": "HCC", "tipo": auditoria},
        {"dia": "2026-09-28", "empresa": "HCC", "tipo": auditoria},
        {"dia": "2026-09-24", "empresa": "Palavra", "tipo": "Saldo mudou sem movimentos"}]}
    deploy_windows._reauditar_primeiro(ctx)
    assert resolvidas == ["2026-09-03"]
    extracao = ctx.estado["extracao"]
    assert (extracao["inicio"], extracao["fim"]) == ("2026-09-18", "2026-09-28")
    assert [d["dia"] for d in extracao["discrepancias"]] == ["2026-09-28", "2026-09-24"]
    assert (ctx.estado["resolvidas_antes"], ctx.estado["total_discrepancias"]) == (1, 3)

    # todas já batiam: os passos seguintes não vão ao banco
    ctx = deploy_windows.Contexto(lambda **campos: None)
    ctx.api = api_falsa
    ctx.estado["extracao"] = {"modo": "periodo", "inicio": "2026-08-28", "fim": "2026-09-03",
                              "discrepancias": [{"dia": "2026-09-03", "empresa": "HCC", "tipo": auditoria}]}
    deploy_windows._reauditar_primeiro(ctx)
    extrair = []
    deploy_windows._se_houver_o_que_fazer(lambda c: extrair.append(1))(ctx)
    assert extrair == [] and ctx.estado["resultado"].startswith("Concluído - 1 de 1 discrepância(s) resolvida(s) só por")
