"""Sincronização automática com o OneDrive (serviço "sincronizador" do
docker-compose).

Faz o mesmo que o botão "Atualizar dados" do dashboard
(POST /atualizar-dados): importa movimentos, saldos e Mapa dos últimos dias
e corrige os saldos provisórios quando chega a versão final. Sem isto, os
dados só entravam quando alguém carregava no botão - o extrato provisório
das 14:00 ficava por importar até lá.

Corre de SYNC_INTERVALO_MINUTOS em SYNC_INTERVALO_MINUTOS (15), só entre
SYNC_HORA_INICIO e SYNC_HORA_FIM (7h-21h, hora de Lisboa) - de noite os
extratos não mudam. Chama a API pela rede (API_BASE_URL), por isso corre
igual no PC e no servidor, e a importação continua num só sítio.

Reporta à Monitorização ("sincronizar_onedrive") só as corridas que
trouxeram dados novos ou deram erro - as outras (a maioria) ficam só no
log do container, para não encher o histórico de execuções.

Uso: python -m app.sincronizador [--uma-vez]
"""
import logging
import os
import sys
import time
from datetime import datetime
from zoneinfo import ZoneInfo

import requests

API_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000").rstrip("/")
INTERVALO_MINUTOS = float(os.environ.get("SYNC_INTERVALO_MINUTOS", "15"))
DIAS_ATRAS = int(os.environ.get("SYNC_DIAS_ATRAS", "7"))
HORA_INICIO = int(os.environ.get("SYNC_HORA_INICIO", "7"))
HORA_FIM = int(os.environ.get("SYNC_HORA_FIM", "21"))
FUSO = ZoneInfo("Europe/Lisbon")
NOME_SCRIPT = "sincronizar_onedrive"

log = logging.getLogger("sincronizador")


def dentro_do_horario(agora: datetime) -> bool:
    return HORA_INICIO <= agora.astimezone(FUSO).hour < HORA_FIM


def _reportar(status: str, erro=None, linhas=None, duracao=None) -> None:
    try:
        requests.post(
            f"{API_URL}/monitorizacao/scripts/{NOME_SCRIPT}/executar",
            json={"status": status, "erro": erro, "log": linhas or [], "duracao_segundos": duracao},
            timeout=10,
        )
    except requests.RequestException as exc:
        log.warning("não foi possível reportar à Monitorização: %s", exc)


def sincronizar() -> dict:
    """Uma sincronização: chama a API e reporta à Monitorização se houve
    dados novos ou erros. Devolve a resposta da API (ou {"erro": ...})."""
    inicio = time.monotonic()
    try:
        r = requests.post(f"{API_URL}/atualizar-dados", params={"dias_atras": DIAS_ATRAS}, timeout=600)
        r.raise_for_status()
        resultado = r.json()
    except requests.RequestException as exc:
        duracao = time.monotonic() - inicio
        log.error("sincronização falhou: %s", exc)
        _reportar("erro", erro=f"Sincronização falhou: {exc}", duracao=duracao)
        return {"erro": str(exc)}

    duracao = time.monotonic() - inicio
    novos = {
        "movimentos": resultado.get("dias_com_movimentos_novos") or [],
        "saldos": resultado.get("dias_com_saldos_novos") or [],
        "mapa": resultado.get("dias_com_mapa_novo") or [],
    }
    erros = resultado.get("erros") or []
    linhas = [f"{tipo} novos: {', '.join(dias)}" for tipo, dias in novos.items() if dias] + erros
    log.info("sincronização em %.1f s: %s", duracao, "; ".join(linhas) or "nada de novo")
    if erros:
        _reportar("warning", erro="; ".join(erros), linhas=linhas, duracao=duracao)
    elif any(novos.values()):
        _reportar("ok", linhas=linhas, duracao=duracao)
    return resultado


def esperar_pela_api(tentativas: int = 60, pausa: float = 5) -> None:
    """No arranque da stack a API demora a subir (cria tabelas, carrega
    modelos) - sem isto a primeira sincronização era reportada como erro."""
    for _ in range(tentativas):
        try:
            requests.get(f"{API_URL}/", timeout=5).raise_for_status()
            return
        except requests.RequestException:
            time.sleep(pausa)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s",
                        stream=sys.stdout)
    esperar_pela_api()
    if "--uma-vez" in sys.argv:
        sincronizar()
        return
    log.info("a sincronizar de %g em %g min, %02d:00-%02d:00 (API %s)",
             INTERVALO_MINUTOS, INTERVALO_MINUTOS, HORA_INICIO, HORA_FIM, API_URL)
    while True:
        if dentro_do_horario(datetime.now(FUSO)):
            sincronizar()
        time.sleep(INTERVALO_MINUTOS * 60)


if __name__ == "__main__":
    main()
