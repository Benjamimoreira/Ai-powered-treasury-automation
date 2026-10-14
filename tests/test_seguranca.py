"""Chave da API (app/seguranca.py): com API_TOKEN, só passa quem a manda -
exceto os reportes dos scripts agendados e os PDFs com link assinado."""
import time

import pytest

from app import seguranca

CHAVE = "chave-de-teste"


@pytest.fixture()
def com_chave(monkeypatch):
    monkeypatch.setenv("API_TOKEN", CHAVE)


def test_sem_api_token_a_api_fica_aberta(client):
    assert client.get("/monitorizacao/deploys").status_code == 200


def test_pedido_sem_chave_da_401(client, com_chave):
    for metodo, caminho in [("GET", "/monitorizacao/deploys"), ("POST", "/monitorizacao/deploys"),
                            ("POST", "/monitorizacao/scripts/preencher_mapa/correr"),
                            ("POST", "/monitorizacao/destinatarios-mapa"), ("GET", "/saldos")]:
        r = client.request(metodo, caminho, json={})
        assert r.status_code == 401, (metodo, caminho)


def test_chave_errada_da_401_e_certa_passa(client, com_chave):
    assert client.get("/monitorizacao/deploys", headers={"X-API-Key": "outra"}).status_code == 401
    assert client.get("/monitorizacao/deploys", headers={"X-API-Key": CHAVE}).status_code == 200


def test_reportes_dos_scripts_nao_precisam_de_chave(client, com_chave):
    """Exes da CGD, Fornecedores e OCR reportam sem chave (compilados noutros repositórios)."""
    assert client.get("/").status_code == 200
    r = client.post("/monitorizacao/scripts/extratos_cgd/eventos", json={"nivel": "info", "mensagem": "x"})
    assert r.status_code != 401
    r = client.post("/monitorizacao/scripts/extratos_cgd/executar", json={"status": "ok"})
    assert r.status_code != 401
    # mas o mesmo prefixo para lançar um script continua protegido
    assert client.post("/monitorizacao/scripts/extratos_cgd/correr").status_code == 401


def test_pdf_so_abre_com_assinatura_valida(client, com_chave):
    url = "/faturas/recebidas/7/pdf"
    assert client.get(url).status_code == 401
    assert client.get(f"{url}?{seguranca.assinar_pdf(8, CHAVE)}").status_code == 401  # de outra fatura
    assert client.get(f"{url}?{seguranca.assinar_pdf(7, 'outra')}").status_code == 401
    expirada = seguranca.assinar_pdf(7, CHAVE, agora=time.time() - seguranca.VALIDADE_LINK_PDF_S - 60)
    assert client.get(f"{url}?{expirada}").status_code == 401
    # assinatura certa passa a autenticação (depois 503/404: não há PDF nos testes)
    assert client.get(f"{url}?{seguranca.assinar_pdf(7, CHAVE)}").status_code != 401


def test_link_do_dashboard_tem_a_mesma_assinatura_que_a_api(monkeypatch, com_chave):
    import importlib
    import sys
    from pathlib import Path

    sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "dashboard"))
    import api_client
    api_client = importlib.reload(api_client)
    url = api_client.url_pdf_fatura(7)
    query = dict(p.split("=") for p in url.split("?", 1)[1].split("&"))
    assert seguranca._pdf_assinado("/faturas/recebidas/7/pdf", query, CHAVE)
    assert api_client.requests._sessao.headers["X-API-Key"] == CHAVE
