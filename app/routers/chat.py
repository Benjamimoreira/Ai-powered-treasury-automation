import os
import time

from fastapi import APIRouter, Depends, HTTPException, Request
from huggingface_hub.errors import HfHubHTTPError
from pydantic import BaseModel
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import ChatRequest, ChatResponse
from app.services.chatbot import criar_agent, perguntar
from app.services.monitorizacao_ia import registar_feedback, registar_interacao

router = APIRouter()


class FeedbackChatRequest(BaseModel):
    util: bool


async def obter_agent(request: Request):
    """Cria e liga o Agent do chatbot na primeira pergunta (lazy) e
    reutiliza-o nos pedidos seguintes - evita o custo de um subprocesso
    MCP + handshake a cada pergunta. Lock evita criar dois agents em
    paralelo se dois pedidos chegarem antes do primeiro terminar."""
    if request.app.state.agent is None:
        async with request.app.state.agent_lock:
            if request.app.state.agent is None:
                agent = criar_agent()
                try:
                    await agent.__aenter__()
                    await agent.load_tools()
                except Exception as e:
                    await agent.cleanup()
                    raise RuntimeError(f"não consegui ligar às ferramentas do chatbot: {e}") from e
                request.app.state.agent = agent
    return request.app.state.agent


async def _responder(pedido: ChatRequest, request: Request, rastreio: dict) -> dict:
    try:
        agent = await obter_agent(request)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))

    try:
        return await perguntar(agent, pedido.pergunta, rastreio)
    except HfHubHTTPError as e:
        status = e.response.status_code if e.response is not None else None
        if status == 429:
            raise HTTPException(
                status_code=503,
                detail="O modelo local (Ollama) está ocupado - espera um pouco e tenta outra vez.",
            ) from e
        raise HTTPException(
            status_code=503, detail=f"O modelo local (Ollama) falhou ao responder: {e}",
        ) from e
    except OSError as e:  # ligação recusada: o Ollama não está a correr
        raise HTTPException(
            status_code=503,
            detail="Não foi possível ligar ao modelo local (Ollama) - confirma que está a correr.",
        ) from e


@router.post("/chat", response_model=ChatResponse)
async def chat(pedido: ChatRequest, request: Request, db: Session = Depends(get_db)):
    """Pergunta ao chatbot da dashboard: explica os dados e vai buscar
    informação real através de tools só de leitura (ver
    chatbot.FERRAMENTAS_PERMITIDAS) - nunca reconcilia nem resolve nada,
    e nunca inventa valores que não tenha ido buscar. Cada pergunta (e as
    que terminam em erro) fica em interacoes_assistente - ver
    monitorizacao_ia.py."""
    modelo = os.environ.get("OLLAMA_MODEL_ID", "qwen2.5:3b")
    inicio = time.perf_counter()
    rastreio = {}
    try:
        resultado = await _responder(pedido, request, rastreio)
    except Exception as e:
        erro = e.detail if isinstance(e, HTTPException) else f"{type(e).__name__}: {e}"
        registar_interacao(db, pedido.pergunta, status="erro", erro=str(erro),
                           duracao_segundos=time.perf_counter() - inicio, modelo=modelo,
                           trace_id=rastreio.get("trace_id"))
        raise
    interacao_id = registar_interacao(
        db, pedido.pergunta, resposta=resultado["resposta"], ferramentas_usadas=resultado["ferramentas_usadas"],
        duracao_segundos=time.perf_counter() - inicio, modelo=modelo, trace_id=rastreio.get("trace_id"),
    )
    return {**resultado, "id": interacao_id}


@router.post("/chat/{interacao_id}/feedback")
def feedback_chat(interacao_id: int, pedido: FeedbackChatRequest, db: Session = Depends(get_db)):
    """👍/👎 de quem fez a pergunta a uma resposta do Assistente."""
    try:
        return registar_feedback(db, interacao_id, pedido.util)
    except ValueError as e:
        raise HTTPException(status_code=404, detail=str(e))


@router.post("/chat/reset")
async def reset_chat(request: Request):
    """Recomeça a conversa do zero (mantém só o prompt de sistema). Sem
    efeito se o chatbot ainda não tiver sido usado nesta sessão da API."""
    agent = request.app.state.agent
    if agent is not None:
        agent.messages = agent.messages[:1]
    return {"status": "ok"}
