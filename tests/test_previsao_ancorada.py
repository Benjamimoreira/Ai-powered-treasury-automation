from datetime import date, timedelta

import pytest

from app.db.models import MovimentoBancario, SaldoDiario
from app.services.comercial import _parse_data_agenda
from app.services.previsao_ancorada import (
    ContextoPrevisao,
    backtest_previsao_ancorada,
    empresa_do_indice,
    prever_saldo_ancorado,
)

EMPRESA = "HABISERVE INVESTIMENTOS IMOBILIARIOS,LDA"
DIA_INICIAL = date(2026, 6, 1)


def _popular(db_session, dias=60, empresa=EMPRESA):
    """Saldo que sobe e desce com os movimentos (entradas às segundas,
    saídas nos outros dias úteis) - sem tendência de fundo."""
    saldo = 10_000.0
    for i in range(dias):
        dia = DIA_INICIAL + timedelta(days=i)
        valor = {0: 400.0, 5: 0.0, 6: 0.0}.get(dia.weekday(), -100.0)
        if valor:
            db_session.add(MovimentoBancario(
                dia=dia, empresa=empresa, descricao="movimento de teste", valor=valor,
                ficheiro_origem="teste.xlsx",
            ))
        saldo += valor
        db_session.add(SaldoDiario(
            dia=dia, entidade=empresa, saldo_contabilistico=saldo, saldo_disponivel=saldo,
        ))
    db_session.commit()
    return saldo


def _recebimento(dia, valor, empresa=EMPRESA):
    return {
        "dia": dia, "valor": valor, "tipo": "Escritura", "empresa": "Habiserve - Invest. Imobiliários",
        "ref": "00.PO.1", "empreendimento": "Alva", "fracao": "Q", "cliente": "Cliente", "empresa_extrato": empresa,
    }


def test_data_agenda_desempata_pelo_dia_do_cpcv():
    # CPCV a 4 de março -> reforço "4/9/2026" é 4 de setembro, não 9 de abril
    assert _parse_data_agenda("4/9/2026", dia_do_cpcv=4) == date(2026, 9, 4)
    # CPCV a 9 de junho -> "12/9/2026" é 9 de dezembro
    assert _parse_data_agenda("12/9/2026", dia_do_cpcv=9) == date(2026, 12, 9)
    # só uma leitura válida
    assert _parse_data_agenda("16/10/2026", dia_do_cpcv=16) == date(2026, 10, 16)
    assert _parse_data_agenda("1/31/2027", dia_do_cpcv=31) == date(2027, 1, 31)
    assert _parse_data_agenda(None) is None


def test_empresa_do_indice_liga_nome_abreviado_a_designacao_social():
    empresas = [EMPRESA, "HABISERVE CONSTRUCOES CENTRO,LDA", "J PINTO CONSTRUCOES,LDA"]
    assert empresa_do_indice("Habiserve - Invest. Imobiliários,Lda", empresas) == EMPRESA
    assert empresa_do_indice("J. Pinto Construções", empresas) == "J PINTO CONSTRUCOES,LDA"
    assert empresa_do_indice("Empresa Desconhecida", empresas) is None


def test_previsao_sem_fluxos_conhecidos_fica_no_saldo_atual(db_session):
    saldo_final = _popular(db_session)

    r = prever_saldo_ancorado(db_session, EMPRESA, 30, contexto=ContextoPrevisao())

    assert len(r["previsao"]) == 30
    assert all(p["valor"] == pytest.approx(saldo_final) for p in r["previsao"])
    assert r["previsao_com_comercial"] is None
    # a banda vem dos dias sorteados (padrão semanal fixo nesta série), à
    # volta da central - sem deriva, fica perto do saldo atual
    banda = r["banda_incerteza"]
    assert banda["baixa"][-1]["valor"] <= banda["alta"][-1]["valor"]
    assert abs(banda["baixa"][-1]["valor"] - saldo_final) < 1_000


def test_recebimentos_comerciais_ficam_so_na_linha_a_parte(db_session):
    saldo_final = _popular(db_session)
    ultimo_dia = DIA_INICIAL + timedelta(days=59)
    dia_escritura = ultimo_dia + timedelta(days=10)
    while dia_escritura.weekday() >= 5:
        dia_escritura += timedelta(days=1)
    contexto = ContextoPrevisao(recebimentos_comerciais=[_recebimento(dia_escritura, 50_000.0)])

    r = prever_saldo_ancorado(db_session, EMPRESA, 30, contexto=contexto)

    assert r["previsao"][-1]["valor"] == pytest.approx(saldo_final)
    assert r["previsao_com_comercial"][-1]["valor"] == pytest.approx(saldo_final + 50_000.0)
    [fluxo] = r["fluxos_conhecidos_previstos"]
    assert fluxo["fonte"] == "comercial" and fluxo["dia"] == dia_escritura.isoformat()


def test_recebimento_comercial_de_outra_empresa_nao_entra(db_session):
    _popular(db_session)
    contexto = ContextoPrevisao(recebimentos_comerciais=[
        _recebimento(DIA_INICIAL + timedelta(days=65), 50_000.0, empresa="OUTRA EMPRESA,LDA"),
    ])

    r = prever_saldo_ancorado(db_session, EMPRESA, 30, contexto=contexto)

    assert r["fluxos_conhecidos_previstos"] == []


def test_previsao_falha_com_historico_insuficiente(db_session):
    _popular(db_session, dias=3)
    with pytest.raises(ValueError):
        prever_saldo_ancorado(db_session, EMPRESA, 30, contexto=ContextoPrevisao())


def test_backtest_compara_com_saldo_fica_igual(db_session, monkeypatch):
    monkeypatch.setattr("app.services.previsao_ancorada.recebimentos_comerciais_por_empresa", lambda db: [])
    _popular(db_session, dias=90)

    b = backtest_previsao_ancorada(db_session, 14, n_cortes=5, empresa=EMPRESA)

    assert len(b["cortes"]) == 5
    assert b["erro_medio_modelo"] is not None and b["erro_medio_sem_alteracao"] is not None
    # sem fluxos conhecidos, a previsão É o "fica igual"
    assert b["erro_medio_modelo"] == pytest.approx(b["erro_medio_sem_alteracao"], abs=1e-6)



# --- risco de liquidez -----------------------------------------------------------

def test_nivel_risco_liquidez():
    from app.services.previsao_ancorada import nivel_risco_liquidez

    assert [nivel_risco_liquidez(p) for p in (0.9, 0.5, 0.3, 0.05, None)] == [
        "alto", "alto", "moderado", "baixo", "sem dados"]


def test_risco_liquidez_distingue_empresa_que_queima_caixa_da_folgada(db_session, monkeypatch):
    from app.services.previsao_ancorada import listar_risco_liquidez

    monkeypatch.setattr("app.services.previsao_ancorada.recebimentos_comerciais_por_empresa", lambda db: [])
    hoje = date(2026, 9, 30)
    for empresa, saldo_final, pagamento in (("QUEIMA CAIXA,LDA", 500.0, -2_000.0), ("FOLGADA,LDA", 900_000.0, -50.0)):
        for i in range(60):
            dia = hoje - timedelta(days=59 - i)
            if dia.weekday() < 5:
                db_session.add(MovimentoBancario(dia=dia, empresa=empresa, descricao="FORNECEDOR",
                                                 valor=pagamento, ficheiro_origem="t.xlsx"))
            db_session.add(SaldoDiario(dia=dia, entidade=empresa, saldo_contabilistico=saldo_final,
                                       saldo_disponivel=saldo_final))
    # empresa sem leitura há meses: não se inventa risco
    db_session.add_all([SaldoDiario(dia=date(2026, 3, d), entidade="PARADA,LDA", saldo_contabilistico=10.0,
                                    saldo_disponivel=10.0) for d in range(1, 11)])
    db_session.add(MovimentoBancario(dia=date(2026, 3, 2), empresa="PARADA,LDA", descricao="X", valor=-1.0,
                                     ficheiro_origem="t.xlsx"))
    db_session.commit()

    por_empresa = {r["empresa"]: r for r in listar_risco_liquidez(db_session, 30)}

    assert por_empresa["QUEIMA CAIXA,LDA"]["nivel"] == "alto"
    assert por_empresa["QUEIMA CAIXA,LDA"]["primeiro_dia_provavel"] is not None
    assert por_empresa["FOLGADA,LDA"]["nivel"] == "baixo"
    assert por_empresa["PARADA,LDA"]["nivel"] == "sem leitura recente"
    assert por_empresa["PARADA,LDA"]["probabilidade_negativo"] is None
