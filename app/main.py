import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.db import models  # noqa: F401  (garante que os modelos são registados antes do create_all)
from app.db.session import Base, engine
from app.routers import ambiguos, anomalias, chat, faturas, monitorizacao, reconciliacao, saldos, sync


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Garante que todas as tabelas de app/db/models.py existem antes de
    aceitar pedidos - create_all só cria o que falta (idempotente, nunca
    altera tabelas existentes), por isso é seguro correr em todos os
    arranques; evita 500 silenciosos em produção quando se esquece o passo
    manual `scripts/criar_tabelas.py` depois de adicionar um modelo novo
    (já aconteceu com ExecucaoScript, FaturaRecebida, AuditoriaDia e
    EventoScript).

    O Agent do chatbot é criado sob demanda (ver app/routers/chat.py:
    obter_agent) na primeira pergunta, não aqui - arrancar o subprocesso
    MCP + handshake a cada arranque da API penalizaria todos os usos da
    API que nunca tocam no chat (incluindo os testes). Aqui só
    preparamos o estado (agent=None, lock para a criação lazy) e
    garantimos que o Agent é fechado corretamente no shutdown, se
    alguma vez chegou a ser criado."""
    Base.metadata.create_all(bind=engine)
    app.state.agent = None
    app.state.agent_lock = asyncio.Lock()
    yield
    if app.state.agent is not None:
        await app.state.agent.cleanup()


app = FastAPI(title="API de Análise de Tesouraria", lifespan=lifespan)

app.include_router(reconciliacao.router)
app.include_router(ambiguos.router)
app.include_router(saldos.router)
app.include_router(sync.router)
app.include_router(monitorizacao.router)
app.include_router(faturas.router)
app.include_router(anomalias.router)
app.include_router(chat.router)


@app.get("/")
def raiz():
    return {"status": "ok"}
