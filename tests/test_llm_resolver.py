from datetime import date

import pytest

from app.db.models import CasoAmbiguo, LinhaMapa, MovimentoBancario
from app.services import llm_resolver
from app.services.llm_resolver import (
    casos_resolvidos_semelhantes,
    sugerir_resolucao,
    texto_do_caso,
)

DIA = date(2026, 7, 21)


def _criar_caso_ambiguo(db_session, empresa="Ancora Apogeu", valor=-100.0, resolvido_por=None, resolucao=None):
    movimento = MovimentoBancario(
        dia=DIA, empresa=empresa, descricao="TRANSF", valor=valor, ficheiro_origem="x.xlsx",
    )
    db_session.add(movimento)
    db_session.flush()

    linha_a = LinhaMapa(dia=DIA, tipo="pagamento", linha=5, empresa=empresa, previsto=valor)
    linha_b = LinhaMapa(dia=DIA, tipo="pagamento", linha=9, empresa=empresa, previsto=valor)
    db_session.add(linha_a)
    db_session.add(linha_b)
    db_session.flush()

    caso = CasoAmbiguo(
        movimento_id=movimento.id, dia=DIA, empresa=empresa, valor=valor,
        candidatos=[linha_a.id, linha_b.id], resolvido_por=resolvido_por, resolucao=resolucao,
    )
    db_session.add(caso)
    db_session.commit()
    return caso, linha_a, linha_b


def test_texto_do_caso_inclui_descricao_do_movimento(db_session):
    caso, _, _ = _criar_caso_ambiguo(db_session)
    texto = texto_do_caso(db_session, caso)
    assert "Ancora Apogeu" in texto
    assert "TRANSF" in texto
    assert "100.00" in texto


def test_casos_resolvidos_semelhantes_devolve_vazio_sem_historico(db_session):
    caso, _, _ = _criar_caso_ambiguo(db_session)
    assert casos_resolvidos_semelhantes(db_session, caso) == []


def test_casos_resolvidos_semelhantes_ordena_por_similaridade(db_session, monkeypatch):
    caso, _, _ = _criar_caso_ambiguo(db_session)
    caso_parecido, _, _ = _criar_caso_ambiguo(
        db_session, empresa="Ancora Apogeu", valor=-100.0, resolvido_por="benjamim", resolucao="linha_id=1",
    )
    caso_diferente, _, _ = _criar_caso_ambiguo(
        db_session, empresa="Palavra", valor=-999.0, resolvido_por="benjamim", resolucao="linha_id=2",
    )

    def embeddings_falsos(textos):
        # dá ao "caso" e ao "caso_parecido" o mesmo vetor, e ao "caso_diferente" um vetor ortogonal
        vetores = []
        for texto in textos:
            if "999.00" in texto:
                vetores.append([0.0, 1.0])
            else:
                vetores.append([1.0, 0.0])
        return vetores

    monkeypatch.setattr(llm_resolver, "obter_embeddings", embeddings_falsos)

    resultado = casos_resolvidos_semelhantes(db_session, caso, top_k=2)

    assert resultado[0][0].id == caso_parecido.id
    assert resultado[0][1] == pytest.approx(1.0)
    assert resultado[1][0].id == caso_diferente.id
    assert resultado[1][1] == pytest.approx(0.0)


def test_sugerir_resolucao_grava_sugestao_valida(db_session, monkeypatch):
    caso, linha_a, _ = _criar_caso_ambiguo(db_session)

    monkeypatch.setattr(
        llm_resolver, "chamar_llm",
        lambda prompt: f'{{"linha_id": {linha_a.id}, "justificacao": "bate com o histórico"}}',
    )

    resultado = sugerir_resolucao(db_session, caso.id)

    assert resultado.resolucao_sugerida == f"linha_id={linha_a.id}"
    assert resultado.justificacao_sugerida == "bate com o histórico"
    # sugerir_resolucao nunca resolve sozinho - fica só a sugestão
    assert resultado.resolvido_por is None


def test_sugerir_resolucao_lida_com_resposta_sem_json(db_session, monkeypatch):
    caso, _, _ = _criar_caso_ambiguo(db_session)

    monkeypatch.setattr(llm_resolver, "chamar_llm", lambda prompt: "não sei responder a isto")

    resultado = sugerir_resolucao(db_session, caso.id)

    assert resultado.resolucao_sugerida is None
    assert "não sei responder a isto" in resultado.justificacao_sugerida


def test_chamar_llm_falha_sem_token(monkeypatch):
    monkeypatch.delenv("HF_TOKEN", raising=False)
    monkeypatch.delenv("GROQ_API_KEY", raising=False)
    with pytest.raises(RuntimeError):
        llm_resolver.chamar_llm("qualquer prompt")
