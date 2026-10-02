import os
import sys
from pathlib import Path

# Os testes nunca enviam traces: sem isto, o load_dotenv() da app lia o
# LANGSMITH_TRACING=true do .env e cada pytest aparecia no LangSmith como
# atividade real (LLM falso, erros simulados). Tem de ficar antes de
# importar a app - o load_dotenv() não substitui variáveis já definidas.
for _var in ("LANGSMITH_TRACING", "LANGCHAIN_TRACING_V2"):
    os.environ[_var] = "false"
os.environ["PHOENIX_COLLECTOR_ENDPOINT"] = ""

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from app.db.session import Base, get_db
from app.main import app


@pytest.fixture()
def session_factory():
    """Fábrica de sessões (sessionmaker) ligada a um motor SQLite em
    memória partilhado (StaticPool) - útil para código como o servidor
    MCP, que abre/fecha a sua própria sessão a cada chamada em vez de
    receber uma por injeção de dependências."""
    engine = create_engine(
        "sqlite:///:memory:",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(bind=engine)
    return sessionmaker(autocommit=False, autoflush=False, bind=engine)


@pytest.fixture()
def db_session(session_factory):
    session = session_factory()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture()
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    with TestClient(app) as test_client:
        yield test_client
    app.dependency_overrides.clear()
