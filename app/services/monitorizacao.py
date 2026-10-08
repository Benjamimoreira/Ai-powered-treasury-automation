import logging
import os
import subprocess
import time
from contextlib import contextmanager
from datetime import date, datetime, timedelta, timezone
from typing import Any, Dict, Iterator, List, Optional
from zoneinfo import ZoneInfo

import json_log_formatter
from sqlalchemy.orm import Session

from app.db.models import EventoScript, ExecucaoScript, PedidoCorrida

FUSO_LOCAL = ZoneInfo("Europe/Lisbon")
TOLERANCIA_ATRASO_MINUTOS = 20

# Logging estruturado em JSON (pedido explícito 25/08/2026), uma linha por
# execução registada (via POST /monitorizacao/scripts/{script}/executar,
# enviado pelos scripts agendados que correm noutra máquina - ver
# monitorizacao_client.py em "tesouraria preenchimento" - ou via
# monitorizar_execucao() para scripts que correm na mesma máquina/venv da
# API). Vai para stdout/stderr do processo uvicorn - em Docker isso é
# `docker logs`, visível no Dozzle/log-archiver, tal como já acontece do
# lado dos scripts. Mesma "message" ("execucao_terminada") e mesmos campos
# (script/status/erro/duracao_s) dos dois lados, para ficar fácil casar um
# log com o outro."""
_logger_json = logging.getLogger("api_tesouraria.monitorizacao")
if not _logger_json.handlers:
    _handler_json = logging.StreamHandler()
    _handler_json.setFormatter(json_log_formatter.JSONFormatter())
    _logger_json.addHandler(_handler_json)
    _logger_json.setLevel(logging.INFO)
    _logger_json.propagate = False

SCRIPT_PADRAO: Dict[str, Dict[str, str]] = {
    "preencher_mapa": {
        "descricao": "Preenchimento do Mapa de Pagamentos e Recebimentos a partir dos extratos bancários",
        "hora_execucao": "08:50, 12:50, 14:10, 16:15, 18:30",
        "ficheiro": "preencher_mapa.py",
    },
    "atualizar_mapa_saldos": {
        "descricao": "Atualização do Mapa de Saldos Bancários (folhas diárias)",
        "hora_execucao": "08:55, 12:55, 14:15, 16:20, 18:35",
        "ficheiro": "atualizar_mapa_saldos.py",
    },
    "avaliacao_online": {
        "descricao": "Avaliação online das respostas do Assistente (juiz LLM, anotações no Phoenix)",
        "hora_execucao": "10:00, 14:00, 18:00",
        # sem "ficheiro": não corre na pasta "tesouraria preenchimento" (é
        # python -m app.evals.avaliacao_online --horas 4, neste projeto),
        # por isso o botão "Correr" da dashboard não se aplica
    },
    "enviar_mapa_smtp": {
        "descricao": "Envio diário do Mapa de Pagamentos e Recebimentos por email",
        "hora_execucao": "16:30",
        "ficheiro": "enviar_mapa_smtp.py",
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


def _raiz_scripts_preenchimento() -> Optional[str]:
    """Pasta do projeto "tesouraria preenchimento" onde vivem preencher_mapa.py,
    atualizar_mapa_saldos.py e enviar_mapa_smtp.py - só existe na máquina onde
    esses scripts correm agendados (não dentro do container Docker da API).
    Por omissão, deriva-a de SCRIPTS_LOG_DIR (.../tesouraria preenchimento/logs),
    que já aponta para lá; SCRIPTS_PREENCHIMENTO_RAIZ permite sobrepor."""
    raiz = os.environ.get("SCRIPTS_PREENCHIMENTO_RAIZ")
    if raiz:
        return raiz
    log_dir = os.environ.get("SCRIPTS_LOG_DIR")
    return os.path.dirname(log_dir) if log_dir else None


def correr_script(nome: str) -> Dict[str, Any]:
    """Dispara a execução do script `nome` (preencher_mapa, atualizar_mapa_saldos
    ou enviar_mapa_smtp) na máquina onde a API corre nativamente - fire-and-forget,
    o próprio script reporta o resultado a registar_execucao() via
    monitorizar()/POST /monitorizacao/scripts/{script}/executar quando terminar."""
    nome = nome.strip().lower()
    info = SCRIPT_PADRAO.get(nome)
    if not info or "ficheiro" not in info:
        raise ValueError(f"Script desconhecido: {nome}")

    raiz = _raiz_scripts_preenchimento()
    if not raiz or not os.path.isdir(raiz):
        raise RuntimeError(
            "Pasta dos scripts (tesouraria preenchimento) não está acessível nesta "
            "máquina/contentor - define SCRIPTS_PREENCHIMENTO_RAIZ."
        )

    caminho_script = os.path.join(raiz, info["ficheiro"])
    if not os.path.isfile(caminho_script):
        raise RuntimeError(f"Ficheiro do script não encontrado: {caminho_script}")

    python_exe = os.environ.get("SCRIPTS_PREENCHIMENTO_PYTHON") or os.path.join(raiz, ".venv", "Scripts", "python.exe")
    if not os.path.isfile(python_exe):
        raise RuntimeError(f"Python do venv dos scripts não encontrado: {python_exe}")

    subprocess.Popen(
        [python_exe, caminho_script],
        cwd=raiz,
        creationflags=getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0) | getattr(subprocess, "DETACHED_PROCESS", 0),
    )
    return {"nome": nome, "status": "iniciado"}


def _pedido_dict(pedido: PedidoCorrida) -> Dict[str, Any]:
    return {
        "id": pedido.id,
        "script": pedido.script,
        "estado": pedido.estado,
        "erro": pedido.erro,
        "pedido_em": _isoformat(pedido.pedido_em),
        "iniciado_em": _isoformat(pedido.iniciado_em) if pedido.iniciado_em else None,
    }


def pedir_corrida(db: Session, nome: str) -> Dict[str, Any]:
    """Botão "Correr" da Monitorização: se os scripts estão nesta máquina
    (API a correr nativamente), lança-o já (correr_script); senão (API em
    Docker) grava um pedido para o agente do Windows (scripts/
    agente_pedidos.py) o lançar. Um pedido ainda pendente para o mesmo
    script não é duplicado (dois cliques seguidos = uma corrida)."""
    nome = nome.strip().lower()
    info = SCRIPT_PADRAO.get(nome)
    if not info or "ficheiro" not in info:
        raise ValueError(f"Script desconhecido: {nome}")

    raiz = _raiz_scripts_preenchimento()
    if raiz and os.path.isdir(raiz):
        return correr_script(nome)

    pendente = (
        db.query(PedidoCorrida)
        .filter(PedidoCorrida.script == nome, PedidoCorrida.estado == "pendente")
        .first()
    )
    pedido = pendente or PedidoCorrida(script=nome, estado="pendente")
    if not pendente:
        db.add(pedido)
        db.commit()
        db.refresh(pedido)
    _logger_json.info("pedido_corrida", extra={"script": nome, "pedido": pedido.id})
    return {"nome": nome, "status": "pedido", "pedido": _pedido_dict(pedido)}


def listar_pedidos(db: Session, estado: Optional[str] = None, limit: int = 20) -> List[Dict[str, Any]]:
    query = db.query(PedidoCorrida)
    if estado:
        query = query.filter(PedidoCorrida.estado == estado)
    pedidos = query.order_by(PedidoCorrida.pedido_em.desc(), PedidoCorrida.id.desc()).limit(limit).all()
    return [_pedido_dict(p) for p in pedidos]


def marcar_pedido(db: Session, pedido_id: int, estado: str, erro: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """O agente do Windows marca o pedido como "iniciado" (lançou o script)
    ou "erro" (não o conseguiu lançar). Só muda pedidos ainda pendentes:
    devolve None se o pedido não existe ou já foi tratado."""
    pedido = db.get(PedidoCorrida, pedido_id)
    if pedido is None or pedido.estado != "pendente":
        return None
    pedido.estado = estado
    pedido.erro = erro
    pedido.iniciado_em = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(pedido)
    return _pedido_dict(pedido)


def listar_logs(db: Session, limit: int = 50, dia: Optional[date] = None) -> List[Dict[str, Any]]:
    query = db.query(ExecucaoScript)
    if dia is not None:
        # timestamp é guardado em UTC "naive" (datetime.utcnow); converte a
        # fronteira do dia local (Europe/Lisbon) para UTC "naive" para poder
        # comparar diretamente na query, sem depender do fuso do Postgres.
        inicio_local = datetime.combine(dia, datetime.min.time(), tzinfo=FUSO_LOCAL)
        fim_local = inicio_local + timedelta(days=1)
        inicio_utc = inicio_local.astimezone(timezone.utc).replace(tzinfo=None)
        fim_utc = fim_local.astimezone(timezone.utc).replace(tzinfo=None)
        query = query.filter(ExecucaoScript.timestamp >= inicio_utc, ExecucaoScript.timestamp < fim_utc)

    execucoes = (
        query
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

    _logger_json.info("execucao_terminada", extra={
        "script": nome,
        "status": status,
        "erro": erro,
        "duracao_s": round(duracao_segundos, 2) if duracao_segundos is not None else None,
    })

    return {
        "nome": nome,
        "status": status,
        "ultima_execucao": _isoformat(execucao.timestamp),
        "ultima_erro": erro,
        "duracao_segundos": duracao_segundos,
        "logs": log or [],
    }


def registar_evento(db: Session, script: str, nivel: str, mensagem: str) -> Dict[str, Any]:
    """Grava um evento de log em tempo real (POST /monitorizacao/scripts/
    {script}/eventos, enviado pelo _HandlerEventoDashboard em
    monitorizacao_client.py assim que um [ERRO]/[AVISO] acontece durante a
    corrida) - separado de execucoes_scripts, que só tem o resultado final
    de cada corrida já terminada."""
    nome = script.strip().lower()
    evento = EventoScript(script=nome, nivel=nivel.strip().lower(), mensagem=mensagem)
    db.add(evento)
    db.commit()
    db.refresh(evento)

    _logger_json.info("evento_script", extra={
        "script": nome,
        "nivel": evento.nivel,
        "mensagem": mensagem,
    })

    return {
        "id": evento.id,
        "script": nome,
        "nivel": evento.nivel,
        "mensagem": mensagem,
        "timestamp": _isoformat(evento.timestamp),
    }


def listar_eventos(db: Session, limit: int = 50, script: Optional[str] = None) -> List[Dict[str, Any]]:
    query = db.query(EventoScript)
    if script:
        query = query.filter(EventoScript.script == script.strip().lower())
    eventos = query.order_by(EventoScript.timestamp.desc()).limit(limit).all()
    return [
        {
            "id": evento.id,
            "script": evento.script,
            "nivel": evento.nivel,
            "mensagem": evento.mensagem,
            "timestamp": _isoformat(evento.timestamp),
        }
        for evento in eventos
    ]


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
