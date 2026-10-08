"""Agente do Windows para o botão "Correr" da Monitorização.

A API e o dashboard correm em Docker; os scripts agendados (preencher_mapa,
atualizar_mapa_saldos, enviar_mapa_smtp) correm no Windows, na pasta
"tesouraria preenchimento". O container não consegue lançar programas no
Windows, por isso o botão só grava um pedido (POST /monitorizacao/scripts/
{script}/correr) e este agente, a correr no Windows, vai buscá-lo de 15 em
15 segundos, lança o script tal como a tarefa agendada o lança e marca o
pedido como "iniciado" (ou "erro"). O resultado da corrida chega à
Monitorização pelo caminho de sempre (monitorizacao_client.py do script).

Pedidos com mais de 15 minutos quando o agente os vê (ex.: o PC estava
desligado) não são corridos - um envio do Mapa às tantas da noite seria
pior que nada - e ficam marcados "erro" com a razão.

Só usa a biblioteca padrão (corre com qualquer Python do Windows).

Uso:
    pyw -3 scripts/agente_pedidos.py            (fica a correr, sem janela)
    py -3 scripts/agente_pedidos.py --uma-vez   (trata os pendentes e sai)

Instalação (tarefa agendada ao iniciar sessão): ver docs/OPERACAO.md,
"Botão Correr da Monitorização".
"""
import json
import logging
import os
import shutil
import subprocess
import sys
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

API_URL = os.environ.get("API_TESOURARIA_URL", "http://127.0.0.1:8000").rstrip("/")
PASTA_SCRIPTS = Path(os.environ.get(
    "SCRIPTS_PREENCHIMENTO_RAIZ",
    Path.home() / "OneDrive - VIDÓR" / "Ambiente de Trabalho" / "tesouraria preenchimento",
))
INTERVALO_SEGUNDOS = 15
VALIDADE_PEDIDO = timedelta(minutes=15)

# Como cada script é lançado - o mesmo que a tarefa agendada correspondente
# ("Tesouraria - ..." no Agendador de Tarefas). O preencher_mapa corre pelo
# painel Tkinter (pyw), como às 8:50/14:10: sem argumentos mostra o diálogo
# de dia e, ao fim de 30 s sem escolha, processa ontem e hoje.
COMANDOS = {
    "preencher_mapa": ("pyw", ["painel_preenchimento.py"]),
    "atualizar_mapa_saldos": ("py", ["atualizar_mapa_saldos.py"]),
    "enviar_mapa_smtp": ("py", ["enviar_mapa_smtp.py"]),
}

log = logging.getLogger("agente_pedidos")


def _lancador(nome: str) -> str:
    """py.exe / pyw.exe (Python Launcher), o que as tarefas agendadas usam."""
    caminho = shutil.which(nome) or str(
        Path(os.environ.get("LOCALAPPDATA", "")) / "Programs" / "Python" / "Launcher" / f"{nome}.exe"
    )
    if not os.path.isfile(caminho):
        raise RuntimeError(f"{nome}.exe (Python Launcher) não encontrado")
    return caminho


def _api(metodo: str, caminho: str, corpo=None):
    dados = json.dumps(corpo).encode() if corpo is not None else None
    pedido = urllib.request.Request(
        f"{API_URL}{caminho}", data=dados, method=metodo, headers={"Content-Type": "application/json"},
    )
    with urllib.request.urlopen(pedido, timeout=10) as resposta:
        return json.loads(resposta.read() or b"null")


def _marcar(pedido_id: int, estado: str, erro=None) -> None:
    try:
        _api("POST", f"/monitorizacao/pedidos/{pedido_id}/estado", {"estado": estado, "erro": erro})
    except urllib.error.HTTPError as exc:
        if exc.code != 409:  # 409 = já tratado (outro agente) - nada a fazer
            raise


def lancar(script: str) -> None:
    if script not in COMANDOS:
        raise RuntimeError(f"Sem comando para o script '{script}'")
    if not PASTA_SCRIPTS.is_dir():
        raise RuntimeError(f"Pasta dos scripts não encontrada: {PASTA_SCRIPTS}")
    lancador, argumentos = COMANDOS[script]
    subprocess.Popen(
        [_lancador(lancador), "-3", *argumentos],
        cwd=PASTA_SCRIPTS,
        creationflags=subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW,
    )


def tratar_pendentes() -> int:
    pedidos = _api("GET", "/monitorizacao/pedidos?estado=pendente")["pedidos"]
    agora = datetime.now(timezone.utc)
    for pedido in reversed(pedidos):  # mais antigo primeiro
        pedido_em = datetime.fromisoformat(pedido["pedido_em"].replace("Z", "+00:00"))
        if pedido_em.tzinfo is None:
            pedido_em = pedido_em.replace(tzinfo=timezone.utc)
        if agora - pedido_em > VALIDADE_PEDIDO:
            log.warning("pedido %s (%s) expirado - pedido às %s", pedido["id"], pedido["script"], pedido["pedido_em"])
            _marcar(pedido["id"], "erro", f"Expirado: o agente do Windows só o viu {agora - pedido_em} depois")
            continue
        try:
            lancar(pedido["script"])
        except Exception as exc:
            log.error("pedido %s (%s): %s", pedido["id"], pedido["script"], exc)
            _marcar(pedido["id"], "erro", str(exc))
        else:
            log.info("pedido %s: %s lançado", pedido["id"], pedido["script"])
            _marcar(pedido["id"], "iniciado")
    return len(pedidos)


def main() -> None:
    pasta_logs = Path(__file__).resolve().parent.parent / "logs"
    pasta_logs.mkdir(exist_ok=True)
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s",
        handlers=[logging.FileHandler(pasta_logs / "agente_pedidos.log", encoding="utf-8")]
        + ([logging.StreamHandler()] if sys.stderr else []),
    )
    uma_vez = "--uma-vez" in sys.argv
    log.info("agente a correr (API %s, scripts em %s)", API_URL, PASTA_SCRIPTS)
    while True:
        try:
            tratar_pendentes()
        except (urllib.error.URLError, OSError) as exc:
            # API em baixo (ex.: durante um deploy) - tenta outra vez a seguir
            log.debug("API indisponível: %s", exc)
        except Exception:
            log.exception("erro inesperado")
        if uma_vez:
            break
        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    main()
