import time
from contextlib import contextmanager
from datetime import datetime, timedelta
from typing import Any, Dict, Iterator, List, Optional
from zoneinfo import ZoneInfo

from sqlalchemy.orm import Session

from app.db.models import ExecucaoScript

FUSO_LOCAL = ZoneInfo("Europe/Lisbon")
TOLERANCIA_ATRASO_MINUTOS = 20

SCRIPT_PADRAO: Dict[str, Dict[str, str]] = {
    "preencher_mapa": {
        "descricao": "Preenchimento do Mapa de Pagamentos e Recebimentos a partir dos extratos bancários",
        "hora_execucao": "08:50, 12:50, 14:10, 16:15, 18:30",
    },
    "atualizar_mapa_saldos": {
        "descricao": "Atualização do Mapa de Saldos Bancários (folhas diárias)",
        "hora_execucao": "08:55, 12:55, 14:15, 16:20, 18:35",
    },
    "enviar_mapa_smtp": {
        "descricao": "Envio diário do Mapa de Pagamentos e Recebimentos por email",
        "hora_execucao": "16:30",
    },
}


def _isoformat(timestamp: datetime) -> str:
    return timestamp.isoformat(timespec="seconds") + "Z"


def _horas_esperadas_hoje(hora_execucao: str) -> List[Any]:
    """Converte 'hora_execucao' (ex.: '08:50, 12:50, 14:10') na lista dos
    horários (datetime.time) esperados para o script, ignorando entradas
    que não sigam o formato HH:MM (nunca deixa a verificação de atraso
    rebentar por causa de um valor mal formatado)."""
    horas = []
    for parte in hora_execucao.split(","):
        parte = parte.strip()
        if not parte:
            continue
        try:
            h, m = parte.split(":")
            horas.append((int(h), int(m)))
        except ValueError:
            continue
    return horas


def _verificar_atraso(hora_execucao: str, ultima_execucao_iso: Optional[str], agora: Optional[datetime] = None) -> Dict[str, Any]:
    """Compara os horários esperados (hora_execucao) com a última execução
    conhecida e sinaliza "atrasado" quando algum horário de hoje já devia
    ter corrido - com TOLERANCIA_ATRASO_MINUTOS de folga, para não acusar
    atraso por causa de um jitter normal do Agendador de Tarefas - e ainda
    não há registo de execução depois desse horário.

    Só considera os horários de HOJE que já passaram; um script cujo
    próximo horário ainda não chegou não é "atrasado" só por a última
    execução ter sido ontem."""
    agora = agora or datetime.now(FUSO_LOCAL)
    limite = agora - timedelta(minutes=TOLERANCIA_ATRASO_MINUTOS)

    horas_devidas_hoje = [
        datetime.combine(agora.date(), datetime.min.time(), tzinfo=FUSO_LOCAL).replace(hour=h, minute=m)
        for h, m in _horas_esperadas_hoje(hora_execucao)
    ]
    horas_devidas_hoje = [h for h in horas_devidas_hoje if h <= limite]
    if not horas_devidas_hoje:
        return {"atrasado": False, "hora_em_falta": None}

    hora_mais_recente_devida = max(horas_devidas_hoje)

    dt_ultima = None
    if ultima_execucao_iso:
        try:
            dt_ultima = datetime.fromisoformat(ultima_execucao_iso.replace("Z", "+00:00")).astimezone(FUSO_LOCAL)
        except ValueError:
            dt_ultima = None

    if dt_ultima is None or dt_ultima < hora_mais_recente_devida:
        return {"atrasado": True, "hora_em_falta": hora_mais_recente_devida.strftime("%H:%M")}
    return {"atrasado": False, "hora_em_falta": None}


def listar_scripts(db: Session) -> List[Dict[str, Any]]:
    nomes = set(SCRIPT_PADRAO) | {nome for (nome,) in db.query(ExecucaoScript.script).distinct()}

    resultado = []
    for nome in sorted(nomes):
        info = SCRIPT_PADRAO.get(nome, {})
        hora_execucao = info.get("hora_execucao", "08:00")
        ultima = (
            db.query(ExecucaoScript)
            .filter(ExecucaoScript.script == nome)
            .order_by(ExecucaoScript.timestamp.desc())
            .first()
        )
        ultima_execucao_iso = _isoformat(ultima.timestamp) if ultima else None
        atraso = _verificar_atraso(hora_execucao, ultima_execucao_iso)
        resultado.append({
            "nome": nome,
            "descricao": info.get("descricao", f"Script {nome}"),
            "hora_execucao": hora_execucao,
            "status": ultima.status if ultima else "ok",
            "ultima_execucao": ultima_execucao_iso,
            "ultima_erro": ultima.erro if ultima else None,
            "atrasado": atraso["atrasado"],
            "hora_em_falta": atraso["hora_em_falta"],
        })
    return resultado


def listar_logs(db: Session, limit: int = 50) -> List[Dict[str, Any]]:
    execucoes = (
        db.query(ExecucaoScript)
        .order_by(ExecucaoScript.timestamp.desc())
        .limit(limit)
        .all()
    )
    logs = []
    for execucao in reversed(execucoes):
        nivel = "erro" if execucao.status == "erro" else "info"
        mensagem = execucao.erro or f"Execução do script {execucao.script} registada com status {execucao.status}."
        logs.append({
            "timestamp": _isoformat(execucao.timestamp),
            "script": execucao.script,
            "nivel": nivel,
            "mensagem": mensagem,
            "detalhe": execucao.log or [],
        })
    return logs


def registar_execucao(db: Session, script: str, status: str, erro: Optional[str] = None, log: Optional[List[str]] = None, duracao_segundos: Optional[float] = None) -> Dict[str, Any]:
    nome = script.strip().lower()
    execucao = ExecucaoScript(script=nome, status=status, erro=erro, log=log or [], duracao_segundos=duracao_segundos)
    db.add(execucao)
    db.commit()
    db.refresh(execucao)

    return {
        "nome": nome,
        "status": status,
        "ultima_execucao": _isoformat(execucao.timestamp),
        "ultima_erro": erro,
        "duracao_segundos": duracao_segundos,
        "logs": log or [],
    }


@contextmanager
def monitorizar_execucao(db: Session, script: str) -> Iterator[List[str]]:
    """Cronometra a execução de um script que já tem acesso direto à BD
    (scripts em scripts/, que correm na mesma máquina/venv da API) e regista
    o resultado em execucoes_scripts, para o dashboard mostrar o histórico
    real de corridas.

    Uso:
        db = SessionLocal()
        try:
            with monitorizar_execucao(db, "importar_extratos") as log:
                log.append("a processar ficheiros...")
                ... lógica do script ...
        finally:
            db.close()
    """
    inicio = time.monotonic()
    log: List[str] = []
    try:
        yield log
    except Exception as exc:
        db.rollback()
        registar_execucao(db, script, "erro", erro=str(exc), log=log, duracao_segundos=time.monotonic() - inicio)
        raise
    else:
        registar_execucao(db, script, "ok", log=log, duracao_segundos=time.monotonic() - inicio)
