"""Reextração automática das discrepâncias (Monitorização > Discrepâncias de
saldos) - para não ficarem esquecidas à espera de alguém carregar no
"🔄 Atualizar".

De DE_QUANTO_EM_QUANTO em DE_QUANTO_EM_QUANTO (ciclo em app/main.py) pede o
mesmo que o botão (deploy "reextrair_discrepancia": reextrair a empresa na
semana até ao dia, preencher os Mapas desses dias e verificar, até 3 vezes -
scripts/deploy_windows.py) para UMA discrepância ainda não tentada. Uma de
cada vez: o agente do Windows faz um deploy de cada vez e o banco só aceita
uma sessão, por isso a fila vai andando ciclo a ciclo.

Fica de fora o que voltar a extrair não resolve ou não convém:
- tipos que são de configuração (conta sem entidade/linha, folha em falta);
- o dia de hoje (as tarefas das 15:00 voltam a extrair hoje) e dias com mais
  de DIAS_ATRAS (mais de 90 dias o banco pede SMS por pesquisa);
- as que o destino_discrepancia já sabe que estão só noutro dia no Mapa;
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
from app.services.destino_discrepancia import destino_discrepancia
from app.services.discrepancias_saldos import TIPO_AUDITORIA, chave_discrepancia, listar_discrepancias
from app.services.monitorizacao import FUSO_LOCAL

log = logging.getLogger(__name__)

ALVO = "reextrair_discrepancia"
DE_QUANTO_EM_QUANTO = timedelta(minutes=10)
DIAS_ATRAS = 7
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


def _chave_do_deploy(deploy: DeployScripts) -> Optional[str]:
    extracao = deploy.extracao or {}
    if not extracao.get("empresa"):
        return None
    return chave_discrepancia(extracao.get("dia") or extracao.get("fim"), extracao["empresa"],
                              extracao.get("tipo") or TIPO_AUDITORIA)


def _ja_tentadas(db: Session, agora: datetime) -> set:
    tentadas = set()
    desde = agora - timedelta(days=DIAS_ATRAS + 2)
    for deploy in db.query(DeployScripts).filter(DeployScripts.alvo == ALVO, DeployScripts.pedido_em >= desde):
        chave = _chave_do_deploy(deploy)
        if chave and ((deploy.extracao or {}).get("origem") == "automatica"
                      or deploy.estado in ESTADOS_ATIVOS or deploy.pedido_em >= agora - MANUAL_RECENTE):
            tentadas.add(chave)
    return tentadas


def candidatas(db: Session, agora: Optional[datetime] = None) -> List[Dict[str, Any]]:
    """Discrepâncias que a reextração automática ainda vai tentar, da maior
    diferença para a menor."""
    agora = agora or _agora()
    hoje = agora.replace(tzinfo=timezone.utc).astimezone(FUSO_LOCAL).date()
    tentadas = _ja_tentadas(db, agora)
    escolhidas = []
    for d in listar_discrepancias(db, dias=DIAS_ATRAS + 1):
        if (d["tipo"] not in TIPOS_REEXTRAIVEIS or not d.get("dia") or d["empresa"] == "-"
                or chave_discrepancia(d["dia"], d["empresa"], d["tipo"]) in tentadas):
            continue
        if not hoje - timedelta(days=DIAS_ATRAS) <= date.fromisoformat(d["dia"]) < hoje:
            continue
        escolhidas.append(d)
    return sorted(escolhidas, key=lambda d: abs(d.get("diferenca") or 0), reverse=True)


def pedir_proxima(db: Session, agora: Optional[datetime] = None, ignorar_horario: bool = False
                  ) -> Optional[Dict[str, Any]]:
    """Pede a reextração da próxima discrepância por tentar. Devolve o
    deploy pedido, ou None (fora de horas, já há uma na fila, nada a fazer)."""
    agora = agora or _agora()
    if not ignorar_horario and not dentro_do_horario(agora):
        return None
    if db.query(DeployScripts).filter(DeployScripts.alvo == ALVO,
                                      DeployScripts.estado.in_(ESTADOS_ATIVOS)).first() is not None:
        return None  # uma de cada vez - a seguinte fica para o próximo ciclo
    for d in candidatas(db, agora):
        dia = date.fromisoformat(d["dia"])
        destino = destino_discrepancia(db, dia, d["empresa"])
        if destino.get("so_noutro_dia"):
            continue  # o extrato já tem tudo: é o Mapa que tem de mudar
        log.info("Reextração automática: %s %s (%s)", d["dia"], d["empresa"], d["tipo"])
        return pedir_deploy(db, ALVO, extracao={
            "modo": "periodo", "inicio": (dia - timedelta(days=6)).isoformat(), "fim": dia.isoformat(),
            "dia": dia.isoformat(), "empresa": str(d["empresa"]), "tipo": d["tipo"], "origem": "automatica"})
    return None
