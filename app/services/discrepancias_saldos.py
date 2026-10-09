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

from app.db.models import EventoScript

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


def listar_discrepancias(db: Session, dias: int = 14) -> List[Dict[str, Any]]:
    """Discrepâncias vistas nos últimos `dias`, uma por (dia, empresa,
    tipo) - a mais recente, com quantas vezes foi reportada."""
    desde = datetime.utcnow() - timedelta(days=dias)
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
    return sorted(agrupadas.values(), key=lambda d: (d["dia"] or "", d["visto_em"]), reverse=True)
