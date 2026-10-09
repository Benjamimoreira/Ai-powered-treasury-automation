from datetime import datetime

from app.db.models import EventoScript
from app.services.discrepancias_saldos import _analisar, tipo_discrepancia

VISTO = datetime(2026, 10, 9, 15, 0)

# mensagens reais (eventos_scripts, 09/10/2026)
SGPS = ("[AVISO] Dia 09 Vidor SGPS: extrato bancário (60081.46 EUR) difere de Mapa de Saldos "
        "(10030.46 EUR) em 50051.00 EUR (> 100 EUR) - dados desincronizados.")
J_PINTO = ("[AVISO] Dia 09 J PINTO CONSTRUCOES,LDA: antes da primeira linha do extrato deste dia "
           "('TRF SORTE PRINCIPIANT'), o saldo devia ser 1312.53 EUR (fecho do último extrato disponível, "
           "dia 08), mas o extrato implica 1814.14 EUR - falta explicar 501.61 EUR algures entre o dia 08 e "
           "este dia - o extrato exportado do CGD provavelmente tem movimento(s) em falta logo no início.")
TOPO = ("[AVISO] Dia 30 PALAVRADICIONAL,LDA: desfasamento entre o saldo do topo do extrato (100.00 EUR) e o "
        "saldo após o último movimento deste dia (80.00 EUR) - diferença de 20.00 EUR.")
SEM_ENTIDADE = ("[AVISO] 06-10: extrato de 'VARANDANIMADA,LDA' sem entidade correspondente conhecida (ver "
                "logica_empresas.EMPRESA_PARA_ENTIDADE) - saldo NÃO reconciliado, fica com o valor já gravado.")
SEM_MOVIMENTOS = ("[AVISO] 06-10 HCC: extrato SEM nenhum movimento neste dia, mas o saldo do topo (5.00 EUR) "
                  "difere do saldo já confirmado")


def test_classifica_e_analisa_mensagens_reais():
    assert tipo_discrepancia(SGPS) == "Extrato/Mapa ≠ Mapa de Saldos"
    assert _analisar(SGPS, VISTO) == {"dia": "2026-10-09", "empresa": "Vidor SGPS", "diferenca": 50051.0}
    assert tipo_discrepancia(J_PINTO) == "Movimentos em falta no extrato"
    assert _analisar(J_PINTO, VISTO)["diferenca"] == 501.61
    # "Dia 30" visto a 09/10 é de setembro
    assert _analisar(TOPO, VISTO) == {"dia": "2026-09-30", "empresa": "PALAVRADICIONAL,LDA", "diferenca": 20.0}
    assert _analisar(SEM_ENTIDADE, VISTO)["empresa"] == "VARANDANIMADA,LDA"
    assert _analisar(SEM_MOVIMENTOS, VISTO)["empresa"] == "HCC"
    assert tipo_discrepancia("[ERRO] Não consegui abrir o Mapa no Excel") is None


def test_discrepancias_agrupadas_e_fora_dos_erros_em_tempo_real(client, db_session):
    for _ in range(3):  # a mesma discrepância repetida em 3 corridas
        db_session.add(EventoScript(script="preencher_mapa", nivel="aviso", mensagem=SGPS))
    db_session.add(EventoScript(script="atualizar_mapa_saldos", nivel="aviso", mensagem=J_PINTO))
    db_session.add(EventoScript(script="enviar_mapa_smtp", nivel="erro", mensagem="[ERRO] Excel preso"))
    db_session.commit()

    discrepancias = client.get("/monitorizacao/discrepancias-saldos").json()["discrepancias"]
    assert len(discrepancias) == 2
    sgps = next(d for d in discrepancias if d["empresa"] == "Vidor SGPS")
    assert sgps["ocorrencias"] == 3 and not sgps["mensagem"].startswith("[AVISO]")

    so_scripts = client.get("/monitorizacao/eventos", params={"sem_saldos": True}).json()["eventos"]
    assert [e["mensagem"] for e in so_scripts] == ["[ERRO] Excel preso"]
    assert len(client.get("/monitorizacao/eventos").json()["eventos"]) == 5


def test_scripts_que_so_reportam_no_fim_ganham_eventos_do_log(client):
    client.post("/monitorizacao/scripts/extrair_faturas/executar", json={
        "status": "erro", "erro": "OCR falhou", "log": ["[INFO] 3 faturas", "[AVISO] fatura sem NIF"]})
    eventos = client.get("/monitorizacao/eventos", params={"script": "extrair_faturas"}).json()["eventos"]
    assert sorted(e["mensagem"] for e in eventos) == ["[AVISO] fatura sem NIF", "[ERRO] OCR falhou"]

    # quem já mandou eventos durante a corrida não fica duplicado
    client.post("/monitorizacao/scripts/preencher_mapa/eventos", json={"nivel": "erro", "mensagem": "[ERRO] x"})
    client.post("/monitorizacao/scripts/preencher_mapa/executar", json={"status": "erro", "erro": "x", "log": ["[ERRO] x"]})
    assert len(client.get("/monitorizacao/eventos", params={"script": "preencher_mapa"}).json()["eventos"]) == 1

    logs = client.get("/monitorizacao/logs", params={"script": "extrair_faturas"}).json()["logs"]
    assert [l["script"] for l in logs] == ["extrair_faturas"]


def test_deploy_e_pedido_falhado_aparecem_nos_separadores(client):
    deploy_id = client.post("/monitorizacao/deploys", json={"alvo": "scripts_cgd"}).json()["id"]
    client.post(f"/monitorizacao/deploys/{deploy_id}/progresso", json={
        "estado": "a_correr", "passo": 1, "total_passos": 3, "linhas": ["== Compilar", "[ERRO] PyInstaller falhou"]})
    client.post(f"/monitorizacao/deploys/{deploy_id}/progresso", json={
        "estado": "erro", "erro": "PyInstaller falhou", "mensagem": "Falhou em: Compilar"})

    eventos = client.get("/monitorizacao/eventos", params={"script": "deploy_scripts_cgd"}).json()["eventos"]
    assert [e["mensagem"] for e in eventos] == ["[ERRO] PyInstaller falhou"]
    logs = client.get("/monitorizacao/logs", params={"script": "deploy_scripts_cgd"}).json()["logs"]
    assert logs[-1]["nivel"] == "erro" and logs[-1]["mensagem"] == "PyInstaller falhou"
    scripts = {s["nome"]: s for s in client.get("/monitorizacao/scripts").json()["scripts"]}
    assert scripts["deploy_scripts_cgd"]["atrasado"] is False  # "a pedido": nunca atrasado

    pedido = client.post("/monitorizacao/scripts/enviar_mapa_smtp/correr").json()["pedido"]
    client.post(f"/monitorizacao/pedidos/{pedido['id']}/estado", json={"estado": "erro", "erro": "pasta não existe"})
    eventos = client.get("/monitorizacao/eventos", params={"script": "enviar_mapa_smtp"}).json()["eventos"]
    assert "pasta não existe" in eventos[0]["mensagem"]
