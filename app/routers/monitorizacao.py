from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.services.deploy_scripts import ALVOS_DEPLOY, listar_deploys, obter_deploy, pedir_deploy, registar_progresso
from app.services.destinatarios_mapa import adicionar_destinatario, listar_destinatarios, remover_destinatario
from app.services.monitorizacao import (
    listar_eventos, listar_logs, listar_pedidos, listar_scripts, marcar_pedido, pedir_corrida, registar_evento,
    registar_execucao,
)
from app.services.monitorizacao_ia import anotar_interacao, calibracao_juiz, listar_para_rever, metricas_ia

router = APIRouter(prefix="/monitorizacao", tags=["monitorizacao"])


class ExecucaoScriptRequest(BaseModel):
    status: str = Field(..., description="ok|erro|warning")
    erro: Optional[str] = None
    log: Optional[List[str]] = None
    duracao_segundos: Optional[float] = None


class PedidoCorridaEstadoRequest(BaseModel):
    estado: str = Field(..., pattern="^(iniciado|erro)$")
    erro: Optional[str] = None


class EventoScriptRequest(BaseModel):
    nivel: str = Field(..., description="erro|aviso|info")
    mensagem: str


class DestinatarioRequest(BaseModel):
    email: str


@router.get("/destinatarios-mapa")
def listar_destinatarios_mapa(db: Session = Depends(get_db)):
    """Para quem o enviar_mapa_smtp.py manda o Mapa (lido pelo script antes
    de cada envio). Nunca vazio: sem ninguém, volta o pduarte@vidor.pt."""
    return {"destinatarios": listar_destinatarios(db)}


@router.post("/destinatarios-mapa")
def adicionar_destinatario_mapa(payload: DestinatarioRequest, db: Session = Depends(get_db)):
    try:
        return {"destinatarios": adicionar_destinatario(db, payload.email)}
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.delete("/destinatarios-mapa/{email}")
def remover_destinatario_mapa(email: str, db: Session = Depends(get_db)):
    return {"destinatarios": remover_destinatario(db, email)}


class DeployRequest(BaseModel):
    alvo: str = Field(..., description="|".join(ALVOS_DEPLOY))


class DeployProgressoRequest(BaseModel):
    estado: str = Field(..., pattern="^(a_correr|ok|erro)$")
    passo: Optional[int] = Field(None, ge=0)
    total_passos: Optional[int] = Field(None, ge=0)
    mensagem: Optional[str] = None
    erro: Optional[str] = None
    linhas: Optional[List[str]] = None


@router.get("/scripts")
def listar_status_scripts(db: Session = Depends(get_db)):
    return {"scripts": listar_scripts(db)}


@router.post("/scripts/{script}/executar")
def executar_script(script: str, payload: ExecucaoScriptRequest, db: Session = Depends(get_db)):
    try:
        return registar_execucao(db, script, payload.status, erro=payload.erro, log=payload.log, duracao_segundos=payload.duracao_segundos)
    except Exception as exc:  # pragma: no cover - não deve acontecer nesta camada
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.post("/scripts/{script}/correr")
def correr_script_endpoint(script: str, db: Session = Depends(get_db)):
    """Botão "Correr" do dashboard: lança o script já, se a API corre
    nativamente na máquina dos scripts, ou grava um pedido para o agente do
    Windows (scripts/agente_pedidos.py) o lançar, se a API corre em Docker.
    O resultado da corrida chega depois pelo caminho normal,
    POST /scripts/{script}/executar, enviado pelo próprio script."""
    try:
        return pedir_corrida(db, script)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/pedidos")
def listar_pedidos_corrida(estado: Optional[str] = None, limit: int = 20, db: Session = Depends(get_db)):
    """Pedidos do botão "Correr" - o agente do Windows lê os pendentes
    (estado=pendente) e o dashboard mostra o estado do último de cada script."""
    return {"pedidos": listar_pedidos(db, estado=estado, limit=limit)}


@router.post("/pedidos/{pedido_id}/estado")
def marcar_pedido_corrida(pedido_id: int, payload: PedidoCorridaEstadoRequest, db: Session = Depends(get_db)):
    """O agente do Windows marca o pedido como iniciado/erro. 409 se o
    pedido já não estava pendente (outro agente já o tratou)."""
    pedido = marcar_pedido(db, pedido_id, payload.estado, erro=payload.erro)
    if pedido is None:
        raise HTTPException(status_code=409, detail="Pedido inexistente ou já tratado")
    return pedido


@router.get("/deploys/alvos")
def listar_alvos_deploy():
    return {"alvos": [{"alvo": alvo, "descricao": descricao} for alvo, descricao in ALVOS_DEPLOY.items()]}


@router.post("/deploys")
def pedir_deploy_scripts(payload: DeployRequest, db: Session = Depends(get_db)):
    """Botão "Deploy" do dashboard: grava o pedido para o agente do Windows
    (scripts/agente_pedidos.py) o correr. Se já há um pendente/a correr
    para o mesmo alvo, devolve esse."""
    try:
        return pedir_deploy(db, payload.alvo)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/deploys")
def listar_deploys_scripts(estado: Optional[str] = None, limit: int = 10, db: Session = Depends(get_db)):
    """Deploys mais recentes primeiro - o agente lê os pendentes
    (estado=pendente) e o dashboard mostra o progresso."""
    return {"deploys": listar_deploys(db, estado=estado, limit=limit)}


@router.get("/deploys/{deploy_id}")
def obter_deploy_scripts(deploy_id: int, db: Session = Depends(get_db)):
    deploy = obter_deploy(db, deploy_id)
    if deploy is None:
        raise HTTPException(status_code=404, detail="Deploy inexistente")
    return deploy


@router.post("/deploys/{deploy_id}/progresso")
def progresso_deploy_scripts(deploy_id: int, payload: DeployProgressoRequest, db: Session = Depends(get_db)):
    """O agente do Windows reporta cada passo. 409 se o deploy não existe ou
    já terminou."""
    deploy = registar_progresso(db, deploy_id, payload.estado, passo=payload.passo,
                                total_passos=payload.total_passos, mensagem=payload.mensagem,
                                erro=payload.erro, linhas=payload.linhas)
    if deploy is None:
        raise HTTPException(status_code=409, detail="Deploy inexistente ou já terminado")
    return deploy


@router.get("/logs")
def listar_monitorizacao_logs(limit: int = 50, dia: Optional[date] = None, db: Session = Depends(get_db)):
    return {"logs": listar_logs(db, limit=limit, dia=dia)}


@router.post("/scripts/{script}/eventos")
def registar_evento_script(script: str, payload: EventoScriptRequest, db: Session = Depends(get_db)):
    """Recebe um evento de log em tempo real (um [ERRO]/[AVISO] apanhado
    a meio de uma corrida ainda a decorrer) - ver _HandlerEventoDashboard
    em monitorizacao_client.py. Separado de /scripts/{script}/executar,
    que só reporta o resultado final da corrida toda."""
    try:
        return registar_evento(db, script, payload.nivel, payload.mensagem)
    except Exception as exc:  # pragma: no cover - não deve acontecer nesta camada
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@router.get("/eventos")
def listar_monitorizacao_eventos(limit: int = 50, script: Optional[str] = None, db: Session = Depends(get_db)):
    return {"eventos": listar_eventos(db, limit=limit, script=script)}


@router.get("/ia")
def metricas_qualidade_ia(dias: int = 30, db: Session = Depends(get_db)):
    """Qualidade da IA em produção nos últimos `dias`: Assistente (perguntas,
    erros, 👍/👎, tempos) e sugestões para casos ambíguos (aceites vs
    rejeitadas, decididas por regras vs LLM, resolvidas sem sugestão) - ver
    app/services/monitorizacao_ia.py."""
    return metricas_ia(db, dias=dias)


class AnotacaoRequest(BaseModel):
    label: str = Field(..., description="correta|incorreta|alucinada|incompleta")
    score: Optional[float] = Field(None, ge=0, le=1)
    notas: Optional[str] = None
    resposta_esperada: Optional[str] = None
    autor: Optional[str] = None


@router.get("/ia/para-rever")
def fila_para_rever(limit: int = 50, db: Session = Depends(get_db)):
    """Respostas do Assistente a rever por uma pessoa: 👎, números não
    verificados, ou chumbadas pelo juiz das avaliações online."""
    return {"interacoes": listar_para_rever(db, limite=limit)}


@router.post("/ia/interacoes/{interacao_id}/anotacao")
def anotar(interacao_id: int, payload: AnotacaoRequest, db: Session = Depends(get_db)):
    """Anotação humana de uma resposta (também vai para o trace no Phoenix).
    Com resposta_esperada, a resposta entra no golden dataset do Assistente
    na próxima corrida de app/evals/promover_golden.py."""
    try:
        return anotar_interacao(db, interacao_id, payload.label, payload.score, payload.notas,
                                payload.resposta_esperada, payload.autor)
    except ValueError as exc:
        status = 404 if "não encontrada" in str(exc) else 422
        raise HTTPException(status_code=status, detail=str(exc)) from exc


@router.get("/ia/calibracao")
def calibracao(db: Session = Depends(get_db)):
    """Concordância (e kappa de Cohen) entre o juiz das avaliações online e
    as pessoas (anotações, ou 👍/👎) - quanto vale o juiz."""
    return calibracao_juiz(db)
