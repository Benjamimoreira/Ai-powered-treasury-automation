"""Indexa os embeddings de todos os movimentos bancários ainda sem embedding
(tabela embeddings_movimentos - ver app/services/indice_vetorial.py).

Não é obrigatório: o que falta é indexado na hora, na primeira sugestão que
precise. Serve para preencher tudo de uma vez (ex. depois de mudar de
modelo) e para não pagar esse custo num pedido.

    python scripts/indexar_embeddings.py [--modelo all-MiniLM-L6-v2]
    docker compose exec api python scripts/indexar_embeddings.py
"""
import argparse
import os
import sys
import time

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.db.session import SessionLocal  # noqa: E402
from app.services import indice_vetorial, rag_historico  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--modelo", default=rag_historico.MODELO_EMBEDDINGS)
    args = parser.parse_args()
    db = SessionLocal()
    try:
        inicio = time.perf_counter()
        n = indice_vetorial.indexar(db, modelo=args.modelo)
        print(f"{n} movimentos indexados com {args.modelo} em {time.perf_counter() - inicio:.1f}s.")
    finally:
        db.close()


if __name__ == "__main__":
    main()
