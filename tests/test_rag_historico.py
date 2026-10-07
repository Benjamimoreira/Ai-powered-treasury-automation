from datetime import date, timedelta

import pytest

from app.db.models import EmbeddingMovimento, LinhaMapa, MovimentoBancario
from app.evals import avaliar as avaliador
from app.evals import avaliar_recuperacao
from app.services import indice_vetorial, rag_historico
from app.services.llm_resolver import historico_da_entidade

EMPRESA = "Ancora Apogeu"
INICIO = date(2026, 6, 1)


def _item(descricao, imputacao, dia=1):
    return {"dia": f"2026-06-{dia:02d}", "descricao": descricao, "valor": -10.0, "imputacao_no_mapa": imputacao}


# histórico cronológico: o movimento relevante para "AGUAS DE GONDOMAR" é o
# mais antigo, os mais recentes são ruído
HISTORICO = [
    _item("AGUAS DE GONDOMAR", "Aguas", 1),
    _item("COMPANHIA DE SEGUROS", "Seguros", 2),
    _item("MAIO 2026", "Condominio", 3),
    _item("VIA VERDE PAY", "Portagens", 4),
    _item("EDP COMERCIAL", "Electricidade", 5),
]


def test_recencia_devolve_os_ultimos_por_ordem_cronologica():
    assert rag_historico.recuperar("AGUAS DE GONDOMAR", HISTORICO, 2, "recencia") == HISTORICO[-2:]


@pytest.mark.parametrize("metodo", ["lexical", "denso", "hibrido"])
def test_metodos_de_relevancia_encontram_o_movimento_parecido_mesmo_antigo(metodo):
    escolhidos = rag_historico.recuperar("AGUAS GONDOMAR FT 12", HISTORICO, 2, metodo)
    assert HISTORICO[0] in escolhidos
    # devolvidos por ordem cronológica, não de relevância
    assert escolhidos == sorted(escolhidos, key=lambda h: h["dia"])


def test_bm25_sem_palavras_em_comum_desempata_pelo_mais_recente():
    ordem = rag_historico.ordenar("NADA A VER", HISTORICO, "lexical")
    assert ordem == [4, 3, 2, 1, 0]


def test_rrf_premia_quem_esta_bem_nas_duas_listas():
    assert rag_historico.fundir_rrf([0, 1, 2], [1, 2, 0])[0] == 1


def test_metodo_desconhecido_da_erro():
    with pytest.raises(ValueError):
        rag_historico.ordenar("x", HISTORICO, "magia")


# ---------------------------------------------------------------------------
# Produção: histórico da entidade a partir da base de dados
# ---------------------------------------------------------------------------


def _criar_par(db, dia, descricao, imputacao, valor):
    mov = MovimentoBancario(dia=dia, empresa=EMPRESA, descricao=descricao, valor=valor, ficheiro_origem="x.xlsx")
    linha = LinhaMapa(dia=dia, tipo="pagamento", linha=1, empresa=EMPRESA, previsto=valor, pago=valor,
                      imputacao=imputacao)
    db.add_all([mov, linha])
    db.commit()
    return mov


def _historico_na_bd(db):
    _criar_par(db, INICIO, "AGUAS DE GONDOMAR", "Aguas", -19.17)
    for i, (descricao, imputacao) in enumerate([("COMPANHIA DE SEGUROS", "Seguros"), ("MAIO 2026", "Condominio"),
                                                ("VIA VERDE PAY", "Portagens"), ("EDP COMERCIAL", "Electricidade")]):
        _criar_par(db, INICIO + timedelta(days=i + 1), descricao, imputacao, -100.0 - i)


def test_historico_sem_consulta_continua_a_ser_o_mais_recente(db_session):
    _historico_na_bd(db_session)
    historico = historico_da_entidade(db_session, EMPRESA, INICIO + timedelta(days=30), n=2)
    assert [h["imputacao_no_mapa"] for h in historico] == ["Portagens", "Electricidade"]


def test_historico_com_consulta_traz_o_parecido_e_guarda_os_embeddings(db_session):
    _historico_na_bd(db_session)
    historico = historico_da_entidade(db_session, EMPRESA, INICIO + timedelta(days=30), n=2,
                                      consulta="AGUAS DE GONDOMAR 0626")
    assert "Aguas" in [h["imputacao_no_mapa"] for h in historico]
    assert db_session.query(EmbeddingMovimento).count() == 5

    # a segunda vez não volta a calcular nada
    assert indice_vetorial.indexar(db_session) == 0


def test_historico_nao_ve_o_futuro(db_session):
    _historico_na_bd(db_session)
    historico = historico_da_entidade(db_session, EMPRESA, INICIO, n=6, consulta="AGUAS DE GONDOMAR")
    assert historico == []


def test_historico_recupera_em_memoria_se_o_indice_falhar(db_session, monkeypatch):
    _historico_na_bd(db_session)

    def falhar(*a, **k):
        raise RuntimeError("sem pgvector")

    monkeypatch.setattr(indice_vetorial, "ordenar", falhar)
    historico = historico_da_entidade(db_session, EMPRESA, INICIO + timedelta(days=30), n=2,
                                      consulta="AGUAS DE GONDOMAR")
    assert "Aguas" in [h["imputacao_no_mapa"] for h in historico]


# ---------------------------------------------------------------------------
# Avaliação
# ---------------------------------------------------------------------------


def test_avaliacao_da_recuperacao_mede_a_vantagem_sobre_a_recencia():
    consultas = [{"id": "c1", "consulta": "AGUAS DE GONDOMAR", "historico": HISTORICO, "alvo": "AGUAS"},
                 # sem nenhum relevante no histórico - fica fora da conta
                 {"id": "c2", "consulta": "IRN", "historico": HISTORICO, "alvo": "IRN"}]
    recencia = avaliar_recuperacao.avaliar_metodo(consultas, "recencia")
    lexical = avaliar_recuperacao.avaliar_metodo(consultas, "lexical")
    assert recencia["consultas_avaliadas"] == lexical["consultas_avaliadas"] == 1
    assert (recencia["recall@1"], recencia["mrr"]) == (0.0, pytest.approx(1 / 5))
    assert (lexical["recall@1"], lexical["mrr"]) == (1.0, 1.0)


def test_consultas_do_conjunto_de_recuperacao_so_usam_o_passado():
    dados = {"empresas": [{"empresa": "EMPRESA_01", "historico": [
        _item("AGUAS", "Aguas", 1), _item("AGUAS", "Aguas", 1), _item("AGUAS", "Aguas", 2),
        _item("X", "Desconheço", 3),
    ]}]}
    consultas = avaliar_recuperacao.consultas_de_recuperacao(dados)
    # a rubrica "Desconheço" não serve de resposta certa
    assert [c["id"] for c in consultas] == ["EMPRESA_01#0", "EMPRESA_01#1", "EMPRESA_01#2"]
    # o do mesmo dia não conta como passado
    assert [len(c["historico"]) for c in consultas] == [0, 0, 2]


def test_avaliacao_do_llm_envia_o_historico_recuperado():
    ruido = [_item(f"RENDA PESSOA_{i:02d}", "Rendas", 10 + i) for i in range(6)]
    caso = {"id": "c", "movimento": {"descricao": "AGUAS DE GONDOMAR"}, "historico_entidade": HISTORICO + ruido}
    assert len(avaliador.dados_para_prompt(caso, "recencia")["historico_entidade"]) == 6
    assert HISTORICO[0] not in avaliador.dados_para_prompt(caso, "recencia")["historico_entidade"]
    assert HISTORICO[0] in avaliador.dados_para_prompt(caso, "denso")["historico_entidade"]
