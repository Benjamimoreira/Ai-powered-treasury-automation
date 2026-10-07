# Avaliação do LLM nas sugestões para casos ambíguos

Como sei que o LLM funciona? Meço-o num conjunto de casos com a resposta conhecida, antes de cada deploy que mexe no LLM, e o deploy não avança se a qualidade cair.

## O problema

Um movimento bancário bate com várias linhas do Mapa de Pagamentos e Recebimentos (mesma empresa, mesmo valor), e é preciso escolher a linha certa ou concluir que nenhuma serve. O sistema **nunca resolve sozinho**: sugere, e uma pessoa decide (ver [GOVERNANCA_IA.md](GOVERNANCA_IA.md)).

## O conjunto de avaliação

[`evals/ambiguos.json`](../evals/ambiguos.json), gerado por [`scripts/construir_conjunto_avaliacao.py`](../scripts/construir_conjunto_avaliacao.py).

- **Porque é construído e não recolhido:** casos ambíguos resolvidos por uma pessoa são raros (0 na base de dados em setembro de 2026). Mas há cerca de 1 800 pares reais de movimento bancário e linha do Mapa que o pagou, e em todos a resposta certa é conhecida.
- **Como se constrói cada caso:**
  - um par real;
  - 2 linhas "isco", reais, da mesma empresa, com o mesmo valor;
  - a resposta esperada, que é a linha real.

  Em 20 % dos casos a linha real é retirada, e a resposta esperada passa a "nenhuma serve".
- **Composição:** 60 casos (48 com a linha certa e 12 "nenhuma serve"), cada um com o histórico da empresa e os movimentos com descritivo parecido.
- **Iscos que não são respostas certas disfarçadas:** um isco não partilha palavras com o descritivo nem com a linha certa, e não tem uma imputação que o histórico tenha dado a esse descritivo. Isto foi um defeito real, corrigido na versão 2 do conjunto: "INSTITUTO REGISTOS NO" tinha como isco uma linha "IRN", o modelo acertava, e contava como erro.
- **Pseudonimizado** ([`app/evals/pseudonimizar.py`](../app/evals/pseudonimizar.py)):
  - nomes de pessoas passam a `PESSOA_nn`, de forma consistente dentro de cada caso;
  - telefones, IBAN e NIF são mascarados;
  - o gerador lista o que fica depois de `TRF`/`TFI` para ser revisto à mão.

## O que se mede

`python -m app.evals.avaliar` ([`app/evals/avaliar.py`](../app/evals/avaliar.py)) regista:
- a **exatidão**, global e por categoria;
- as **respostas válidas**: JSON com um id que existe entre os candidatos (um id inventado é o erro mais grave);
- as **chamadas ao LLM**, e quantos casos foram decididos sem LLM;
- a **latência** média e o percentil 95 (p95), e os **tokens**.

Cada corrida grava um relatório em `evals/resultados/`.

## Resultados (30/09/2026, 60 casos)

| Estratégia | Modelo | Exatidão | Linha certa | Nenhuma serve | Chamadas ao LLM | Latência média |
|---|---|---|---|---|---|---|
| Só LLM, prompt **v1** (o original) | Groq `gpt-oss-20b` | **23,3 %** | 25,0 % | 16,7 % | 60 | 5,3 s |
| Só LLM, prompt **v2** | Groq `gpt-oss-20b` | 68,3 %* | 79,2 %* | 25,0 %* | 60 | 6,4 s |
| Só LLM, prompt v2 | **Ollama `qwen2.5:3b`** (local) | 30,0 % | 14,6 % | 91,7 % | 60 | 21,6 s |
| **Regras + LLM** (produção) | **Ollama `qwen2.5:3b`** (local) | **71,7 %** | 68,8 % | 83,3 % | **26** | 15,2 s |
| Agente (LangGraph) + regras | Ollama `qwen2.5:3b` (local) | 70,0 % | **77,1 %** | 41,7 % | 31 | 22,5 s (p95 85 s) |
| Regras + LLM, modelo maior (06/10/2026) | Ollama `qwen2.5:7b` (local) | 66,7 % | 75,0 % | 33,3 % | 27 | 35,1 s** (p95 85 s) |

\* Medido na versão 1 do conjunto, antes da correção dos iscos. A corrida da Groq na versão 2 foi interrompida quando a Groq foi retirada.

\*\* Medido com o CPU partilhado com outros processos; a latência isolada deve ser um pouco menor. A exatidão não depende disso (temperatura 0).

Só com as regras (sem LLM): decidem **33 dos 60 casos (55 %), com 33 certos**. Contra **distratores reais** (955 pares, com todas as outras linhas da mesma empresa nesse dia como candidatas), decidem **36 % dos casos, com 99,7 % de precisão** (339 em 340).

## O que os números dizem

1. **A avaliação encontrou um defeito que ninguém tinha visto.** O prompt original não enviava o descritivo do banco, só a empresa e o valor, e o modelo escolhia às cegas: 23 %, abaixo do acaso para 3 linhas (~33 %). Com o descritivo, a descrição das linhas e o histórico da empresa (v2), a Groq chegou aos 68 %.
2. **Decisão condicional: o LLM só quando é preciso.** Há dois passos baratos antes do LLM ([`resolucao_regras.py`](../app/services/resolucao_regras.py)):
   - a triagem pelo texto, que não consulta nada;
   - o histórico, lido só se a triagem não decidir.

   O LLM fica com o resto: empates, sinais contraditórios e casos sem sinal. Com o modelo local, isto faz a exatidão **passar de 30 % para 71,7 % e corta 57 % das chamadas** (de 60 para 26).
3. **O modelo pequeno é conservador: quase nunca escolhe uma linha.** Só com LLM, acerta 92 % dos "nenhuma serve" mas só 15 % das linhas certas. No modo híbrido, dos 27 casos que chegam ao LLM, **respondeu "nenhuma serve" em 25**: acertou 10 dos 12 "nenhuma serve" e nenhum dos 15 com linha certa. As regras fazem o trabalho útil (as linhas fáceis), e o modelo local funciona sobretudo como filtro de "nenhuma destas". É um erro do lado seguro, porque uma sugestão errada de linha é pior do que "não sei", mas quer dizer que **os casos difíceis com resposta ficam para a pessoa**. É aqui que um modelo maior faria diferença.
4. **Agente contra sugestão simples: mais linhas certas, mais falsos positivos.** Com as provas que recolhe (histórico longo, movimentos parecidos, e pedir mais quando precisa, o que aconteceu em 8 casos), o agente encontra 4 linhas certas que a sugestão simples não encontrava (77 % contra 69 % na linha certa). Em troca, arrisca mais: em 7 dos 12 "nenhuma serve" escolheu uma linha (42 % contra 83 %). É também mais lento e fez mais chamadas (31 contra 26). Daí a divisão em produção:
   - a **sugestão híbrida** corre sempre, porque é mais conservadora;
   - o **agente** é pedido para um caso concreto (`POST /ambiguos/{id}/investigar`), e o dossier dele é lido por uma pessoa, com as provas e os alertas à frente.
5. **Limite do conjunto:** os iscos são escolhidos para não partilharem palavras com o movimento, e é esse o sinal que as regras usam. Por isso, no conjunto, as regras nunca caem num isco, e os 100 % são otimistas. A medição com distratores reais (99,7 %) é a estimativa honesta.

## Porque é que o modelo é local (Ollama) e não uma API externa

| | API externa (Groq) | Local (Ollama, `qwen2.5:3b`) |
|---|---|---|
| Exatidão em produção (regras + LLM) | não medida na v2 (estima-se ≥ 75 %, pelos 68 % só com LLM) | **71,7 %** |
| Latência | ~6 s, mais esperas por limite de pedidos (uma corrida de 60 casos chegou a demorar > 30 min) | 15 s média, 40 s p95, em CPU (Ryzen 5, sem GPU) |
| Custo | ~0,01 USD por 60 casos (gratuito até ao limite) | 0 por pedido; o custo é a máquina |
| **Privacidade** | Descritivos bancários com nomes de pessoas enviados para os EUA: DPA e base para a transferência (RGPD, cap. V) | **Nada sai da máquina** |
| Disponibilidade | Limites do plano gratuito, e modelos retirados sem aviso (o `llama-3.1-8b-instant` deixou de existir em setembro de 2026) | Depende de esta máquina estar ligada |

**Decisão:** modelo local. Com as regras à frente, a qualidade fica ao nível da API externa só com LLM, a privacidade deixa de ser um problema, e não há limites de pedidos nem modelos a desaparecer. O preço é a latência, aceitável para um fluxo que é sempre revisto por uma pessoa.

**Próximo passo, se for preciso mais qualidade:** um modelo local maior, como o `qwen2.5:7b` (~4,7 GB, cabe nos 14 GB de RAM, e deve ser 2 a 3 vezes mais lento). Com o conjunto de avaliação, a troca mede-se em vez de se adivinhar:
```powershell
ollama pull qwen2.5:7b
python -m app.evals.avaliar --estrategia hibrida --modelo qwen2.5:7b
```
O vLLM ficou de fora porque precisa de uma GPU NVIDIA.

**Medido em 06/10/2026: o `qwen2.5:7b` não compensa nas sugestões.** Exatidão de 66,7 % contra 71,7 % do `qwen2.5:3b`, com mais do dobro da latência. Encontra mais linhas certas (75 % contra 69 %), mas arrisca mais: em 8 dos 12 casos "nenhuma serve" escolhe uma linha (33 % contra 83 %). Como uma sugestão de linha errada é pior do que "não sei", **as sugestões ficam com o 3b**. O 7b fica como juiz das avaliações online do Assistente: julgar uma resposta é outra tarefa, e o juiz deve ser maior do que o modelo avaliado. A calibração contra as anotações humanas vai dizer se é um bom juiz.

## Recuperação (a parte "R" do RAG)

Medida à parte do LLM, porque a exatidão final mistura duas coisas: o contexto que o modelo recebe e o que faz com ele.

### O problema que a avaliação encontrou

O "RAG" original procurava casos ambíguos **já resolvidos por uma pessoa**, e havia 0 na base de dados. Nos 60 casos de avaliação, essa parte do prompt estava sempre vazia: a recuperação não contribuía nada. O conhecimento útil está noutro lado, nos ~1 800 pares reais "descritivo do banco → rubrica no Mapa". Mas o prompt recebia os **6 mais recentes** da empresa, fossem ou não parecidos com o caso.

### O conjunto

[`evals/recuperacao.json`](../evals/recuperacao.json), gerado por [`scripts/construir_conjunto_recuperacao.py`](../scripts/construir_conjunto_recuperacao.py) e pseudonimizado como o outro (um pseudonimizador por empresa, para o mesmo arrendatário manter o mesmo pseudónimo em todo o histórico).

- **Cada par real é uma consulta**, contra todo o histórico anterior da empresa (só dias anteriores, sem espreitar o futuro): 1 281 consultas.
- **Um item é relevante** se foi imputado à mesma rubrica que a resposta certa. A relevância sai dos dados, sem etiquetagem manual.
- **621 consultas têm pelo menos um relevante** no histórico. Nas outras (a primeira vez que uma rubrica aparece), nenhum método pode acertar, e ficam fora da conta.

O `evals/ambiguos.json` não servia para isto: guarda só 25 itens de histórico por caso e um caso por rubrica, por isso **só 18 dos 48 casos** têm algum relevante. São poucos para distinguir métodos ou modelos.

### Resultados (07/10/2026, 621 consultas)

`python -m app.evals.avaliar_recuperacao` ([`app/evals/avaliar_recuperacao.py`](../app/evals/avaliar_recuperacao.py)). Os métodos estão em [`app/services/rag_historico.py`](../app/services/rag_historico.py).

| Método | Modelo de embeddings | R@1 | R@3 | **R@6** | **MRR** | P@6 |
|---|---|---|---|---|---|---|
| Recência (o que estava em produção) | - | 12,4 % | 22,7 % | 38,8 % | 0,230 | 9,7 % |
| Lexical (BM25) | - | 57,0 % | 68,1 % | 77,1 % | 0,650 | 30,3 % |
| **Denso** (produção) | `all-MiniLM-L6-v2` | **57,8 %** | 71,3 % | **80,5 %** | **0,669** | **33,4 %** |
| Denso | `multilingual-e5-small` | 57,0 % | 71,8 % | 80,2 % | 0,665 | 33,4 % |
| Denso | `multilingual-e5-base` | 58,1 % | 71,8 % | 79,7 % | 0,670 | 33,0 % |
| Denso | `paraphrase-multilingual-MiniLM-L12-v2` | 57,6 % | 70,0 % | 79,4 % | 0,662 | 33,0 % |
| Híbrido (BM25 + denso, RRF) | `all-MiniLM-L6-v2` | 57,3 % | 70,4 % | 79,5 % | 0,664 | 31,6 % |
| Híbrido + valor (RRF de 3 listas) | `multilingual-e5-small` | 51,4 % | 71,2 % | 83,1 % | 0,638 | 32,4 % |

R@k: fração das consultas com um relevante entre os k primeiros (k=6 é o que vai para o prompt). MRR: média de 1/posição do primeiro relevante. P@6: fração dos 6 itens do prompt que são relevantes.

### O que os números dizem

1. **A recuperação duplica a qualidade do contexto.** Com os 6 mais recentes, só em 39 % das consultas o LLM via um exemplo da rubrica certa; com os 6 mais parecidos, em 80 %. O sinal útil no contexto passa de 1 em 10 itens para 1 em 3.
2. **Um modelo multilingue não ajuda.** A hipótese era que um modelo treinado só em inglês (`all-MiniLM-L6-v2`) falhasse em descritivos portugueses. Os quatro modelos ficam dentro de 1 ponto. Os descritivos bancários são códigos e nomes de entidades ("AGUAS DE GONDOMAR", "TRF PESSOA_03", "FT 01P202620"), não frases. **Fica o mais pequeno** (~90 MB contra ~470 MB do e5-base).
3. **O híbrido não ganha ao denso.** O BM25 sozinho já é bom (as palavras são muito específicas), mas a fusão por RRF não acrescenta nada. Fica o mais simples.
4. **O valor como sinal piora.** A ideia era que a mesma renda se repete com o mesmo valor. Mas fundido com o texto baixa o R@1 de 57 % para 47-51 %, porque valores parecidos aparecem em rubricas diferentes. Sobe o R@6 para 83 %, mas à custa de pôr itens irrelevantes à frente. Ficou de fora.

### Em produção: pgvector

Os embeddings dos movimentos ficam na tabela `embeddings_movimentos` ([`app/services/indice_vetorial.py`](../app/services/indice_vetorial.py)), com a coluna do tipo `vector(384)` do **pgvector**. A pesquisa é feita na própria base de dados, com a distância de cosseno (`<=>`), filtrada pelos movimentos anteriores da empresa. Verificado contra o cálculo em Python, com dados reais:
- **5 278 movimentos indexados em 96 s** (CPU). Depois disso, só se calculam os embeddings dos movimentos novos;
- a mesma escolha de 6 em **38 de 40 consultas**; as 2 diferentes são empates (o mesmo descritivo em dias diferentes, ou semelhanças iguais até à 3.ª casa decimal);
- a pesquisa vetorial demora **~7 ms**.

**Pesquisa exata, sem índice HNSW, de propósito.** Cada consulta é sobre o histórico de uma empresa (centenas de vetores, ~5 mil no total), e o filtro por empresa e dia tiraria o proveito de um índice aproximado. Um HNSW só compensa com dezenas de milhares de vetores pesquisados sem filtro.

O Postgres continua a ser o `postgres:16-alpine` com o pgvector compilado lá dentro ([`docker/db/Dockerfile`](../docker/db/Dockerfile)), e não a imagem oficial do pgvector (Debian). O volume de dados foi criado com a musl do Alpine, e passar para a glibc muda a ordenação do texto por baixo dos índices já existentes. Se a extensão não estiver disponível, a app arranca na mesma e a recuperação calcula os embeddings em memória.

### E no LLM? (ablação, 07/10/2026)

Mesmos 60 casos, só LLM (estratégia `sugestao`, `qwen2.5:3b`), muda apenas o histórico que vai para o prompt (`python -m app.evals.avaliar --estrategia sugestao --recuperacao recencia|denso`):

| Histórico no prompt | Exatidão | Linha certa | Nenhuma serve | Respostas "nenhuma serve" |
|---|---|---|---|---|
| Os 6 mais recentes | 30,0 % | 14,6 % | 91,7 % | 50 de 60 |
| Os 6 mais parecidos | 30,0 % | 14,6 % | 91,7 % | 51 de 60 |

**Um contexto melhor não mudou nada no modelo pequeno.** Mudaram 9 respostas, mas compensam-se: 3 linhas certas ganhas e 3 perdidas, e 1 "nenhuma serve" ganho e 1 perdido. O `qwen2.5:3b` responde "nenhuma serve" em 5 de cada 6 casos, venha o contexto que vier. O limite está no modelo, não na recuperação. Dois cuidados a ter:
- neste conjunto só 18 casos têm algum item relevante no histórico (ver acima), por isso o efeito possível era pequeno à partida;
- a latência (33 s contra 21 s) não é comparável, porque as duas corridas tiveram o CPU partilhado com outros processos de forma diferente. Os tokens são iguais (~590 de entrada).

A recuperação fica em produção porque é ela que decide o que o modelo vê: com um modelo que use o contexto (o 7b, ou outro), parte de 80 % em vez de 39 %. Isso é o que se mede a seguir, já com este contexto.

## Porta de qualidade no deploy

No job `deploy` do [ci.yml](../.github/workflows/ci.yml), no runner desta máquina, onde está o Ollama:
1. a imagem nova é descarregada;
2. se mudou algo que afeta o LLM (prompt, regras, agente, recuperação, conjuntos, avaliadores), as avaliações **correm dentro da imagem nova**;
3. **primeiro a recuperação**, sem LLM (~1 minuto): se o recall@6 do método em produção ficar **abaixo de 75 %**, o passo falha;
4. **depois o LLM**, contra o Ollama: se a exatidão ficar **abaixo de 65 %**, o passo falha;
5. se algum falhar, os **containers antigos continuam a correr**.

O limiar do LLM fica cerca de 4 casos abaixo dos 71,7 % medidos: uma margem para a variação normal do modelo, que ainda assim apanha qualquer regressão a sério. O da recuperação fica 5 pontos abaixo dos 80,5 % medidos. Esta avaliação é determinística, por isso a margem existe só para o conjunto poder crescer com dados novos sem partir o deploy.

## Observabilidade

Desde 06/10/2026, a observabilidade dos LLMs é só o **Arize Phoenix** (`http://localhost:6006`), num container local: nem os traces com dados reais nem o conjunto de avaliação saem da máquina. O LangSmith foi desligado. Ver [`llm_tracing.py`](../app/services/llm_tracing.py).

| O quê | Como aparece no Phoenix |
|---|---|
| Pergunta ao Assistente | Trace `assistente`: uma ronda do modelo por span LLM (`assistente_llm`, com o prompt, as chamadas a ferramentas e os tokens) e um span por ferramenta, com o resultado |
| Sugestão para um caso ambíguo | Span LLM com o prompt, a resposta, os tokens e o modelo |
| Agente de investigação | Trace `agente_ambiguos` com o grafo LangGraph nó a nó, e a chamada ao LLM dentro do nó que a fez |
| Feedback, guardrail, juiz, anotações | Anotações de cada trace |

### Experiências no Phoenix

`python -m app.evals.avaliar --phoenix` corre a mesma avaliação como uma **experiência** no Phoenix ([`phoenix_experiencias.py`](../app/evals/phoenix_experiencias.py)):

- **Dataset** `tesouraria-ambiguos-v2`: os 60 casos. Cada caso é o *input*, e a resposta esperada e a categoria ficam como *referência*, por isso o modelo nunca as vê. É sincronizado pelo id do caso: correr duas vezes não duplica, e uma nova versão do conjunto dá um dataset novo.
- **Uma experiência por corrida** (`<estratégia>-<modelo>`), com três avaliações por caso (`certa`, `resposta_valida`, `decidido_sem_llm`). Cada run aponta para o trace do caso, por isso um caso falhado abre-se com o prompt e, na estratégia agente, o grafo inteiro. Em *Datasets → tesouraria-ambiguos-v2 → Experiments* dá para comparar corridas lado a lado.
- **A porta de qualidade do deploy também corre assim**, com o commit da imagem nos metadados, por isso cada deploy que mexe no LLM fica registado como uma experiência. Se o Phoenix não responder, a avaliação corre localmente e o limiar é verificado na mesma.

Comparar dois modelos passa a ser correr os dois e abrir as experiências:
```powershell
python -m app.evals.avaliar --estrategia hibrida --phoenix
python -m app.evals.avaliar --estrategia hibrida --phoenix --modelo qwen2.5:7b
```

O `--langsmith` ([`langsmith_experiencias.py`](../app/evals/langsmith_experiencias.py)) continua a funcionar, mas já não é usado.

## Avaliação em produção (o Assistente)

A avaliação offline acima mede os casos ambíguos antes do deploy. As respostas do Assistente são avaliadas também depois, em produção, em três camadas, cada uma no sítio onde funciona melhor. Os resultados ficam no **Phoenix** como anotações de cada trace, e na base de dados para a dashboard (*Monitorização → Qualidade da IA*).

| Camada | Quando | O que faz | Onde |
|---|---|---|---|
| **Guardrail de números** | Em cada resposta, antes de a pessoa a ver | Cada valor da resposta tem de aparecer nos resultados das ferramentas, com a precisão com que foi escrito ("233 203,52 €" ao cêntimo, "233 mil €" a ±500 €). Os que não aparecem são mostrados num aviso por baixo da resposta | [`guardrail_numeros.py`](../app/services/guardrail_numeros.py) · anotação `guardrail_numeros` (CODE) |
| **Feedback humano** | Quando a pessoa clica | 👍/👎 em cada resposta | anotação `feedback_utilizador` (HUMAN) |
| **Avaliações online** | Às 10:00, 14:00 e 18:00, sobre as 4 h anteriores (até 20 conversas) | LLM as a judge com dois templates: `fundamentacao` (a resposta é suportada pelos dados? - o template de *hallucination* do Phoenix) e `relevancia` (responde à pergunta?). Opcional: `nli`, um classificador multilingue pequeno (mDeBERTa) que não depende de nenhum LLM | [`avaliacao_online.py`](../app/evals/avaliacao_online.py) · anotações LLM/CODE |

**O juiz é um modelo maior do que o avaliado** (`qwen2.5:7b` a julgar o `qwen2.5:3b`, configurável em `AVALIACAO_JUIZ_MODELO`): um modelo a julgar as próprias respostas é um juiz enviesado. Antes de confiar nos números do juiz, compará-los com as anotações humanas no Phoenix - a concordância entre os dois é o que diz quanto o juiz vale.

### Fechar o ciclo

1. Uma resposta com 👎, com números não verificados ou chumbada pelo juiz entra na fila **Para rever** (*Monitorização → Qualidade da IA*).
2. Uma pessoa anota-a: avaliação (correta, incorreta, alucinada, incompleta), score de 0 a 1, notas e, se estava errada, a **resposta esperada**. A anotação vai também para o trace no Phoenix (`anotacao_humana`).
3. [`promover_golden.py`](../app/evals/promover_golden.py) passa as anotações com resposta esperada para o golden dataset do Assistente ([`evals/assistente.json`](../evals/assistente.json) e o dataset `tesouraria-assistente` no Phoenix). Cada caso leva os resultados das ferramentas desse momento, tirados do trace, e é pseudonimizado como o conjunto dos ambíguos. Rever o diff antes do commit.
4. [`avaliar_assistente.py`](../app/evals/avaliar_assistente.py) repete cada caso em *replay* (o modelo recebe os dados gravados e a pergunta) e mede as respostas certas (juiz contra a resposta esperada) e os números não verificados. Com `--limiar`, serve de porta de qualidade, como a dos ambíguos.

### Como correr

Em produção, os jobs correm **dentro do container da API**, onde estão o Postgres, o Phoenix (`http://phoenix:6006`) e o Ollama:

```powershell
# avaliações online - agendar no Agendador de Tarefas às 10:00, 14:00 e 18:00
docker exec ai-powered-treasury-automation-api-1 python -m app.evals.avaliacao_online --horas 4

# promover as anotações ao golden dataset e trazê-lo para o repositório
docker exec ai-powered-treasury-automation-api-1 python -m app.evals.promover_golden
docker cp ai-powered-treasury-automation-api-1:/app/evals/assistente.json evals/assistente.json

# avaliar o Assistente contra o golden dataset
docker exec ai-powered-treasury-automation-api-1 python -m app.evals.avaliar_assistente --limiar 0.7
```

A corrida das avaliações online fica registada como o script `avaliacao_online`, por isso a Monitorização mostra se correu, se falhou ou se está atrasada.
