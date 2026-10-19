# API

Referência completa dos endpoints HTTP (FastAPI, `app/main.py` +
`app/routers/`) e das tools MCP (`mcp_server.py`). O contrato exato
(schemas de pedido e resposta) está sempre atualizado no Swagger:
`http://localhost:8000/docs` (ou `/openapi.json`). Este documento diz
**para que serve cada endpoint, quem o chama, se escreve alguma coisa e
como falha**.

Índice:
[Como funciona](#como-funciona) ·
[Convenções](#convenções) ·
[Autenticação](#autenticação) ·
[Estado](#estado) ·
[Sincronização](#sincronização-onedrive) ·
[Reconciliação e auditoria](#reconciliação-e-auditoria) ·
[Casos ambíguos](#casos-ambíguos) ·
[Saldos](#saldos) ·
[Previsão](#previsão) ·
[Previsão mensal](#previsão-mensal-ficheiro-de-tesouraria) ·
[Anomalias](#anomalias) ·
[Comercial](#comercial) ·
[Rendas](#rendas) ·
[Faturas](#faturas) ·
[Assistente](#assistente) ·
[Monitorização](#monitorização) ·
[Deploys](#deploys) ·
[Servidor MCP](#servidor-mcp)

## Como funciona

Visão rápida de como a API está montada e de quem fala com ela. O detalhe
dos componentes e do modelo de dados está em
[ARQUITETURA.md](ARQUITETURA.md).

```
 dashboard (:8501) ─┐
 exes CGD / scripts ─┤                    ┌─ app/routers/*  (HTTP: validar e responder)
 sincronizador ──────┼─► API :8000 ─► middleware ─┤
 agente do Windows ──┘   (uvicorn)   X-API-Key    └─► app/services/*  (a lógica)
                                                         │        │
                                                         ▼        ▼
                                                  Postgres   OneDrive /onedrive (só leitura)
                                                  (+pgvector) extratos .xlsx, Mapas, faturas
```

**Um pedido.** O middleware `exigir_chave` (`app/main.py`) valida o
`X-API-Key` (ver [Autenticação](#autenticação)); o router chama o serviço
correspondente com uma sessão SQLAlchemy aberta só para esse pedido
(`get_db`). Os routers são finos: a lógica está toda em `app/services/`,
que é também o que o servidor MCP chama diretamente.

**Arranque** (`lifespan` em `app/main.py`):

1. `criar_tabelas` cria as tabelas que faltam (nunca altera as que já
   existem: colunas novas precisam de `ALTER TABLE` à mão).
2. Sem `API_TOKEN` avisa no log que a API está aberta à rede.
3. Liga o tracing dos LLM (Phoenix/LangSmith, se configurados).
4. Lança o ciclo da **reextração automática das discrepâncias**: de 10 em
   10 min pede um deploy `reextrair_discrepancia` para **uma** discrepância
   de saldos ainda não tentada (fora de hoje, até 7 dias atrás, fora das
   janelas das tarefas CGD 08:00–08:30 e 15:00–15:15). Desliga-se com
   `REEXTRACAO_AUTOMATICA=0`. Ver `app/services/reextracao_automatica.py`.
5. O agente do Assistente (subprocesso MCP) **não** arranca aqui: é
   criado na primeira pergunta ao `/chat`.

**A API não mexe no Windows.** Corre em Docker e só lê o OneDrive
(montado em `/onedrive:ro`). Tudo o que tem de acontecer no PC — correr o
`preencher_mapa`, compilar e instalar executáveis, reextrair do banco — é
uma **fila na base de dados**: a API grava um pedido (botão Correr ou
deploy), o agente do Windows (`scripts/agente_pedidos.py`) consulta-a de
15 em 15 s, executa e reporta o estado de volta pela API.

**Quem chama o quê, num dia normal:**

| Quando | Quem | Pedido | Efeito |
|---|---|---|---|
| Início/fim de cada extração | exes CGD (`monitorizacao_cgd.py`) | `/monitorizacao/scripts/{script}/inicio` e `/executar` | Estado na Monitorização; no fim importa hoje e ontem e pede o `preencher_mapa` ao agente. |
| 15 em 15 min, 07h–21h | `sincronizador` | `/atualizar-dados`, `/faturas/recebidas/importar` | Movimentos, saldos e folhas do Mapa novas; faturas do Excel mensal. |
| 15 s / 60 s | agente | `/monitorizacao/pedidos`, `/deploys`, `/tarefas-agendadas` | Executa pedidos pendentes; reporta as tarefas agendadas. |
| 30 em 30 s | dashboard | `/sync/estado` | Recarrega quando entram dados novos. |
| A pedido | dashboard | o resto | Leituras, reconciliação, ambíguos, previsão, Assistente. |

## Convenções

- Datas em `AAAA-MM-DD` (`dia`, `desde`, `ate`, `dia_inicio`, `dia_fim`).
  Instantes (`*_em`, `ultima_execucao`, …) em ISO 8601 UTC.
- Valores em euros, `float`. Débitos/pagamentos são negativos.
- `empresa` é o nome tal como está na base de dados (ver `GET /empresas`).
  A comparação ignora LDA/SA mas não aceita nomes parciais.
- Coluna **E**: ✏️ escreve na base de dados · 📥 também lê o OneDrive ·
  🤖 chama o LLM local (lento: dezenas de segundos) · ⚙️ pede trabalho ao
  agente do Windows · vazio = só leitura.
- Coluna **Quem**: quem chama o endpoint em produção — `dash` (dashboard),
  `script` (scripts agendados / exes da CGD), `agente`
  (`scripts/agente_pedidos.py`), `sinc` (container `sincronizador`).

Códigos de erro usados de forma consistente (o corpo é sempre
`{"detail": "<mensagem>"}`):

| Código | Quando |
|---|---|
| 401 | `API_TOKEN` definido e o pedido não traz `X-API-Key` válido. Ver [Autenticação](#autenticação). |
| 404 | Recurso inexistente (caso ambíguo, interação, fatura, script, deploy, folha do Mapa de Saldos). |
| 409 | Pedido do botão Correr ou deploy que já não está pendente / já terminou (outro agente tratou dele). |
| 422 | Parâmetros inválidos, ou dados insuficientes para a previsão (ex.: menos de 5 pontos de histórico). |
| 503 | Dependência externa indisponível: Ollama em baixo ou ocupado, `ONEDRIVE_RAIZ` ou `FORNECEDORES_RAIZ` por configurar, Mapa de Saldos ilegível. A mensagem diz qual. |

## Autenticação

Chave partilhada, definida em `API_TOKEN` no `.env` (ver
`app/seguranca.py`). Gerar com
`python -c "import secrets; print(secrets.token_hex(32))"`.

- **Com `API_TOKEN`**: todos os pedidos têm de trazer o cabeçalho
  `X-API-Key: <API_TOKEN>`; senão 401.
- **Sem `API_TOKEN`**: a API fica aberta a toda a rede (só para
  desenvolvimento) e avisa no arranque.

Exceções — aceites sem chave mesmo com `API_TOKEN` definido:

| Método | Caminho | Porquê |
|---|---|---|
| GET | `/`, `/docs`, `/redoc`, `/openapi.json` | Estado e documentação. |
| POST | `/monitorizacao/scripts/{script}/inicio`, `/executar`, `/eventos` | Reportes dos scripts agendados (compilados noutros repositórios); só acrescentam registos. |
| POST | `/faturas/recebidas` | Script de faturas. |
| POST | `/extratos/prontos` | Exes da CGD no fim da extração. |
| GET | `/faturas/recebidas/{id}/pdf?expira=&assinatura=` | Link assinado (HMAC-SHA256 com a chave, válido 12 h) que o browser abre diretamente sem poder mandar cabeçalhos. Gerado pelo dashboard (`api_client.py`) com a mesma fórmula de `seguranca.assinar_pdf`. |

```bash
curl -H "X-API-Key: $API_TOKEN" localhost:8000/empresas
```

O dashboard e o agente do Windows leem a chave da variável `API_TOKEN` (o
agente também a procura no `.env`).

## Estado

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| GET | `/` | | | `{"status": "ok"}` — verificação de vida (sem chave). |
| GET | `/sync/estado` | | dash | `{atualizado_em, dias, origem}`: quando entraram dados novos na BD pela última vez, de que dias e por que via. O dashboard pergunta de 30 em 30 s e recarrega quando muda. Em memória: volta a "arranque da API" a cada arranque. |

## Sincronização (OneDrive)

Todos são idempotentes: podem repetir-se sem duplicar nada. O OneDrive é
só lido, nunca escrito. 503 se `ONEDRIVE_RAIZ` não estiver configurado.

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| POST | `/atualizar-dados?dias_atras=7` | ✏️📥 | dash, sinc | Importa os últimos `dias_atras` dias + hoje: movimentos em falta, saldos alterados, folhas do Mapa novas. Devolve `{dias_verificados, dias_com_movimentos_novos, dias_com_saldos_novos, dias_com_mapa_novo, erros}`. |
| POST | `/atualizar-dados/{dia}` | ✏️📥 | dash | O mesmo para um dia qualquer (fora da janela dos 7 dias). |
| POST | `/atualizar-historico?desde=` | ✏️📥 | manual | Todo o histórico da pasta de extratos (mensais + diários) desde `desde` (omissão: 1 de janeiro). Pode demorar minutos. |
| POST | `/extratos/prontos?dia=` | ✏️📥⚙️ | script | Chamado pelos exes da CGD no fim de uma extração bem sucedida. 1) pede o `preencher_mapa` (que já corre o `atualizar_mapa_saldos`) ao agente do Windows; 2) importa `dia` e o dia anterior. O `enviar_mapa_smtp` fica à hora fixa. `dia` por omissão = hoje (Lisboa). Devolve `{dia, preencher_mapa, sincronizacao}`; erros de cada passo vêm dentro da resposta, não como código HTTP. |
| POST | `/saldos/atualizar/{dia}` | ✏️📥 | manual | Corpo `{"pasta_extratos": "<caminho>"}`. Lê os saldos de uma pasta concreta (uso de migração). Devolve `{dia, entidades_registadas}`. |

## Reconciliação e auditoria

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| POST | `/reconciliar/{dia}` | ✏️ | dash | Casa os movimentos ainda não processados do dia com as linhas do Mapa (empresa + valor, tolerância 0,01 €). Devolve `{dia, casados, novos, ambiguos}`. |
| GET | `/auditoria/{dia}` | | dash | Compara extrato ⇄ Mapa nos dois sentidos: `movimentos_sem_match`, `linhas_sem_match`, `movimentos_dia`, somas e `diferencas_por_empresa`. Não grava nada. |
| POST | `/auditoria/{dia}/registar` | ✏️📥 | dash | Sincroniza o dia, audita, grava em `auditorias_dia` e envia o email de alerta se houver diferenças (com SMTP configurado). Mesma resposta do GET. |
| POST | `/auditoria/geral?dias_atras=31` | ✏️📥 | dash | Sincroniza `dias_atras` dias e regista a auditoria de **todos** os dias com dados. Devolve `{dias_auditados, resultados: {dia: {sem_match_fwd, sem_match_rev, diferenca_extrato_mapa} \| {erro}}}`. Lento; a dashboard usa timeout de 300 s. |
| GET | `/auditoria/historico?limit=100` | | dash | Auditorias registadas, mais recentes primeiro. `{historico: [...]}`. |
| GET | `/movimentos/{dia}` | | dash | Cada movimento do dia com o estado da reconciliação (`tipo_match`, `linha_id`, `linha_imputacao`). |
| GET | `/movimentos/resumo-diario` | | dash | Por dia: `recebimentos`, `pagamentos` e as versões `_externos` (sem transferências entre empresas do grupo). |
| GET | `/movimentos/empresa/{empresa}` | | dash | Histórico de movimentos de uma empresa, por dia. |
| GET | `/empresas` | | dash | Nomes exatos das empresas com movimentos. |
| GET | `/analise/imputacoes?empresa=&dia_inicio=&dia_fim=` | | dash | Totais recebidos/pagos por imputação (categoria do Mapa). |
| GET | `/analise/imputacoes/linhas?…` | | dash | As linhas do Mapa por trás desses totais (mesmos filtros). `{linhas: [...]}`. |

## Casos ambíguos

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| GET | `/ambiguos` | | dash | Casos por resolver, com `candidatos_detalhe` (linhas candidatas) e a sugestão, se existir. |
| POST | `/ambiguos/{id}/sugerir` | ✏️🤖 | dash | Regras primeiro; o LLM (com RAG sobre casos parecidos já resolvidos) só se elas não decidirem. Grava `resolucao_sugerida` (`linha_id=<n>` ou `novo`) e `justificacao_sugerida` (começa por `[regra: …]` quando decidiu sem LLM). **Não resolve.** 404 caso inexistente, 503 LLM indisponível. |
| POST | `/ambiguos/{id}/investigar` | ✏️🤖 | dash | Agente LangGraph: recolhe provas (histórico da empresa, movimentos parecidos, faturas com o mesmo valor) e devolve um dossier (`recomendação`, `confiança`, provas, alertas) com `id, caso_id, criado_em, fornecedor, modelo`. Fica gravado. **Não resolve.** |
| GET | `/ambiguos/{id}/dossiers` | | dash | Dossiers já preparados para o caso, mais recentes primeiro. |
| POST | `/ambiguos/{id}/resolver` | ✏️ | dash | **A decisão humana.** Corpo `{"linha_id": <int ou null>, "resolvido_por": "<nome>"}`. `null` = nenhuma linha serve (fica como novo). Aceita qualquer linha que exista, mesmo fora dos candidatos (não é validado); 404 se o caso ou a linha não existirem. |

```bash
curl -X POST -H "X-API-Key: $API_TOKEN" localhost:8000/ambiguos/42/sugerir
curl -X POST -H "X-API-Key: $API_TOKEN" localhost:8000/ambiguos/42/resolver \
     -H 'Content-Type: application/json' \
     -d '{"linha_id": 1873, "resolvido_por": "benjamim"}'
```

## Saldos

Duas fontes: os **extratos CGD** (tabela `saldos`) e o **Mapa de Saldos**
(Excel no OneDrive, com as contas de outros bancos — o mesmo valor que a
contabilidade vê).

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| GET | `/saldo-total?dia=` | | dash | Saldo total da folha do Mapa de Saldos de `dia` (ou a última antes dele; hoje, sem `dia`). Sem Mapa, soma o último saldo conhecido de cada entidade nos extratos. `{entidades, saldo_contabilistico_total, saldo_disponivel_total, fonte: "mapa_saldos"\|"extratos", dia_referencia}`. |
| GET | `/saldos?dia=` | | dash | Último saldo conhecido de cada entidade (extratos) até `dia` (omissão: o mais recente). |
| GET | `/saldos/mapa?dia=` | | dash | Saldos (extratos) de todas as entidades num dia, com a leitura anterior e a variação (€ e %). |
| GET | `/saldos/mapa-saldos?dia=` | | dash | As contas da folha do Mapa de Saldos, com variação face à folha anterior: `{dia_referencia, dia_anterior, contas: [...]}`. 404 sem Mapa para esse dia (o dashboard volta aos extratos); 503 se o ficheiro não se ler. |
| GET | `/saldos/serie-total` | | dash | Evolução do saldo total do grupo, como no Mapa de Saldos. `{serie: [...]}`. |
| GET | `/saldos/resumo-mensal?ano=` | | dash | Por mês: liquidez (início, fim, média, mínimo/máximo e dia), recebido/pago e vendas (CPCVs, reforços de sinal, escrituras). `{ano, meses, diario}`. `ano` por omissão = ano corrente. |
| GET | `/saldos/{empresa}?dia=` | | dash | Saldos guardados de uma entidade. |

## Previsão

A previsão usada na dashboard é a `/previsao/ancorada`. As outras usam os
modelos de séries temporais e mantêm-se para comparação. Todas só leem;
respondem 422 quando não há histórico suficiente.

| Método | Caminho | Descrição |
|---|---|---|
| GET | `/previsao/ancorada?dias=30&empresa=` | Saldo de hoje + fluxos conhecidos (rendas, recorrentes, Mapa com data futura), banda P10–P90 e série à parte com os recebimentos do comercial. Sem `empresa`: o grupo. Horizonte máximo 180 dias. |
| GET | `/previsao/ancorada-backtest?dias=30&cortes=20&empresa=` | Erro da previsão ancorada a partir de `cortes` datas passadas (uma por semana), contra "o saldo fica igual". |
| GET | `/previsao/risco-liquidez?dias=30` | Empresas por probabilidade de o saldo ficar negativo (trajetórias simuladas), com o 1.º dia provável e o pior caso. Mais arriscada primeiro. |
| GET | `/previsao/risco-ranking?dias=30` | Zona de risco atual (ok/alerta/crítico) e prevista de cada empresa, crítico primeiro, com `saldo_previsto_fim`. |
| GET | `/previsao/risco/{empresa}?dias=7` | Zona de risco de uma empresa (pela despesa mensal média), dias de autonomia e `dia_risco` se a previsão entrar numa zona pior. |
| GET | `/previsao/saldo/{empresa}?dias=7` | 5 modelos lado a lado (linear, média móvel, exponencial, ARIMA, Markov-switching) + ensemble. |
| GET | `/previsao/saldo-total?dias=7` | O mesmo para o total do grupo. |
| GET | `/previsao/saldo-total-cashflow?dias=30` | Último saldo total + cash-flow previsto acumulado, com trajetórias de exemplo, cash-flow semanal e fluxos conhecidos. |
| GET | `/previsao/saldo-total-backtest?dias=30&cortes=6&fluxos_conhecidos=true` | Backtest do saldo total; `fluxos_conhecidos=false` repete-o sem rendas/recorrentes. |
| GET | `/previsao/avaliacao/{empresa}?dias_teste=5&janelas=4` | RMSE/MAE/MAPE/MASE de cada modelo em `janelas` (1–12) cortes de `dias_teste` dias retidos, e `melhor_modelo`. |
| GET | `/previsao/saldo-total-avaliacao?dias_teste=5&janelas=4` | O mesmo para o total. |
| GET | `/previsao/cashflow?dias=7` · `/previsao/cashflow/{empresa}` | Previsão do cash-flow líquido diário (agregado ou de uma entidade), com importância das features. |
| GET | `/previsao/cashflow-avaliacao?empresa=&dias_teste=5&janelas=4` | Avaliação dos modelos de cash-flow; sem `empresa`, a carteira agregada. |

## Previsão mensal (ficheiro de tesouraria)

O ficheiro "Previsão Tesouraria_<MÊS><ANO>.xlsx" carregado no dashboard,
comparado com o realizado do Mapa.

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| POST | `/previsao-mensal/importar` | ✏️⚙️ | dash | Corpo `{"ficheiro": "Previsão Tesouraria_OUT2026.xlsx", "conteudo_base64": "<xlsx em base64>"}`. Substitui os meses que o ficheiro tem. Depois pede (melhor esforço) o `aplicar_previsao_mensal` ao agente, que escreve a folha "Previsão" de cada Mapa mensal. Devolve `{ficheiro, linhas, meses, …, mapas: "pedido" \| "não pedido: <motivo>"}`. 422 se o base64 ou o ficheiro forem inválidos. |
| GET | `/previsao-mensal` | | dash | `{ficheiro, importado_em, meses: [...]}`: por mês, previsto (recebimentos, pagamentos, líquido, vendas diretas, sinais e reforços) ao lado do realizado, e as linhas do ficheiro. |

## Previsões manuais (linhas no Mapa)

Linhas de Valor Previsto acrescentadas no dashboard (Predição > Acrescentar
previsão). A API guarda-as e o `aplicar_previsoes_manuais.py` (PT2), pedido
ao agente, escreve cada uma na folha do dia do Mapa de Pagamentos e
Recebimentos (secção de recebimentos ou pagamentos, com a imputação e sem
empresa) e marca-a `aplicada` ou `erro`.

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| POST | `/previsoes-manuais` | ✏️⚙️ | dash | Corpo `{"dia": 27, "mes": 10, "ano": 2026, "tipo": "recebimento\|pagamento", "descricao": …, "valor": 1234.5, "imputacao": …}` (valor positivo). Guarda como `pendente` e pede o `aplicar_previsoes_manuais` ao agente (melhor esforço: `mapa: "pedido" \| "não pedido: <motivo>"`). 422 se a data não existe, o valor não é positivo ou falta a descrição/imputação. |
| GET | `/previsoes-manuais?por_aplicar=false&limit=50` | | dash, script | Mais recentes primeiro. `por_aplicar=true`: só `pendente` e `erro`, mais antigas primeiro (o que o script escreve). |
| GET | `/previsoes-manuais/imputacoes?tipo=` | | dash | Imputações já usadas no Mapa e nas previsões manuais, por ordem alfabética. |
| POST | `/previsoes-manuais/aplicar` | ⚙️ | dash | Volta a pedir o script (ex.: depois de um erro por o Mapa estar aberto no Excel). |
| POST | `/previsoes-manuais/{id}/estado` | ✏️ | script | Corpo `{"estado": "aplicada\|erro", "erro": …}`. 409 se não existe ou já estava aplicada. |
| DELETE | `/previsoes-manuais/{id}` | ✏️ | dash | Só as que ainda não estão no Mapa (409 se aplicada). |

## Anomalias

| Método | Caminho | Descrição |
|---|---|---|
| GET | `/anomalias/{dia}?contaminacao=0.1` | Movimentos do dia fora do padrão da própria empresa (Isolation Forest). Empresas com menos de 10 movimentos de histórico são ignoradas. |

## Comercial

Lê o índice do Departamento Comercial (`COMERCIAL_INDICE_PATH` ou
`COMERCIAL_INDICE_CSV`). Sem ficheiro configurado, devolvem listas vazias
(nunca inventam dados).

| Método | Caminho | Descrição |
|---|---|---|
| GET | `/comercial/cpcv?dia_inicio=&dia_fim=` | CPCVs/escrituras assinados no período. `{linhas: [...]}`. |
| GET | `/comercial/espaco-fracao-por-ref` | `{por_ref: {REF: espaço físico/fração}}` para todo o índice. |
| GET | `/comercial/negocios?empresa=&dia_inicio=&dia_fim=` | Negócios com a agenda de pagamentos (sinal, reforços, escritura), o que já entrou segundo o Mapa (pela REF), o que falta, o próximo pagamento e as vendas por mês. Um negócio entra se o CPCV ou algum pagamento cair no período. |

## Rendas

| Método | Caminho | Descrição |
|---|---|---|
| GET | `/rendas?empresa=&ate=` | Contratos do Mapa de Rendas do ano de `ate` (omissão: hoje) com o estado de cada mês (pago no extrato, por registar no Mapa, em falta…), os recebimentos identificados pelo nome do arrendatário e pelo valor, e um resumo (`renda_mensal`, `recebido`, `valor_em_falta`, …). `meses_sem_extrato` diz que meses não se podem avaliar. Sem Mapa de Rendas acessível: `{contratos: [], resumo: {}, mapa: null, erro: "<motivo>"}` com 200. |

## Faturas

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| POST | `/faturas/recebidas` | ✏️ | script | Corpo `{"linhas": [FaturaRecebidaIn, …]}` (`outlook_id` e `data_recebido` obrigatórios). Deduplica por `outlook_id`; devolve `{novas, duplicadas}`. Sem chave. Desde 09/10/2026 o script de faturas já não o chama (ver a linha seguinte). |
| POST | `/faturas/recebidas/importar?ano=&mes=&reportar_sem_novas=true` | ✏️📥 | dash, sinc | Importa as faturas do Excel mensal do SharePoint que o `recolher_faturas_recebidas.py` escreve (omissão: mês corrente; `ano` e `mes` têm de vir juntos, senão 422). Deduplica por id do Outlook. Regista a corrida na Monitorização como `importar_faturas_recebidas`; com `reportar_sem_novas=false` (o sincronizador) só regista se houver novas ou erros. Devolve `{novas, duplicadas, linhas, erros}`; Excel em falta ou `ONEDRIVE_RAIZ` por configurar vêm em `erros`, com 200. Precisa de chave. |
| GET | `/faturas/recebidas?dia=&desde=&ate=&pesquisa=&limit=200` | | dash | Faturas, com `fornecedor_normalizado` e `fonte_fornecedor` (`extraido`/`nif`/`remetente`); `fornecedor` mantém o original. |
| GET | `/faturas/recebidas/{id}/pdf` | | dash | O PDF, `inline`. Com chave ou link assinado (ver [Autenticação](#autenticação)). Procura primeiro na pasta do ano no SharePoint (onde o script grava hoje) e depois em `FORNECEDORES_RAIZ` (faturas antigas). 404 se a fatura não tiver PDF, o ficheiro não existir em nenhuma das duas ou o caminho sair da pasta. |

## Assistente

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| POST | `/chat` | ✏️🤖 | dash | Corpo `{"pergunta": "…"}`. Devolve `{resposta, ferramentas_usadas, id, numeros_nao_verificados}` (os números da resposta que não vieram de nenhuma ferramenta). Cada pergunta, incluindo as que dão erro, fica em `interacoes_assistente`. A primeira pergunta depois de um arranque é mais lenta (arranca o subprocesso MCP). 503 se o Ollama não responder ou estiver ocupado. |
| POST | `/chat/{id}/feedback` | ✏️ | dash | Corpo `{"util": true\|false}` — 👍/👎. 404 se a interação não existir. |
| POST | `/chat/reset` | | dash | Esquece a conversa (mantém o prompt de sistema). A conversa é **partilhada** por todos os utilizadores da API: há um só agente por processo. |

## Monitorização

Scripts conhecidos (`SCRIPT_PADRAO` em `app/services/monitorizacao.py`):
`preencher_mapa`, `atualizar_mapa_saldos`, `enviar_mapa_smtp`,
`aplicar_previsao_mensal` (estes quatro têm botão Correr), `extratos_cgd`,
`extratos_cgd_noite`, `extratos_cgd_anual`, `movimentos_diarios_cgd`,
`movimentos_dia_atual_cgd`, `movimentos_escolher_dia_cgd`,
`preencher_resumo_mensal`, `deploy_scripts_cgd`,
`deploy_tesouraria_preenchimento`, `avaliacao_online`,
`sincronizar_onedrive`. Um nome desconhecido também é aceite nos reportes
e passa a aparecer na lista.

### Scripts e execuções

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| GET | `/monitorizacao/scripts` | | dash | Cada script: `status` e `ultima_execucao` da última corrida, `a_correr_desde`, `atrasado`/`hora_em_falta`, `corrivel`, `agendamento` (`ativo`/`desativado`/`horario`/`a pedido`/`desconhecido`) e `proxima_execucao` (pelas tarefas do Windows reportadas ou pelo horário). `{scripts: [...]}`. |
| POST | `/monitorizacao/scripts/{script}/inicio` | ✏️ | script | O script começou: fica "a correr" até ao `/executar`. Corpo opcional `{"origem": "…"}`. Sem chave. |
| POST | `/monitorizacao/scripts/{script}/executar` | ✏️📥⚙️ | script | Resultado da corrida. Corpo `{"status": "ok\|erro\|warning", "erro": …, "log": [...], "duracao_segundos": …}`. Os `[ERRO]`/`[AVISO]` do log viram eventos. Se o script produz dados (`movimentos_*_cgd`, `preencher_mapa`, `atualizar_mapa_saldos`) e não falhou, importa hoje e ontem em segundo plano; depois das extrações de movimentos pede também o `preencher_mapa`, se não estiver já a correr. Sem chave. |
| POST | `/monitorizacao/scripts/{script}/eventos` | ✏️ | script | Erro/aviso a meio de uma corrida. Corpo `{"nivel": "erro\|aviso\|info", "mensagem": "…"}`. Sem chave. |
| POST | `/monitorizacao/scripts/{script}/correr` | ✏️⚙️ | dash | Botão Correr. Com a API nativa no Windows lança o script já (`{"status": "iniciado"}`); em Docker grava um pedido para o agente (`{"status": "pedido", "pedido": {...}}`). Um pedido pendente do mesmo script não é duplicado. 404 se o script não for corrível. |
| GET | `/monitorizacao/logs?limit=50&dia=&script=` | | dash | Execuções registadas. `{logs: [...]}`. |
| GET | `/monitorizacao/eventos?limit=50&script=&sem_saldos=false` | | dash | Eventos em tempo real. `sem_saldos=true` tira as discrepâncias de saldos (estão em `/discrepancias-saldos`). |

### Pedidos do botão Correr e tarefas agendadas

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| GET | `/monitorizacao/pedidos?estado=&limit=20` | | agente, dash | Pedidos (`pendente`, `iniciado`, `erro`), mais recentes primeiro. O agente lê os `pendente`. |
| POST | `/monitorizacao/pedidos/{id}/estado` | ✏️ | agente | Corpo `{"estado": "iniciado\|erro", "erro": …}`. `iniciado` marca o script como a correr; `erro` grava um evento de erro. 409 se o pedido não existe ou já não estava pendente. |
| POST | `/monitorizacao/tarefas-agendadas` | ✏️ | agente | Corpo `{"tarefas": [{nome, estado, proxima_execucao, ultima_execucao, ultimo_resultado}, …]}`. **Substitui** o estado anterior. Datas antes de 2000 (o "nunca" do Windows) ficam `null`. Devolve `{registadas}`. |
| GET | `/monitorizacao/tarefas-agendadas` | | dash | As tarefas do Agendador de Tarefas tal como o agente as reportou. |

### Discrepâncias de saldos

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| GET | `/monitorizacao/discrepancias-saldos?dias=14&dia_inicio=&dia_fim=` | | dash | Avisos de saldos dos scripts + diferenças da auditoria, uma por (dia, empresa, tipo), com `ocorrencias` e `visto_em`. Sem as marcadas como resolvidas depois da última vez que foram vistas. Com `dia_inicio` (e `dia_fim`), só as desses dias; senão as dos últimos `dias`. |
| GET | `/monitorizacao/discrepancias-saldos/destino?dia=&empresa=` | | dash | Para onde foi o valor: numa janela à volta do dia, cada item que não bate (`lado: extrato\|mapa`, `estado: noutro_dia\|em_falta`, `explicacao`). `so_noutro_dia=true` quer dizer que reextrair não resolve — é o Mapa que tem de mudar de dia. Considera fins de semana/feriados (vão para a folha do dia útil seguinte). |
| POST | `/monitorizacao/discrepancias-saldos/resolvida` | ✏️ | agente | Corpo `{"dia", "empresa", "tipo"}`. A tarefa do botão "Atualizar" confirmou que já não há discrepância. |
| POST | `/monitorizacao/discrepancias-saldos/corrigir-todas` | ⚙️ | dash | Botão "Corrigir todas agora": pede o deploy `corrigir_discrepancias` com todas as discrepâncias dos últimos 60 dias (sem hoje), também as já tentadas. A corrida: reauditar com o Mapa relido (fecha as que já batem, sem ir ao banco), uma extração de todas as empresas para as que sobram, um preencher, as correções seguras no Mapa (`corrigir_discrepancias.py`) e a verificação de cada uma. 409 se já há uma a correr ou não há nenhuma. A automática faz o mesmo de 10 em 10 min para as dos últimos 7 dias ainda não tentadas. |
| POST | `/monitorizacao/discrepancias-saldos/correcoes-seguras` | | script | Corpo `{"discrepancias": [{dia, empresa}] \| null, "dias": 8, "sincronizar": true}`. As linhas do Mapa a mudar de dia sem dúvida (par único extrato/linha, linha com o descritivo do banco e sem Valor Previsto, mesmo mês): `{discrepancias, correcoes: [{tipo, empresa, descricao, valor, dia_origem, linha_origem, dia_destino, motivo}]}`. Sem `discrepancias`: as da corrida `corrigir_discrepancias` a decorrer, senão as dos últimos `dias`. `sincronizar`: relê antes o Mapa dos dias à volta de cada uma. |

### Destinatários do Mapa

Para quem o `enviar_mapa_smtp.py` manda o Mapa (lido antes de cada envio).
A lista nunca fica vazia: sem ninguém, volta `pduarte@vidor.pt`. Todos
devolvem `{destinatarios: [...]}` já atualizada.

| Método | Caminho | E | Descrição |
|---|---|---|---|
| GET | `/monitorizacao/destinatarios-mapa` | | Lista atual. |
| POST | `/monitorizacao/destinatarios-mapa` | ✏️ | Corpo `{"email": "…"}`. Normaliza para minúsculas; repetido é ignorado; 422 se o email for inválido. |
| DELETE | `/monitorizacao/destinatarios-mapa/{email}` | ✏️ | Remove (sem erro se não existir). |

### Qualidade da IA

| Método | Caminho | E | Descrição |
|---|---|---|---|
| GET | `/monitorizacao/ia?dias=30` | | Assistente (perguntas, erros, 👍/👎, tempos) e sugestões para ambíguos (aceites/rejeitadas, regras vs LLM, resolvidas sem sugestão). |
| GET | `/monitorizacao/ia/para-rever?limit=50` | | Respostas a rever: 👎, números não verificados ou chumbadas pelo juiz. `{interacoes: [...]}`. |
| POST | `/monitorizacao/ia/interacoes/{id}/anotacao` | ✏️ | Corpo `{"label": "correta\|incorreta\|alucinada\|incompleta", "score": 0-1, "notas", "resposta_esperada", "autor"}`. Também vai para o trace no Phoenix. Com `resposta_esperada`, entra no golden dataset na próxima corrida de `app/evals/promover_golden.py`. 404 interação inexistente, 422 label inválido. |
| GET | `/monitorizacao/ia/calibracao` | | Concordância (e kappa de Cohen) entre o juiz e as pessoas. |

Integração de um script externo (o que o `monitorizacao_client.py` faz):

```python
requests.post(f"{API}/monitorizacao/scripts/preencher_mapa/inicio", json={"origem": "tarefa"}, timeout=10)
# ... corrida ...
requests.post(f"{API}/monitorizacao/scripts/preencher_mapa/executar",
              json={"status": "ok", "duracao_segundos": 41.2, "log": ["..."]}, timeout=10)
```

## Deploys

Botão "Deploy" do dashboard. A API só grava o pedido; quem instala é o
agente do Windows (`scripts/agente_pedidos.py`), que reporta cada passo.

| Método | Caminho | E | Quem | Descrição |
|---|---|---|---|---|
| GET | `/monitorizacao/deploys/alvos` | | dash | `{alvos: [{alvo, descricao, usa_extracao}]}`. |
| POST | `/monitorizacao/deploys` | ✏️⚙️ | dash | Corpo `{"alvo": "<alvo>", "extracao": {...}?}`. Se já há um `pendente`/`a_correr` para o mesmo alvo, devolve esse (um `a_correr` sem notícias há 30 min deixa de bloquear). 404 alvo desconhecido; 422 extração inválida. |
| GET | `/monitorizacao/deploys?estado=&limit=10` | | agente, dash | Deploys mais recentes primeiro. `{deploys: [...]}`. |
| GET | `/monitorizacao/deploys/{id}` | | dash | Um deploy: `estado`, `passo`/`total_passos`, `mensagem`, `erro`, `log` (últimas 300 linhas), `extracao`, instantes. 404 se não existir. |
| POST | `/monitorizacao/deploys/{id}/progresso` | ✏️ | agente | Corpo `{"estado": "a_correr\|ok\|erro", "passo", "total_passos", "mensagem", "erro", "linhas": [...]}`. 409 se o deploy não existe ou já terminou. |

Alvos (`ALVOS_DEPLOY` em `app/services/deploy_scripts.py`):

| Alvo | O quê |
|---|---|
| `cgd_MovimentosCGD` | Extratos CGD (mês 08:00, noite 15:15, anual 00:00/12:00). |
| `cgd_MovimentosDiaAtualCGD` | Movimentos do dia atual (08:20, 15:00). |
| `cgd_MovimentosDiariosCGD` | Movimentos de ontem (08:30). |
| `cgd_MovimentosEscolherDiaCGD` | Movimentos de um dia escolhido à mão. |
| `cgd_PreencherResumoMensalCGD` | Resumo Mensal CGD (09:00, 16:30). |
| `scripts_cgd` | Todos os scripts CGD de uma vez. |
| `tesouraria_preenchimento` | Atualizar Mapa Tesouraria, Atualizar Mapa Saldos, Enviar Mapa. |
| `reextrair_discrepancia` | Não instala nada: reextrai a empresa no dia, preenche os Mapas e verifica. |

`extracao` (só nos alvos `cgd_*` e `scripts_cgd`, mais
`reextrair_discrepancia`) — o que fazer a seguir ao deploy:

```json
{"modo": "nenhuma"}
{"modo": "ano"}
{"modo": "periodo", "inicio": "2026-10-01", "fim": "2026-10-15"}
{"modo": "periodo", "inicio": "2026-10-07", "fim": "2026-10-14",
 "empresa": "…", "tipo": "…", "dia": "2026-10-14"}
```

O último é o de `reextrair_discrepancia`: `dia` tem de estar dentro do
período. Para este alvo, um pedido com outra discrepância cria um deploy
novo em vez de reaproveitar o pendente.

## Servidor MCP

`mcp_server.py` (FastMCP, transporte stdio) expõe as mesmas operações como
tools para um agente. Usa a base de dados de `DATABASE_URL` diretamente
(não passa pela API HTTP, por isso não precisa de `API_TOKEN`).

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
