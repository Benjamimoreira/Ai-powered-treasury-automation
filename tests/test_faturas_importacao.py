from datetime import date

import openpyxl

from app.db.models import ExecucaoScript, FaturaRecebida
from app.services.faturas import caminho_pdf, importar_faturas_excel, meses_a_importar

CABECALHO = ["dia", "hora", "remetente", "assunto", "motivo", "empresa", "fornecedor", "nif_fornecedor",
             "n_anexos_pdf", "debito", "credito", "saldo", "valor_fatura", "hiperligacao_pdf", "id"]
PDF = "Faturas Recebidas/2026-10/20261016_153202 - Habiserve - Recibo_4.pdf"


def _sharepoint(tmp_path, monkeypatch):
    """Pasta AFaturas/2026 como o recolher_faturas_recebidas.py a deixa: Excel
    mensal com a folha "Todas" e o PDF na subpasta do mês."""
    monkeypatch.setenv("ONEDRIVE_RAIZ", str(tmp_path))
    pasta = tmp_path / "DOCUMENTOS A TRATAR" / "AFaturas" / "2026"
    (pasta / "Faturas Recebidas" / "2026-10").mkdir(parents=True)
    (pasta / PDF).write_bytes(b"%PDF-1.4")

    livro = openpyxl.Workbook()
    folha = livro.active
    folha.title = "Todas"
    folha.append(CABECALHO)
    folha.append(["16-10", "15:32", "a@b.pt", "Recibo", None, "Habiserve", "Curado", "500000000",
                  1, None, None, None, 129.72, "Abrir PDF", "ID-1"])
    folha.cell(2, 14).hyperlink = PDF.replace(" ", "%20")
    folha.append(["16-10", "16:00", "c@d.pt", "Sem anexo", None, "Vidor", None, None,
                  0, None, None, None, None, None, "ID-2"])
    livro.save(pasta / "10-Faturas Recebidas.xlsx")
    return pasta


def test_importa_o_excel_mensal_sem_duplicar(tmp_path, monkeypatch, db_session):
    _sharepoint(tmp_path, monkeypatch)

    resultado = importar_faturas_excel(db_session, [(2026, 10)])
    assert (resultado["novas"], resultado["erros"]) == (2, [])

    fatura = db_session.query(FaturaRecebida).filter_by(outlook_id="ID-1").one()
    assert (fatura.dia, fatura.hora, fatura.valor_fatura) == (date(2026, 10, 16), "15:32", "129.72")
    assert fatura.pdf_relativo == PDF.replace("/", "\\")
    assert db_session.query(FaturaRecebida).filter_by(outlook_id="ID-2").one().pdf_relativo is None

    assert importar_faturas_excel(db_session, [(2026, 10)])["novas"] == 0


def test_excel_em_falta_e_erro_e_nao_rebenta(tmp_path, monkeypatch, db_session):
    _sharepoint(tmp_path, monkeypatch)
    resultado = importar_faturas_excel(db_session, [(2026, 9)])
    assert resultado["novas"] == 0
    assert "09-Faturas Recebidas.xlsx" in resultado["erros"][0]


def test_pdf_na_pasta_do_ano_e_nas_faturas_antigas(tmp_path, monkeypatch):
    pasta = _sharepoint(tmp_path, monkeypatch)
    nova = FaturaRecebida(dia=date(2026, 10, 16), pdf_relativo=PDF.replace("/", "\\"))
    assert caminho_pdf(nova) == str((pasta / PDF).resolve())

    antiga_raiz = tmp_path / "Fornecedores"
    (antiga_raiz / "Faturas Recebidas").mkdir(parents=True)
    (antiga_raiz / "Faturas Recebidas" / "x.pdf").write_bytes(b"%PDF")
    antiga = FaturaRecebida(dia=date(2026, 9, 1), pdf_relativo="Faturas Recebidas\\x.pdf")
    assert caminho_pdf(antiga, str(antiga_raiz)) is not None

    fora = FaturaRecebida(dia=date(2026, 10, 16), pdf_relativo="..\\..\\..\\segredo.pdf")
    assert caminho_pdf(fora) is None


def test_meses_a_importar_inclui_o_anterior_no_inicio_do_mes():
    assert meses_a_importar(date(2026, 10, 16)) == [(2026, 10)]
    assert meses_a_importar(date(2026, 1, 3)) == [(2025, 12), (2026, 1)]


def test_botao_correr_da_monitorizacao_importa_e_regista(tmp_path, monkeypatch, client, db_session):
    _sharepoint(tmp_path, monkeypatch)
    monkeypatch.setattr("app.services.faturas.meses_a_importar", lambda hoje: [(2026, 10)])

    scripts = {s["nome"]: s for s in client.get("/monitorizacao/scripts").json()["scripts"]}
    assert scripts["importar_faturas_recebidas"]["corrivel"]

    r = client.post("/monitorizacao/scripts/importar_faturas_recebidas/correr")
    assert r.status_code == 200
    assert (r.json()["status"], r.json()["novas"]) == ("concluido", 2)
    execucao = db_session.query(ExecucaoScript).filter_by(script="importar_faturas_recebidas").one()
    assert execucao.status == "ok"

    fatura = db_session.query(FaturaRecebida).filter_by(outlook_id="ID-1").one()
    pdf = client.get(f"/faturas/recebidas/{fatura.id}/pdf")
    assert pdf.status_code == 200
    assert pdf.headers["content-type"] == "application/pdf"


def test_sincronizador_nao_regista_corridas_vazias(tmp_path, monkeypatch, client, db_session):
    _sharepoint(tmp_path, monkeypatch)
    monkeypatch.setattr("app.services.faturas.meses_a_importar", lambda hoje: [(2026, 10)])
    client.post("/faturas/recebidas/importar", params={"reportar_sem_novas": "false"})
    client.post("/faturas/recebidas/importar", params={"reportar_sem_novas": "false"})
    assert db_session.query(ExecucaoScript).filter_by(script="importar_faturas_recebidas").count() == 1
