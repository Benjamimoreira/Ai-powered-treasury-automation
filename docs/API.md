# API

Referência dos endpoints HTTP (FastAPI) e das tools MCP. O contrato exato
(schemas de pedido e resposta) está sempre atualizado no Swagger:
`http://localhost:8000/docs` (ou `/openapi.json`). Este documento diz
**para que serve cada endpoint, se escreve alguma coisa e como falha**.

Convenções:

- Datas em `AAAA-MM-DD` (`dia`, `desde`, `ate`, `dia_inicio`, `dia_fim`).
- Valores em euros, `float`. Débitos/pagamentos são negativos.
- `empresa` é o nome tal como está na base de dados (ver `GET /empresas`).
  A comparação ignora LDA/SA mas não aceita nomes parciais.
- **Sem autenticação.** Ver [ARQUITETURA.md §8](ARQUITETURA.md#8-decisões-e-limites).
- Coluna **E**: ✏️ escreve na base de dados · 📥 também lê o OneDrive ·
  🤖 chama o LLM local (lento: dezenas de segundos) · vazio = só leitura.

Códigos de erro usados de forma consistente:

| Código | Quando |
|---|---|
| 404 | Recurso inexistente (caso ambíguo, interação, fatura, script). |
| 422 | Parâmetros inválidos, ou dados insuficientes para a previsão (ex.: menos de 5 pontos de histórico). |
| 503 | Dependência externa indisponível: Ollama em baixo ou ocupado, `ONEDRIVE_RAIZ` ou `FORNECEDORES_RAIZ` por configurar. A mensagem diz qual. |

## Estado

| Método | Caminho | E | Descrição |
|---|---|---|---|
| GET | `/` | | `{"status": "ok"}` — verificação de vida. |

## Sincronização (OneDrive)

Todos são idempotentes: podem repetir-se sem duplicar nada.

| Método | Caminho | E | Descrição |
|---|---|---|---|
| POST | `/atualizar-dados?dias_atras=7` | ✏️📥 | Importa os últimos `dias_atras` dias + hoje: movimentos em falta, saldos alterados, folhas do Mapa novas. Devolve os dias com novidades e os erros por dia. |
| POST | `/atualizar-dados/{dia}` | ✏️📥 | O mesmo para um dia qualquer. |
| POST | `/atualizar-historico?desde=` | ✏️📥 | Todo o histórico da pasta de extratos (mensais + diários) desde `desde` (omissão: 1 de janeiro). Pode demorar minutos. |
| POST | `/extratos/prontos?dia=` | ✏️📥 | Chamado pelo script de extração dos extratos da CGD no fim de uma extração: pede o `preencher_mapa` (que já corre o `atualizar_mapa_saldos`) ao agente do Windows e importa o dia e o anterior. O `enviar_mapa_smtp` fica à hora fixa. `dia` por omissão = hoje. |
| POST | `/saldos/atualizar/{dia}` | ✏️📥 | Corpo `{"pasta_extratos": "<caminho>"}`. Lê os saldos de uma pasta concreta (uso de migração). |

## Reconciliação e auditoria

| Método | Caminho | E | Descrição |
|---|---|---|---|
| POST | `/reconciliar/{dia}` | ✏️ | Casa os movimentos ainda não processados do dia com as linhas do Mapa (empresa + valor, tolerância 0,01 €). Devolve `{casados, novos, ambiguos}`. |
| GET | `/auditoria/{dia}` | | Compara extrato ⇄ Mapa nos dois sentidos (movimentos sem linha, linhas sem movimento, diferença por empresa). Não grava nada. |
| POST | `/auditoria/{dia}/registar` | ✏️📥 | Sincroniza o dia, audita, grava em `auditorias_dia` e envia o email de alerta se houver diferenças (com SMTP configurado). |
| POST | `/auditoria/geral?dias_atras=31` | ✏️📥 | Sincroniza `dias_atras` dias e regista a auditoria de **todos** os dias com dados. Lento; a dashboard usa timeout de 300 s. |
| GET | `/auditoria/historico?limit=100` | | Auditorias registadas, mais recentes primeiro. |
| GET | `/movimentos/{dia}` | | Cada movimento do dia com o estado da reconciliação. |
| GET | `/movimentos/resumo-diario` | | Recebimentos e pagamentos por dia, somando todas as empresas. |
| GET | `/movimentos/empresa/{empresa}` | | Histórico de movimentos de uma empresa. |
| GET | `/empresas` | | Nomes exatos das empresas com movimentos. |
| GET | `/analise/imputacoes?empresa=&dia_inicio=&dia_fim=` | | Totais recebidos/pagos por imputação (categoria do Mapa). |
| GET | `/analise/imputacoes/linhas?…` | | As linhas do Mapa por trás desses totais. |

## Casos ambíguos

| Método | Caminho | E | Descrição |
|---|---|---|---|
| GET | `/ambiguos` | | Casos por resolver, com o detalhe das linhas candidatas e a sugestão, se existir. |
| POST | `/ambiguos/{id}/sugerir` | ✏️🤖 | Regras primeiro; o LLM só se elas não decidirem. Grava `resolucao_sugerida` (`linha_id=<n>` ou `novo`) e `justificacao_sugerida` (começa por `[regra: …]` quando decidiu sem LLM). **Não resolve.** |
| POST | `/ambiguos/{id}/investigar` | ✏️🤖 | Agente LangGraph: recolhe provas e devolve um dossier (recomendação, confiança, provas, alertas), que fica gravado. **Não resolve.** |
| GET | `/ambiguos/{id}/dossiers` | | Dossiers já preparados para o caso, mais recentes primeiro. |
| POST | `/ambiguos/{id}/resolver` | ✏️ | **A decisão humana.** Corpo `{"linha_id": <int ou null>, "resolvido_por": "<nome>"}`. `null` = nenhuma linha serve (fica como novo). Aceita qualquer linha que exista, mesmo fora dos candidatos (não é validado); 404 se a linha não existir. |

Exemplo:

```bash
curl -X POST localhost:8000/ambiguos/42/sugerir
curl -X POST localhost:8000/ambiguos/42/resolver \
     -H 'Content-Type: application/json' \
     -d '{"linha_id": 1873, "resolvido_por": "benjamim"}'
```

## Saldos

| Método | Caminho | E | Descrição |
|---|---|---|---|
| GET | `/saldos?dia=` | | Último saldo conhecido de cada entidade até `dia` (omissão: o mais recente). |
| GET | `/saldos/{empresa}?dia=` | | Saldos guardados de uma entidade. |
| GET | `/saldos/mapa?dia=` | | Saldos de todas as entidades num dia, com a leitura anterior e a variação (€ e %). |
| GET | `/saldos/serie-total` | | Evolução do saldo total do grupo. |
| GET | `/saldo-total?dia=` | | Soma do último saldo conhecido de cada entidade (nem todas as contas têm leitura todos os dias). |

## Previsão

A previsão usada na dashboard é a `/previsao/ancorada`. As outras usam os
modelos de séries temporais e mantêm-se para comparação.

| Método | Caminho | Descrição |
|---|---|---|
| GET | `/previsao/ancorada?dias=30&empresa=` | Saldo de hoje + fluxos conhecidos, banda P10–P90 e série à parte com os recebimentos do comercial. Sem `empresa`: o grupo. Horizonte máximo 180 dias. |
| GET | `/previsao/ancorada-backtest?dias=30&cortes=20&empresa=` | Erro da previsão ancorada a partir de `cortes` datas passadas, contra "o saldo fica igual". |
| GET | `/previsao/risco-liquidez?dias=30` | Empresas por probabilidade de o saldo ficar negativo, com o 1.º dia provável e o pior caso. |
| GET | `/previsao/risco-ranking?dias=30` | Zona de risco atual (ok/alerta/crítico) e prevista de cada empresa. |
| GET | `/previsao/risco/{empresa}?dias=7` | Zona de risco de uma empresa e aviso se vai piorar. |
| GET | `/previsao/saldo/{empresa}?dias=7` | 5 modelos lado a lado (linear, média móvel, exponencial, ARIMA, Markov-switching) + ensemble. |
| GET | `/previsao/saldo-total?dias=7` | O mesmo para o total do grupo. |
| GET | `/previsao/saldo-total-cashflow?dias=30` | Último saldo total + cash-flow previsto acumulado. |
| GET | `/previsao/saldo-total-backtest?dias=30&cortes=6&fluxos_conhecidos=true` | Backtest do saldo total. |
| GET | `/previsao/avaliacao/{empresa}?dias_teste=5` | RMSE de cada modelo nos últimos `dias_teste` dias retidos. |
| GET | `/previsao/saldo-total-avaliacao?dias_teste=5` | O mesmo para o total. |
| GET | `/previsao/cashflow?dias=7` · `/previsao/cashflow/{empresa}` | Previsão do cash-flow líquido diário. |
| GET | `/previsao/cashflow-avaliacao?empresa=&dias_teste=5` | RMSE dos modelos de cash-flow. |

Todos só leem; respondem 422 quando não há histórico suficiente.

## Anomalias

| Método | Caminho | Descrição |
|---|---|---|
| GET | `/anomalias/{dia}?contaminacao=0.1` | Movimentos do dia fora do padrão da própria empresa (Isolation Forest). Empresas com menos de 10 movimentos de histórico são ignoradas. |

## Comercial

| Método | Caminho | Descrição |
|---|---|---|
| GET | `/comercial/cpcv?dia_inicio=&dia_fim=` | CPCVs/escrituras do índice comercial assinados no período. |
| GET | `/comercial/espaco-fracao-por-ref` | `{REF: espaço físico/fração}` para todo o índice. |
| GET | `/comercial/negocios?empresa=&dia_inicio=&dia_fim=` | Negócios com a agenda de pagamentos, o que já entrou segundo o Mapa, o que falta e as vendas por mês. |

Sem ficheiro do índice configurado, devolvem listas vazias (nunca inventam
dados).

## Faturas

| Método | Caminho | E | Descrição |
|---|---|---|---|
| POST | `/faturas/recebidas` | ✏️ | Corpo `{"linhas": [FaturaRecebidaIn, …]}`. Usado pelo script de faturas. Deduplica por `outlook_id`; devolve `{novas, duplicadas}`. |
| GET | `/faturas/recebidas?dia=&desde=&ate=&pesquisa=&limit=200` | | Faturas, com `fornecedor_normalizado` e `fonte_fornecedor` (`extraido`/`nif`/`remetente`). |
| GET | `/faturas/recebidas/{id}/pdf` | | O PDF, `inline`. 503 sem `FORNECEDORES_RAIZ`; 404 se o ficheiro não existir ou o caminho sair da pasta montada. |

## Assistente

| Método | Caminho | E | Descrição |
|---|---|---|---|
| POST | `/chat` | ✏️🤖 | Corpo `{"pergunta": "…"}`. Devolve `{resposta, ferramentas_usadas, id, numeros_nao_verificados}`. A primeira pergunta depois de um arranque é mais lenta (arranca o subprocesso MCP). 503 se o Ollama não responder. |
| POST | `/chat/{id}/feedback` | ✏️ | Corpo `{"util": true\|false}` — 👍/👎. |
| POST | `/chat/reset` | | Esquece a conversa (mantém o prompt de sistema). A conversa é **partilhada** por todos os utilizadores da API: há um só agente por processo. |

## Monitorização

| Método | Caminho | E | Descrição |
|---|---|---|---|
| GET | `/monitorizacao/scripts` | | Cada script conhecido: último estado, última execução, se está atrasado. |
| POST | `/monitorizacao/scripts/{script}/executar` | ✏️ | Reportado pelo script no fim da corrida. Corpo `{"status": "ok\|erro\|warning", "erro": …, "log": [...], "duracao_segundos": …}`. |
| POST | `/monitorizacao/scripts/{script}/eventos` | ✏️ | Erro/aviso a meio de uma corrida. Corpo `{"nivel": "erro\|aviso\|info", "mensagem": "…"}`. |
| POST | `/monitorizacao/scripts/{script}/correr` | ✏️ | Botão Correr. Com a API nativa no Windows lança o script já; em Docker grava um pedido para o agente do Windows (`scripts/agente_pedidos.py`): `{"status": "pedido", "pedido": {...}}`. Um pedido pendente do mesmo script não é duplicado. |
| GET | `/monitorizacao/pedidos?estado=pendente&limit=20` | | Pedidos do botão Correr (`pendente`, `iniciado`, `erro`), mais recentes primeiro. |
| POST | `/monitorizacao/pedidos/{id}/estado` | ✏️ | Usado pelo agente. Corpo `{"estado": "iniciado\|erro", "erro": …}`; 409 se já não estava pendente. |
| GET | `/monitorizacao/logs?limit=50&dia=` | | Execuções registadas. |
| GET | `/monitorizacao/eventos?limit=50&script=` | | Eventos em tempo real. |
| GET | `/monitorizacao/ia?dias=30` | | Qualidade da IA: perguntas, erros, 👍/👎, tempos do Assistente; sugestões aceites/rejeitadas, regras vs LLM. |
| GET | `/monitorizacao/ia/para-rever?limit=50` | | Respostas a rever: 👎, números não verificados ou chumbadas pelo juiz. |
| POST | `/monitorizacao/ia/interacoes/{id}/anotacao` | ✏️ | Corpo `{"label": "correta\|incorreta\|alucinada\|incompleta", "score": 0-1, "notas", "resposta_esperada", "autor"}`. Também vai para o trace no Phoenix. |
| GET | `/monitorizacao/ia/calibracao` | | Concordância (e kappa de Cohen) entre o juiz e as pessoas. |

Integração de um script externo (o que o `monitorizacao_client.py` faz):

```python
requests.post(f"{API}/monitorizacao/scripts/preencher_mapa/executar",
              json={"status": "ok", "duracao_segundos": 41.2, "log": ["..."]}, timeout=10)
```

## Servidor MCP

`mcp_server.py` (FastMCP, transporte stdio) expõe as mesmas operações como
tools para um agente. Usa a base de dados de `DATABASE_URL`.

```bash
python mcp_server.py          # para um cliente MCP (ex. Claude Desktop)
mcp dev mcp_server.py         # com o inspector
python scripts/teste_manual_mcp.py   # teste real do protocolo
```

| Tool | Argumentos | Escreve | No Assistente |
|---|---|---|---|
| `reconciliar_dia_tool` | `dia` | ✏️ | — |
| `resolver_ambiguo_tool` | `caso_id`, `linha_id`, `resolvido_por` | ✏️ | — |
| `auditoria_dia_tool` | `dia` | | ✔ |
| `movimentos_do_dia_tool` | `dia` | | ✔ |
| `movimentos_empresa_tool` | `empresa`, `desde`, `ate` | | ✔ |
| `consultar_saldo_tool` | `empresa`, `dia?` | | ✔ |
| `listar_saldos_tool` | — | | ✔ |
| `saldo_total_tool` | — | | ✔ |
| `listar_empresas_tool` | — | | ✔ |
| `listar_ambiguos_tool` | — | | ✔ |
| `previsao_saldo_tool` | `empresa?`, `dias=30` | | ✔ |
| `avaliar_previsao_tool` | `empresa?`, `dias=30` | | ✔ |
| `ranking_risco_tool` | `dias=30`, `so_em_risco=true` | | ✔ |
| `anomalias_do_dia_tool` | `dia` | | ✔ |
| `faturas_recebidas_tool` | `pesquisa?`, `desde?`, `ate?`, `limite=15` | | ✔ |
| `estado_scripts_tool` | — | | ✔ |

O Assistente da dashboard só tem acesso às tools de leitura
(`FERRAMENTAS_PERMITIDAS` em `app/services/chatbot.py`). Um cliente MCP
externo vê todas, incluindo as duas que escrevem. Os resultados são
arredondados para poupar contexto ao modelo.
