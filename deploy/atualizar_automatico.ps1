<#
Atualização automática da stack Docker (corre de X em X minutos numa tarefa
agendada - ver deploy\registar_atualizacao_automatica.ps1).

Faz o que o job "deploy" do CI faria se houvesse um runner nesta máquina:
  1. git pull --ff-only     (código do agente, compose, scripts)  [sem -SemGit]
  2. docker compose pull    (imagens novas publicadas pelo CI no GHCR)
  3. se o compose recriaria algum serviço a correr: docker compose up -d --no-build <esses serviços>
  4. docker image prune -f

Não reinicia a API enquanto houver um deploy dos scripts a correr (o agente
perdia o progresso a meio) - fica para a volta seguinte.

PC de produção:   .\deploy\atualizar_automatico.ps1
Servidor Windows: .\deploy\atualizar_automatico.ps1 -ComposeFile docker-compose.dashboard.yml -SemGit
Experimentar:     .\deploy\atualizar_automatico.ps1 -Simular
#>
param(
    [string]$Pasta = (Split-Path -Parent $PSScriptRoot),
    [string]$ComposeFile = "docker-compose.yml",
    [switch]$SemGit,
    [switch]$Simular,
    [string]$ApiUrl = $(if ($env:API_TESOURARIA_URL) { $env:API_TESOURARIA_URL } else { "http://127.0.0.1:8000" })
)

$ErrorActionPreference = "Continue"
Set-Location $Pasta
New-Item -ItemType Directory -Force (Join-Path $Pasta "logs") | Out-Null
$ficheiroLog = Join-Path $Pasta "logs\atualizar_automatico.log"

function Log([string]$msg) {
    $linha = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $ficheiroLog -Value $linha -Encoding utf8
    Write-Output $linha
}

# Corre um comando externo pelo cmd: no Windows PowerShell 5.1 o progresso
# que o docker/git escrevem no stderr passaria a ser tratado como erro.
function Externo([string]$comando) {
    $saida = cmd /c "$comando 2>&1"
    return [pscustomobject]@{ Codigo = $LASTEXITCODE; Saida = ($saida -join "`n") }
}

# Log com mais de 1 MB: guardar um anterior e recomeçar.
if ((Test-Path $ficheiroLog) -and (Get-Item $ficheiroLog).Length -gt 1MB) {
    Move-Item $ficheiroLog "$ficheiroLog.1" -Force
}

# Uma volta de cada vez (a anterior pode estar a puxar uma imagem grande).
$mutex = New-Object System.Threading.Mutex($false, "Global\TesourariaAtualizarAutomatico")
if (-not $mutex.WaitOne(0)) { Log "Outra atualização ainda a correr - salto esta volta."; exit 0 }

try {
    $compose = "docker compose -f `"$ComposeFile`""
    $mudou = $false

    # 1. Código
    if (-not $SemGit) {
        $r = Externo "git fetch -q"
        if ($r.Codigo -ne 0) { Log "[ERRO] git fetch: $($r.Saida)" }
        else {
            $atras = (Externo "git rev-list --count HEAD..@{u}").Saida.Trim()
            if ($atras -match '^\d+$' -and [int]$atras -gt 0) {
                if ($Simular) { Log "[SIMULAR] git pull --ff-only ($atras commit(s) novos)" }
                else {
                    $r = Externo "git pull -q --ff-only"
                    if ($r.Codigo -ne 0) { Log "[ERRO] git pull --ff-only: $($r.Saida)" }
                    else { Log "git pull: $atras commit(s) novos."; $mudou = $true }
                }
            }
        }
    }

    # 2. Imagens
    $r = Externo "$compose pull -q"
    if ($r.Codigo -ne 0) { Log "[ERRO] docker compose pull: $($r.Saida)"; exit 1 }

    # Só os serviços que já estão a correr: um "up -d" de todos arrancava
    # também os que são só a pedido (faturas-ocr, "docker compose run").
    $servicos = ((Externo "$compose ps --services --status running").Saida -split "`n" |
        ForEach-Object { $_.Trim() } | Where-Object { $_ -match '^[\w.-]+$' }) -join " "
    if (-not $servicos) { Log "[AVISO] Nenhum serviço a correr - nada a atualizar (arrancar à mão com up -d)."; exit 0 }

    # É o próprio compose a dizer o que recriaria. Comparar digests dava falsos
    # positivos: o CI volta a publicar db/log-archiver a cada push com digest
    # novo e o mesmo conteúdo, e o compose (bem) não os recria.
    $plano = (Externo "$compose up -d --no-build --dry-run $servicos").Saida -split "`n" |
        Where-Object { $_ -match 'Container (\S+)\s+Recreate' } | ForEach-Object { $Matches[1] } | Select-Object -Unique
    if ($plano) { Log "Imagem/configuração nova para: $($plano -join ', ')"; $mudou = $true }

    if (-not $mudou) { exit 0 }

    # 3. Não reiniciar a API a meio de um deploy dos scripts do Windows.
    if ($ComposeFile -eq "docker-compose.yml") {
        try {
            # Sem notícias há 30 min = agente morreu a meio (mesma regra que SEM_NOTICIAS na API).
            # chave da API (app/seguranca.py), do .env deste repositório
            $cabecalhos = @{}
            $linhaToken = Get-Content (Join-Path $Pasta ".env") -Encoding UTF8 -ErrorAction SilentlyContinue |
                Where-Object { $_ -match '^API_TOKEN=(.+)$' } | Select-Object -First 1
            if ($linhaToken) { $cabecalhos["X-API-Key"] = ($linhaToken -replace '^API_TOKEN=', '').Trim() }
            $ativos = @((Invoke-RestMethod -TimeoutSec 5 -Headers $cabecalhos "$ApiUrl/monitorizacao/deploys?estado=a_correr&limit=5").deploys |
                Where-Object { [datetime]$_.atualizado_em -gt (Get-Date).AddMinutes(-30) })
            if ($ativos.Count -gt 0) {
                Log "Deploy a correr ($($ativos[0].alvo), #$($ativos[0].id)) - reinicio adiado para a próxima volta."
                exit 0
            }
        } catch { Log "[AVISO] Não consegui ver os deploys na API ($($_.Exception.Message)) - continuo." }
    }

    if ($Simular) { Log "[SIMULAR] $compose up -d --no-build --remove-orphans"; exit 0 }

    $r = Externo "$compose up -d --no-build $servicos"
    if ($r.Codigo -ne 0) { Log "[ERRO] docker compose up: $($r.Saida)"; exit 1 }
    Log "Contentores atualizados."

    # 4. Limpeza
    Externo "docker image prune -f" | Out-Null
}
finally {
    $mutex.ReleaseMutex()
}
