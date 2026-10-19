"""Reextração automática das discrepâncias (Monitorização > Discrepâncias de
saldos) - para não ficarem esquecidas à espera de alguém carregar no
"🔄 Atualizar".

De DE_QUANTO_EM_QUANTO em DE_QUANTO_EM_QUANTO (ciclo em app/main.py) pede
UMA corrida para TODAS as discrepâncias ainda não tentadas (pedido
19/10/2026 - antes era uma de cada vez): deploy "corrigir_discrepancias"
(scripts/deploy_windows.py) - uma só extração de todas as empresas desde a
semana antes da discrepância mais antiga até à mais recente (o banco só
aceita uma sessão), um preencher desses dias, as correções seguras no Mapa
(corrigir_discrepancias.py do PT2, ver correcoes_discrepancias.py) e a
verificação de cada uma.

Fica de fora o que voltar a extrair não resolve ou não convém:
- tipos que são de configuração (conta sem entidade/linha, folha em falta);
- o dia de hoje (as tarefas das 15:00 voltam a extrair hoje) e dias com mais
  de DIAS_ATRAS (mais de 90 dias o banco pede SMS por pesquisa);
- as já tentadas (automática uma vez, ou à mão nas últimas 24 h);
- fora do horário em que alguém pode responder a um SMS do banco e longe
  das tarefas agendadas da CGD (08:00-08:30, 15:00-15:15).
"""
import logging
from datetime import date, datetime, time, timedelta, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import DeployScripts
from app.services.deploy_scripts import ESTADOS_ATIVOS, pedir_deploy
from app.services.discrepancias_saldos import TIPO_AUDITORIA, chave_discrepancia, listar_discrepancias
from app.services.monitorizacao import FUSO_LOCAL

log = logging.getLogger(__name__)

ALVO = "corrigir_discrepancias"
ALVO_BOTAO = "reextrair_discrepancia"  # o "🔄 Atualizar" de uma discrepância
DE_QUANTO_EM_QUANTO = timedelta(minutes=10)
DIAS_ATRAS = 7
# botão "Corrigir todas agora": até aqui (o banco só pede SMS por pesquisa
# a mais de 90 dias, e a extração ainda vai uma semana para trás)
DIAS_ATRAS_BOTAO = 60
# só estas se resolvem com uma extração nova do banco
TIPOS_REEXTRAIVEIS = {
    TIPO_AUDITORIA,
    "Extrato/Mapa ≠ Mapa de Saldos",
    "Movimentos em falta no extrato",
    "Saldo do topo ≠ saldo de fecho",
    "Saldo mudou sem movimentos",
}
# hora local, dias úteis
JANELAS = [(time(9, 0), time(14, 30)), (time(15, 45), time(19, 0))]
MANUAL_RECENTE = timedelta(hours=24)


def _agora() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def dentro_do_horario(agora_utc: datetime) -> bool:
    local = agora_utc.replace(tzinfo=timezone.utc).astimezone(FUSO_LOCAL)
    return local.weekday() < 5 and any(inicio <= local.time() < fim for inicio, fim in JANELAS)


def _chaves_do_deploy(deploy: DeployScripts) -> List[str]:
    extracao = deploy.extracao or {}
    if extracao.get("discrepancias"):
        return [chave_discrepancia(d["dia"], d["empresa"], d.get("tipo") or TIPO_AUDITORIA)
                for d in extracao["discrepancias"]]
    if not extracao.get("empresa"):
        return []
    return [chave_discrepancia(extracao.get("dia") or extracao.get("fim"), extracao["empresa"],
                               extracao.get("tipo") or TIPO_AUDITORIA)]


def _ja_tentadas(db: Session, agora: datetime) -> set:
    tentadas = set()
    desde = agora - timedelta(days=DIAS_ATRAS + 2)
    for deploy in db.query(DeployScripts).filter(DeployScripts.alvo.in_((ALVO, ALVO_BOTAO)),
                                                 DeployScripts.pedido_em >= desde):
        if ((deploy.extracao or {}).get("origem") == "automatica"
                or deploy.estado in ESTADOS_ATIVOS or deploy.pedido_em >= agora - MANUAL_RECENTE):
            tentadas.update(_chaves_do_deploy(deploy))
    return tentadas


def candidatas(db: Session, agora: Optional[datetime] = None, incluir_tentadas: bool = False,
               dias_atras: int = DIAS_ATRAS) -> List[Dict[str, Any]]:
    """Discrepâncias que a reextração automática ainda vai tentar, da maior
    diferença para a menor (incluir_tentadas: também as já tentadas)."""
    agora = agora or _agora()
    hoje = agora.replace(tzinfo=timezone.utc).astimezone(FUSO_LOCAL).date()
    tentadas = set() if incluir_tentadas else _ja_tentadas(db, agora)
    escolhidas = []
    for d in listar_discrepancias(db, dias=dias_atras + 1):
        if (d["tipo"] not in TIPOS_REEXTRAIVEIS or not d.get("dia") or d["empresa"] == "-"
                or chave_discrepancia(d["dia"], d["empresa"], d["tipo"]) in tentadas):
            continue
        if not hoje - timedelta(days=dias_atras) <= date.fromisoformat(d["dia"]) < hoje:
            continue
        escolhidas.append(d)
    return sorted(escolhidas, key=lambda d: abs(d.get("diferenca") or 0), reverse=True)


def pedir_proxima(db: Session, agora: Optional[datetime] = None, ignorar_horario: bool = False,
                  incluir_tentadas: bool = False, dias_atras: int = DIAS_ATRAS) -> Optional[Dict[str, Any]]:
    """Pede UMA corrida para todas as discrepâncias por tentar. Devolve o
    deploy pedido, ou None (fora de horas, já há uma a correr, nada a fazer)."""
    agora = agora or _agora()
    if not ignorar_horario and not dentro_do_horario(agora):
        return None
    if db.query(DeployScripts).filter(DeployScripts.alvo.in_((ALVO, ALVO_BOTAO)),
                                      DeployScripts.estado.in_(ESTADOS_ATIVOS)).first() is not None:
        return None  # o agente faz um de cada vez e o banco só aceita uma sessão
    lista = candidatas(db, agora, incluir_tentadas=incluir_tentadas, dias_atras=dias_atras)
    if not lista:
        return None
    dias = [date.fromisoformat(d["dia"]) for d in lista]
    log.info("Correção automática de %d discrepância(s), %s a %s", len(lista), min(dias), max(dias))
    return pedir_deploy(db, ALVO, extracao={
        "modo": "periodo", "inicio": (min(dias) - timedelta(days=6)).isoformat(), "fim": max(dias).isoformat(),
        "discrepancias": [{"dia": d["dia"], "empresa": str(d["empresa"]), "tipo": d["tipo"]} for d in lista],
        "origem": "botao" if incluir_tentadas else "automatica"})
