import time
from contextlib import contextmanager
from datetime import datetime
from typing import Any, Dict, Iterator, List, Optional

from sqlalchemy.orm import Session

from app.db.models import ExecucaoScript

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


def listar_scripts(db: Session) -> List[Dict[str, Any]]:
    nomes = set(SCRIPT_PADRAO) | {nome for (nome,) in db.query(ExecucaoScript.script).distinct()}

    resultado = []
    for nome in sorted(nomes):
        info = SCRIPT_PADRAO.get(nome, {})
        ultima = (
            db.query(ExecucaoScript)
            .filter(ExecucaoScript.script == nome)
            .order_by(ExecucaoScript.timestamp.desc())
            .first()
        )
        resultado.append({
            "nome": nome,
            "descricao": info.get("descricao", f"Script {nome}"),
            "hora_execucao": info.get("hora_execucao", "08:00"),
            "status": ultima.status if ultima else "ok",
            "ultima_execucao": _isoformat(ultima.timestamp) if ultima else None,
            "ultima_erro": ultima.erro if ultima else None,
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
