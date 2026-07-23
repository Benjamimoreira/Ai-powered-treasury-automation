from fastapi import APIRouter, HTTPException, Request

from app.models import ChatRequest, ChatResponse
from app.services.chatbot import criar_agent, perguntar

router = APIRouter()


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


@router.post("/chat", response_model=ChatResponse)
async def chat(pedido: ChatRequest, request: Request):
    """Pergunta ao chatbot da dashboard: explica os dados e vai buscar
    informação real através de tools só de leitura (ver
    chatbot.FERRAMENTAS_PERMITIDAS) - nunca reconcilia nem resolve nada,
    e nunca inventa valores que não tenha ido buscar."""
    try:
        agent = await obter_agent(request)
    except RuntimeError as e:
        raise HTTPException(status_code=503, detail=str(e))
    return await perguntar(agent, pedido.pergunta)


@router.post("/chat/reset")
async def reset_chat(request: Request):
    """Recomeça a conversa do zero (mantém só o prompt de sistema). Sem
    efeito se o chatbot ainda não tiver sido usado nesta sessão da API."""
    agent = request.app.state.agent
    if agent is not None:
        agent.messages = agent.messages[:1]
    return {"status": "ok"}
