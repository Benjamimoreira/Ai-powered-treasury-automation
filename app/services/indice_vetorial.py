"""Índice vetorial dos descritivos dos movimentos bancários (pgvector).

Os embeddings ficam guardados na tabela embeddings_movimentos - calculados
uma vez por movimento e modelo, em vez de a cada sugestão. A pesquisa
"quais destes movimentos têm o descritivo mais parecido com este?" é feita:
- no Postgres, na própria base de dados, com a distância de cosseno do
  pgvector (operador <=>) - imagem docker/db;
- no SQLite (testes, desenvolvimento), em Python sobre os vetores em JSON.

Pesquisa exata e sem índice HNSW de propósito: a consulta é sempre sobre o
histórico de uma empresa (algumas centenas de movimentos, ~5 mil no total),
e o filtro por empresa/dia tiraria o proveito de um índice aproximado. Um
HNSW (CREATE INDEX ... USING hnsw (vetor vector_cosine_ops)) só compensa
com dezenas de milhares de vetores pesquisados sem filtro.

O que falta indexar é indexado na hora (movimentos novos vindos do
OneDrive); scripts/indexar_embeddings.py indexa tudo de uma vez.
"""
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db.models import EmbeddingMovimento, MovimentoBancario
from app.services import rag_historico

LOTE = 256


def _postgres(db: Session) -> bool:
    return db.get_bind().dialect.name == "postgresql"


def indexar(db: Session, movimento_ids=None, modelo: str = None) -> int:
    """Calcula e guarda os embeddings em falta (de todos os movimentos, ou
    só de `movimento_ids`). Devolve quantos foram acrescentados."""
    modelo = modelo or rag_historico.MODELO_EMBEDDINGS
    ja = select(EmbeddingMovimento.movimento_id).where(EmbeddingMovimento.modelo == modelo)
    consulta = db.query(MovimentoBancario.id, MovimentoBancario.descricao).filter(MovimentoBancario.id.notin_(ja))
    if movimento_ids is not None:
        consulta = consulta.filter(MovimentoBancario.id.in_(list(movimento_ids)))
    em_falta = consulta.all()
    for i in range(0, len(em_falta), LOTE):
        lote = em_falta[i:i + LOTE]
        textos = [descricao or "" for _, descricao in lote]
        for (movimento_id, _), texto, vetor in zip(lote, textos, rag_historico.vetorizar(textos, modelo)):
            db.add(EmbeddingMovimento(movimento_id=movimento_id, modelo=modelo, texto=texto, vetor=vetor))
        db.commit()
    return len(em_falta)


def ordenar(db: Session, consulta: str, movimento_ids: list, modelo: str = None) -> list:
    """`movimento_ids` do descritivo mais parecido com `consulta` para o
    menos (empate -> o mais recente, como em rag_historico)."""
    if not movimento_ids:
        return []
    modelo = modelo or rag_historico.MODELO_EMBEDDINGS
    indexar(db, movimento_ids, modelo)
    vetor_consulta = rag_historico.vetorizar([consulta], modelo)[0]
    filtro = (EmbeddingMovimento.modelo == modelo, EmbeddingMovimento.movimento_id.in_(list(movimento_ids)))

    if _postgres(db):
        return list(db.scalars(
            select(EmbeddingMovimento.movimento_id).where(*filtro)
            .join(MovimentoBancario, MovimentoBancario.id == EmbeddingMovimento.movimento_id)
            .order_by(EmbeddingMovimento.vetor.cosine_distance(vetor_consulta),
                      MovimentoBancario.dia.desc(), MovimentoBancario.id.desc())
        ))

    import numpy as np

    linhas = db.execute(
        select(EmbeddingMovimento.movimento_id, EmbeddingMovimento.vetor, MovimentoBancario.dia).where(*filtro)
        .join(MovimentoBancario, MovimentoBancario.id == EmbeddingMovimento.movimento_id)
    ).all()
    semelhanca = np.asarray([v for _, v, _ in linhas]) @ np.asarray(vetor_consulta)
    ordem = sorted(range(len(linhas)), key=lambda i: (semelhanca[i], linhas[i][2], linhas[i][0]), reverse=True)
    return [linhas[i][0] for i in ordem]
