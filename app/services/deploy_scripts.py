"""Deploy dos scripts do Windows a partir do dashboard.

O dashboard pode correr noutra máquina (servidor) e a API em Docker, por
isso nenhum dos dois consegue instalar nada no Windows: a API só grava o
pedido (deploys_scripts) e o agente do Windows (scripts/agente_pedidos.py)
vai buscá-lo, corre os passos de scripts/deploy_windows.py e reporta o
progresso de cada um aqui - é isso que a barra de progresso mostra.
"""
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import DeployScripts

# O que se pode instalar - as chaves têm de existir em ALVOS de
# scripts/deploy_windows.py (é lá que estão os passos).
ALVOS_DEPLOY: Dict[str, str] = {
    "scripts_cgd": "Scripts CGD (MovimentosCGD, Movimentos Diários, Resumo Mensal)",
    "tesouraria_preenchimento": "Tesouraria preenchimento (preencher_mapa, enviar_mapa_smtp, ...)",
}
ESTADOS_ATIVOS = ("pendente", "a_correr")
ESTADOS_FINAIS = ("ok", "erro")
# Um deploy "a_correr" sem notícias do agente há mais que isto (PC desligado,
# agente morto a meio) deixa de bloquear um deploy novo.
SEM_NOTICIAS = timedelta(minutes=30)
MAX_LINHAS_LOG = 300


def _agora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _iso(timestamp: Optional[datetime]) -> Optional[str]:
    return timestamp.isoformat(timespec="seconds") + "Z" if timestamp else None


def _deploy_dict(deploy: DeployScripts) -> Dict[str, Any]:
    return {
        "id": deploy.id,
        "alvo": deploy.alvo,
        "descricao": ALVOS_DEPLOY.get(deploy.alvo, deploy.alvo),
        "estado": deploy.estado,
        "passo": deploy.passo,
        "total_passos": deploy.total_passos,
        "mensagem": deploy.mensagem,
        "erro": deploy.erro,
        "log": deploy.log or [],
        "pedido_em": _iso(deploy.pedido_em),
        "atualizado_em": _iso(deploy.atualizado_em),
        "terminado_em": _iso(deploy.terminado_em),
    }


def _ativo(db: Session, alvo: str) -> Optional[DeployScripts]:
    limite = _agora() - SEM_NOTICIAS
    return (
        db.query(DeployScripts)
        .filter(DeployScripts.alvo == alvo, DeployScripts.estado.in_(ESTADOS_ATIVOS),
                DeployScripts.atualizado_em >= limite)
        .order_by(DeployScripts.id.desc())
        .first()
    )


def pedir_deploy(db: Session, alvo: str) -> Dict[str, Any]:
    """Grava um pedido de deploy. Se já há um pendente/a correr para o
    mesmo alvo, devolve esse (dois cliques seguidos = um deploy)."""
    alvo = alvo.strip().lower()
    if alvo not in ALVOS_DEPLOY:
        raise ValueError(f"Alvo de deploy desconhecido: {alvo}")
    deploy = _ativo(db, alvo)
    if deploy is None:
        deploy = DeployScripts(alvo=alvo, estado="pendente", mensagem="À espera do agente do Windows", log=[])
        db.add(deploy)
        db.commit()
        db.refresh(deploy)
    return _deploy_dict(deploy)


def listar_deploys(db: Session, estado: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    query = db.query(DeployScripts)
    if estado:
        query = query.filter(DeployScripts.estado == estado)
    return [_deploy_dict(d) for d in query.order_by(DeployScripts.id.desc()).limit(limit).all()]


def obter_deploy(db: Session, deploy_id: int) -> Optional[Dict[str, Any]]:
    deploy = db.get(DeployScripts, deploy_id)
    return _deploy_dict(deploy) if deploy else None


def registar_progresso(db: Session, deploy_id: int, estado: str, passo: Optional[int] = None,
                       total_passos: Optional[int] = None, mensagem: Optional[str] = None,
                       erro: Optional[str] = None, linhas: Optional[List[str]] = None) -> Optional[Dict[str, Any]]:
    """O agente do Windows reporta o progresso. Devolve None se o deploy não
    existe ou já terminou (ok/erro) - um deploy terminado não volta atrás."""
    deploy = db.get(DeployScripts, deploy_id)
    if deploy is None or deploy.estado in ESTADOS_FINAIS:
        return None
    deploy.estado = estado
    if passo is not None:
        deploy.passo = passo
    if total_passos is not None:
        deploy.total_passos = total_passos
    if mensagem is not None:
        deploy.mensagem = mensagem
    if erro is not None:
        deploy.erro = erro
    if linhas:
        # nova lista: o SQLAlchemy não deteta alterações dentro do JSON
        deploy.log = ((deploy.log or []) + list(linhas))[-MAX_LINHAS_LOG:]
    deploy.atualizado_em = _agora()
    if estado in ESTADOS_FINAIS:
        deploy.terminado_em = deploy.atualizado_em
        if estado == "ok" and deploy.total_passos:
            deploy.passo = deploy.total_passos
    db.commit()
    db.refresh(deploy)
    return _deploy_dict(deploy)
