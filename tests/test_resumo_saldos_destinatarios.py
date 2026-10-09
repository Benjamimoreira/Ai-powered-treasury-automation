from datetime import date

from app.db.models import LinhaMapa, SaldoDiario


# ------------------------------------------------- destinatários do Mapa

def test_destinatarios_do_mapa_comecam_no_pduarte_e_aceitam_mais(client):
    url = "/monitorizacao/destinatarios-mapa"
    assert client.get(url).json()["destinatarios"] == ["pduarte@vidor.pt"]

    corpo = client.post(url, json={"email": "  Outro@Vidor.pt "}).json()
    assert corpo["destinatarios"] == ["pduarte@vidor.pt", "outro@vidor.pt"]
    # repetido não duplica
    assert client.post(url, json={"email": "outro@vidor.pt"}).json()["destinatarios"] == corpo["destinatarios"]
    assert client.post(url, json={"email": "nao-e-email"}).status_code == 422

    assert client.delete(f"{url}/outro@vidor.pt").json()["destinatarios"] == ["pduarte@vidor.pt"]
    # nunca fica vazio: sem ninguém, volta o pduarte
    assert client.delete(f"{url}/pduarte@vidor.pt").json()["destinatarios"] == ["pduarte@vidor.pt"]


# ------------------------------------------------------ resumo mensal

def _linha(dia, descricao, pago=None, previsto=None, imputacao=None, tipo="recebimento"):
    return LinhaMapa(dia=dia, tipo=tipo, linha=1, empresa="H.I.I", descricao=descricao,
                     imputacao=imputacao, pago=pago, previsto=previsto)


def test_resumo_mensal_liquidez_e_vendas(client, db_session):
    db_session.add_all([
        SaldoDiario(dia=date(2026, 9, 1), entidade="A,LDA", saldo_contabilistico=100, saldo_disponivel=100),
        SaldoDiario(dia=date(2026, 9, 1), entidade="B,LDA", saldo_contabilistico=50, saldo_disponivel=50),
        SaldoDiario(dia=date(2026, 9, 2), entidade="A,LDA", saldo_contabilistico=400, saldo_disponivel=400),
        SaldoDiario(dia=date(2026, 9, 3), entidade="B,LDA", saldo_contabilistico=10, saldo_disponivel=10),
        SaldoDiario(dia=date(2026, 10, 1), entidade="A,LDA", saldo_contabilistico=300, saldo_disponivel=300),
        # setembro: um de cada tipo + um pendente + coisas que não são vendas
        _linha(date(2026, 9, 2), "CPCV - 00.PO.23.035", pago=41750, imputacao="00.PO.23.035 Cpcv ALVA Lote 3 AI"),
        _linha(date(2026, 9, 2), "Reforço Sinal - 00.PO.23.001", pago=41000, imputacao="00.PO.23.001 Cpcv ALVA Lote 1 A"),
        _linha(date(2026, 9, 3), "Escritura", pago=265000, imputacao="Escritura - 00.AV.04.002"),
        _linha(date(2026, 9, 3), "Reforço Sinal - 00.PO.23.020", previsto=39000),
        _linha(date(2026, 9, 3), "TRF REFORCO SALDO", pago=200000, imputacao="TRF REFORCO SALDO"),
        _linha(date(2026, 9, 3), "ATM", pago=-300, imputacao="Reforço de Caixa", tipo="pagamento"),
    ])
    db_session.commit()

    corpo = client.get("/saldos/resumo-mensal", params={"ano": 2026}).json()
    setembro = next(m for m in corpo["meses"] if m["mes"] == 9)

    # liquidez = soma da última leitura de cada conta: 150, 450 (A=400 + B=50), 410 (A=400 + B=10)
    assert (setembro["saldo_inicio"], setembro["saldo_fim"]) == (150, 410)
    assert setembro["liquidez_minima"] == 150 and setembro["dia_minimo"] == "2026-09-01"
    assert setembro["liquidez_maxima"] == 450 and setembro["dia_maximo"] == "2026-09-02"
    assert setembro["liquidez_media"] == round((150 + 450 + 410) / 3, 2)

    assert (setembro["cpcv"], setembro["reforco_sinal"], setembro["escritura"]) == (41750, 41000, 265000)
    assert (setembro["n_cpcv"], setembro["n_reforco_sinal"], setembro["n_escritura"]) == (1, 1, 1)
    assert setembro["pendente_reforco_sinal"] == 39000
    assert setembro["total_vendas"] == 41750 + 41000 + 265000  # o "REFORCO SALDO" não é venda

    dia_2 = next(d for d in corpo["diario"] if d["dia"] == "2026-09-02")
    assert (dia_2["liquidez"], dia_2["cpcv"], dia_2["reforco_sinal"]) == (450, 41750, 41000)
    assert [m["mes"] for m in corpo["meses"]] == [9, 10]
