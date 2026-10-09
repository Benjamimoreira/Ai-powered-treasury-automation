"""Passos do deploy dos scripts do Windows (botão "Deploy" da Monitorização).

Corrido pelo agente do Windows (scripts/agente_pedidos.py) numa thread: cada
passo é reportado à API (POST /monitorizacao/deploys/{id}/progresso) e o
dashboard mostra-o na barra de progresso.

Alvos (as chaves têm de bater com ALVOS_DEPLOY em app/services/deploy_scripts.py):

  scripts_cgd               exes PyInstaller da CGD, compilados a partir da
                            pasta de desenvolvimento (os .spec não estão no
                            git) e instalados em Desktop\\Programas\\Scripts CGD.
                            Só troca os exes com o banco livre (mesmo lock
                            que as extrações, bloqueio_sessao.py) e reinicia a
                            extração anual, que fica sempre aberta.
  tesouraria_preenchimento  o mesmo que o deploy do GitHub Actions do
                            Treasury-Automation-PT2 (git pull, testes,
                            robocopy para C:\\Apps\\tesouraria-preenchimento,
                            dependências).

Só usa a biblioteca padrão. Para parar e reiniciar os exes da CGD (correm
elevados), o agente tem de correr com privilégios máximos - ver
docs/OPERACAO.md, "Deploy dos scripts".
"""
import contextlib
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Callable, List, Optional

_DESKTOP = Path.home() / "Desktop"
PASTA_CGD = Path(os.environ.get("SCRIPTS_CGD_RAIZ") or _DESKTOP / "Code" / "script-extratos")
PASTA_CGD_INSTALADA = Path(os.environ.get("SCRIPTS_CGD_INSTALACAO") or _DESKTOP / "Programas" / "Scripts CGD")
PYTHON_CGD = os.environ.get("SCRIPTS_CGD_PYTHON") or shutil.which("python") or "python"
# (nome do .spec/.exe, subpasta da instalação)
EXES_CGD = [
    ("MovimentosCGD", "Movimentos CGD"),
    ("MovimentosDiaAtualCGD", "Movimentos Diários CGD"),
    ("MovimentosDiariosCGD", "Movimentos Diários CGD"),
    ("MovimentosEscolherDiaCGD", "Movimentos Diários CGD"),
    ("PreencherResumoMensalCGD", "Movimentos Diários CGD"),
]
TAREFA_ANUAL_CGD = "Extratos CGD (Anual)"

PASTA_PT2_REPO = Path(os.environ.get("SCRIPTS_PT2_REPO") or r"C:\Apps\pt2-instalador")
PASTA_PT2 = Path(os.environ.get("SCRIPTS_PREENCHIMENTO_RAIZ") or r"C:\Apps\tesouraria-preenchimento")
VENV_TESTES_PT2 = Path(r"C:\Apps\tesouraria-ci\.venv")

SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
INTERVALO_REPORTE_S = 2


class FalhaDeploy(RuntimeError):
    pass


class Contexto:
    """O que um passo pode usar: correr comandos (com o output a ir para o
    log do deploy), escrever linhas e guardar estado entre passos."""

    def __init__(self, reportar: Callable[..., None]):
        self._reportar = reportar
        self._pendentes: List[str] = []
        self._ultimo_envio = time.monotonic()
        self.pilha = contextlib.ExitStack()  # recursos que duram vários passos (lock do banco)
        self.estado = {}

    def linha(self, texto: str) -> None:
        self._pendentes.append(texto.rstrip())
        if time.monotonic() - self._ultimo_envio >= INTERVALO_REPORTE_S or len(self._pendentes) >= 40:
            self.enviar_linhas()

    def enviar_linhas(self, **campos) -> None:
        linhas, self._pendentes = self._pendentes, []
        self._ultimo_envio = time.monotonic()
        if linhas or campos:
            self._reportar(linhas=linhas, **campos)

    def correr(self, comando: List[str], cwd: Optional[Path] = None, codigos_ok=(0,)) -> int:
        self.linha(f"$ {' '.join(str(c) for c in comando)}")
        processo = subprocess.Popen(
            [str(c) for c in comando], cwd=str(cwd) if cwd else None,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT, stdin=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace", creationflags=SEM_JANELA,
        )
        for texto in processo.stdout:
            if texto.strip():
                self.linha(texto)
        codigo = processo.wait()
        if codigo not in codigos_ok:
            raise FalhaDeploy(f"'{Path(str(comando[0])).name}' terminou com código {codigo}")
        return codigo


# ---------------------------------------------------------------- scripts_cgd

def _cgd_verificar(ctx: Contexto) -> None:
    if not PASTA_CGD.is_dir():
        raise FalhaDeploy(f"Pasta dos scripts CGD não encontrada: {PASTA_CGD}")
    em_falta = [nome for nome, _ in EXES_CGD if not (PASTA_CGD / f"{nome}.spec").is_file()]
    if em_falta:
        raise FalhaDeploy(f".spec em falta em {PASTA_CGD}: {', '.join(em_falta)}")
    ficheiros = sorted(str(p.name) for p in PASTA_CGD.glob("*.py"))
    ctx.correr([PYTHON_CGD, "-m", "py_compile", *ficheiros], cwd=PASTA_CGD)
    ctx.linha(f"{len(ficheiros)} ficheiros .py compilam.")


def _cgd_compilar(nome: str) -> Callable[[Contexto], None]:
    def passo(ctx: Contexto) -> None:
        exe = PASTA_CGD / "dist" / f"{nome}.exe"
        # sem isto o PyInstaller reaproveita o exe antigo quando acha que
        # nada mudou - e assim nunca se instala um exe de uma compilação velha
        exe.unlink(missing_ok=True)
        ctx.correr([PYTHON_CGD, "-m", "PyInstaller", "--noconfirm", f"{nome}.spec"], cwd=PASTA_CGD)
        if not exe.is_file():
            raise FalhaDeploy(f"O PyInstaller não gerou {exe}")
        ctx.linha(f"{exe.name}: {exe.stat().st_size / 1e6:.1f} MB")
    return passo


def _cgd_esperar_banco(ctx: Contexto) -> None:
    """Trocar os exes com o banco livre: o lock é o mesmo das extrações
    (bloqueio_sessao.py), por isso nenhuma é interrompida a meio e nenhuma
    começa até a instalação acabar (o lock só é largado no fim do deploy)."""
    sys.path.insert(0, str(PASTA_CGD))
    try:
        from bloqueio_sessao import sessao_exclusiva
    finally:
        sys.path.remove(str(PASTA_CGD))
    ctx.pilha.enter_context(sessao_exclusiva("Deploy dos scripts CGD (dashboard)", log=ctx.linha))
    ctx.linha("Banco livre - nenhuma extração a correr.")


def _processos_a_correr(nome_exe: str) -> List[str]:
    saida = subprocess.run(
        ["tasklist", "/FI", f"IMAGENAME eq {nome_exe}", "/FO", "CSV", "/NH"],
        capture_output=True, text=True, errors="replace", creationflags=SEM_JANELA,
    ).stdout
    return [linha for linha in saida.splitlines() if linha.lower().startswith(f'"{nome_exe.lower()}"')]


def _cgd_parar(ctx: Contexto) -> None:
    parados = []
    for nome, _ in EXES_CGD:
        exe = f"{nome}.exe"
        if not _processos_a_correr(exe):
            continue
        resultado = subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True, text=True,
                                   errors="replace", creationflags=SEM_JANELA)
        ctx.linha((resultado.stdout or resultado.stderr).strip())
        if _processos_a_correr(exe):
            raise FalhaDeploy(
                f"Não foi possível parar {exe} (corre como administrador). O agente do Windows "
                "tem de correr com privilégios máximos - ver docs/OPERACAO.md, 'Deploy dos scripts'.")
        parados.append(nome)
    ctx.estado["parados"] = parados
    ctx.linha(f"Parados: {', '.join(parados)}" if parados else "Nenhum programa CGD estava a correr.")


def _cgd_instalar(ctx: Contexto) -> None:
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    for nome, subpasta in EXES_CGD:
        origem = PASTA_CGD / "dist" / f"{nome}.exe"
        destino = PASTA_CGD_INSTALADA / subpasta / f"{nome}.exe"
        destino.parent.mkdir(parents=True, exist_ok=True)
        if destino.exists():
            # renomear (e não sobrescrever): funciona mesmo que o exe ainda
            # esteja aberto, e fica o backup para voltar atrás
            destino.rename(destino.with_name(f"{destino.name}.bak_{carimbo}"))
        shutil.copy2(origem, destino)
        ctx.linha(f"{destino}  (anterior: {destino.name}.bak_{carimbo})")


def _cgd_reiniciar_anual(ctx: Contexto) -> None:
    ctx.pilha.close()  # larga o lock do banco: a extração anual arranca já
    ctx.correr(["schtasks", "/Run", "/TN", TAREFA_ANUAL_CGD])
    ctx.linha(f"Tarefa '{TAREFA_ANUAL_CGD}' lançada (faz já uma extração do ano e fica com o agendador 00:00/12:00).")


# --------------------------------------------------- tesouraria_preenchimento

def _pt2_git_pull(ctx: Contexto) -> None:
    if not (PASTA_PT2_REPO / ".git").is_dir():
        raise FalhaDeploy(f"Repositório não encontrado: {PASTA_PT2_REPO}")
    ctx.correr(["git", "-C", PASTA_PT2_REPO, "pull", "--ff-only"])
    ctx.correr(["git", "-C", PASTA_PT2_REPO, "log", "-1", "--format=%h %s"])


def _pt2_testes(ctx: Contexto) -> None:
    python = VENV_TESTES_PT2 / "Scripts" / "python.exe"
    if not python.is_file():
        ctx.linha(f"[AVISO] Sem venv de testes em {VENV_TESTES_PT2} - testes saltados.")
        return
    ctx.correr([python, "-m", "pip", "install", "-q", "-r", "requirements-dev.txt"], cwd=PASTA_PT2_REPO)
    # os testes nunca tocam nos Mapas verdadeiros (como no deploy.yml)
    os.environ["RAIZ_DOCUMENTOS_OVERRIDE"] = r"C:\Apps\tesouraria-ci\sem-documentos"
    try:
        ctx.correr([python, "-m", "pytest", "tests", "-q", "-p", "no:cacheprovider"], cwd=PASTA_PT2_REPO)
    finally:
        os.environ.pop("RAIZ_DOCUMENTOS_OVERRIDE", None)


def _pt2_copiar(ctx: Contexto) -> None:
    PASTA_PT2.mkdir(parents=True, exist_ok=True)
    # mesmas exclusões do deploy.yml: protegem o que só existe em produção
    ctx.correr([
        "robocopy", PASTA_PT2_REPO, PASTA_PT2, "/MIR",
        "/XD", ".git", ".github", "logs", "backups_onedrive", "mapas", "__pycache__", ".venv",
        "/XF", ".env", "*.xlsx", "*.log", "*.json", "*.pdf", "*.png", "*.csv",
        "/R:2", "/W:5", "/NP", "/NDL",
    ], codigos_ok=tuple(range(8)))  # robocopy: < 8 = sucesso


def _pt2_dependencias(ctx: Contexto) -> None:
    python = PASTA_PT2 / ".venv" / "Scripts" / "python.exe"
    if not python.is_file():
        raise FalhaDeploy(f"Sem venv em {PASTA_PT2} - corre primeiro o deploy do GitHub Actions/instalar_maquina.ps1.")
    ctx.correr([python, "-m", "pip", "install", "-q", "-r", "requirements.txt"], cwd=PASTA_PT2)
    ctx.correr([python, "-c", "import openpyxl, win32com.client, office365; print('Dependências OK')"], cwd=PASTA_PT2)


ALVOS = {
    "scripts_cgd": [
        ("Verificar o código", _cgd_verificar),
        *[(f"Compilar {nome}.exe", _cgd_compilar(nome)) for nome, _ in EXES_CGD],
        ("Esperar que o banco fique livre", _cgd_esperar_banco),
        ("Parar os programas CGD abertos", _cgd_parar),
        ("Instalar os exes", _cgd_instalar),
        ("Reiniciar a extração anual", _cgd_reiniciar_anual),
    ],
    "tesouraria_preenchimento": [
        ("Atualizar o código (git pull)", _pt2_git_pull),
        ("Correr os testes", _pt2_testes),
        (f"Copiar para {PASTA_PT2}", _pt2_copiar),
        ("Instalar dependências", _pt2_dependencias),
    ],
}


def correr(alvo: str, reportar: Callable[..., None]) -> bool:
    """Corre os passos de `alvo`. reportar(estado=, passo=, total_passos=,
    mensagem=, erro=, linhas=) envia o progresso à API. Devolve True se
    todos os passos correram."""
    passos = ALVOS.get(alvo)
    if passos is None:
        reportar(estado="erro", erro=f"Alvo de deploy desconhecido: {alvo}", linhas=[])
        return False
    total = len(passos)
    ctx = Contexto(lambda **campos: reportar(estado="a_correr", **campos))
    inicio = time.monotonic()
    try:
        for indice, (descricao, funcao) in enumerate(passos):
            ctx.enviar_linhas(passo=indice, total_passos=total, mensagem=f"{indice + 1}/{total} · {descricao}…")
            ctx.linha(f"== {descricao}")
            funcao(ctx)
    except Exception as exc:
        erro = str(exc) if isinstance(exc, FalhaDeploy) else f"{exc.__class__.__name__}: {exc}"
        ctx.linha(f"[ERRO] {erro}")
        ctx.pilha.close()
        linhas, ctx._pendentes = ctx._pendentes, []
        reportar(estado="erro", erro=erro, mensagem=f"Falhou em: {descricao}", linhas=linhas)
        return False
    ctx.pilha.close()
    linhas, ctx._pendentes = ctx._pendentes, []
    minutos = (time.monotonic() - inicio) / 60
    reportar(estado="ok", passo=total, total_passos=total,
             mensagem=f"Concluído em {minutos:.0f} min" if minutos >= 1 else "Concluído", linhas=linhas)
    return True
