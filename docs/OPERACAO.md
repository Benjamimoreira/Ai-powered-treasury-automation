# Operação

Como correr, configurar, fazer deploy, vigiar e recuperar o sistema. A
arquitetura está em [ARQUITETURA.md](ARQUITETURA.md) e os endpoints em
[API.md](API.md).

## 1. Ambientes

| | Desenvolvimento | Produção |
|---|---|---|
| Onde | Qualquer máquina com Python 3.11 | Máquina Windows com Docker Desktop (WSL2) e o runner self-hosted do GitHub |
| Base de dados | SQLite `api_tesouraria.db` (omissão) | Postgres + pgvector (container `db`, volume `postgres_data`) |
| Como chega lá | `uvicorn` + `streamlit` à mão | Push para `master` → GitHub Actions → `docker compose up -d` |
| LLM | Ollama local (opcional: sem ele as regras continuam a funcionar) | Ollama no Windows anfitrião, fora do Docker |

**O que está a correr em produção vem das imagens publicadas pelo CI, não
desta pasta.** Alterações locais não chegam à produção sem commit e push
para `master`.

## 2. Configuração (`.env`)

Copiar `.env.example` para `.env`. Em produção, o `.env` vive na pasta de
trabalho do runner (`_work/<repo>/<repo>`) e não é apagado entre deploys
(`clean: false`). Nunca vai para o Git.

| Variável | Obrigatória | Para quê |
|---|---|---|
| `ONEDRIVE_RAIZ` | Dev | Pasta "…Documentos" do OneDrive (caminho Windows). Sem ela, a sincronização responde 503. |
| `ONEDRIVE_RAIZ_DOCKER` | Prod | A mesma pasta com barras normais, para o bind mount `/onedrive`. |
| `FORNECEDORES_RAIZ` / `FORNECEDORES_RAIZ_DOCKER` | Para os PDFs das faturas | Pasta das faturas arquivadas (`/fornecedores`). |
| `COMERCIAL_INDICE_PATH` (dev) · `COMERCIAL_INDICE_RAIZ_DOCKER` + `COMERCIAL_INDICE_NOME` (prod) | Para o comercial | Índice do Departamento Comercial sincronizado do SharePoint. `COMERCIAL_INDICE_CSV` é a alternativa antiga. |
| `SCRIPTS_LOG_DIR`, `SCRIPTS_LOG_DIR_FATURAS` | Prod | Pastas de logs dos scripts fora do Docker (para o Dozzle e o arquivo). Têm de estar preenchidas: vazias, os bind mounts ficam inválidos. |
| `SCRIPTS_PREENCHIMENTO_RAIZ`, `SCRIPTS_PREENCHIMENTO_PYTHON` | Não | Botão "Correr" da Monitorização (só com a API nativa). |
| `LLM_FORNECEDOR`, `OLLAMA_URL`, `OLLAMA_MODEL_ID` | Não | `ollama`, `http://localhost:11434/v1`, `qwen2.5:3b`. O compose sobrepõe o URL com `host.docker.internal`. |
| `PHOENIX_COLLECTOR_ENDPOINT`, `PHOENIX_URL_PUBLICA` | Não | Tracing; vazio = sem tracing. O compose já define o endpoint interno. |
| `LANGSMITH_*` | Não | Tracing externo opcional (região UE). Desligado por omissão. |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USER`, `SMTP_PASSWORD`, `SMTP_FROM`, `SMTP_ALERTA_PARA` | Não | Email de alerta da auditoria. Sem `SMTP_HOST`, não envia (nunca falha a auditoria). |
| `DATABASE_URL` | Não | Omissão: SQLite local. O compose define o Postgres. |
| `RAG_METODO`, `EMBEDDINGS_MODELO` | Não | Método de recuperação (`denso`) e modelo de embeddings (`all-MiniLM-L6-v2`). Mudar só com a avaliação da recuperação a justificar. |
| `IMAGE_PREFIX`, `IMAGE_TAG` | Não | Que imagens o compose usa (omissão: GHCR, `latest`). |

Ollama, uma vez por máquina:

```powershell
ollama pull qwen2.5:3b
# variável de ambiente do utilizador, depois reiniciar o Ollama:
setx OLLAMA_CONTEXT_LENGTH 8192
```

Com o contexto por omissão (4096), o prompt do Assistente mais o resultado
de uma ferramenta grande ultrapassa o limite e o Ollama corta as
instruções.

## 3. Correr localmente

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt
python scripts\criar_tabelas.py
uvicorn app.main:app --reload                 # http://127.0.0.1:8000/docs
.\venv\Scripts\streamlit.exe run dashboard\app.py   # http://127.0.0.1:8501
pytest -v                                     # sem rede nem LLM
```

Para ter dados: botão **Atualizar dados** na dashboard, ou
`POST /atualizar-dados` e `POST /atualizar-historico` (histórico completo
para a previsão).

## 4. Deploy

### Pipeline (`.github/workflows/ci.yml`)

```
push master ─▶ test (ubuntu, pytest)
            ─▶ build-push (api, db, log-archiver, faturas-ocr → ghcr.io/…:latest e :<sha>)
            ─▶ deploy (runner self-hosted, cmd):
                 1. diagnóstico: docker, compose, existe .env, compose config válido
                 2. docker compose pull
                 3. mudou algo que afeta o LLM/RAG?  ── não ─┐
                      sim: porta da recuperação (recall@6 ≥ 0,75, ~1 min)
                           porta do LLM (exatidão ≥ 0,65 em evals/ambiguos.json, ~15–20 min, Ollama local)
                 4. docker compose up -d --no-build --remove-orphans  ◀─┘
                 5. docker image prune -f
```

Em pull requests só correm os testes e o build (sem push nem deploy).

As portas de qualidade correm **dentro da imagem nova, antes de reiniciar
os containers**. Se falharem, o passo falha e os containers antigos
continuam a correr. Disparam quando mudam `llm_resolver`, `llm_tracing`,
`agente_ambiguos`, `resolucao_regras`, `rag_historico`, `indice_vetorial`,
`app/evals/`, os conjuntos `evals/*.json`, `requirements.txt`, o
`Dockerfile` ou o próprio `ci.yml`, ou num *Run workflow* manual.

### Deploy manual (sem runner)

```powershell
docker login ghcr.io -u <utilizador-github>     # token com read:packages
docker compose pull
docker compose up -d --no-build
```

### Voltar a uma versão anterior

Cada imagem tem a tag do SHA do commit:

```powershell
$env:IMAGE_TAG = "<sha>"
docker compose up -d --no-build
```

O esquema da base de dados só cresce (tabelas novas), por isso uma versão
anterior normalmente funciona com uma base de dados mais recente: ignora
as tabelas que não conhece.

### Alterações ao esquema

- **Tabela nova:** nada a fazer. O arranque da API cria-a.
- **Coluna nova numa tabela existente:** não é aplicada sozinha. Correr o
  `ALTER TABLE` no Postgres antes (ou logo depois) do deploy:

  ```powershell
  docker compose exec db psql -U tesouraria -c "ALTER TABLE <tabela> ADD COLUMN <coluna> <tipo>;"
  ```

## 5. URLs em produção

| Serviço | URL |
|---|---|
| Dashboard | http://localhost:8501 |
| API / Swagger | http://localhost:8000/docs |
| Phoenix (traces, avaliações) | http://localhost:6006 |
| Dozzle (logs) | http://localhost:8080 |

## 6. Rotina

| Quando | O quê | Como |
|---|---|---|
| Várias vezes por dia | Preencher o Mapa e os saldos | Scripts agendados (`preencher_mapa` 08:50, 12:50, 14:10, 16:15, 18:30; `atualizar_mapa_saldos` 5 min depois). Reportam à Monitorização. |
| 16:30 | Enviar o Mapa por email | `enviar_mapa_smtp`, agendado. |
| 10:00, 14:00, 18:00 | Avaliações online do Assistente | Agendador do Windows: `docker exec ai-powered-treasury-automation-api-1 python -m app.evals.avaliacao_online --horas 4` |
| De hora a hora | Arquivo de logs | `log-archiver` (automático). |
| Ao abrir a dashboard | Sincronizar | Botão **Atualizar dados** (últimos 7 dias). |
| Diário | Reconciliar e auditar | Dashboard: reconciliar o dia, **Auditar este dia**; resolver os casos em **Ambíguos**. |
| Semanal (sugestão) | Rever a qualidade da IA | Monitorização › Qualidade da IA: fila "para rever", anotar; depois `python -m app.evals.promover_golden` e rever o diff de `evals/assistente.json` antes do commit. |
| Sob pedido | OCR de faturas | `docker compose run --rm faturas-ocr` |
| Depois de mudar o modelo de embeddings | Reindexar | `docker compose exec api python scripts/indexar_embeddings.py --modelo <modelo>` |

Um script é marcado como **atrasado** quando passaram mais de 20 minutos
da hora prevista sem execução registada.

### Botão Correr da Monitorização

A Monitorização tem botões **▶ Correr** para `preencher_mapa` e
`enviar_mapa_smtp` (sempre) e para qualquer script em erro/atrasado. O envio
pede confirmação: manda o email a sério.

A API corre em Docker e os scripts no Windows, por isso o botão só grava um
pedido (`POST /monitorizacao/scripts/{script}/correr`). Quem o lança é o
**agente do Windows**, `scripts/agente_pedidos.py`: de 15 em 15 s vai buscar
os pedidos pendentes e lança o script como a tarefa agendada o lança
(`preencher_mapa` pelo painel, que ao fim de 30 s sem escolha processa ontem e
hoje). Pedidos com mais de 15 minutos não correm (ficam "não lançado"). O log
do agente fica em `logs/agente_pedidos.log`.

Instalar o agente (uma vez, como tarefa agendada ao iniciar sessão):

```powershell
$pasta = "C:\Users\Benjamim\OneDrive - VIDÓR\Ficheiros de Helpdesk VIDÓR - api-tesouraria"
$acao = New-ScheduledTaskAction -Execute "$env:LOCALAPPDATA\Programs\Python\Launcher\pyw.exe" `
    -Argument '-3 "scripts\agente_pedidos.py"' -WorkingDirectory $pasta
$definicoes = New-ScheduledTaskSettingsSet -ExecutionTimeLimit 0 -RestartCount 999 `
    -RestartInterval (New-TimeSpan -Minutes 1) -AllowStartIfOnBatteries -DontStopIfGoingOnBatteries
Register-ScheduledTask -TaskName "Tesouraria - Agente pedidos Correr" -Action $acao `
    -Trigger (New-ScheduledTaskTrigger -AtLogOn -User $env:USERNAME) -Settings $definicoes
Start-ScheduledTask -TaskName "Tesouraria - Agente pedidos Correr"
```

Se o pedido fica em "⏳ à espera do agente do Windows", o agente não está a
correr: `Get-ScheduledTask "Tesouraria - Agente pedidos Correr"`.

## 7. Avaliações à mão

```powershell
# recuperação (sem LLM, ~1 min)
python -m app.evals.avaliar_recuperacao --metodos recencia denso --limiar-recall 0.75
# sugestões para ambíguos (LLM local, ~15-20 min)
python -m app.evals.avaliar --estrategia hibrida --limiar 0.65 --phoenix
# Assistente contra o golden dataset
python -m app.evals.avaliar_assistente --limiar <0-1>
```

Dentro do container: prefixar com `docker compose exec api`. Método e
resultados em [AVALIACAO_LLM.md](AVALIACAO_LLM.md).

## 8. Backups

Os dados de origem estão no OneDrive. A base de dados pode reconstruir-se
a partir deles (`/atualizar-historico`), **exceto** o que só existe nela:
as resoluções de casos ambíguos, as auditorias registadas, as faturas
registadas, as interações e anotações do Assistente e o histórico de
execuções dos scripts. Vale a pena um dump regular:

```powershell
docker compose exec -T db pg_dump -U tesouraria -Fc tesouraria > backup_$(Get-Date -f yyyyMMdd).dump
# restaurar:
docker compose exec -T db pg_restore -U tesouraria -d tesouraria --clean < backup_<data>.dump
```

Outros volumes: `phoenix_data` (traces, datasets, experiências) e
`logs_archive` (logs compactados por dia).

> Os volumes Docker sobrevivem a `docker compose down`, mas **não** a
> `docker compose down -v`.

## 9. Resolução de problemas

| Sintoma | Causa provável | O que fazer |
|---|---|---|
| Sincronização devolve 503 "ONEDRIVE_RAIZ não definido" | `.env` sem a variável | Preencher `ONEDRIVE_RAIZ` (e `_DOCKER` em produção). |
| "Atualizar dados" diz que não há nada de novo, mas há | Pasta do dia ainda sem `.xlsx` (CGD ainda não publicou), OneDrive não sincronizado, ou o mount aponta para o sítio errado | Confirmar a pasta `…/CGD/<MM_Mês>/<dd-mm-aaaa>` no Explorador; `docker compose exec api ls /onedrive`. |
| Saldos de ontem diferentes do banco | A versão final do extrato (8:30) ainda não foi lida | Voltar a sincronizar: os saldos são atualizados quando o extrato muda. |
| Movimentos da tarde em falta | Mesmo motivo: foi lida a versão provisória das 14:00 | Voltar a sincronizar depois das 8:30 do dia seguinte. |
| `PermissionError` a ler um `.xlsx` | Ficheiro aberto no Excel ou bloqueado pelo OneDrive/antivírus | Fechar o Excel; há 5 tentativas automáticas. |
| Mapa: "Não encontrei a linha de totais" | Folha do dia com formato diferente | A linha de totais tem de ser `=SUM(`, `=SUBTOTAL(` ou `=AGGREGATE(`. |
| Sugestão/Assistente: 503 "Não foi possível ligar ao Ollama" | Ollama parado, ou modelo não descarregado | Arrancar o Ollama (ícone da barra de tarefas ou `ollama serve`); `ollama pull qwen2.5:3b`; `curl http://localhost:11434/api/tags`. |
| Assistente: 503 "modelo ocupado" | O Ollama está a responder a outro pedido (CPU) | Esperar e repetir. |
| Assistente ignora as instruções do prompt (formato, só ferramentas de leitura) | Contexto do Ollama em 4096 | `OLLAMA_CONTEXT_LENGTH=8192` e reiniciar o Ollama. |
| Assistente: "tabela saldos_diarios não existe" | Subprocesso MCP sem `DATABASE_URL` | Confirmar que a API corre com `DATABASE_URL` no ambiente (o compose define-a). |
| Primeira sugestão muito lenta depois de um deploy | Embeddings a ser calculados | O arranque já indexa em segundo plano; ver `/var/log/app/indexar_embeddings.log` no volume `logs`. |
| Log "pgvector indisponível – embeddings só em memória" | Imagem `db` sem a extensão | Usar a imagem `…/db` do projeto, não `postgres:16-alpine` direta. |
| 500 numa coluna que "não existe" depois de um deploy | Coluna nova numa tabela existente (sem migrações) | `ALTER TABLE` à mão (ver §4). |
| Deploy falha em "Diagnóstico" | Falta o `.env` na pasta do runner, ou o compose não valida | Criar o `.env` em `_work/<repo>/<repo>`; correr `docker compose config` lá. |
| Deploy falha na porta do LLM | Exatidão abaixo de 0,65, ou Ollama parado na máquina | Ver o resultado no Phoenix (experiência do deploy). Os containers antigos continuam a correr. |
| Deploy falha num passo `docker` sem mensagem | Runner a usar o Windows PowerShell 5.1 | Os passos usam `shell: cmd` por esse motivo; não mudar. |
| Botão "Correr" da Monitorização dá erro 500 | A API está em Docker | Só funciona com a API nativa no Windows. Correr o script à mão. |
| PDF da fatura não abre (404) | Caminho com `\` ou fora de `FORNECEDORES_RAIZ` | Confirmar o mount `/fornecedores` e o `pdf_relativo` da fatura. |
| Email de alerta da auditoria não chega | `SMTP_HOST` vazio ou credenciais erradas | Ver os logs da `api` (`api_tesouraria.alertas`). |

Comandos úteis:

```powershell
docker compose ps
docker compose logs -f api
docker compose exec api python -c "from app.db.session import engine; print(engine.url)"
docker compose exec db psql -U tesouraria -c "\dt"
docker compose restart api
```

## 10. Segurança

- A API e a dashboard **não têm autenticação**. Manter as portas
  8000/8501/6006/8080 acessíveis só na máquina ou na rede interna, nunca
  publicadas para a Internet.
- As credenciais do Postgres no `docker-compose.yml` são fixas
  (`tesouraria`/`tesouraria`). Aceitável porque a porta 5432 não é
  publicada; se passar a ser, mudar.
- O Dozzle monta o socket do Docker (só leitura). Quem chega à porta 8080
  vê os logs de todos os containers.
- Os dados pessoais nos extratos e o tratamento pela IA estão descritos em
  [GOVERNANCA_IA.md](GOVERNANCA_IA.md).
