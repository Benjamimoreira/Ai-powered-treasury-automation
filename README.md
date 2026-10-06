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
| **LLM + RAG** | Para cada caso ambíguo, dá ao LLM o descritivo do banco, as linhas candidatas, o histórico de imputações da empresa e casos parecidos já resolvidos (embeddings `sentence-transformers`), e grava uma sugestão com justificação. Nunca aplica sozinho. |
| **Agente de investigação (LangGraph)** | `POST /ambiguos/{id}/investigar`: recolhe provas (histórico da empresa, movimentos com o mesmo descritivo, faturas com o mesmo valor), pede mais histórico se precisar, e prepara um dossier com recomendação, confiança e alertas. Regras fixas no fim: id inexistente descartado, confiança baixa obriga a revisão, decisão sempre humana (`app/services/agente_ambiguos.py`). |
| **Avaliação de LLMs** | Conjunto de 60 casos com resposta conhecida, construído a partir de dados reais e pseudonimizado (`evals/ambiguos.json`). `python -m app.evals.avaliar` mede exatidão, respostas válidas, latência, tokens e custo; no CI, o deploy não avança se a exatidão descer abaixo do limiar. Resultados e escolha do modelo em [docs/AVALIACAO_LLM.md](docs/AVALIACAO_LLM.md). |
| **Observabilidade de LLMs** | Tudo no **Arize Phoenix** (self-hosted, `http://localhost:6006`): cada ronda do Assistente, cada sugestão e o grafo do agente nó a nó, com prompt, resposta, latência e tokens (`app/services/llm_tracing.py`). As avaliações offline são experiências no Phoenix, e o feedback, o guardrail de números e o juiz das avaliações online ficam como anotações de cada trace - ver [docs/AVALIACAO_LLM.md](docs/AVALIACAO_LLM.md). |
| **Modelo local** | Todo o LLM (sugestões, agente, Assistente) corre localmente no **Ollama** (`qwen2.5:3b`) - nenhum dado sai da máquina. As APIs externas foram retiradas; a comparação que sustenta a escolha está em [docs/AVALIACAO_LLM.md](docs/AVALIACAO_LLM.md). |
| **Decisão sem LLM primeiro** | Regras baratas antes do LLM (`app/services/resolucao_regras.py`): triagem pelo texto sem consultar nada, histórico só se preciso, LLM só para o que sobra. Decidem ~36 % dos casos reais com 99,7 % de precisão. |
| **Governança** | Mapa do sistema face ao AI Act e ao RGPD (nível de risco, supervisão humana, registo de decisões, transferências, lacunas): [docs/GOVERNANCA_IA.md](docs/GOVERNANCA_IA.md). |
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
sentence-transformers · Ollama (LLM local) · LangGraph ·
OpenTelemetry + Arize Phoenix · scikit-learn · statsmodels · MCP SDK · Streamlit ·
pytest · Docker · GitHub Actions

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
  evals/                     # avaliação das sugestões do LLM + pseudonimização
dashboard/
  app.py                      # Streamlit (Visão Geral, Reconciliação, Saldos, Análise de Contas, Ambíguos, Assistente)
  api_client.py                # cliente HTTP fino - o dashboard nunca acede à BD diretamente
mcp_server.py                # servidor MCP (tools)
scripts/                     # scripts de migração/importação únicos + testes manuais
evals/ambiguos.json          # conjunto de avaliação (pseudonimizado)
docs/                        # avaliação de LLMs, governança (AI Act/RGPD)
tests/                       # suite pytest
Dockerfile · docker-compose.yml · .github/workflows/ci.yml
```

## Como correr localmente

```powershell
python -m venv venv
.\venv\Scripts\Activate.ps1
pip install -r requirements.txt

copy .env.example .env
# edita o .env: ONEDRIVE_RAIZ; o LLM é local - instalar o Ollama
# (ollama.com) e descarregar o modelo: ollama pull qwen2.5:3b
# e subir o contexto do Ollama para 8192 tokens (variável de ambiente do
# utilizador OLLAMA_CONTEXT_LENGTH=8192, depois reiniciar o Ollama): com os
# 4096 por omissão, o prompt do Assistente (~2 200 tokens) mais o resultado de
# uma ferramenta grande passava o limite e o Ollama cortava as instruções

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
Todos com mocks/dados sintéticos (sem chamadas de rede nem
custos) - a qualidade do LLM real mede-se à parte (e no deploy, como
porta de qualidade), com
`python -m app.evals.avaliar` (ver [docs/AVALIACAO_LLM.md](docs/AVALIACAO_LLM.md)). O LLM e o RAG são isolados em funções próprias precisamente
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
- A camada LLM depende do Ollama estar a correr nesta máquina (sem ele,
  as sugestões e o Assistente devolvem um erro explícito; as regras sem
  LLM continuam a decidir o que conseguem). Um modelo de 3B em CPU é lento
  (dezenas de segundos por resposta) e erra mais do que um modelo grande -
  ver [docs/AVALIACAO_LLM.md](docs/AVALIACAO_LLM.md).
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
