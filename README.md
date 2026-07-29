# Este repositório reúne dois projetos de portefólio

1. **[Plataforma de Análise de Tesouraria](#plataforma-de-análise-de-tesouraria)** — API FastAPI de reconciliação bancária, LLM+RAG, ML e dashboard Streamlit.
2. **[Pipeline AWS de Documentos (Serverless OCR)](#pipeline-aws-de-documentos-serverless-ocr)** — S3 + Textract + Lambda + DynamoDB + SNS + API Gateway, projeto independente do primeiro.

---

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
  app.py                      # Streamlit (Visão Geral, Reconciliação, Saldos, Análise de Contas, Ambíguos, Documentos AWS, Assistente)
  api_client.py                # cliente HTTP fino - o dashboard nunca acede à BD diretamente
mcp_server.py                # servidor MCP (tools)
scripts/                     # scripts de migração/importação únicos + testes manuais
tests/                       # suite pytest (75 testes)
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
75 testes, todos com mocks/dados sintéticos (sem chamadas de rede nem
custos). O LLM e o RAG são isolados em funções próprias precisamente
para poderem ser substituídos nos testes.

## Docker

```powershell
docker compose up --build
```
Sobe a API + Postgres. **Nota**: não testado de facto nesta máquina de
desenvolvimento (Docker Desktop sem WSL2 disponível) - a configuração
foi escrita e revista com cuidado, mas fica por confirmar o build real.

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

---

# Pipeline AWS de Documentos (Serverless OCR)

Arquitetura serverless na AWS para digitalizar e estruturar documentos
por OCR: upload → extração de texto (Textract) → validação/estruturação
→ base de dados → alerta automático em caso de anomalia → consulta via
API. Projeto de portefólio **independente** do projeto de reconciliação
acima — não partilham dados nem fluxo.

## Porquê este projeto

Demonstra uma arquitetura serverless completa na AWS (ingestão,
processamento assíncrono orientado a eventos, OCR, base de dados
NoSQL, alertas, API pública) para o caso genérico de digitalizar
documentos em papel/imagem que ainda não estão em formato estruturado
(ex: faturas de fornecedores recebidas por email ou scan).

> **Nota:** os extratos bancários usados no projeto de reconciliação
> (acima) já chegam estruturados via OneDrive - não precisam de OCR.
> Este pipeline não faz parte desse fluxo; é uma peça de portefólio à
> parte, focada em mostrar competências de arquitetura AWS serverless.

## Arquitetura

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

## Stack

AWS SAM (infraestrutura como código) · S3 · Lambda (Python 3.12) ·
Textract · DynamoDB · SNS · API Gateway · boto3 · IAM

## Estrutura

```
infra_aws/
  template.yaml               # infraestrutura (AWS SAM)
  README.md                    # instruções de deploy detalhadas
  lambdas/
    textract_handler/app.py     # Lambda #1 - chama o Textract
    consulta_handler/app.py      # Lambda #3 - GET /documentos (API Gateway)
app/services/
  aws_pipeline.py              # parser determinístico (fornecedor, valor, data, nº doc)
  aws_pipeline_lambda.py        # Lambda #2 - valida/estrutura + grava DynamoDB + alerta SNS
dashboard/aws_client.py        # cliente do dashboard (upload direto p/ S3, listagem via API)
tests/
  test_aws_pipeline.py           # testes do parser
  test_aws_lambda_handlers.py     # testes das 3 Lambdas (boto3 mockado, sem AWS real)
```

## Deploy

Instruções completas (pré-requisitos, `sam build && sam deploy --guided`,
como testar, como ligar ao dashboard, como remover tudo) em
[`infra_aws/README.md`](infra_aws/README.md).

Resumo:
```powershell
cd infra_aws
sam build
sam deploy --guided
```
Precisas de conta AWS + AWS CLI + SAM CLI configurados. Os `Outputs` do
deploy (URL da API, nome do bucket) ligam-se ao dashboard via `.env`
(`AWS_API_URL`, `AWS_RAW_BUCKET`, `AWS_REGION`).

## Testes

```powershell
pytest tests/test_aws_pipeline.py tests/test_aws_lambda_handlers.py -v
```
10 testes, boto3 completamente mockado - sem custos nem chamadas reais
à AWS.

## Limitações conhecidas

- `Textract.detect_document_text` extrai só texto/linhas (sem
  `AnalyzeDocument` FORMS/TABLES) - suficiente para o parser
  determinístico atual, menos robusto a layouts de fatura muito
  diferentes.
- Só processa imagens e PDFs de uma página (API síncrona do Textract);
  documentos multi-página precisariam da API assíncrona
  (`StartDocumentTextDetection` + notificação de conclusão).
- API Gateway sem autenticação (endpoint público) - para um cenário
  real, adicionar API key ou autorizador (Cognito/IAM).
