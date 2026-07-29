# Plataforma de Análise de Tesouraria

Serviço de reconciliação bancária — casa movimentos de extratos CGD com
linhas de um Mapa de Pagamentos e Recebimentos, com uma camada de
LLM+RAG para casos ambíguos, deteção de anomalias por ML, um servidor
MCP, e um dashboard Streamlit.

Nasceu de um protótipo real (scripts Python usados em produção numa
tesouraria de grupo) e foi reconstruído aqui como serviço testável,
containerizado, como peça de portfólio para AI Engineer júnior e para a
tese sobre geração de código assistida por LLM (RAG, agentes, MCP).

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
| **Previsão de saldos (ML)** | 5 modelos por conta (regressão linear, média móvel, suavização exponencial, ARIMA, Markov-switching) lado a lado, mais avaliação treino/teste (RMSE) para saber qual acerta mais em cada conta. |
| **Saldos** | Lê saldos diretamente dos extratos, histórico por conta e total geral. |
| **Sincronização** | Importa do OneDrive (só leitura) os dias ainda não existentes localmente - sob pedido (botão) ou script. |
| **MCP** | As mesmas operações expostas como *tools* para um agente LLM chamar diretamente. |
| **Assistente (chat)** | Separador no dashboard que conversa sobre os dados reais via MCP (só ferramentas de leitura) - nunca reconcilia nem resolve nada sozinho. |
| **Dashboard** | Streamlit: visão geral com KPIs e gráficos, análise por conta, saldos, ambíguos, assistente, documentos (AWS). |
| **Pipeline AWS de documentos** | Serverless (S3 + Textract + Lambda + DynamoDB + SNS + API Gateway) para digitalizar/estruturar documentos por OCR. Peça de portefólio à parte - ver nota abaixo. |

## Stack

FastAPI · SQLAlchemy (SQLite local / Postgres em Docker) · Pydantic ·
sentence-transformers · Groq / HuggingFace Inference (LLM) · scikit-learn ·
statsmodels · MCP SDK · Streamlit · pytest · Docker · GitHub Actions · AWS SAM
(S3, Lambda, Textract, DynamoDB, SNS, API Gateway) · boto3

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
  app.py                      # Streamlit (Visão Geral, Reconciliação, Saldos, Análise de Contas, Ambíguos)
  api_client.py                # cliente HTTP fino - o dashboard nunca acede à BD diretamente
mcp_server.py                # servidor MCP (tools)
scripts/                     # scripts de migração/importação únicos + testes manuais
tests/                       # suite pytest (75 testes)
infra_aws/                   # infraestrutura AWS SAM do pipeline de documentos (ver secção própria)
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
48 testes, todos com mocks/dados sintéticos (sem chamadas de rede nem
custos). O LLM e o RAG são isolados em funções próprias precisamente
para poderem ser substituídos nos testes.

## Docker

```powershell
docker compose up --build
```
Sobe a API + Postgres. **Nota**: não testado de facto nesta máquina de
desenvolvimento (Docker Desktop sem WSL2 disponível) - a configuração
foi escrita e revista com cuidado, mas fica por confirmar o build real.

## Pipeline AWS de documentos (Textract)

Além do fluxo principal (extratos CGD já estruturados, sincronizados do
OneDrive), o projeto inclui uma segunda peça de portefólio, separada:
um pipeline serverless na AWS para digitalizar documentos por OCR.

```
Upload (dashboard ou pasta)
  → S3 (bucket "raw")
  → Lambda #1 (Textract - OCR)
  → Lambda #2 (valida/estrutura, reaproveita app/services/aws_pipeline.py)
  → DynamoDB
       → SNS (alerta por email se o documento não ficar bem extraído)
  → API Gateway + Lambda #3 (consulta)
  → Dashboard (separador "Documentos (AWS)")
```

**Nota importante sobre o âmbito:** os extratos bancários usados na
reconciliação (o fluxo principal deste projeto) já chegam estruturados
(via OneDrive) - não precisam de OCR. Este pipeline Textract não faz
parte desse fluxo de reconciliação; existe como demonstração autónoma de
arquitetura serverless AWS (S3, Lambda, Textract, DynamoDB, SNS, API
Gateway, IAM, SAM) para o caso genérico de digitalizar documentos em
papel/imagem (ex: faturas de fornecedores recebidas por email/scan) que
ainda não estejam em formato estruturado.

Infraestrutura, código das 3 Lambdas e instruções de deploy em
[`infra_aws/`](infra_aws/README.md) (AWS SAM - `sam build && sam deploy
--guided`). Testado com 8 testes unitários (`tests/test_aws_lambda_handlers.py`)
com boto3 completamente mockado, sem custos nem chamadas reais à AWS.

## Limitações conhecidas

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
- Build Docker não confirmado nesta máquina (ver acima).

## Roadmap

Ver [`ROADMAP.md`](ROADMAP.md) para o plano completo por fases, com o
que já está feito e o que falta.
