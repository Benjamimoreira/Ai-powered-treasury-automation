# Financial Forecasting & Treasury Automation (FastAPI, ML, RAG, MCP)

Plataforma de forecast financeiro para uma tesouraria de grupo: prevê
saldo e cash-flow (ensemble de modelos de séries temporais com banda de
incerteza), simula cenários what-if e sinaliza empresas em risco de
liquidez. Assenta numa camada de reconciliação bancária (extratos CGD vs.
Mapa de Pagamentos e Recebimentos), com LLM+RAG para casos ambíguos,
deteção de anomalias por ML, um servidor MCP e um dashboard Streamlit.

Nasceu de um protótipo real (scripts Python usados em produção numa
tesouraria de grupo) e foi reconstruído aqui como serviço testável,
containerizado, como peça de portfólio para AI Engineer júnior e para a
tese sobre geração de código assistida por LLM (RAG, agentes, MCP).

> Também tenho outro projeto de portefólio, independente deste:
> [Treasury Document Processing on AWS](https://github.com/Benjamimoreira/treasury-document-processing-aws)
> (S3 + Textract + Lambda + DynamoDB + SNS + API Gateway).

## Porquê este projeto

A reconciliação bancária manual é repetitiva e propensa a erro humano
(duplicados, valores trocados). Este projeto separa o problema em
camadas com responsabilidades claras:

- **Regras determinísticas** (matching por empresa+valor) para os casos
  óbvios — rápido, testável, sem custos.
- **LLM + RAG** só para os casos genuinamente ambíguos, com histórico de
  decisões humanas como contexto — nunca decide sozinho.
- **ML clássico** (Isolation Forest) para deteção de anomalias — não
  tudo tem de passar por um LLM.
- **MCP** para um agente (Claude, etc.) poder chamar estas operações
  diretamente, sem scripts manuais.

## Arquitetura

```
┌──────────────┐     ┌─────────────────────────────────────┐
│  Dashboard   │────▶│              FastAPI                 │
│  (Streamlit) │     │  reconciliação · ambíguos · saldos    │
└──────────────┘     │  anomalias (ML) · sync OneDrive       │
                      └───────┬───────────────┬──────────────┘
┌──────────────┐              │               │
│  MCP Server  │──────────────┘               │
│ (mcp_server) │                       ┌───────▼────────┐
└──────────────┘                       │  SQLite/Postgres │
                                        └──────────────────┘
        │
        ▼
┌──────────────────┐        ┌────────────────────────┐
│ HuggingFace       │        │  OneDrive (só leitura)  │
│ (LLM + RAG local) │        │  extratos CGD + Mapa    │
└──────────────────┘        └────────────────────────┘
```

## Funcionalidades

| Área | O que faz |
|---|---|
| **Reconciliação** | Casa movimentos bancários com linhas "Valor Previsto" do Mapa, por empresa (ignora LDA/SA) + valor. Idempotente - nunca reprocessa nem duplica. |
| **Ambíguos** | Movimentos com mais que uma linha candidata ficam em fila para decisão humana. |
| **LLM + RAG** | Para cada caso ambíguo, procura casos parecidos já resolvidos (embeddings `sentence-transformers`) e pede a um LLM uma sugestão com justificação. Nunca aplica sozinho. |
| **Anomalias (ML)** | `IsolationForest` por empresa (scikit-learn) - assinala movimentos fora do padrão habitual da própria conta. |
| **Previsão de saldos** | Saldo de hoje + fluxos conhecidos (rendas, recorrentes, Mapa com data futura), com banda de incerteza por simulação de dias reais e, à parte, os recebimentos marcados no índice comercial (sinal, reforços, escritura). Mesmo motor para o grupo (Forecast) e por empresa (Análise de Contas), com backtest contra "o saldo fica igual" (`/previsao/ancorada`, `app/services/previsao_ancorada.py`). |
| **Modelos de séries temporais** | 5 modelos por conta (regressão linear, média móvel, suavização exponencial, ARIMA, Markov-switching) e ensemble, ainda disponíveis na API (`/previsao/saldo`, `/previsao/cashflow`) - já não usados na dashboard: no backtest erravam mais do que "o saldo fica igual". |
| **Saldos** | Lê saldos diretamente dos extratos, histórico por conta e total geral. |
| **Sincronização** | Importa do OneDrive (só leitura) os dias ainda não existentes localmente - sob pedido (botão) ou script. |
| **MCP** | As mesmas operações expostas como *tools* para um agente LLM chamar diretamente. |
| **Assistente (chat)** | Separador no dashboard que conversa sobre os dados reais via MCP (só ferramentas de leitura) - nunca reconcilia nem resolve nada sozinho. |
| **Dashboard** | Streamlit: visão geral com KPIs e gráficos, análise por conta, saldos, ambíguos, assistente. |

## Stack

FastAPI · SQLAlchemy (SQLite local / Postgres em Docker) · Pydantic ·
sentence-transformers · Groq / HuggingFace Inference (LLM) · scikit-learn ·
statsmodels · MCP SDK · Streamlit · pytest · Docker · GitHub Actions

## Estrutura do projeto

```
app/
  main.py                  # entrypoint FastAPI
  models.py                 # schemas Pydantic
  db/
    models.py                # tabelas SQLAlchemy
    session.py                # engine/sessão (SQLite local, Postgres via DATABASE_URL)
  routers/                   # endpoints HTTP
  services/                  # lógica de negócio (reutilizada por API, MCP e scripts)
dashboard/
  app.py                      # Streamlit (Visão Geral, Reconciliação, Saldos, Análise de Contas, Ambíguos, Assistente)
  api_client.py                # cliente HTTP fino - o dashboard nunca acede à BD diretamente
mcp_server.py                # servidor MCP (tools)
scripts/                     # scripts de migração/importação únicos + testes manuais
tests/                       # suite pytest (88 testes)
Dockerfile · docker-compose.yml · .github/workflows/ci.yml
```

## Como correr localmente

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
# edita o .env: GROQ_API_KEY (console.groq.com/keys, gratuito - ou
# HF_TOKEN como alternativa), ONEDRIVE_RAIZ

python scripts\criar_tabelas.py
uvicorn app.main:app --reload
```
Abre `http://127.0.0.1:8000/docs` (Swagger).

Dashboard (noutro terminal):
```powershell
.\venv\Scripts\streamlit.exe run dashboard\app.py
```
Abre `http://127.0.0.1:8501`.

## Testes

```powershell
pytest -v
```
88 testes, todos com mocks/dados sintéticos (sem chamadas de rede nem
custos). O LLM e o RAG são isolados em funções próprias precisamente
para poderem ser substituídos nos testes.

## Docker

```powershell
docker compose up --build
```
Sobe a API + Postgres + dashboard. Build confirmado em produção via CI
(GitHub Actions, runner self-hosted) - não nesta máquina de
desenvolvimento (Docker Desktop sem WSL2 disponível).

## Deploy noutra máquina

A cada push para `master`, o GitHub Actions ([ci.yml](.github/workflows/ci.yml))
corre os testes, faz o build das imagens e publica-as no GitHub Container
Registry (`ghcr.io/benjamimoreira/ai-powered-treasury-automation/{api,log-archiver,faturas-ocr}`,
tags `latest` e `<sha do commit>`). A máquina de destino só precisa de
Docker - não compila nada nem precisa de Python.

**Preparar a máquina (uma vez):**

1. Instalar Docker Desktop (Windows, com WSL2) ou Docker Engine (Linux).
2. Instalar um runner self-hosted do GitHub: no repositório,
   *Settings > Actions > Runners > New self-hosted runner*, seguir os
   comandos indicados e instalá-lo como serviço.
3. Na pasta de trabalho do runner (`_work/<repo>/<repo>`), criar o `.env`
   a partir do `.env.example` com os caminhos *dessa* máquina
   (`ONEDRIVE_RAIZ_DOCKER`, `FORNECEDORES_RAIZ_DOCKER`, `SCRIPTS_LOG_DIR`, ...).
   O deploy usa `clean: false`, por isso o `.env` não é apagado.

A partir daí cada push para `master` faz deploy sozinho (`docker compose pull`
+ `up -d`). Também se pode correr à mão em *Actions > CI/CD > Run workflow*.

**Alternativa sem runner (deploy manual):** copiar `docker-compose.yml` e
`.env` para a máquina e correr:

```powershell
docker login ghcr.io -u <utilizador-github>   # password: token com read:packages
docker compose pull
docker compose up -d --no-build
```

Para voltar a uma versão anterior: `$env:IMAGE_TAG="<sha>"; docker compose up -d --no-build`.

## Limitações conhecidas

- Não há Alembic - novas colunas em tabelas já existentes só ficam ativas
  depois de correr `python scripts\criar_tabelas.py` (ou manualmente)
  contra a BD de produção, porque `create_all` nunca altera tabelas já
  criadas. Tabelas novas em `app/db/models.py` já não sofrem deste
  problema: o `lifespan` de `app/main.py` corre `create_all` em todos os
  arranques (idempotente - só cria o que falta), por isso uma tabela nova
  fica disponível a partir do próximo deploy sem passo manual.
- A reconciliação não deteta movimentos já lançados diretamente no Mapa
  sem terem passado por "Valor Previsto" (equivalente ao
  `filtrar_ja_registados` do script original) - aparecem como "novo".
- A camada LLM depende de disponibilidade de um provedor externo (Groq
  por omissão, gratuito; HuggingFace Inference como alternativa se
  `GROQ_API_KEY` não estiver definido); falha de forma controlada
  (guarda a resposta em bruto) se o LLM não devolver JSON válido.
- Deteção de anomalias exige pelo menos 10 movimentos históricos por
  empresa para ativar - contas novas não são avaliadas.
- Previsão de saldos exige pelo menos 5 pontos de histórico; é
  comparação entre modelos simples (regressão linear, média móvel,
  suavização exponencial), não uma previsão de produção "garantida" -
  contas com quebras/eventos pontuais grandes podem dar previsões pouco
  úteis num dos modelos (ver os 3 lado a lado, não confiar só num).

## Roadmap

Ver [`ROADMAP.md`](ROADMAP.md) para o plano completo por fases, com o
que já está feito e o que falta.
