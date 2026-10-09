"""Dados novos na BD -> dashboard atualizado.

1. Quando um script que produz dados termina (reporta em POST
   /monitorizacao/scripts/{script}/executar), a API importa logo hoje e
   ontem do OneDrive (sincronizar_apos_script), em vez de esperar pelo
   próximo ciclo do sincronizador (15 min).
2. Cada sincronização que traz algo novo - esta, a do sincronizador, o
   botão "Atualizar dados", /extratos/prontos - fica registada
   (registar_resultado). O dashboard pergunta (GET /sync/estado) de 30 em
   30 s e recarrega a página quando muda.

O estado fica em memória (um processo uvicorn): depois de a API reiniciar,
os dashboards abertos recarregam uma vez, o que é inofensivo.
"""
import logging
import threading
from datetime import datetime, timedelta, timezone
from typing import Any, Dict, Optional

from app.services.monitorizacao import FUSO_LOCAL

# Scripts cujo fim muda o que está no OneDrive: os extratos do dia da CGD
# (pastas diárias), o Mapa e o Mapa de Saldos.
SCRIPTS_QUE_ATUALIZAM_DADOS = {
    "movimentos_diarios_cgd",
    "movimentos_dia_atual_cgd",
    "preencher_mapa",
    "atualizar_mapa_saldos",
}
CHAVES_RESULTADO = ("dias_com_movimentos_novos", "dias_com_saldos_novos", "dias_com_mapa_novo")

log = logging.getLogger("api_tesouraria.atualizacao_dados")
_lock = threading.Lock()
_estado: Dict[str, Any] = {
    "atualizado_em": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    "dias": [],
    "origem": "arranque da API",
}


def registar_resultado(resultado: Optional[dict], origem: str) -> bool:
    """Regista uma sincronização. Devolve True se trouxe dados novos."""
    if not resultado:
        return False
    dias = sorted({d for chave in CHAVES_RESULTADO for d in (resultado.get(chave) or [])})
    if not dias:
        return False
    with _lock:
        _estado.update(atualizado_em=datetime.now(timezone.utc).isoformat(timespec="seconds"),
                       dias=dias, origem=origem)
    log.info("dados novos (%s): %s", origem, ", ".join(dias))
    return True


def estado() -> Dict[str, Any]:
    with _lock:
        return dict(_estado)


def sincronizar_apos_script(script: str, status: str) -> None:
    """Corre em segundo plano (BackgroundTasks) depois de um script
    reportar o fim. Sessão própria: a do pedido já fechou."""
    script = script.strip().lower()
    if script not in SCRIPTS_QUE_ATUALIZAM_DADOS or status == "erro":
        return
    from app.db.session import SessionLocal
    from app.services.onedrive_sync import atualizar_dados_do_dia

    hoje = datetime.now(FUSO_LOCAL).date()
    juntos = {chave: [] for chave in CHAVES_RESULTADO}
    db = SessionLocal()
    try:
        for dia in (hoje - timedelta(days=1), hoje):
            try:
                resultado = atualizar_dados_do_dia(db, dia)
            except Exception as exc:  # OneDrive indisponível, etc. - o sincronizador tenta depois
                db.rollback()
                log.warning("sincronização depois de %s (%s) falhou: %s", script, dia, exc)
                continue
            for chave in CHAVES_RESULTADO:
                juntos[chave] += resultado.get(chave) or []
    finally:
        db.close()
    registar_resultado(juntos, f"fim de {script}")
