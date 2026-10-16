"""Deploy dos scripts do Windows a partir do dashboard.

O dashboard pode correr noutra máquina (servidor) e a API em Docker, por
isso nenhum dos dois consegue instalar nada no Windows: a API só grava o
pedido (deploys_scripts) e o agente do Windows (scripts/agente_pedidos.py)
vai buscá-lo, corre os passos de scripts/deploy_windows.py e reporta o
progresso de cada um aqui - é isso que a barra de progresso mostra.
"""
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import DeployScripts, EventoScript

# O que se pode instalar - as chaves têm de existir em ALVOS de
# scripts/deploy_windows.py (é lá que estão os passos).
ALVOS_DEPLOY: Dict[str, str] = {
    "cgd_MovimentosCGD": "MovimentosCGD - Extratos CGD (mês 08:00, noite 15:15 e anual 00:00/12:00)",
    "cgd_MovimentosDiaAtualCGD": "MovimentosDiaAtualCGD - Movimentos do dia atual (08:20, 15:00)",
    "cgd_MovimentosDiariosCGD": "MovimentosDiariosCGD - Movimentos de ontem (08:30)",
    "cgd_MovimentosEscolherDiaCGD": "MovimentosEscolherDiaCGD - Movimentos de um dia escolhido à mão",
    "cgd_PreencherResumoMensalCGD": "PreencherResumoMensalCGD - Resumo Mensal CGD (09:00, 16:30)",
    "tesouraria_preenchimento": "Tesouraria - Atualizar Mapa Tesouraria, Atualizar Mapa Saldos, Enviar Mapa",
    "scripts_cgd": "Todos os scripts CGD de uma vez",
    # botão "Atualizar" de uma discrepância (não instala nada): reextrair a
    # empresa nesse dia, preencher os Mapas desse dia e verificar
    "reextrair_discrepancia": "Atualizar uma discrepância (reextrair a empresa no dia)",
}
# Alvos da CGD: no fim do deploy fazem o que foi escolhido no dashboard -
# "Hoje"/"Intervalo": extrair esses dias, atualizar os Mapas e o dashboard
# (scripts/deploy_windows.py, passos_atualizar); "Ano": extração anual.
ALVOS_COM_EXTRACAO = {alvo for alvo in ALVOS_DEPLOY if alvo.startswith("cgd_") or alvo == "scripts_cgd"}
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
        "extracao": deploy.extracao,
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


def _validar_extracao(extracao: Optional[Dict[str, Any]]) -> Optional[Dict[str, Any]]:
    """{"modo": "nenhuma"|"ano"} ou {"modo": "periodo", "inicio", "fim"} (AAAA-MM-DD)."""
    if not extracao:
        return None
    modo = extracao.get("modo")
    if modo in ("nenhuma", "ano"):
        return {"modo": modo}
    if modo != "periodo":
        raise ValueError(f"Modo de extração desconhecido: {modo}")
    try:
        inicio = date.fromisoformat(str(extracao["inicio"]))
        fim = date.fromisoformat(str(extracao.get("fim") or extracao["inicio"]))
    except (KeyError, ValueError) as exc:
        raise ValueError(f"Período de extração inválido: {extracao}") from exc
    if fim < inicio:
        raise ValueError("A data de fim da extração é anterior à de início.")
    resultado = {"modo": "periodo", "inicio": inicio.isoformat(), "fim": fim.isoformat()}
    # reextração de uma discrepância: a empresa, o tipo e o dia dela (para a
    # verificar - a extração vai uma semana para trás desse dia)
    # origem "automatica": pedida pela reextracao_automatica.py, não pelo botão
    for campo in ("empresa", "tipo", "origem"):
        if extracao.get(campo):
            resultado[campo] = str(extracao[campo])
    if extracao.get("dia"):
        try:
            dia = date.fromisoformat(str(extracao["dia"]))
        except ValueError as exc:
            raise ValueError(f"Dia da discrepância inválido: {extracao['dia']}") from exc
        if not inicio <= dia <= fim:
            raise ValueError("O dia da discrepância tem de estar dentro do período a extrair.")
        resultado["dia"] = dia.isoformat()
    return resultado


def pedir_deploy(db: Session, alvo: str, extracao: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    """Grava um pedido de deploy. Se já há um pendente/a correr para o
    mesmo alvo, devolve esse (dois cliques seguidos = um deploy)."""
    alvo = alvo.strip().lower()
    if alvo not in ALVOS_DEPLOY:
        raise ValueError(f"Alvo de deploy desconhecido: {alvo}")
    extracao = _validar_extracao(extracao)
    deploy = _ativo(db, alvo)
    if deploy is not None and alvo == "reextrair_discrepancia" and deploy.extracao != extracao:
        deploy = None  # outra discrepância: pedido novo (o agente faz um de cada vez)
    if deploy is None:
        deploy = DeployScripts(alvo=alvo, estado="pendente", mensagem="À espera do agente do Windows", log=[],
                               extracao=extracao)
        db.add(deploy)
        db.commit()
        db.refresh(deploy)
    return _deploy_dict(deploy)


# a correr sem nenhuma notícia do agente há mais do que isto = o agente
# parou ou o fim perdeu-se (o agente manda sinal de vida de minuto a minuto,
# mesmo à espera dos Mapas)
SEM_NOTICIAS_A_CORRER = timedelta(minutes=20)


def _expirar_parados(db: Session) -> None:
    limite = _agora() - SEM_NOTICIAS_A_CORRER
    parados = (db.query(DeployScripts)
               .filter(DeployScripts.estado == "a_correr", DeployScripts.atualizado_em < limite).all())
    for deploy in parados:
        hora = deploy.atualizado_em.strftime("%H:%M") if deploy.atualizado_em else "?"
        deploy.estado = "erro"
        deploy.erro = (f"Sem notícias do agente do Windows desde as {hora} UTC - o agente parou ou perdeu a "
                       "ligação à API. Pede outra vez.")
        deploy.mensagem = deploy.mensagem or "Parado"
        deploy.terminado_em = _agora()
    if parados:
        db.commit()


def listar_deploys(db: Session, estado: Optional[str] = None, limit: int = 10) -> List[Dict[str, Any]]:
    _expirar_parados(db)
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
    # O deploy aparece também nos separadores da Monitorização como o
    # script "deploy_<alvo>": [ERRO]/[AVISO] nos "Erros em tempo real" e o
    # resultado no "Histórico de logs"/"Detalhe por script".
    nome_script = f"deploy_{deploy.alvo}"
    for linha in linhas or []:
        nivel = "erro" if "[ERRO]" in linha else "aviso" if "[AVISO]" in linha else None
        if nivel:
            db.add(EventoScript(script=nome_script, nivel=nivel, mensagem=linha))
    db.commit()
    db.refresh(deploy)
    if estado in ESTADOS_FINAIS:
        from app.services.monitorizacao import registar_execucao

        duracao = (deploy.terminado_em - deploy.pedido_em).total_seconds() if deploy.pedido_em else None
        registar_execucao(
            db, nome_script, "ok" if estado == "ok" else "erro",
            erro=deploy.erro if estado == "erro" else None,
            log=[deploy.mensagem or ""] + [l for l in (deploy.log or []) if l.startswith(("==", "[ERRO]", "[AVISO]"))][-40:],
            duracao_segundos=duracao,
        )
    return _deploy_dict(deploy)
