from datetime import date

from app.db.models import LinhaMapa, MovimentoBancario
from app.services.correcoes_discrepancias import correcoes_seguras, descricao_do_banco, movimentos_de_linha

DIA = date(2026, 10, 14)  # quarta-feira


def _mov(db, dia, valor, descricao="TRF MBWAY JOAO SILVA", empresa="HCC"):
    db.add(MovimentoBancario(empresa=empresa, dia=dia, descricao=descricao, valor=valor, ficheiro_origem="x.xlsx"))


def _linha(db, dia, valor, linha=10, descricao="TRF MBWAY JOAO SILVA", previsto=None, empresa="HCC"):
    db.add(LinhaMapa(empresa=empresa, dia=dia, tipo="pagamento" if valor < 0 else "recebimento", linha=linha,
                     descricao=descricao, pago=valor, previsto=previsto))


def test_muda_de_dia_a_linha_escrita_pelo_preencher(db_session):
    _mov(db_session, DIA, -374.5)
    _linha(db_session, date(2026, 10, 12), -374.5)
    # o que já bate certo na mesma folha não conta
    _mov(db_session, DIA, -50, descricao="COMISSAO")
    _linha(db_session, DIA, -50, linha=11, descricao="COMISSAO")
    db_session.commit()
    assert movimentos_de_linha(db_session, DIA, "HCC") == [{
        "tipo": "pagamento", "empresa": "HCC", "descricao": "TRF MBWAY JOAO SILVA", "valor": 374.5,
        "dia_origem": "2026-10-12", "linha_origem": 10, "dia_destino": "2026-10-14",
        "motivo": "no extrato a 14/10 (TRF MBWAY JOAO SILVA)", "discrepancia": {"dia": "2026-10-14", "empresa": "HCC"}}]


def test_linha_no_dia_da_discrepancia_vai_para_o_dia_do_extrato(db_session):
    _mov(db_session, date(2026, 10, 16), 1200, descricao="TRF CLIENTE X")
    _linha(db_session, DIA, 1200, descricao="TRF CLIENTE X")
    db_session.commit()
    [mov] = movimentos_de_linha(db_session, DIA, "HCC")
    assert (mov["dia_origem"], mov["dia_destino"], mov["tipo"], mov["valor"]) == ("2026-10-14", "2026-10-16", "recebimento", 1200)


def test_movimento_de_sabado_vai_para_a_folha_de_segunda(db_session):
    _mov(db_session, date(2026, 10, 17), -80, descricao="PAGAMENTO SERVICOS")  # sábado -> folha de 19/10
    _linha(db_session, DIA, -80, descricao="PAGAMENTO SERVICOS")
    db_session.commit()
    assert movimentos_de_linha(db_session, DIA, "HCC")[0]["dia_destino"] == "2026-10-19"


def test_nao_mexe_quando_ha_duvida(db_session):
    # com Valor Previsto (planeada à mão)
    _mov(db_session, DIA, -374.5)
    _linha(db_session, date(2026, 10, 12), -374.5, previsto=-374.5)
    # descrição escrita à mão
    _mov(db_session, DIA, -99)
    _linha(db_session, date(2026, 10, 12), -99, linha=11, descricao="Almoço equipa")
    # dois movimentos com o mesmo valor: ambíguo
    _mov(db_session, DIA, -20)
    _mov(db_session, date(2026, 10, 13), -20)
    _linha(db_session, date(2026, 10, 9), -20, linha=12)
    db_session.commit()
    assert movimentos_de_linha(db_session, DIA, "HCC") == []


def test_nao_muda_entre_meses_nem_mistura_empresas(db_session):
    dia = date(2026, 10, 1)
    _mov(db_session, dia, -300)
    _linha(db_session, date(2026, 9, 29), -300)  # outro ficheiro do Mapa
    _mov(db_session, dia, -400, empresa="Outra Lda")
    _linha(db_session, date(2026, 10, 2), -400, linha=11, empresa="Outra Lda")
    db_session.commit()
    assert movimentos_de_linha(db_session, dia, "HCC") == []


def test_correcoes_seguras_nao_repete_a_mesma_linha(db_session):
    _mov(db_session, DIA, -374.5)
    _linha(db_session, date(2026, 10, 12), -374.5)
    db_session.commit()
    lista = [{"dia": "2026-10-14", "empresa": "HCC"}, {"dia": "2026-10-12", "empresa": "HCC"},
             {"dia": "2026-10-14", "empresa": "-"}]
    assert len(correcoes_seguras(db_session, lista)) == 1


def test_endpoint(client, db_session):
    _mov(db_session, DIA, -374.5)
    _linha(db_session, date(2026, 10, 12), -374.5)
    db_session.commit()
    r = client.post("/monitorizacao/discrepancias-saldos/correcoes-seguras", json={
        "discrepancias": [{"dia": "2026-10-14", "empresa": "HCC"}], "sincronizar": False}).json()
    assert r["discrepancias"] == 1 and r["correcoes"][0]["linha_origem"] == 10


def test_descricao_do_banco():
    assert descricao_do_banco("TRF MBWAY João Silva", "TRF MBWAY JOAO SILVA")
    assert descricao_do_banco("TRF MBWAY JOAO", "TRF MBWAY JOAO SILVA 123")  # extrato truncado
    assert not descricao_do_banco("Almoços", "TRF MBWAY JOAO SILVA")
    assert not descricao_do_banco(None, "TRF")


def test_endpoint_usa_as_discrepancias_da_corrida_a_decorrer(client, db_session):
    from app.db.models import DeployScripts

    _mov(db_session, DIA, -374.5)
    _linha(db_session, date(2026, 10, 12), -374.5)
    db_session.add(DeployScripts(alvo="corrigir_discrepancias", estado="a_correr", log=[], extracao={
        "modo": "periodo", "inicio": "2026-10-08", "fim": "2026-10-14",
        "discrepancias": [{"dia": "2026-10-14", "empresa": "HCC", "tipo": "x"}]}))
    db_session.commit()
    r = client.post("/monitorizacao/discrepancias-saldos/correcoes-seguras", json={"sincronizar": False}).json()
    assert r["discrepancias"] == 1 and len(r["correcoes"]) == 1


def test_corrigir_todas_sem_nada_da_409(client):
    assert client.post("/monitorizacao/discrepancias-saldos/corrigir-todas").status_code == 409
