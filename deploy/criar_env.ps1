<#
Cria o .env da stack da tesouraria numa máquina Windows nova, sem passwords
em ficheiros partilhados nem no GitHub:

  1. caminhos desta máquina, encontrados sozinhos (biblioteca
     "ADMINISTRATIVO, ECONÓMICO E FINANCEIRO - Documentos" sincronizada,
     pastas Fornecedores e "tesouraria preenchimento");
  2. definições sem passwords, de FINANCEIRO\Tesouraria API - configuracao\
     tesouraria.partilhado.env (nessa biblioteca, sincronizada pelo OneDrive);
  3. passwords (SMTP, Groq, Hugging Face) pedidas no teclado - Enter deixa vazio.

Uso (na pasta do repositório):
    powershell -ExecutionPolicy Bypass -File deploy\criar_env.ps1
    powershell -ExecutionPolicy Bypass -File deploy\criar_env.ps1 -Destino C:\actions-runner\_work\...\.env
#>
param(
    [string]$Destino = (Join-Path (Split-Path $PSScriptRoot -Parent) ".env"),
    [switch]$SemPasswords   # para testar: não pede passwords (ficam vazias)
)
$ErrorActionPreference = "Stop"

function Procurar-Pasta([string]$nome, [string]$ficheiroMarca) {
    $candidatas = Get-ChildItem $env:USERPROFILE -Directory -Recurse -Depth 3 -Filter $nome -ErrorAction SilentlyContinue
    $comMarca = $candidatas | Where-Object { $ficheiroMarca -and (Test-Path (Join-Path $_.FullName $ficheiroMarca)) }
    $escolhida = @($comMarca) + @($candidatas) | Select-Object -First 1
    if (-not $escolhida) { throw "Pasta '$nome' não encontrada em $env:USERPROFILE - está sincronizada no OneDrive?" }
    return $escolhida.FullName
}

function Barras([string]$caminho) { return $caminho -replace '\', '/' }

# 1. caminhos desta máquina
$biblioteca = Get-ChildItem (Join-Path $env:USERPROFILE "VID*R") -Directory -Filter "ADMINISTRATIVO*" -ErrorAction SilentlyContinue | Select-Object -First 1
if (-not $biblioteca) { throw "Biblioteca 'ADMINISTRATIVO, ECONÓMICO E FINANCEIRO - Documentos' não sincronizada (esperada em $env:USERPROFILE\VIDÓR\)." }
$biblioteca = $biblioteca.FullName
$pastaConfig = Join-Path $biblioteca "FINANCEIRO\Tesouraria API - configuracao"
$partilhado = Join-Path $pastaConfig "tesouraria.partilhado.env"
if (-not (Test-Path $partilhado)) { throw "Falta $partilhado - o OneDrive já sincronizou a pasta?" }
$fornecedores = Procurar-Pasta "Fornecedores" "recolher_faturas_recebidas.py"
$preenchimento = Procurar-Pasta "tesouraria preenchimento" "preencher_mapa.py"

# 2. definições partilhadas (sem passwords)
$linhas = Get-Content $partilhado -Encoding UTF8 | Where-Object { $_ -match '^[A-Z_]+=' }

# 3. passwords, só no teclado
function Pedir([string]$nome, [string]$explicacao) {
    if ($SemPasswords) { return "" }
    $seguro = Read-Host "$nome ($explicacao; Enter = vazio)" -AsSecureString
    return [Runtime.InteropServices.Marshal]::PtrToStringAuto([Runtime.InteropServices.Marshal]::SecureStringToBSTR($seguro))
}
$smtp = Pedir "SMTP_PASSWORD" "password do email que envia o Mapa"
$groq = Pedir "GROQ_API_KEY" "só se usar o Groq em vez do Ollama"
$hf = Pedir "HF_TOKEN" "só se usar a Hugging Face em vez do Ollama"
$dashboard = Pedir "DASHBOARD_PASSWORD" "password para abrir o dashboard"

# Chave da API (app/seguranca.py): mantém a do .env anterior - o dashboard do
# servidor e o agente usam a mesma; só gera uma nova numa máquina nova.
$token = $null
if (Test-Path $Destino) {
    $token = Get-Content $Destino -Encoding UTF8 | Where-Object { $_ -match '^API_TOKEN=(.+)$' } |
        ForEach-Object { ($_ -replace '^API_TOKEN=', '').Trim() } | Select-Object -First 1
}
if (-not $token) {
    $bytes = New-Object byte[] 32
    [Security.Cryptography.RandomNumberGenerator]::Create().GetBytes($bytes)
    $token = -join ($bytes | ForEach-Object { $_.ToString("x2") })
}

$env_ = @(
    "# Gerado por deploy\criar_env.ps1 em $(Get-Date -Format 'yyyy-MM-dd HH:mm') - NÃO partilhar (tem passwords)"
    "ONEDRIVE_RAIZ=$biblioteca"
    "ONEDRIVE_RAIZ_DOCKER=$(Barras $biblioteca)"
    "FORNECEDORES_RAIZ_DOCKER=$(Barras $fornecedores)"
    "SCRIPTS_LOG_DIR=$(Barras (Join-Path $preenchimento 'logs'))"
    "SCRIPTS_LOG_DIR_FATURAS=$(Barras (Join-Path $fornecedores 'logs'))"
    "COMERCIAL_INDICE_RAIZ_DOCKER=$(Barras $pastaConfig)"
) + $linhas + @(
    "SMTP_PASSWORD=$smtp"
    "GROQ_API_KEY=$groq"
    "HF_TOKEN=$hf"
    "API_TOKEN=$token"
    "DASHBOARD_PASSWORD=$dashboard"
)

if (Test-Path $Destino) {
    $copia = "$Destino.anterior"
    Copy-Item $Destino $copia -Force
    Write-Host "Já havia um .env - cópia em $copia"
}
# UTF-8 sem BOM: com BOM o docker compose lia mal a primeira linha
[IO.File]::WriteAllText($Destino, (($env_ -join "`n") + "`n"), (New-Object Text.UTF8Encoding($false)))

Write-Host ""
Write-Host ".env criado em $Destino" -ForegroundColor Green
$env_ | Where-Object { $_ -notmatch '^(SMTP_PASSWORD|GROQ_API_KEY|HF_TOKEN|API_TOKEN|DASHBOARD_PASSWORD)=' } | ForEach-Object { "  $_" }
Write-Host "  (passwords: $(@($smtp, $groq, $hf, $dashboard | Where-Object { $_ }).Count) de 4 preenchidas; API_TOKEN definido)"
