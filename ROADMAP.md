# Roteiro — Plataforma de Análise de Tesouraria

Projeto para transformar os scripts `preencher_mapa.py` /
`atualizar_mapa_saldos.py` (protótipo original, pasta `tesouraria
preenchimento`) num serviço com FastAPI + Docker + CI/CD + MCP, como
peça de portfólio para AI engineer júnior e ligação à tese (geração de
código com LLM, RAG, agentes, MCP).

## Objetivo

Pegar na lógica de reconciliação bancária já validada (empresa+valor,
deteção de duplicados, auditoria) e expô-la como um serviço testável,
containerizado, com um passo de LLM+RAG para os casos ambíguos que antes
eram resolvidos à mão.

## Fase 0 — Preparação ✅

- [x] Repositório Git próprio (separado do protótipo original).
- [x] Estrutura de pastas (`app/`, `routers/`, `services/`, `db/`, `tests/`).
- [x] Ambiente virtual + `requirements.txt` inicial.

## Fase 1 — Modelo de dados ✅

- [x] Tabelas: `movimentos_bancarios`, `linhas_mapa`, `reconciliacoes`, `casos_ambiguos`, `saldos_diarios`.
- [x] Leitura de extratos a gravar na base de dados (`importar_extrato_para_bd`).
- [x] Scripts de migração/importação (`scripts/importar_*.py`).

## Fase 2 — Endpoints FastAPI ✅

- [x] `POST /reconciliar/{dia}`, `GET /auditoria/{dia}`, `GET /movimentos/{dia}`.
- [x] `GET /saldos/{empresa}`, `GET /saldos`, `GET /saldo-total`, `POST /saldos/atualizar/{dia}`.
- [x] `GET /ambiguos`, `POST /ambiguos/{id}/resolver`.
- [x] Testes pytest (42 no total, todos com mocks/dados sintéticos).
- [x] `/docs` (Swagger).

## Fase 3 — Camada LLM / RAG ✅

- [x] RAG: embeddings (`sentence-transformers`) sobre casos ambíguos já resolvidos.
- [x] LLM (HuggingFace Inference, `Qwen2.5-72B-Instruct`) propõe resolução + justificação.
- [x] Nunca aplica sozinho — fica em `resolucao_sugerida`/`justificacao_sugerida`, confirmação via `POST /ambiguos/{id}/resolver`.
- [x] `POST /ambiguos/{id}/sugerir`.

## Fase 3.5 — Modelos de ML clássico ✅

- [x] **Deteção de anomalias**: `IsolationForest` por empresa sobre o histórico de movimentos. `GET /anomalias/{dia}`.
- [x] **Previsão de saldos**: 5 modelos por conta para comparação (regressão linear, média móvel, suavização exponencial de Holt, ARIMA, Markov-switching). `GET /previsao/saldo/{empresa}` + `GET /previsao/avaliacao/{empresa}` (avaliação treino/teste com RMSE, para saber qual modelo acerta mais em cada conta).
- [ ] Deteção de fraude (regras de IBAN/faturas duplicadas) — ainda não implementado, ficou como extensão futura das anomalias.

## Fase 4 — MCP ✅

- [x] `mcp_server.py` com 6 tools (`reconciliar_dia_tool`, `auditoria_dia_tool`, `movimentos_do_dia_tool`, `consultar_saldo_tool`, `listar_ambiguos_tool`, `resolver_ambiguo_tool`).
- [x] Testado via cliente MCP real (stdio), não só chamadas diretas às funções.

## Fase 5 — Docker + CI/CD ✅

- [x] `Dockerfile` + `docker-compose.yml` (API + Postgres + dashboard + log-archiver + faturas-ocr).
- [x] GitHub Actions (`pytest` em cada push/PR + build da imagem Docker + deploy no runner self-hosted).
- [x] Build Docker confirmado em produção (runner self-hosted, `docker compose up -d --build`).
- [ ] Substituir tarefas do Agendador do Windows por chamadas HTTP — ainda não feito (o botão "Atualizar dados" no dashboard cobre parcialmente isto).

## Fase 6 — Dashboard (Streamlit) ✅

- [x] App Streamlit separado, consome a API via HTTP.
- [x] Páginas: Visão Geral (KPIs, saldo total, ranking de contas, gráfico de estado, anomalias), Reconciliação, Saldos, Análise de Contas (tendência de saldo + fluxo diário), Ambíguos (com sugestão do LLM e resolução manual).

## Fase 7 — Pipeline AWS de documentos (mudou de repositório) ✅

O pipeline serverless de documentos (S3 + Textract + Lambda + DynamoDB +
SNS + API Gateway) passou a viver como projeto de portefólio
independente, em repositório próprio: veja
[treasury-document-processing-aws](https://github.com/Benjamimoreira/treasury-document-processing-aws).
Não faz parte do fluxo de reconciliação deste projeto (os extratos
bancários reais já chegam estruturados via OneDrive, sem precisar de OCR).

## Extra (não estava no roteiro original)

- [x] Sincronização automática a partir do OneDrive (só leitura): `POST /atualizar-dados` + botão no dashboard, importa dias novos sem duplicar.

## Notas

- Manter sempre a separação: a API nunca escreve diretamente no ficheiro
  do OneDrive sem confirmação/backup — só lê.
- A reconciliação não deteta ainda movimentos já lançados diretamente no
  Mapa sem terem passado por "Valor Previsto" (equivalente ao
  `filtrar_ja_registados` do script original) — fica marcado como "novo".
