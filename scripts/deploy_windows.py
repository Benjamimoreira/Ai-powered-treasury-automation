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
import json
import os
import shutil
import subprocess
import sys
import time
from datetime import datetime, timezone
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
FICHEIRO_PROXIMA_EXTRACAO = Path(os.environ.get("PROGRAMDATA", r"C:\ProgramData")) / "ExtratosCGD" / "proxima_extracao.json"

PASTA_PT2_REPO = Path(os.environ.get("SCRIPTS_PT2_REPO") or r"C:\Apps\pt2-instalador")
PASTA_PT2 = Path(os.environ.get("SCRIPTS_PREENCHIMENTO_RAIZ") or r"C:\Apps\tesouraria-preenchimento")
VENV_TESTES_PT2 = Path(r"C:\Apps\tesouraria-ci\.venv")

SEM_JANELA = getattr(subprocess, "CREATE_NO_WINDOW", 0)
INTERVALO_REPORTE_S = 2


class FalhaDeploy(RuntimeError):
    pass


# (texto que aparece no log do programa, motivo em português para o dashboard)
# - o primeiro que bater, por esta ordem
MOTIVOS_FALHA = [
    (("SmsNaoIntroduzido", "SMS Token não introduzido", "SMS não introduzido", "Validação SMS"),
     "o código SMS do banco não foi introduzido a tempo"),
    (("net::ERR_INTERNET_DISCONNECTED", "net::ERR_NAME_NOT_RESOLVED", "net::ERR_CONNECTION",
      "net::ERR_NETWORK", "net::ERR_ADDRESS_UNREACHABLE", "net::ERR_TIMED_OUT", "getaddrinfo failed",
      "Name or service not known", "No address associated", "Temporary failure in name resolution"),
     "falha de internet (sem ligação ou o site do banco não responde)"),
    (("Target page, context or browser has been closed", "Browser closed", "browser has disconnected"),
     "o Chrome fechou-se a meio da extração"),
    (("Timeout", "timed out", "Tempo esgotado"),
     "o site do banco demorou demasiado a responder (internet lenta ou banco em baixo)"),
    (("Login incompleto", "URL inesperado", "Caixadirecta não abriu", "ecrã inicial falhou",
      "Botão Caixadirecta"),
     "o login no banco falhou (o site da CGD não abriu o ecrã de login)"),
    (("Permission denied", "PermissionError", "Acesso negado"),
     "sem permissão para um ficheiro (outro programa a usá-lo ou criado como administrador)"),
    (("sessao_banco", "outro programa tem a sessão"),
     "outro programa da CGD está com a sessão do banco aberta"),
]


def motivo_falha(linhas: List[str]) -> str:
    """O porquê de um programa ter terminado com erro, a partir do que ele
    escreveu: um motivo conhecido (internet, SMS, login...) ou a primeira
    linha [ERRO] - "" se não escreveu nada que ajude."""
    erros = [l.strip() for l in linhas if "[ERRO]" in l or "Traceback" in l or "Error" in l]
    for padroes, motivo in MOTIVOS_FALHA:
        if any(p.lower() in l.lower() for l in erros for p in padroes):
            return motivo
    primeiro = next((l for l in erros if "[ERRO]" in l), erros[-1] if erros else "")
    return primeiro.replace("[ERRO]", "").strip()[:200]


class Contexto:
    """O que um passo pode usar: correr comandos (com o output a ir para o
    log do deploy), escrever linhas e guardar estado entre passos."""

    def __init__(self, reportar: Callable[..., None]):
        self._reportar = reportar
        self._pendentes: List[str] = []
        self._ultimo_envio = time.monotonic()
        self.pilha = contextlib.ExitStack()  # recursos que duram vários passos (lock do banco)
        self.estado = {}
        self.api = None  # _api do agente (GET/POST à API) - para os passos que acompanham a cadeia
        self.lancar = None  # lança o preencher_mapa com argumentos de dia (agente_pedidos.lancar)

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
        recentes: List[str] = []
        for texto in processo.stdout:
            if texto.strip():
                self.linha(texto)
                recentes = (recentes + [texto])[-300:]
        codigo = processo.wait()
        if codigo not in codigos_ok:
            motivo = motivo_falha(recentes)
            raise FalhaDeploy(f"'{Path(str(comando[0])).name}' terminou com código {codigo}"
                              + (f": {motivo}" if motivo else ""))
        return codigo


# ---------------------------------------------------------------- scripts_cgd

def _cgd_verificar(ctx: Contexto, exes=None) -> None:
    exes = EXES_CGD if exes is None else exes
    if not PASTA_CGD.is_dir():
        raise FalhaDeploy(f"Pasta dos scripts CGD não encontrada: {PASTA_CGD}")
    em_falta = [nome for nome, _ in exes if not (PASTA_CGD / f"{nome}.spec").is_file()]
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


def _cgd_parar(ctx: Contexto, exes=None) -> None:
    exes = EXES_CGD if exes is None else exes
    parados = []
    for nome, _ in exes:
        exe = f"{nome}.exe"
        if not _processos_a_correr(exe):
            continue
        resultado = subprocess.run(["taskkill", "/F", "/IM", exe], capture_output=True, text=True,
                                   errors="replace", creationflags=SEM_JANELA)
        ctx.linha((resultado.stdout or resultado.stderr).strip())
        # o taskkill responde antes de o processo desaparecer de facto (12/10/2026
        # o deploy falhou por verificar logo a seguir) - esperar até 10 s
        for _ in range(20):
            if not _processos_a_correr(exe):
                break
            time.sleep(0.5)
        if _processos_a_correr(exe):
            raise FalhaDeploy(
                f"Não foi possível parar {exe} (corre como administrador). O agente do Windows "
                "tem de correr com privilégios máximos - ver docs/OPERACAO.md, 'Deploy dos scripts'.")
        parados.append(nome)
    ctx.estado["parados"] = parados
    ctx.linha(f"Parados: {', '.join(parados)}" if parados else "Nenhum programa CGD estava a correr.")


def _cgd_instalar(ctx: Contexto, exes=None) -> None:
    exes = EXES_CGD if exes is None else exes
    carimbo = datetime.now().strftime("%Y%m%d_%H%M%S")
    for nome, subpasta in exes:
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
    # O que a extração anual faz ao arrancar (escolhido no dashboard): o
    # main.py --anual lê e apaga este ficheiro. Sem ele extrai o ano inteiro,
    # o que pede SMS ao banco (movimentos com mais de 90 dias). Com "Hoje"/
    # "Intervalo" quem extrai é o passo seguinte (pastas diárias, para os
    # Mapas) - a anual não extrai nada.
    escolha = ctx.estado.get("extracao") or {"modo": "ano"}
    extracao = {"modo": "ano"} if escolha.get("modo") == "ano" else {"modo": "nenhuma"}
    FICHEIRO_PROXIMA_EXTRACAO.parent.mkdir(parents=True, exist_ok=True)
    FICHEIRO_PROXIMA_EXTRACAO.write_text(json.dumps(extracao), encoding="utf-8")
    descricao = {
        "nenhuma": "sem extração agora",
        "ano": "extração do ano inteiro",
        "periodo": f"extração de {extracao.get('inicio')} a {extracao.get('fim')}",
    }.get(extracao.get("modo"), str(extracao))
    ctx.pilha.close()  # larga o lock do banco: a extração anual arranca já
    ctx.correr(["schtasks", "/Run", "/TN", TAREFA_ANUAL_CGD])
    ctx.linha(f"Tarefa '{TAREFA_ANUAL_CGD}' lançada ({descricao}; fica com o agendador 00:00/12:00).")


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


def _passos_cgd(exes, reiniciar_anual: bool) -> list:
    """Passos do deploy de alguns exes da CGD. Só o MovimentosCGD tem a
    instância anual sempre aberta, por isso só ele reinicia a extração anual
    (e usa a extração escolhida no dashboard)."""
    from functools import partial

    passos = [
        ("Verificar o código", partial(_cgd_verificar, exes=exes)),
        *[(f"Compilar {nome}.exe", _cgd_compilar(nome)) for nome, _ in exes],
        ("Esperar que o banco fique livre", _cgd_esperar_banco),
        ("Parar os programas CGD abertos", partial(_cgd_parar, exes=exes)),
        ("Instalar os exes" if len(exes) > 1 else f"Instalar {exes[0][0]}.exe", partial(_cgd_instalar, exes=exes)),
    ]
    if reiniciar_anual:
        passos.append(("Reiniciar a extração anual", _cgd_reiniciar_anual))
    return passos


# ------------------------------------------- depois de instalar: atualizar
# Fluxo pedido em 12/10/2026 para o deploy com "Hoje"/"Intervalo": extrair
# os extratos desses dias para as pastas diárias -> o fim da extração faz a
# API pedir o preencher_mapa (Mapa de Saldos + Mapa de Pagamentos e
# Recebimentos) -> o fim desse importa os dias para o dashboard. Os passos
# esperam por cada fase para a barra de progresso mostrar até ao fim.
ESPERA_MAPA = 45 * 60
ESPERA_DASHBOARD = 3 * 60


def _execucoes_depois(ctx: Contexto, script: str, desde: str) -> list:
    logs = ctx.api("GET", f"/monitorizacao/logs?limit=5&script={script}")["logs"]
    return [l for l in logs if l["timestamp"] > desde]


def _agora_iso() -> str:
    return datetime.now(timezone.utc).replace(tzinfo=None).isoformat(timespec="seconds") + "Z"


def _cgd_extrair_dias(ctx: Contexto) -> None:
    extracao = ctx.estado["extracao"]
    pasta = PASTA_CGD_INSTALADA / "Movimentos Diários CGD"
    exe = pasta / "MovimentosEscolherDiaCGD.exe"
    if not exe.is_file():
        raise FalhaDeploy(f"Não encontrei {exe}")
    ctx.pilha.close()  # larga o lock do banco: é a extração que o vai pedir
    ctx.estado["extracao_inicio"] = _agora_iso()
    comando = [exe, "--dias", f"{extracao['inicio']}:{extracao['fim']}"]
    if extracao.get("empresa"):
        comando += ["--empresa", extracao["empresa"]]
    ctx.correr(comando, cwd=pasta)


def _dias_por_mes(extracao: dict) -> list:
    """[("AAAA-MM", "D" | "D1-D2"), ...] - os argumentos do preencher_mapa
    (painel_preenchimento.py: mês opcional + dia/intervalo), um por mês."""
    from datetime import date as _date, timedelta as _td

    inicio, fim = _date.fromisoformat(extracao["inicio"]), _date.fromisoformat(extracao["fim"])
    meses = {}
    dia = inicio
    while dia <= fim:
        meses.setdefault((dia.year, dia.month), []).append(dia.day)
        dia += _td(days=1)
    return [(f"{a}-{m:02d}", f"{d[0]}" if len(d) == 1 else f"{d[0]}-{d[-1]}") for (a, m), d in meses.items()]


def _atualizar_mapas(ctx: Contexto) -> None:
    """Preenche o Mapa de Saldos e o Mapa de Pagamentos e Recebimentos DOS
    DIAS EXTRAÍDOS (o pedido automático do fim de uma extração só faz
    ontem/hoje) e espera que cada corrida termine."""
    if ctx.api is None or ctx.lancar is None:
        ctx.linha("[AVISO] Sem ligação ao agente/API - não dá para preencher os Mapas daqui.")
        return
    for mes, dias in _dias_por_mes(ctx.estado["extracao"]):
        desde = _agora_iso()
        ctx.estado.setdefault("mapas_inicio", desde)
        ctx.linha(f"Atualizar Mapa Tesouraria {mes} dia(s) {dias}...")
        ctx.lancar([mes, dias])
        limite = time.monotonic() + ESPERA_MAPA
        while True:
            fim = _execucoes_depois(ctx, "preencher_mapa", desde)
            if fim:
                break
            if time.monotonic() > limite:
                raise FalhaDeploy(f"O Atualizar Mapa Tesouraria ({mes} {dias}) não terminou em 45 min.")
            time.sleep(15)
        ctx.linha(f"Atualizar Mapa Tesouraria terminou ({fim[-1]['nivel']}): {fim[-1]['mensagem'][:160]}")
        ctx.estado["mapas_fim"] = fim[-1]["timestamp"]
        if fim[-1]["nivel"] == "erro":
            raise FalhaDeploy(f"O Atualizar Mapa Tesouraria falhou: {fim[-1]['mensagem'][:200]}")


def _esperar_dashboard(ctx: Contexto) -> None:
    if ctx.api is None:
        return
    desde = ctx.estado.get("mapas_fim") or _agora_iso()
    limite = time.monotonic() + ESPERA_DASHBOARD
    while time.monotonic() < limite:
        estado = ctx.api("GET", "/sync/estado")
        if estado.get("atualizado_em", "").replace("+00:00", "Z") >= desde.replace("+00:00", "Z")[:19]:
            ctx.linha(f"Dashboard com dados novos ({estado.get('origem')}): {', '.join(estado.get('dias') or [])}")
            return
        time.sleep(10)
    ctx.linha("[AVISO] Sem dados novos para o dashboard em 3 min (os Mapas podem não ter mudado).")


TENTATIVAS_DISCREPANCIA = 3
TIPO_AUDITORIA = "Extrato ≠ Mapa de Pagamentos (auditoria)"


def _normalizar(texto: str) -> str:
    import unicodedata
    texto = unicodedata.normalize("NFKD", str(texto)).encode("ascii", "ignore").decode()
    return "".join(c for c in texto.upper() if c.isalnum())


def _dia_discrepancia(extracao: dict) -> str:
    """O dia da discrepância - a extração vai uma semana para trás dele."""
    return extracao.get("dia") or extracao["fim"]


def _discrepancia_continua(ctx: Contexto) -> bool:
    """Depois de reextrair e preencher: a discrepância ainda lá está?"""
    extracao = ctx.estado["extracao"]
    dia, empresa, tipo = _dia_discrepancia(extracao), extracao["empresa"], extracao.get("tipo") or TIPO_AUDITORIA
    if tipo == TIPO_AUDITORIA:
        # auditar outra vez o dia (a API sincroniza-o antes) e ver a empresa
        resultado = ctx.api("POST", f"/auditoria/{dia}/registar")
        for d in resultado.get("diferencas_por_empresa") or []:
            if _normalizar(d.get("empresa")) == _normalizar(empresa) and abs(float(d.get("diferenca") or 0)) > 0.01:
                ctx.linha(f"Ainda há diferença: {float(d['diferenca']):.2f} EUR")
                return True
        return False
    # aviso de saldo: voltou a ser reportado pela corrida dos Mapas de agora?
    desde = ctx.estado.get("mapas_inicio") or ctx.estado.get("extracao_inicio") or _agora_iso()
    for d in ctx.api("GET", "/monitorizacao/discrepancias-saldos?dias=31")["discrepancias"]:
        if (d["dia"] == dia and _normalizar(d["empresa"]) == _normalizar(empresa) and d["tipo"] == tipo
                and d["visto_em"] >= desde[:19]):
            ctx.linha(f"Ainda reportada: {d['mensagem'][:160]}")
            return True
    return False


def _verificar_discrepancia(ctx: Contexto) -> None:
    extracao = ctx.estado["extracao"]
    for tentativa in range(1, TENTATIVAS_DISCREPANCIA + 1):
        if not _discrepancia_continua(ctx):
            ctx.api("POST", "/monitorizacao/discrepancias-saldos/resolvida", {
                "dia": _dia_discrepancia(extracao), "empresa": extracao["empresa"],
                "tipo": extracao.get("tipo") or TIPO_AUDITORIA})
            ctx.linha(f"Discrepância resolvida (tentativa {tentativa}).")
            return
        if tentativa == TENTATIVAS_DISCREPANCIA:
            break
        ctx.linha(f"[AVISO] Discrepância continua - nova extração ({tentativa + 1}/{TENTATIVAS_DISCREPANCIA})...")
        ctx.estado.pop("mapas_inicio", None)
        _cgd_extrair_dias(ctx)
        _atualizar_mapas(ctx)
    raise FalhaDeploy(f"A discrepância continua depois de {TENTATIVAS_DISCREPANCIA} extrações - o extrato do banco "
                      "pode estar mesmo diferente do Mapa (ver Monitorização > Discrepâncias).")


def passos_atualizar(extracao: Optional[dict]) -> list:
    if not extracao or extracao.get("modo") != "periodo":
        return []
    if extracao.get("empresa"):
        return [
            (f"Reextrair {extracao['empresa']} de {extracao['inicio']} a {extracao['fim']}", _cgd_extrair_dias),
            ("Atualizar Mapa de Saldos e Mapa de Pagamentos e Recebimentos desses dias", _atualizar_mapas),
            ("Verificar a discrepância", _verificar_discrepancia),
        ]
    return [
        (f"Extrair extratos de {extracao['inicio']} a {extracao['fim']}", _cgd_extrair_dias),
        ("Atualizar Mapa de Saldos e Mapa de Pagamentos e Recebimentos", _atualizar_mapas),
        ("Atualizar o dashboard", _esperar_dashboard),
    ]


ALVOS = {
    # todos os exes da CGD de uma vez
    "scripts_cgd": _passos_cgd(EXES_CGD, reiniciar_anual=True),
    # um exe de cada vez (Monitorização > Deploy dos scripts: um botão por script)
    **{f"cgd_{nome}": _passos_cgd([(nome, subpasta)], reiniciar_anual=(nome == "MovimentosCGD"))
       for nome, subpasta in EXES_CGD},
    # botão "Atualizar" de uma discrepância: não instala nada - só os passos
    # de passos_atualizar (reextrair, Mapas desse dia, verificar)
    "reextrair_discrepancia": [],
    "tesouraria_preenchimento": [
        ("Atualizar o código (git pull)", _pt2_git_pull),
        ("Correr os testes", _pt2_testes),
        (f"Copiar para {PASTA_PT2}", _pt2_copiar),
        ("Instalar dependências", _pt2_dependencias),
    ],
}


def correr(alvo: str, reportar: Callable[..., None], extracao: Optional[dict] = None, api=None,
           lancar=None) -> bool:
    """Corre os passos de `alvo`. reportar(estado=, passo=, total_passos=,
    mensagem=, erro=, linhas=) envia o progresso à API. `extracao` (só
    scripts_cgd): o que a extração anual reiniciada faz - ver
    _cgd_reiniciar_anual. Devolve True se todos os passos correram."""
    passos = ALVOS.get(alvo)
    if passos is None:
        reportar(estado="erro", erro=f"Alvo de deploy desconhecido: {alvo}", linhas=[])
        return False
    if alvo.startswith(("cgd_", "scripts_cgd", "reextrair_")):
        passos = list(passos) + passos_atualizar(extracao)
    total = len(passos)
    ctx = Contexto(lambda **campos: reportar(estado="a_correr", **campos))
    ctx.estado["extracao"] = extracao
    ctx.api = api
    ctx.lancar = lancar  # lancar(argumentos): Atualizar Mapa Tesouraria desses dias, sem elevação
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
