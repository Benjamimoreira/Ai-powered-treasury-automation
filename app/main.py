import asyncio
import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse

from app import seguranca
from app.db import models  # noqa: F401  (garante que os modelos são registados antes do create_all)
from app.db.session import engine
from app.routers import (ambiguos, anomalias, chat, comercial, faturas, monitorizacao, previsao_mensal, reconciliacao,
                         rendas, saldos, sync)


async def _reextrair_discrepancias() -> None:
    """De 10 em 10 min pede a reextração de uma discrepância ainda não
    tentada (ver app/services/reextracao_automatica.py). Desliga-se com
    REEXTRACAO_AUTOMATICA=0."""
    from app.db.session import SessionLocal
    from app.services import reextracao_automatica

    def uma_vez() -> None:
        db = SessionLocal()
        try:
            reextracao_automatica.pedir_proxima(db)
        finally:
            db.close()

    while True:
        await asyncio.sleep(reextracao_automatica.DE_QUANTO_EM_QUANTO.total_seconds())
        try:
            await asyncio.to_thread(uma_vez)
        except Exception:
            logging.getLogger("uvicorn.error").exception("Reextração automática das discrepâncias falhou")


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
    models.criar_tabelas(engine)
    if not seguranca.token():
        logging.getLogger("uvicorn.error").warning(
            "API_TOKEN não definido no .env - a API aceita pedidos de qualquer PC da rede sem chave.")
    from app.services.llm_tracing import iniciar_tracing

    iniciar_tracing()
    app.state.agent = None
    app.state.agent_lock = asyncio.Lock()
    ciclo = asyncio.create_task(_reextrair_discrepancias()) if os.environ.get(
        "REEXTRACAO_AUTOMATICA", "1") != "0" else None
    yield
    if ciclo is not None:
        ciclo.cancel()
    if app.state.agent is not None:
        await app.state.agent.cleanup()


app = FastAPI(title="API de Análise de Tesouraria", lifespan=lifespan)


@app.middleware("http")
async def exigir_chave(request: Request, call_next):
    """Ver app/seguranca.py: com API_TOKEN definido, pedidos sem X-API-Key
    válido dão 401 (exceto os reportes dos scripts e os PDFs assinados)."""
    if not seguranca.pedido_autorizado(request.method, request.url.path,
                                       request.headers.get(seguranca.CABECALHO), request.query_params):
        return JSONResponse(status_code=401,
                            content={"detail": f"Chave da API em falta ou inválida (cabeçalho {seguranca.CABECALHO})."})
    return await call_next(request)


app.include_router(reconciliacao.router)
app.include_router(ambiguos.router)
app.include_router(saldos.router)
app.include_router(sync.router)
app.include_router(monitorizacao.router)
app.include_router(faturas.router)
app.include_router(anomalias.router)
app.include_router(chat.router)
app.include_router(comercial.router)
app.include_router(rendas.router)
app.include_router(previsao_mensal.router)


@app.get("/")
def raiz():
    return {"status": "ok"}
