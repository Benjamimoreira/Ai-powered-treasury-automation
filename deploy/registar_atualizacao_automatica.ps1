<#
Regista a tarefa agendada "Tesouraria - Atualizacao automatica", que corre
deploy\atualizar_automatico.ps1 de X em X minutos (sem janela).

PC de produção (em C:\tesouraria):
    .\deploy\registar_atualizacao_automatica.ps1
Servidor Windows, só o dashboard:
    .\deploy\registar_atualizacao_automatica.ps1 -ComposeFile docker-compose.dashboard.yml -SemGit
Remover:
    Unregister-ScheduledTask "Tesouraria - Atualizacao automatica" -Confirm:$false

Corre como o utilizador atual e só com sessão iniciada (o Docker Desktop
precisa da sessão). Não precisa de administrador.
#>
param(
    [int]$Minutos = 10,
    [string]$ComposeFile = "docker-compose.yml",
    [switch]$SemGit
)

$script = Join-Path $PSScriptRoot "atualizar_automatico.ps1"
$argumentos = "--headless powershell.exe -NoProfile -ExecutionPolicy Bypass -File `"$script`" -ComposeFile $ComposeFile"
if ($SemGit) { $argumentos += " -SemGit" }

# conhost --headless: corre sem abrir (nem piscar) uma janela de consola.
$acao = New-ScheduledTaskAction -Execute "conhost.exe" -Argument $argumentos -WorkingDirectory (Split-Path -Parent $PSScriptRoot)
$gatilho = New-ScheduledTaskTrigger -Once -At (Get-Date).AddMinutes(1) `
    -RepetitionInterval (New-TimeSpan -Minutes $Minutos)
$principal = New-ScheduledTaskPrincipal -UserId "$env:USERDOMAIN\$env:USERNAME" -LogonType Interactive -RunLevel Limited
$definicoes = New-ScheduledTaskSettingsSet -ExecutionTimeLimit (New-TimeSpan -Minutes 30) `
    -MultipleInstances IgnoreNew -StartWhenAvailable

Register-ScheduledTask -TaskName "Tesouraria - Atualizacao automatica" -Action $acao -Trigger $gatilho `
    -Principal $principal -Settings $definicoes -Force | Out-Null
Write-Output "Tarefa registada: de $Minutos em $Minutos min, log em logs\atualizar_automatico.log"
