# Migração da produção para outra máquina (PC novo / servidor)

A produção é a stack Docker (`docker-compose.yml`: db, api com o MCP,
dashboard, sincronizador, phoenix, logs) mais os scripts do Windows
(`tesouraria preenchimento`: preencher_mapa, enviar_mapa_smtp, agente dos
botões Correr). O deploy é feito pelo runner self-hosted do GitHub na
máquina de produção, a cada push para `master` (ver `.github/workflows/ci.yml`).

O projeto já está preparado para Windows **e** Linux:

| | Windows (Docker Desktop) | Linux (Docker Engine) |
|---|---|---|
| Passos do deploy no CI | `cmd` (os de sempre) | `deploy/deploy.sh` |
| Ollama a partir dos containers | `host.docker.internal` (Docker Desktop) | `extra_hosts: host-gateway` + `OLLAMA_HOST=0.0.0.0` |
| Pastas do OneDrive | cliente OneDrive | `rclone mount` (ver abaixo) |
| Nomes de ficheiros (`Outubro`/`outubro`) | indiferente | resolvido em `onedrive_sync.resolver_caminho` |

## 1. Máquina antiga: preparar

```powershell
$m = "$env:OneDrive\migracao"; mkdir "$m\volumes" -Force
$p = "ai-powered-treasury-automation"
# base de dados (dump no container + docker cp: ">" no PowerShell corrompe binários)
docker exec "$p-db-1" pg_dump -U tesouraria -Fc -f /tmp/tesouraria.dump tesouraria
docker cp "$p-db-1:/tmp/tesouraria.dump" "$m\tesouraria.dump"
# volumes com dados que não estão na BD (OCR das faturas, logs, Phoenix - parado durante a cópia)
foreach ($v in "faturas_extraidas","logs","logs_archive") {
    docker run --rm -v "${p}_${v}:/v:ro" -v "$m\volumes:/b" alpine tar czf "/b/$v.tar.gz" -C /v . }
docker stop "$p-phoenix-1"
docker run --rm -v "${p}_phoenix_data:/v:ro" -v "$m\volumes:/b" alpine tar czf /b/phoenix_data.tar.gz -C /v .
docker start "$p-phoenix-1"
# tarefas agendadas (Tesouraria - ..., agente dos botões Correr, recolha de faturas)
Get-ScheduledTask | Where-Object { $_.TaskName -like "Tesouraria*" -or $_.TaskName -eq "Faturas Recebidas - Recolha Horaria" } | ForEach-Object {
    Export-ScheduledTask -TaskName $_.TaskName | Out-File -Encoding utf8 "$m\$($_.TaskName).xml" }
# .env de produção (está na pasta de trabalho do runner)
copy C:\actions-runner\_work\Ai-powered-treasury-automation\Ai-powered-treasury-automation\.env "$env:OneDrive\migracao\.env"
```

A pasta `migracao` tem passwords (`.env`): apagar no fim.

## 2. Máquina nova: instalar

- **Docker**: Docker Desktop com WSL2 (Windows) ou Docker Engine + plugin compose (Linux).
- **Ollama** + `ollama pull qwen2.5:3b`. Em Linux, para os containers lhe
  chegarem: `sudo systemctl edit ollama` → `Environment="OLLAMA_HOST=0.0.0.0"`
  e reiniciar (só fica aberto na rede se a firewall deixar - fechar a 11434 ao exterior).
- **Pastas do OneDrive** (só leitura chega para a stack): `FINANCEIRO`,
  `CONTABILIDADE`, Fornecedores, Índice Comercial.
  - Windows: cliente OneDrive com a conta da VIDÓR.
  - Linux: `rclone config` (remote do SharePoint/OneDrive da VIDÓR) e
    `rclone mount vidor: /srv/onedrive --read-only --vfs-cache-mode full --daemon`
    (como serviço systemd, para sobreviver a reinícios).
- **Runner do GitHub**: Settings › Actions › Runners › New self-hosted
  runner, **com a label `servidor`**, instalado como serviço. Em Linux, o
  utilizador do runner tem de estar no grupo `docker`.

## 3. Máquina nova: configurar

1. `.env` em `<pasta do runner>/_work/Ai-powered-treasury-automation/Ai-powered-treasury-automation/.env`
   (a partir do `.env` da máquina antiga):
   - `ONEDRIVE_RAIZ_DOCKER`, `FORNECEDORES_RAIZ_DOCKER`, `COMERCIAL_INDICE_RAIZ_DOCKER`,
     `SCRIPTS_LOG_DIR`, `SCRIPTS_LOG_DIR_FATURAS` com os caminhos desta máquina;
   - `POSTGRES_PASSWORD=<nova, só letras e números>` (sem ela fica `tesouraria`).
     Só conta quando o volume da BD é criado - numa BD já existente:
     `docker exec ai-powered-treasury-automation-db-1 psql -U tesouraria -c "ALTER USER tesouraria PASSWORD '<nova>'"`.
2. GitHub › Settings › Secrets and variables › Actions › **Variables**:
   `DEPLOY_RUNNER = servidor`. A partir daqui o deploy só vai para a
   máquina nova. Remover o runner da máquina antiga (Settings › Actions › Runners).

## 4. Passar a produção

1. Máquina antiga - parar a stack e as tarefas (para não haver duas a sincronizar/escrever):
   ```powershell
   cd C:\actions-runner\_work\Ai-powered-treasury-automation\Ai-powered-treasury-automation
   docker compose down
   Get-ScheduledTask -TaskName "Tesouraria*" | Disable-ScheduledTask
   ```
2. GitHub › Actions › CI/CD › **Run workflow** (testes, imagens, deploy, avaliação do LLM: ~20 min).
3. Restaurar a BD na máquina nova:
   ```bash
   docker cp tesouraria.dump ai-powered-treasury-automation-db-1:/tmp/
   docker exec ai-powered-treasury-automation-db-1 pg_restore -U tesouraria -d tesouraria --clean --if-exists /tmp/tesouraria.dump
   docker restart ai-powered-treasury-automation-api-1
   ```
   E os volumes (com os serviços que os usam parados):
   ```powershell
   $m = "$env:OneDrive\migracao"; $p = "ai-powered-treasury-automation"
   docker stop "$p-phoenix-1" "$p-log-archiver-1"
   foreach ($v in "faturas_extraidas","logs_archive","phoenix_data") {
       docker run --rm -v "${p}_${v}:/v" -v "$m\volumes:/b:ro" alpine tar xzf "/b/$v.tar.gz" -C /v }
   docker start "$p-phoenix-1" "$p-log-archiver-1"
   ```
   Números para comparar com a máquina antiga (dump de 09/10/2026):
   5 532 movimentos, 3 070 linhas do Mapa, 5 205 saldos, 204 faturas.
4. Scripts do Windows (`tesouraria preenchimento` + agente dos botões Correr):
   - se correm na máquina nova (Windows): importar as tarefas. Os XML trazem
     o utilizador da máquina antiga (`WKS15\Benjamim` e o SID dele) e os
     caminhos `C:\Users\Benjamim\...` - isto troca-os pelos desta máquina:
     ```powershell
     $eu = "$env:USERDOMAIN\$env:USERNAME"
     Get-ChildItem "$env:OneDrive\migracao\*.xml" | ForEach-Object {
         $xml = (Get-Content $_.FullName -Raw) -replace '<UserId>[^<]*</UserId>', "<UserId>$eu</UserId>" `
                                               -replace 'C:\\Users\\Benjamim\\', "$env:USERPROFILE\"
         Register-ScheduledTask -TaskName $_.BaseName -Xml $xml }
     ```
   - se ficam noutro Windows: `setx API_TESOURARIA_URL "http://<ip-da-maquina-nova>:8000"`
     nesse Windows (os scripts e o agente reportam/leem pedidos a este endereço).
   - O script que extrai os extratos da CGD chama `POST /extratos/prontos` no
     fim (dispara o preencher_mapa e a importação) - mesmo `API_TESOURARIA_URL`.

## 5. Verificar

| O quê | Como |
|---|---|
| API | `http://<máquina>:8000/docs` |
| Dashboard | `http://<máquina>:8501` - saldos de hoje |
| Sincronização | `docker logs ai-powered-treasury-automation-sincronizador-1` |
| MCP + Ollama | uma pergunta ao Assistente (Visão Geral) |
| Scripts do Windows | Monitorização › **▶ Atualizar Mapa Tesouraria** passa a "lançado" em ~15 s |
| Traces / logs | `:6006` (Phoenix), `:8080` (Dozzle) |

## 6. Segurança

As portas 8000, 8501, 6006 e 8080 não têm autenticação: só na rede interna
/ VPN, ou atrás de um proxy com login. A 5432 (Postgres) não está publicada.
