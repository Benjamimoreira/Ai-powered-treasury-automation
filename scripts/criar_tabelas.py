"""Cria as tabelas da base de dados (api_tesouraria.db) a partir dos
modelos definidos em app/db/models.py. Corre uma vez para preparar a BD."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from app.db import models  # noqa: F401  (garante que os modelos são registados)
from app.db.session import Base, engine

if __name__ == "__main__":
    Base.metadata.create_all(bind=engine)
    print("Tabelas criadas em api_tesouraria.db.")
