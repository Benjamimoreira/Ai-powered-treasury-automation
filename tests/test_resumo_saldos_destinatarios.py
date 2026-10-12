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


def test_resumo_mensal_tem_recebido_pago_e_saldo_bancario(client, db_session):
    db_session.add_all([
        SaldoDiario(dia=date(2026, 9, 1), entidade="A,LDA", saldo_contabilistico=120, saldo_disponivel=100),
        _linha(date(2026, 9, 1), "Renda", pago=500, imputacao="Rendas"),
        _linha(date(2026, 9, 1), "Fornecedor", pago=-200, imputacao="Obras", tipo="pagamento"),
        _linha(date(2026, 9, 1), "Por confirmar", previsto=50, imputacao="Rendas"),
    ])
    db_session.commit()
    corpo = client.get("/saldos/resumo-mensal", params={"ano": 2026}).json()
    setembro = next(m for m in corpo["meses"] if m["mes"] == 9)
    assert (setembro["recebido"], setembro["pago"]) == (500, 200)
    assert setembro["saldo_contabilistico_fim"] == 120 and setembro["saldo_fim"] == 100
    dia = next(d for d in corpo["diario"] if d["dia"] == "2026-09-01")
    assert (dia["recebido"], dia["pago"], dia["saldo_contabilistico"], dia["liquidez"]) == (500, 200, 120, 100)


def test_discrepancias_de_um_dia_ou_intervalo(client, db_session):
    from app.db.models import EventoScript

    for dia in ("09", "10", "12"):
        db_session.add(EventoScript(script="preencher_mapa", nivel="aviso", mensagem=(
            f"[AVISO] Dia {dia} Vidor SGPS: extrato bancário (1.00 EUR) difere de Mapa de Saldos (0.00 EUR) em 1.00 EUR")))
    db_session.commit()
    um_dia = client.get("/monitorizacao/discrepancias-saldos", params={"dia_inicio": "2026-10-12"}).json()["discrepancias"]
    intervalo = client.get("/monitorizacao/discrepancias-saldos",
                           params={"dia_inicio": "2026-10-09", "dia_fim": "2026-10-10"}).json()["discrepancias"]
    assert {d["dia"][-2:] for d in um_dia} <= {"12"}
    assert {d["dia"][-2:] for d in intervalo} <= {"09", "10"}


# ------------------------------------------- para onde foi o valor (discrepância)

def test_destino_discrepancia_encontra_o_movimento_noutro_dia(client, db_session):
    from app.db.models import MovimentoBancario

    db_session.add_all([
        # o banco lançou a 10/10, o Mapa tem a 12/10 (caso real da Palavra, 12/10/2026)
        MovimentoBancario(dia=date(2026, 10, 10), empresa="PALAVRADICIONAL,LDA", descricao="Trf Mbway 967XXX326",
                          valor=-374.5, ficheiro_origem="x"),
        MovimentoBancario(dia=date(2026, 10, 10), empresa="PALAVRADICIONAL,LDA", descricao="TRF Yesika Tatiana Ca",
                          valor=-2920.02, ficheiro_origem="x"),
        MovimentoBancario(dia=date(2026, 10, 12), empresa="PALAVRADICIONAL,LDA", descricao="COMPRA X",
                          valor=-10, ficheiro_origem="x"),
        LinhaMapa(dia=date(2026, 10, 12), tipo="pagamento", linha=19, empresa="Palavra",
                  descricao="Trf Mbway 967XXX326", pago=-374.5),
        LinhaMapa(dia=date(2026, 10, 12), tipo="pagamento", linha=20, empresa="Palavra",
                  descricao="TRF Yesika Tatiana Ca", imputacao="Salários", pago=-2920.02),
        LinhaMapa(dia=date(2026, 10, 12), tipo="pagamento", linha=21, empresa="Palavra", descricao="COMPRA X", pago=-10),
        LinhaMapa(dia=date(2026, 10, 12), tipo="pagamento", linha=22, empresa="Palavra", descricao="Fantasma", pago=-5),
        # de outra empresa: não entra
        LinhaMapa(dia=date(2026, 10, 12), tipo="pagamento", linha=1, empresa="Vidor SGPS", pago=-999),
    ])
    db_session.commit()
    corpo = client.get("/monitorizacao/discrepancias-saldos/destino",
                       params={"dia": "2026-10-12", "empresa": "Palavra"}).json()
    assert (corpo["soma_extrato"], corpo["soma_mapa"]) == (-10, -3309.52)
    por_linha = {i["linha"]: i for i in corpo["itens"]}
    assert set(por_linha) == {19, 20, 22}  # a COMPRA X bate no próprio dia
    assert por_linha[20]["estado"] == "noutro_dia" and por_linha[20]["outro_dia"] == "2026-10-10"
    assert "no extrato está a 10/10 (TRF Yesika Tatiana Ca)" in por_linha[20]["explicacao"]
    assert "Salários" in por_linha[20]["explicacao"] and "-2.920,02 €" in por_linha[20]["explicacao"]
    assert por_linha[22]["estado"] == "em_falta"
    assert corpo["so_noutro_dia"] is False
