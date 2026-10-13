import base64
import io
from datetime import date

import openpyxl

from app.db.models import LinhaMapa


def _ficheiro() -> bytes:
    """Como o "Previsão Tesouraria_OUT2026.xlsx": blocos (EUR) com REA/FOR por cima dos meses."""
    wb = openpyxl.Workbook()
    ws = wb.active
    linhas = [
        [None, "(EUR)", "REA", "FOR", "FOR", "FOR"],
        [None, "AUTOS DE OBRA (t-1)", "SET 2026", "OUT 2026", "JAN 27", "YTD DEZ 26"],
        [None, "ALVA", 35008, 66000, 1, 999],
        [None, "Grand Total", 35008, 66000, 1, 999],
        [],
        [None, "(EUR)", "REA", "FOR", "FOR", "FOR"],
        [None, "DESPESAS", "SET 2026", "OUT 2026", "JAN 27", "YTD DEZ 26"],
        [None, "Ordenados", 103000, 103000, 103000, 999],
        [None, "Autos de Obra", 35008, 66000, 1, 999],
        [None, "Grand Total", 138008, 169000, 103001, 999],
        [],
        [None, "(EUR)", "REA", "FOR", "FOR", "FOR"],
        [None, "Vendas Comerciais", "SET 2026", "OUT 2026", "JAN 27", "YTD DEZ 26"],
        [None, "Loja D J Pinto A", 0, 120000, 0, 999],
        [None, "Vendas Diretas", None, 120000, 0, 999],
        [],
        [None, "(EUR)", "REA", "FOR", "FOR", "FOR"],
        [None, "Sinais e Reforços", "SET 2026", "OUT 2026", "JAN 27", "YTD DEZ 26"],
        [None, "ALVA U", 0, 39000, 0, 999],
        [None, "Sinais + Reforços", None, 39000, 0, 999],
        [],
        [None, "GT Comercial", None, 159000, 0, 999],
    ]
    for linha in linhas:
        ws.append(linha)
    saida = io.BytesIO()
    wb.save(saida)
    return saida.getvalue()


def test_importa_a_previsao_e_compara_com_o_realizado(client, db_session):
    db_session.add_all([
        LinhaMapa(dia=date(2026, 10, 2), tipo="recebimento", linha=1, empresa="H.I.I", descricao="Escritura",
                  imputacao="Escritura - 00.AV.04.002", pago=100000),
        LinhaMapa(dia=date(2026, 10, 2), tipo="pagamento", linha=2, empresa="H.I.I", descricao="Ordenados", pago=-90000),
    ])
    db_session.commit()
    corpo = {"ficheiro": "Previsão Tesouraria_OUT2026.xlsx",
             "conteudo_base64": base64.b64encode(_ficheiro()).decode()}
    r = client.post("/previsao-mensal/importar", json=corpo).json()
    assert r["meses"] == ["2026-09", "2026-10", "2027-01"] and r["avisos"] == []
    assert r["mapas"] == "pedido"  # o agente aplica a folha "Previsão" nos Mapas

    meses = {(m["ano"], m["mes"]): m for m in client.get("/previsao-mensal").json()["meses"]}
    out = meses[(2026, 10)]
    # despesas sem o detalhe dos autos (já está na rubrica "Autos de Obra"), sem YTD nem totais
    assert out["previsto_pagamentos"] == 169000
    assert out["previsto_recebimentos"] == 159000
    assert (out["previsto_vendas_diretas"], out["previsto_sinais_reforcos"]) == (120000, 39000)
    assert (out["recebido"], out["pago"], out["vendas_diretas"]) == (100000, 90000, 100000)
    assert meses[(2026, 9)]["realizado_no_ficheiro"] is True
    assert any(l["tipo"] == "detalhe" and l["rubrica"] == "ALVA" for l in out["linhas"])

    # voltar a importar substitui, não duplica
    client.post("/previsao-mensal/importar", json=corpo)
    out2 = {(m["ano"], m["mes"]): m for m in client.get("/previsao-mensal").json()["meses"]}[(2026, 10)]
    assert out2["previsto_pagamentos"] == 169000


def test_ficheiro_invalido_da_422(client):
    r = client.post("/previsao-mensal/importar", json={"ficheiro": "x.xlsx", "conteudo_base64": "bmFvLWUteGxzeA=="})
    assert r.status_code == 422
