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

Também corre os deploys dos scripts pedidos no dashboard (Monitorização >
Deploy dos scripts): um de cada vez, numa thread (compilar os exes demora
minutos e os botões "Correr" continuam a funcionar entretanto), com os
passos de scripts/deploy_windows.py a reportar o progresso à API.

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
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

try:  # a correr como script (pasta scripts/ no sys.path)
    import deploy_windows
except ImportError:  # importado como scripts.agente_pedidos (testes)
    from scripts import deploy_windows

API_URL = os.environ.get("API_TESOURARIA_URL", "http://127.0.0.1:8000").rstrip("/")
# Instalação feita pelo deploy do repositório dos scripts
# (Treasury-Automation-PT2: deploy/instalar_maquina.ps1) - senão, a pasta do
# OneDrive onde os scripts sempre viveram.
PASTA_INSTALADA = Path(r"C:\Apps\tesouraria-preenchimento")
PASTA_SCRIPTS = Path(os.environ.get("SCRIPTS_PREENCHIMENTO_RAIZ") or (
    PASTA_INSTALADA if PASTA_INSTALADA.is_dir()
    else Path.home() / "OneDrive - VIDÓR" / "Ambiente de Trabalho" / "tesouraria preenchimento"
))
INTERVALO_SEGUNDOS = 15
VALIDADE_PEDIDO = timedelta(minutes=15)
VALIDADE_DEPLOY = timedelta(minutes=30)

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


def _executavel(lancador: str) -> list:
    """O Python do .venv da pasta dos scripts, quando existe (é o que as
    tarefas agendadas instaladas pelo deploy usam, com as dependências lá
    dentro); senão o Python Launcher ("py -3"/"pyw -3"), como antes. No PC
    novo o "py -3" apanhava uma versão sem as dependências."""
    nome = "pythonw.exe" if lancador == "pyw" else "python.exe"
    venv = PASTA_SCRIPTS / ".venv" / "Scripts" / nome
    if venv.is_file():
        return [str(venv)]
    return [_lancador(lancador), "-3"]


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


def _elevado() -> bool:
    """O agente corre com privilégios de administrador (tarefa com
    -RunLevel Highest, precisa disso para o deploy dos exes da CGD)?"""
    try:
        import ctypes
        return bool(ctypes.windll.shell32.IsUserAnAdmin())
    except Exception:
        return False


# Regista e corre uma tarefa agendada só desta vez, com os privilégios
# normais do utilizador (RunLevel Limited) na sessão dele - os valores vêm
# por variáveis de ambiente, para não ter de escapar aspas/acentos.
_PS_LANCAR_SEM_ELEVACAO = (
    "$a = New-ScheduledTaskAction -Execute $env:AG_EXE -Argument $env:AG_ARGS -WorkingDirectory $env:AG_DIR; "
    "$p = New-ScheduledTaskPrincipal -UserId \"$env:USERDOMAIN\\$env:USERNAME\" -LogonType Interactive -RunLevel Limited; "
    "$s = New-ScheduledTaskSettingsSet -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries "
    "-ExecutionTimeLimit (New-TimeSpan -Hours 2); "
    "Register-ScheduledTask -TaskName $env:AG_TAREFA -Action $a -Principal $p -Settings $s -Force | Out-Null; "
    "Start-ScheduledTask -TaskName $env:AG_TAREFA"
)


def _lancar_sem_elevacao(script: str, comando: list, cwd: Path) -> None:
    """Com o agente elevado, um Popen direto lançava o script também como
    administrador - e o Excel aberto por ele (enviar_mapa_smtp,
    preencher_mapa) ficava elevado, sem conseguir falar com o Excel normal
    e a prender o Mapa (09/10/2026: um envio deixou um Excel elevado preso
    e os seguintes falharam todos com "Permission denied"). Por isso lança
    pela tarefa "Tesouraria - Agente correr <script>", como as tarefas
    agendadas "Tesouraria - ..." sempre lançaram."""
    ambiente = {
        **os.environ,
        "AG_EXE": str(comando[0]),
        "AG_ARGS": subprocess.list2cmdline([str(c) for c in comando[1:]]),
        "AG_DIR": str(cwd),
        "AG_TAREFA": f"Tesouraria - Agente correr {script}",
    }
    resultado = subprocess.run(
        ["powershell", "-NoProfile", "-NonInteractive", "-Command", _PS_LANCAR_SEM_ELEVACAO],
        env=ambiente, capture_output=True, text=True, errors="replace", timeout=60,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    if resultado.returncode != 0:
        raise RuntimeError(f"Não foi possível lançar sem privilégios de administrador: "
                           f"{(resultado.stderr or resultado.stdout).strip()[:300]}")


def lancar(script: str) -> None:
    if script not in COMANDOS:
        raise RuntimeError(f"Sem comando para o script '{script}'")
    if not PASTA_SCRIPTS.is_dir():
        raise RuntimeError(f"Pasta dos scripts não encontrada: {PASTA_SCRIPTS}")
    lancador, argumentos = COMANDOS[script]
    comando = [*_executavel(lancador), *argumentos]
    if _elevado():
        _lancar_sem_elevacao(script, comando, PASTA_SCRIPTS)
        return
    subprocess.Popen(
        comando,
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


_deploy_em_curso = None  # threading.Thread do deploy a correr (um de cada vez)


def _reportar_deploy(deploy_id: int):
    def reportar(**campos):
        try:
            _api("POST", f"/monitorizacao/deploys/{deploy_id}/progresso", campos)
        except urllib.error.HTTPError as exc:
            if exc.code != 409:  # 409 = já terminado - nada a fazer
                log.warning("deploy %s: progresso recusado (%s)", deploy_id, exc)
        except (urllib.error.URLError, OSError) as exc:
            # API em baixo: o deploy continua, só a barra fica parada
            log.warning("deploy %s: progresso não enviado (%s)", deploy_id, exc)
    return reportar


def correr_deploy(deploy: dict) -> bool:
    log.info("deploy %s (%s) a começar", deploy["id"], deploy["alvo"])
    ok = deploy_windows.correr(deploy["alvo"], _reportar_deploy(deploy["id"]))
    log.info("deploy %s (%s) %s", deploy["id"], deploy["alvo"], "concluído" if ok else "falhou")
    return ok


def tratar_deploys() -> int:
    """Pega no deploy pendente mais antigo e corre-o numa thread. Enquanto
    um corre, os outros ficam pendentes (dois deploys ao mesmo tempo
    compilariam/copiariam por cima um do outro)."""
    global _deploy_em_curso
    if _deploy_em_curso is not None and _deploy_em_curso.is_alive():
        return 0
    deploys = _api("GET", "/monitorizacao/deploys?estado=pendente&limit=20")["deploys"]
    agora = datetime.now(timezone.utc)
    for deploy in reversed(deploys):  # mais antigo primeiro
        pedido_em = datetime.fromisoformat(deploy["pedido_em"].replace("Z", "+00:00"))
        if pedido_em.tzinfo is None:
            pedido_em = pedido_em.replace(tzinfo=timezone.utc)
        if agora - pedido_em > VALIDADE_DEPLOY:
            log.warning("deploy %s (%s) expirado - pedido às %s", deploy["id"], deploy["alvo"], deploy["pedido_em"])
            _reportar_deploy(deploy["id"])(
                estado="erro", erro=f"Expirado: o agente do Windows só o viu {agora - pedido_em} depois")
            continue
        try:
            _api("POST", f"/monitorizacao/deploys/{deploy['id']}/progresso",
                 {"estado": "a_correr", "passo": 0, "mensagem": "Iniciado pelo agente do Windows"})
        except urllib.error.HTTPError as exc:
            if exc.code == 409:  # outro agente já pegou nele
                continue
            raise
        _deploy_em_curso = threading.Thread(target=correr_deploy, args=(deploy,), name=f"deploy-{deploy['id']}")
        _deploy_em_curso.start()
        break
    return len(deploys)


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
        for tratar in (tratar_pendentes, tratar_deploys):
            try:
                tratar()
            except (urllib.error.URLError, OSError) as exc:
                # API em baixo (ex.: durante um deploy) - tenta outra vez a seguir
                log.debug("API indisponível: %s", exc)
            except Exception:
                log.exception("erro inesperado")
        if uma_vez:
            if _deploy_em_curso is not None:
                _deploy_em_curso.join()
            break
        time.sleep(INTERVALO_SEGUNDOS)


if __name__ == "__main__":
    main()
