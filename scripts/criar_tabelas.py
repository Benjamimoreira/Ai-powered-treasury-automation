"""Cria as tabelas da base de dados (api_tesouraria.db) a partir dos
modelos definidos em app/db/models.py. Corre uma vez para preparar a BD."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import models  # noqa: F401  (garante que os modelos são registados)
from app.db.session import engine

if __name__ == "__main__":
    # com CREATE EXTENSION vector no Postgres (pgvector) - ver models.criar_tabelas
    models.criar_tabelas(engine)
    print(f"Tabelas criadas em {engine.url.render_as_string(hide_password=True)}.")
