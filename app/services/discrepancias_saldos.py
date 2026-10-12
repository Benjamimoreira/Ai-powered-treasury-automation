"""Discrepâncias de saldos (Monitorização > Discrepâncias de saldos).

O preencher_mapa e o atualizar_mapa_saldos reportam em tempo real
(eventos_scripts) avisos sobre os saldos das empresas - extrato que não
bate com o Mapa de Saldos, movimentos em falta, saldo do topo diferente do
de fecho, etc. São problemas dos DADOS, não dos scripts, e a mesma
mensagem repete-se a cada corrida (dezenas de vezes por dia): por isso
ficam numa secção própria, agrupados por (dia, empresa, tipo) com a última
ocorrência, e saem dos "Erros em tempo real".
"""
import re
from collections import OrderedDict
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import AuditoriaDia, EventoScript

# (padrão na mensagem, tipo mostrado) - por ordem: o primeiro que bate ganha
TIPOS_DISCREPANCIA = [
    (re.compile(r"difere de Mapa de Saldos", re.I), "Extrato/Mapa ≠ Mapa de Saldos"),
    (re.compile(r"falta explicar", re.I), "Movimentos em falta no extrato"),
    (re.compile(r"desfasamento entre o saldo do topo", re.I), "Saldo do topo ≠ saldo de fecho"),
    (re.compile(r"SEM nenhum movimento neste dia, mas o saldo", re.I), "Saldo mudou sem movimentos"),
    (re.compile(r"sem entidade correspondente conhecida", re.I), "Conta sem entidade conhecida"),
    (re.compile(r"Sem linha correspondente na folha de saldos", re.I), "Conta sem linha no Mapa de Saldos"),
    (re.compile(r"Sem folha do dia \d+ no Mapa de Saldos", re.I), "Folha do dia em falta no Mapa de Saldos"),
]

_DIA_EMPRESA = [
    re.compile(r"Dia (?P<dia>\d{1,2}) (?P<empresa>[^:]+):"),
    re.compile(r"(?P<dia>\d{2})-(?P<mes>\d{2}) (?P<empresa>[^:]+):"),
    re.compile(r"(?P<dia>\d{2})-(?P<mes>\d{2}): extrato de '(?P<empresa>[^']+)'"),
    re.compile(r"Sem folha do dia (?P<dia>\d{1,2})"),
]
_EMPRESAS_LISTA = re.compile(r"folha de saldos \(ignoradas\): \[(?P<empresas>[^\]]*)\]")
_DIFERENCA = [
    re.compile(r"em (?P<valor>-?[\d.]+) EUR"),
    re.compile(r"falta explicar (?P<valor>-?[\d.]+) EUR"),
    re.compile(r"diferença de (?P<valor>-?[\d.]+) EUR"),
]


def tipo_discrepancia(mensagem: str) -> Optional[str]:
    for padrao, tipo in TIPOS_DISCREPANCIA:
        if padrao.search(mensagem or ""):
            return tipo
    return None


def e_discrepancia_saldo(mensagem: str) -> bool:
    return tipo_discrepancia(mensagem) is not None


def _dia_da_mensagem(dia: int, mes: Optional[int], visto_em: datetime) -> Optional[str]:
    """As mensagens só trazem o dia (e às vezes o mês); o ano/mês vêm de
    quando foi vista - um "Dia 30" visto a 02/10 é de setembro."""
    try:
        if mes:
            ano = visto_em.year - (1 if mes > visto_em.month else 0)
            return date(ano, mes, dia).isoformat()
        base = visto_em.date()
        if dia > base.day:
            base = base.replace(day=1) - timedelta(days=1)
        return base.replace(day=dia).isoformat()
    except ValueError:
        return None


def _analisar(mensagem: str, visto_em: datetime) -> Dict[str, Any]:
    dia = empresa = None
    for padrao in _DIA_EMPRESA:
        m = padrao.search(mensagem)
        if m:
            grupos = m.groupdict()
            dia = _dia_da_mensagem(int(grupos["dia"]), int(grupos["mes"]) if grupos.get("mes") else None, visto_em)
            empresa = (grupos.get("empresa") or "").strip() or None
            break
    if empresa is None:
        m = _EMPRESAS_LISTA.search(mensagem)
        if m:
            empresa = m.group("empresas").replace("'", "").strip() or None
    diferenca = None
    for padrao in _DIFERENCA:
        m = padrao.search(mensagem)
        if m:
            try:
                diferenca = float(m.group("valor"))
            except ValueError:
                pass
            break
    return {"dia": dia, "empresa": empresa or "-", "diferenca": diferenca}


TIPO_AUDITORIA = "Extrato ≠ Mapa de Pagamentos (auditoria)"
PREFIXO_RESOLVIDA = "[RESOLVIDA] "


def chave_discrepancia(dia: Optional[str], empresa: str, tipo: str) -> str:
    return f"{dia}|{empresa}|{tipo}"


def marcar_resolvida(db: Session, dia: str, empresa: str, tipo: str) -> None:
    """O botão "Atualizar" confirmou que a discrepância desapareceu: fica um
    evento [RESOLVIDA] e deixa de aparecer (até voltar a ser reportada)."""
    db.add(EventoScript(script="discrepancias", nivel="info",
                        mensagem=PREFIXO_RESOLVIDA + chave_discrepancia(dia, empresa, tipo)))
    db.commit()


def _discrepancias_da_auditoria(db: Session, desde: datetime) -> List[Dict[str, Any]]:
    """Diferenças extrato vs Mapa por empresa da auditoria MAIS RECENTE de
    cada dia (a que antes ia por email) - uma auditoria nova sem a empresa
    quer dizer que já bate certo."""
    ultimas: Dict[Any, AuditoriaDia] = {}
    # pelo DIA auditado (não pela hora da auditoria: uma "auditoria geral"
    # recente cobre o ano inteiro e trazia diferenças de janeiro)
    for auditoria in (db.query(AuditoriaDia).filter(AuditoriaDia.dia >= desde.date())
                      .order_by(AuditoriaDia.timestamp).all()):
        ultimas[auditoria.dia] = auditoria
    itens = []
    for dia, auditoria in ultimas.items():
        for d in auditoria.diferencas_por_empresa or []:
            itens.append({
                "dia": dia.isoformat(),
                "empresa": d.get("empresa") or "-",
                "diferenca": round(float(d.get("diferenca") or 0), 2),
                "tipo": TIPO_AUDITORIA,
                "script": "auditoria",
                "mensagem": (f"Extrato {d.get('soma_extrato', 0):.2f} EUR vs Mapa {d.get('soma_mapa', 0):.2f} EUR "
                             f"- diferença {d.get('diferenca', 0):.2f} EUR"),
                "visto_em": auditoria.timestamp.isoformat(timespec="seconds") + "Z",
                "ocorrencias": 1,
            })
    return itens


def listar_discrepancias(db: Session, dias: int = 14, dia_inicio: Optional[date] = None,
                         dia_fim: Optional[date] = None) -> List[Dict[str, Any]]:
    """Discrepâncias vistas nos últimos `dias`, uma por (dia, empresa,
    tipo) - a mais recente, com quantas vezes foi reportada. Junta as
    diferenças da auditoria (extrato vs Mapa) e tira as marcadas como
    resolvidas depois da última vez que foram vistas."""
    desde = datetime.utcnow() - timedelta(days=dias)
    if dia_inicio is not None:
        # um dia/intervalo escolhido: as discrepâncias DESSES dias, que só
        # podem ter sido vistas a partir do próprio dia
        desde = datetime.combine(dia_inicio - timedelta(days=1), datetime.min.time())
    resolvidas = {}
    for e in (db.query(EventoScript).filter(EventoScript.script == "discrepancias",
                                            EventoScript.timestamp >= desde).all()):
        if e.mensagem.startswith(PREFIXO_RESOLVIDA):
            resolvidas[e.mensagem[len(PREFIXO_RESOLVIDA):]] = e.timestamp
    eventos = (
        db.query(EventoScript)
        .filter(EventoScript.timestamp >= desde, EventoScript.nivel.in_(("aviso", "erro", "warning", "error")))
        .order_by(EventoScript.timestamp.desc())
        .all()
    )
    agrupadas: "OrderedDict[tuple, Dict[str, Any]]" = OrderedDict()
    for evento in eventos:  # mais recentes primeiro
        tipo = tipo_discrepancia(evento.mensagem)
        if tipo is None:
            continue
        info = _analisar(evento.mensagem, evento.timestamp)
        chave = (info["dia"], info["empresa"], tipo)
        if chave in agrupadas:
            agrupadas[chave]["ocorrencias"] += 1
            continue
        agrupadas[chave] = {
            **info,
            "tipo": tipo,
            "script": evento.script,
            "mensagem": re.sub(r"^\s*\[(AVISO|ERRO)\]\s*", "", evento.mensagem),
            "visto_em": evento.timestamp.isoformat(timespec="seconds") + "Z",
            "ocorrencias": 1,
        }
    itens = list(agrupadas.values()) + _discrepancias_da_auditoria(db, desde)
    itens = [i for i in itens
             if not (chave_discrepancia(i["dia"], i["empresa"], i["tipo"]) in resolvidas
                     and resolvidas[chave_discrepancia(i["dia"], i["empresa"], i["tipo"])].isoformat(timespec="seconds") + "Z"
                     >= i["visto_em"])]
    if dia_inicio is not None:
        ate = (dia_fim or dia_inicio).isoformat()
        itens = [i for i in itens if i["dia"] and dia_inicio.isoformat() <= i["dia"] <= ate]
    return sorted(itens, key=lambda d: (d["dia"] or "", d["visto_em"]), reverse=True)
