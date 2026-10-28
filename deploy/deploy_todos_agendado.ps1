<#
"Deploy de todos" da Monitorização, agendado: pede à API o mesmo que o botão
"🚀 Deploy de todos" do dashboard - scripts_cgd (os 5 exes da CGD) e
tesouraria_preenchimento - e o agente do Windows (scripts\agente_pedidos.py)
faz um a seguir ao outro. O progresso aparece no dashboard como os do botão.

Extração depois de instalar os exes da CGD (-Extracao), como no dashboard:
  hoje     extrai hoje e atualiza o Mapa de Saldos, o Mapa de Pagamentos e
           Recebimentos e o dashboard (o que o botão faz por omissão)
  nenhuma  só instala

Se já houver um deploy pendente/a correr para o mesmo alvo, a API devolve esse
(não duplica). Log em logs\deploy_todos_agendado.log.

Correr agora:                .\deploy\deploy_todos_agendado.ps1
Registar a tarefa (07:00 e 17:30, uma vez, como administrador):
                             .\deploy\deploy_todos_agendado.ps1 -Registar
#>
param(
    [ValidateSet("hoje", "nenhuma")]
    [string]$Extracao = "hoje",
    [string]$ApiUrl = $(if ($env:API_TESOURARIA_URL) { $env:API_TESOURARIA_URL } else { "http://127.0.0.1:8000" }),
    [switch]$Registar
)

$ErrorActionPreference = "Stop"
$pasta = Split-Path -Parent $PSScriptRoot
$nomeTarefa = "Tesouraria - Deploy de todos (07h00 e 17h30)"

if ($Registar) {
    $acao = New-ScheduledTaskAction -Execute "powershell.exe" -WorkingDirectory $pasta `
        -Argument "-NoProfile -ExecutionPolicy Bypass -WindowStyle Hidden -File `"$PSCommandPath`" -Extracao $Extracao"
    $horas = @(
        (New-ScheduledTaskTrigger -Daily -At "07:00"),
        (New-ScheduledTaskTrigger -Daily -At "17:30")
    )
    # o mesmo utilizador do agente (a API é local; não precisa de privilégios)
    $utilizador = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\goncalo.coval" -LogonType S4U -RunLevel Limited
    $definicoes = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 5) -StartWhenAvailable `
        -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
    Register-ScheduledTask -TaskName $nomeTarefa -Action $acao -Trigger $horas -Principal $utilizador `
        -Settings $definicoes -Force | Out-Null
    "Tarefa '$nomeTarefa' registada: todos os dias às 07:00 e 17:30 (extração: $Extracao)."
    return
}

New-Item -ItemType Directory -Force (Join-Path $pasta "logs") | Out-Null
$ficheiroLog = Join-Path $pasta "logs\deploy_todos_agendado.log"
function Log([string]$msg) {
    $linha = "[{0}] {1}" -f (Get-Date -Format "yyyy-MM-dd HH:mm:ss"), $msg
    Add-Content -Path $ficheiroLog -Value $linha -Encoding utf8
    Write-Output $linha
}

# chave da API (app/seguranca.py), do .env da stack
$cabecalhos = @{}
$env_ = Join-Path $pasta ".env"
if (Test-Path $env_) {
    $token = Get-Content $env_ -Encoding UTF8 | Where-Object { $_ -match '^API_TOKEN=(.+)$' } |
        ForEach-Object { ($_ -replace '^API_TOKEN=', '').Trim() } | Select-Object -First 1
    if ($token) { $cabecalhos["X-API-Key"] = $token }
}

$hoje = Get-Date -Format "yyyy-MM-dd"
$extracaoCgd = if ($Extracao -eq "hoje") { @{ modo = "periodo"; inicio = $hoje; fim = $hoje } } else { @{ modo = "nenhuma" } }
# como _pedir_deploys do dashboard: a extração só vai com o alvo da CGD
$pedidos = @(
    @{ alvo = "scripts_cgd"; extracao = $extracaoCgd },
    @{ alvo = "tesouraria_preenchimento"; extracao = $null }
)

$falhou = $false
foreach ($pedido in $pedidos) {
    try {
        $corpo = $pedido | ConvertTo-Json -Compress -Depth 4
        $deploy = Invoke-RestMethod -Method Post -Uri "$($ApiUrl.TrimEnd('/'))/monitorizacao/deploys" -Headers $cabecalhos `
            -ContentType "application/json; charset=utf-8" -Body ([Text.Encoding]::UTF8.GetBytes($corpo)) -TimeoutSec 30
        Log "Deploy $($pedido.alvo) pedido (#$($deploy.id), $($deploy.estado)); extração: $Extracao"
    } catch {
        Log "[ERRO] Não foi possível pedir o deploy de $($pedido.alvo): $_"
        $falhou = $true
        break   # como o dashboard: pára no primeiro que falha
    }
}
if ($falhou) { exit 1 }
