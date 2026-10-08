# Arquitetura

Como o sistema está montado: componentes, de onde vêm os dados, como
circulam e onde ficam. Para os endpoints ver [API.md](API.md); para correr,
fazer deploy e diagnosticar ver [OPERACAO.md](OPERACAO.md).

## 1. Visão geral

```
                        ┌───────────────────────── máquina de produção (Windows + Docker Desktop) ─────────────────────────┐
                        │                                                                                                   │
  OneDrive (sync) ──ro──┼──▶ /onedrive ─┐                                                                                   │
  Fornecedores    ──ro──┼──▶ /fornecedores ─┐      ┌──────────────┐  HTTP   ┌───────────────────────────────┐            │
  SharePoint Com. ──ro──┼──▶ /comercial ──┐ │ │    │  dashboard   │────────▶│  api (FastAPI, uvicorn :8000)  │            │
                        │                 ▼ ▼ ▼    │ Streamlit    │         │  routers → services            │            │
                        │                ┌─────────┤  :8501       │         │  └─ chat: subprocesso MCP      │──SQL──┐    │
                        │                │         └──────────────┘         └──┬──────────────┬─────────────┘       │    │
                        │                │                                     │ OTLP         │ HTTP (OpenAI API)    ▼    │
                        │                │                          ┌──────────▼──┐   ┌───────▼─────────────┐  ┌─────────┐│
                        │                │                          │ phoenix     │   │ Ollama (no Windows,  │  │ db      ││
                        │                │                          │ :6006       │   │ fora do Docker)      │  │ Postgres││
                        │                │                          └─────────────┘   │ qwen2.5:3b :11434    │  │+pgvector││
                        │                │                                            └──────────────────────┘  └─────────┘│
                        │  scripts agendados (Agendador do Windows, fora do Docker):                                        │
                        │    preencher_mapa, atualizar_mapa_saldos, enviar_mapa_smtp ──POST /monitorizacao──▶ api           │
                        │    avaliacao_online (docker exec na api)                                                          │
                        │                                                                                                   │
                        │  logs: log-viewer (Dozzle :8080) · scripts-log · scripts-log-faturas · log-archiver (cron)        │
                        │  sob pedido: faturas-ocr (Tesseract) ──POST /monitorizacao──▶ api                                │
                        └───────────────────────────────────────────────────────────────────────────────────────────────────┘
```

Princípios que atravessam todo o desenho:

- **As fontes nunca são escritas.** Extratos, Mapa, faturas e índice
  comercial são montados só-leitura (`:ro`). A base de dados é uma cópia
  derivada que se pode reconstruir a partir das fontes.
- **Uma só camada de lógica.** Tudo o que faz alguma coisa vive em
  `app/services/`. A API (`app/routers/`), o servidor MCP (`mcp_server.py`),
  os scripts (`scripts/`) e as avaliações (`app/evals/`) chamam as mesmas
  funções. A dashboard nunca acede à base de dados: só fala HTTP com a API
  (`dashboard/api_client.py`).
- **Determinístico primeiro, LLM no fim.** Regras → histórico → LLM, e o
  LLM nunca decide sozinho: grava uma sugestão que uma pessoa confirma.
- **Nenhum dado sai da máquina.** O LLM (Ollama) e os traces (Phoenix) são
  locais. Ver [GOVERNANCA_IA.md](GOVERNANCA_IA.md).

## 2. Componentes (serviços do `docker-compose.yml`)

| Serviço | Imagem | Porta | Papel |
|---|---|---|---|
| `db` | `…/db` (Postgres 16 Alpine + pgvector 0.8.1, `docker/db/Dockerfile`) | interna 5432 | Base de dados. Volume `postgres_data`. |
| `api` | `…/api` (`Dockerfile`) | 8000 | FastAPI. No arranque corre `criar_tabelas.py`, lança `indexar_embeddings.py` em segundo plano e sobe o uvicorn. |
| `dashboard` | `…/api` (a mesma imagem) | 8501 | Streamlit. `API_BASE_URL=http://api:8000`. |
| `sincronizador` | `…/api` (a mesma imagem) | - | `POST /atualizar-dados` de 15 em 15 min, 07h-21h (`app/sincronizador.py`; `SYNC_INTERVALO_MINUTOS`, `SYNC_HORA_INICIO`, `SYNC_HORA_FIM`). |
| `phoenix` | `arizephoenix/phoenix:version-12.0.0` | 6006 | Traces dos LLMs (OpenTelemetry/OpenInference), datasets e experiências. Volume `phoenix_data`. |
| `log-viewer` | `amir20/dozzle` | 8080 | Logs de todos os containers no browser. |
| `scripts-log`, `scripts-log-faturas` | `busybox` | — | `tail -F` dos `.log` dos scripts que correm fora do Docker, para aparecerem no Dozzle. |
| `log-archiver` | `…/log-archiver` (Alpine + crond) | — | De hora a hora copia os logs para `/archive/<dia>`, extrai `errors.log` e compacta o dia anterior. Volume `logs_archive`. |
| `faturas-ocr` | `…/faturas-ocr` (Python + Tesseract + Poppler) | — | Não arranca com o `up`; corre-se sob pedido (`docker compose run --rm faturas-ocr`). Saída no volume `faturas_extraidas`. |

Fora do Docker, na mesma máquina:

- **Ollama** com `qwen2.5:3b`, em `127.0.0.1:11434`. O container chega lá
  por `host.docker.internal`.
- **Scripts agendados** do projeto "tesouraria preenchimento"
  (`preencher_mapa.py`, `atualizar_mapa_saldos.py`, `enviar_mapa_smtp.py`).
  Escrevem no Mapa (esse é o trabalho deles, não desta app) e reportam cada
  corrida à API.
- **Runner self-hosted do GitHub Actions**, que faz o deploy.

## 3. Estrutura do código

```
app/
  main.py            FastAPI + lifespan (create_all, tracing, estado do Assistente)
  models.py          schemas Pydantic de entrada/saída
  db/models.py       tabelas SQLAlchemy + criar_tabelas() (CREATE EXTENSION vector)
  db/session.py      engine a partir de DATABASE_URL (SQLite por omissão)
  routers/           um ficheiro por área, só tradução HTTP ⇄ serviço
  services/          a lógica (ver tabela abaixo)
  evals/             avaliações offline/online, pseudonimização, golden dataset
dashboard/           Streamlit (app.py) + cliente HTTP (api_client.py)
mcp_server.py        as operações como tools MCP (FastMCP, stdio)
scripts/             migração/importação, indexação, testes manuais
evals/*.json         conjuntos de avaliação pseudonimizados
docker/              imagens auxiliares (db, log-archiver, faturas-ocr)
```

| Serviço (`app/services/`) | Responsabilidade |
|---|---|
| `onedrive_sync.py` | Encontra e importa extratos, saldos e folhas do Mapa a partir do OneDrive. |
| `reconciliador.py` | Leitura de extratos, normalização de nomes de empresa, reconciliação, auditoria, análises por imputação. |
| `mapa_importer.py` | Leitura das folhas diárias do Mapa de Pagamentos e Recebimentos. |
| `saldos.py` | Saldos por entidade, total do grupo, séries. |
| `resolucao_regras.py` | Decisão sem LLM para casos ambíguos (triagem pelo texto, depois histórico). |
| `llm_resolver.py` | Prompt, chamada ao Ollama, interpretação da resposta, sugestão. |
| `rag_historico.py` · `indice_vetorial.py` | Recuperação do histórico parecido (embeddings, pgvector). |
| `agente_ambiguos.py` | Agente LangGraph que prepara um dossier por caso. |
| `chatbot.py` · `guardrail_numeros.py` | Assistente (agente com tools MCP só de leitura) e verificação dos números da resposta. |
| `anomalias.py` | Isolation Forest por empresa. |
| `previsao.py` · `previsao_ancorada.py` · `fluxos_conhecidos.py` | Modelos de séries temporais (legado), previsão ancorada (a usada), rendas e recorrentes. |
| `comercial.py` · `vendas.py` | Índice do Departamento Comercial e cruzamento com o que entrou no Mapa. |
| `faturas.py` | Registo e consulta das faturas recebidas. |
| `monitorizacao.py` · `monitorizacao_ia.py` · `alertas.py` | Estado dos scripts agendados, métricas de qualidade da IA, email de alerta da auditoria. |
| `llm_tracing.py` · `phoenix_cliente.py` | Spans para o Phoenix (e LangSmith, opcional) e leitura de traces. |

## 4. Fontes de dados

Todas por caminho de ficheiro, sob `ONEDRIVE_RAIZ` (montado em `/onedrive`):

| Fonte | Caminho | O que se lê |
|---|---|---|
| Extratos diários CGD | `FINANCEIRO/03 - Extratos Bancários/Movimentos Diários/CGD/<MM_Mês>/<dd-mm-aaaa>/*.xlsx` | Um ficheiro por empresa: movimentos e, no topo, saldo contabilístico/disponível. |
| Extratos mensais | mesma pasta, meses sem pastas diárias (jan–mar 2026) | Movimentos com data e "saldo após movimento" — só para o histórico (`/atualizar-historico`). |
| Mapa de Pagamentos e Recebimentos | `CONTABILIDADE/12- Mapa de Pagamentos e Recebimentos/<ano>/<MM> - Mapa de Pagamentos  e Recebimentos de <Mês>.xlsx` | Uma folha por dia (`"21"`). Recebimentos nas colunas B–F, pagamentos em H–L. |
| Mapa de Rendas | `FINANCEIRO/04 - Mapa de Rendas - CPCV - Condomínios a receber/<ano>/<MM>_<ano>/Mapa de Rendas - <MM> <ano>.xlsx` (o do mês ou o mais recente até 12 meses antes) | Folha `RENDAS` — contratos para a previsão. |
| Faturas | `FORNECEDORES_RAIZ` (`/fornecedores`) | PDFs servidos por `GET /faturas/recebidas/{id}/pdf`. Os metadados chegam por `POST /faturas/recebidas`. |
| Índice comercial | `COMERCIAL_INDICE_PATH` (`/comercial/<nome>`) | CPCVs, escrituras e agenda de pagamentos de cada fração. |

Particularidades que o código trata:

- O CGD publica uma versão **provisória** do dia às 14:00 e substitui a
  pasta pela **final** às 8:30 do dia seguinte. A sincronização volta a ler
  os dias recentes, acrescenta os movimentos novos (comparando por
  descrição+valor, com contagem) e atualiza os saldos que mudaram. Nunca
  apaga movimentos: podem ter reconciliações e resoluções manuais.
- Ficheiros sincronizados podem estar bloqueados (Excel aberto,
  antivírus). `abrir_workbook_com_retry` tenta 5 vezes e lê de uma cópia
  temporária.
- Sinal: no Mapa os pagamentos aparecem positivos; ao importar ficam
  negativos, como os débitos do extrato, para se poderem comparar
  diretamente.

## 5. Modelo de dados

```
movimentos_bancarios 1──* reconciliacoes *──0..1 linhas_mapa
        │ 1                                         ▲
        ├──* embeddings_movimentos (vector(384))    │ candidatos (ids, JSON)
        └──* casos_ambiguos ────────────────────────┘
                   └──* dossiers_ambiguos

saldos_diarios            faturas_recebidas         auditorias_dia
execucoes_scripts         eventos_scripts
interacoes_assistente 1──* anotacoes_assistente     avaliacoes_online (por trace_id)
```

| Tabela | Conteúdo |
|---|---|
| `movimentos_bancarios` | Um movimento do extrato: dia, empresa, descrição, valor (débito < 0), ficheiro de origem (`mensal:` para os extratos mensais). |
| `linhas_mapa` | Uma linha do Mapa: dia, tipo, n.º da linha, empresa, previsto, pago, imputação, descrição. |
| `reconciliacoes` | Resultado por movimento: `tipo_match` `exato` / `ambiguo` / `novo`, ou a resolução manual. Um movimento com reconciliação nunca é reprocessado. |
| `casos_ambiguos` | Movimento com várias linhas candidatas: candidatos, sugestão (`resolucao_sugerida`, `justificacao_sugerida`) e decisão humana (`resolvido_por`, `resolucao`). |
| `dossiers_ambiguos` | Dossiers do agente, com modelo e fornecedor — registo das decisões assistidas por IA. |
| `embeddings_movimentos` | Embedding do descritivo, por movimento e modelo. `vector(384)` no Postgres, JSON no SQLite. |
| `saldos_diarios` | Saldo contabilístico e disponível por entidade e dia. |
| `auditorias_dia` | Histórico das auditorias registadas (não só a última). |
| `faturas_recebidas` | Faturas recebidas por email, com o caminho relativo do PDF. Chave única `outlook_id`. |
| `execucoes_scripts` · `eventos_scripts` | Resultado de cada corrida dos scripts agendados e eventos de erro/aviso a meio de uma corrida. |
| `interacoes_assistente` | Cada pergunta ao Assistente: resposta, ferramentas, estado, duração, 👍/👎, `trace_id`, números não verificados. |
| `avaliacoes_online` · `anotacoes_assistente` | Notas do juiz LLM e anotações humanas; as anotações com resposta esperada são promovidas ao golden dataset. |

**Esquema sem migrações.** Não há Alembic. No arranque, `criar_tabelas()`
corre `CREATE EXTENSION IF NOT EXISTS vector` e `create_all`, que cria as
tabelas em falta mas **não altera tabelas existentes**. Por isso as
alterações ao esquema fazem-se com tabelas novas (foi o caso de
`dossiers_ambiguos`), e uma coluna nova numa tabela existente pede um
`ALTER TABLE` à mão. Se o pgvector não estiver disponível, cria tudo menos
`embeddings_movimentos` e a recuperação calcula os embeddings em memória.

## 6. Fluxos principais

### 6.1 Sincronização → reconciliação → auditoria

```
POST /atualizar-dados (botão "Atualizar dados", últimos N dias)
  └─ por dia: extratos (*.xlsx) → movimentos_bancarios (só os que faltam)
              topo dos extratos → saldos_diarios (só os que mudaram)
              folha do dia no Mapa → linhas_mapa (se o dia ainda não tem linhas)

POST /reconciliar/{dia}
  └─ por cada movimento sem reconciliação:
       linhas do mesmo dia, ainda não pagas, mesma chave_empresa, |previsto − valor| < 0,01
       1 candidata  → exato   (linha.pago = valor)
       >1           → ambiguo (+ casos_ambiguos)
       0            → novo

POST /auditoria/{dia}/registar
  └─ sincroniza o dia, compara extrato ⇄ Mapa nos dois sentidos,
     grava em auditorias_dia e, havendo diferenças e SMTP configurado, envia email
```

`chave_empresa` normaliza o nome (sem acentos, maiúsculas, ignora
LDA/SA/DE/DA/DO/E), porque o extrato e o Mapa escrevem as empresas de
maneiras diferentes.

### 6.2 Casos ambíguos

```
POST /ambiguos/{id}/sugerir                         POST /ambiguos/{id}/investigar
  1. triagem pelo texto (sem consultas)               grafo LangGraph:
  2. histórico da empresa (só se 1 não decidir)        decidir_sem_llm ─decidiu─▶ verificar
  3. LLM com RAG (só se 2 não decidir)                       └─não─▶ recolher_provas ▶ analisar
       contexto: descritivo, candidatos,                               ▲          │ precisa de mais?
       6 movimentos parecidos da empresa (pgvector),          alargar_historico ◀─┘
       casos resolvidos semelhantes                                     └─▶ verificar ▶ fim
  → grava resolucao_sugerida + justificação            → grava dossier (recomendação, confiança, alertas)

                 decisão sempre humana: POST /ambiguos/{id}/resolver
```

A resposta do LLM só é aceite se for JSON com um `linha_id` que exista
entre os candidatos (ou `null`, "nenhuma serve"). Um id inventado conta
como resposta inválida e não é gravado como sugestão.

### 6.3 Assistente (chat)

```
dashboard ──POST /chat──▶ api ── huggingface_hub.Agent ──▶ Ollama (qwen2.5:3b)
                                     │ tools (stdio)
                                     ▼
                              mcp_server.py (subprocesso, mesma BD via DATABASE_URL)
                              só as 14 tools de leitura (FERRAMENTAS_PERMITIDAS)
```

O agente é criado na primeira pergunta e reutilizado (com um lock contra
criação dupla). A memória da conversa é compactada para caber no contexto
do modelo. Cada resposta passa pelo guardrail de números, que marca os
valores que não aparecem nos resultados das ferramentas, e fica registada
em `interacoes_assistente`. As avaliações online (juiz LLM) correm depois,
em lote.

### 6.4 Previsão de saldo

A previsão usada na dashboard é a **ancorada** (`/previsao/ancorada`):
saldo de hoje mais os fluxos com data e valor conhecidos (rendas,
recorrentes, linhas do Mapa com data futura), com uma banda de incerteza
(percentis 10–90) tirada de dias reais do histórico. Os recebimentos do
índice comercial ficam numa série à parte, como cenário. Os 5 modelos de
séries temporais (`/previsao/saldo`, `/previsao/cashflow`) continuam na API
mas não na dashboard: no backtest erravam mais do que "o saldo fica igual".

### 6.5 Monitorização dos scripts

Os scripts agendados reportam à API:

- `POST /monitorizacao/scripts/{script}/executar` no fim de cada corrida
  (estado, erro, log, duração);
- `POST /monitorizacao/scripts/{script}/eventos` quando apanham um erro ou
  aviso a meio da corrida.

`GET /monitorizacao/scripts` compara a última corrida com o horário
esperado (`SCRIPT_PADRAO` em `monitorizacao.py`, 20 min de tolerância) e
marca os scripts atrasados.

## 7. Observabilidade

| O quê | Onde |
|---|---|
| Traces dos LLMs (prompt, resposta, tokens, latência, grafo do agente nó a nó) | Phoenix, `http://localhost:6006` |
| Avaliações (experiências, datasets) e anotações por trace | Phoenix |
| Logs de todos os containers | Dozzle, `http://localhost:8080` |
| Logs dos scripts fora do Docker | Dozzle, containers `scripts-log*` |
| Arquivo diário de logs + só os erros | volume `logs_archive` (`/archive/<dia>/errors.log`) |
| Execuções em JSON estruturado (`execucao_terminada`) | stdout da `api` |
| Estado dos scripts, auditorias e qualidade da IA | Dashboard › Monitorização |

O tracing nunca pode fazer falhar uma chamada: sem
`PHOENIX_COLLECTOR_ENDPOINT` (ou sem as bibliotecas), `span_llm()` não faz
nada.

## 8. Decisões e limites

- **Sem autenticação.** A API e a dashboard não têm login nem CORS
  configurado: assumem uma rede de confiança (a máquina de produção). Não
  devem ser expostas à Internet tal como estão. Os endpoints de escrita
  (`/reconciliar`, `/ambiguos/{id}/resolver`, `/monitorizacao/scripts/*`)
  estão abertos a quem chegar à porta 8000.
- **SQLite em desenvolvimento, Postgres em produção.** O mesmo código serve
  os dois (`DATABASE_URL`). As diferenças estão isoladas em
  `indice_vetorial.py` (pgvector ou Python) e no `with_variant` da coluna
  do embedding.
- **Pesquisa vetorial exata, sem HNSW.** Cada consulta é sobre o histórico
  de uma empresa (centenas de vetores), filtrado por empresa e dia. Um
  índice aproximado não traria ganho.
- **Postgres Alpine.** A imagem `db` acrescenta o pgvector à mesma base
  (`postgres:16-alpine`) em vez de usar a imagem Debian do pgvector. Mudar
  de musl para glibc alteraria a ordenação de texto sob os índices do
  volume existente.
- **Modelo local pequeno.** O `qwen2.5:3b` em CPU leva dezenas de segundos
  por resposta e erra mais do que um modelo grande. Escolha e números em
  [AVALIACAO_LLM.md](AVALIACAO_LLM.md).
