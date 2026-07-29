import asyncio
from contextlib import asynccontextmanager

from fastapi import FastAPI

from app.routers import ambiguos, anomalias, chat, reconciliacao, saldos, sync
from app.services.aws_pipeline import extrair_dados_do_texto


@asynccontextmanager
async def lifespan(app: FastAPI):
    """O Agent do chatbot é criado sob demanda (ver app/routers/chat.py:
    obter_agent) na primeira pergunta, não aqui - arrancar o subprocesso
    MCP + handshake a cada arranque da API penalizaria todos os usos da
    API que nunca tocam no chat (incluindo os testes). Aqui só
    preparamos o estado (agent=None, lock para a criação lazy) e
    garantimos que o Agent é fechado corretamente no shutdown, se
    alguma vez chegou a ser criado."""
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
app.include_router(anomalias.router)
app.include_router(chat.router)


@app.get("/")
def raiz():
    return {"status": "ok"}


@app.post("/aws/processar-documento")
def processar_documento(texto: str, origem: str = "upload"):
    """Endpoint de demonstração para o pipeline AWS.

    Recebe texto extraído de um ficheiro e devolve um payload estruturado
    com fornecedor, valor, data e número de documento.
    """
    return extrair_dados_do_texto(texto, origem=origem)
