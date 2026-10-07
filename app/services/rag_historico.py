"""Recuperação do histórico da entidade - a parte "R" do RAG.

O LLM decide melhor um caso ambíguo se vir como movimentos *parecidos*
desta empresa foram imputados no Mapa ("AGUAS DE GONDOMAR" -> "Aguas").
Até aqui o prompt levava os 6 movimentos mais recentes da empresa, fossem
ou não parecidos com o caso. Este módulo escolhe os k mais relevantes do
histórico, com quatro métodos comparáveis na mesma avaliação
(app/evals/avaliar_recuperacao.py):

- recencia: os k mais recentes (o comportamento anterior - linha de base);
- lexical:  BM25 sobre as palavras do descritivo (empates -> mais recente);
- denso:    semelhança de cosseno entre embeddings (sentence-transformers)
            - apanha abreviaturas e ordem diferente das palavras;
- hibrido:  fusão das duas listas por Reciprocal Rank Fusion (RRF).

Em produção fica o denso: em 621 consultas reais leva o recall@6 de 39 %
(recência) para 80 %; o híbrido não ganha nada e um sinal de valor piora
(resultados em docs/AVALIACAO_LLM.md, "Recuperação").

Cada item do histórico é um dict {"dia", "descricao", "valor",
"imputacao_no_mapa"} - a mesma forma do conjunto de avaliação e de
llm_resolver.historico_da_entidade, para a produção e a avaliação usarem
exatamente o mesmo código. Em produção, os embeddings dos movimentos ficam
guardados na base de dados (pgvector no Postgres - ver
app/services/indice_vetorial.py); aqui só se calcula o que falta.
"""
import math
import os
from collections import Counter

from app.services.resolucao_regras import palavras_significativas

METODOS = ("recencia", "lexical", "denso", "hibrido")
METODO_POR_OMISSAO = os.environ.get("RAG_METODO") or "denso"
# Só foi treinado em inglês, mas empata com três multilingues (e5-small,
# e5-base, paraphrase-multilingual) dentro de 1 ponto: os descritivos
# bancários são códigos e nomes de entidades, não frases em português. Fica
# o mais pequeno (~90 MB, 384 dimensões, CPU).
MODELO_EMBEDDINGS = os.environ.get("EMBEDDINGS_MODELO") or "all-MiniLM-L6-v2"
DIMENSAO_EMBEDDINGS = 384
# constante do RRF (Cormack et al., 2009): amortece o peso das primeiras
# posições, para uma lista não dominar a outra só pelo 1.º lugar
K_RRF = 60

_modelos = {}
_cache_vetores = {}


def _prefixo(modelo: str) -> str:
    """Os modelos E5 foram treinados com "query: " / "passage: " à frente
    do texto; para comparar textos curtos do mesmo tipo usa-se "query: "
    dos dois lados (recomendação dos autores)."""
    return "query: " if "e5" in modelo.lower() else ""


def _carregar(modelo: str):
    if modelo not in _modelos:
        from sentence_transformers import SentenceTransformer
        _modelos[modelo] = SentenceTransformer(modelo)
    return _modelos[modelo]


def vetorizar(textos: list, modelo: str = None) -> list:
    """Embeddings normalizados (norma 1, o cosseno é o produto interno),
    com cache por (modelo, texto): o mesmo descritivo aparece no histórico
    de muitos casos e só é calculado uma vez. Isolado numa função para os
    testes o substituírem por uma versão determinística."""
    modelo = modelo or MODELO_EMBEDDINGS
    em_falta = list(dict.fromkeys(t for t in textos if (modelo, t) not in _cache_vetores))
    if em_falta:
        vetores = _carregar(modelo).encode([_prefixo(modelo) + t for t in em_falta], normalize_embeddings=True)
        for texto, vetor in zip(em_falta, vetores):
            _cache_vetores[(modelo, texto)] = [float(x) for x in vetor]
    return [_cache_vetores[(modelo, t)] for t in textos]


# ---------------------------------------------------------------------------
# Pontuações - maior é mais relevante; o índice do item desempata (os itens
# vêm por ordem cronológica, por isso o mais recente ganha um empate)
# ---------------------------------------------------------------------------


def _tokens(texto: str) -> list:
    return sorted(palavras_significativas(texto))


def pontuacoes_bm25(consulta: str, documentos: list, k1: float = 1.2, b: float = 0.75) -> list:
    """BM25 clássico. O IDF é calculado sobre o próprio histórico: uma
    palavra que aparece em todos os movimentos da empresa (o nome dela)
    não distingue nada e pesa pouco."""
    docs = [_tokens(d) for d in documentos]
    n = len(docs)
    if not n:
        return []
    media = sum(len(d) for d in docs) / n or 1
    df = Counter(t for d in docs for t in set(d))
    termos = _tokens(consulta)
    resultado = []
    for d in docs:
        tf = Counter(d)
        pontos = 0.0
        for t in termos:
            if tf[t]:
                idf = math.log(1 + (n - df[t] + 0.5) / (df[t] + 0.5))
                pontos += idf * tf[t] * (k1 + 1) / (tf[t] + k1 * (1 - b + b * len(d) / media))
        resultado.append(pontos)
    return resultado


def pontuacoes_densas(consulta: str, documentos: list, modelo: str = None) -> list:
    if not documentos:
        return []
    import numpy as np

    vetores = np.asarray(vetorizar([consulta] + list(documentos), modelo))
    return (vetores[1:] @ vetores[0]).tolist()


def _ordem(pontuacoes: list) -> list:
    """Índices do mais relevante para o menos; empate -> mais recente."""
    return sorted(range(len(pontuacoes)), key=lambda i: (pontuacoes[i], i), reverse=True)


def fundir_rrf(*ordens: list, k: int = K_RRF) -> list:
    """Reciprocal Rank Fusion: cada lista dá 1/(k + posição) a cada item.
    Não precisa de calibrar escalas (BM25 e cosseno não são comparáveis)."""
    pontos = Counter()
    for ordem in ordens:
        for posicao, i in enumerate(ordem, start=1):
            pontos[i] += 1 / (k + posicao)
    return sorted(pontos, key=lambda i: (pontos[i], i), reverse=True)


def ordenar(consulta: str, historico: list, metodo: str = None, modelo: str = None) -> list:
    """Índices de `historico` (cronológico) do mais para o menos relevante."""
    metodo = metodo or METODO_POR_OMISSAO
    if metodo not in METODOS:
        raise ValueError(f"Método de recuperação '{metodo}' desconhecido - use um de {METODOS}.")
    textos = [h.get("descricao") or "" for h in historico]
    if metodo == "recencia":
        return list(range(len(historico) - 1, -1, -1))
    if metodo == "lexical":
        return _ordem(pontuacoes_bm25(consulta, textos))
    if metodo == "denso":
        return _ordem(pontuacoes_densas(consulta, textos, modelo))
    return fundir_rrf(_ordem(pontuacoes_bm25(consulta, textos)), _ordem(pontuacoes_densas(consulta, textos, modelo)))


def recuperar(consulta: str, historico: list, k: int, metodo: str = None, modelo: str = None) -> list:
    """Os k itens do histórico mais relevantes para o descritivo `consulta`,
    devolvidos por ordem cronológica (o prompt lê-se como uma história; a
    relevância decide quem entra, não a ordem)."""
    escolhidos = ordenar(consulta, historico, metodo, modelo)[:k]
    return [historico[i] for i in sorted(escolhidos)]
