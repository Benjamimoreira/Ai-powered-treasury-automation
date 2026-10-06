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

\* Medido na versão 1 do conjunto, antes da correção dos iscos. A corrida da Groq na versão 2 foi interrompida quando a Groq foi retirada.

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

## Porta de qualidade no deploy

No job `deploy` do [ci.yml](../.github/workflows/ci.yml), no runner desta máquina, onde está o Ollama:
1. a imagem nova é descarregada;
2. se mudou algo que afeta o LLM (prompt, regras, agente, conjunto, avaliador), a avaliação **corre dentro da imagem nova**, contra o Ollama;
3. se a exatidão ficar **abaixo de 65 %**, o passo falha e os **containers antigos continuam a correr**.

O limiar fica cerca de 4 casos abaixo dos 71,7 % medidos: uma margem para a variação normal do modelo, que ainda assim apanha qualquer regressão a sério.

## Observabilidade

Cada chamada ao LLM fica registada com prompt, resposta, tokens, latência e modelo:
- no **Arize Phoenix** (`http://localhost:6006`), local;
- no **LangSmith**, opcional: com `LANGSMITH_TRACING=true`, o grafo do agente aparece passo a passo, com as chamadas ao LLM lá dentro.

Ver [`app/services/llm_tracing.py`](../app/services/llm_tracing.py).

### Experiências no LangSmith

`python -m app.evals.avaliar --langsmith` corre a mesma avaliação como uma **experiência** no LangSmith ([`app/evals/langsmith_experiencias.py`](../app/evals/langsmith_experiencias.py)):

- **Dataset** `tesouraria-ambiguos-v2`: os 60 casos. Cada caso é o *input*, e a resposta esperada e a categoria ficam como *referência*, por isso o modelo nunca as vê. É sincronizado pelo id do caso: correr duas vezes não duplica, e uma nova versão do conjunto dá um dataset novo.
- **Uma experiência por corrida** (`<estratégia>-<modelo>`), com três métricas por caso (`certa`, `resposta_valida`, `decidido_sem_llm`) e os traces do LLM e do agente dentro de cada caso. No LangSmith, *Datasets → tesouraria-ambiguos-v2 → Compare* põe as corridas lado a lado e deixa abrir cada caso falhado.
- **A porta de qualidade do deploy também corre assim**, por isso cada deploy que mexe no LLM fica registado como uma experiência. Se o LangSmith não responder, a avaliação corre localmente e o limiar é verificado na mesma: a porta nunca depende de um serviço externo.

Comparar dois modelos passa a ser correr os dois e abrir o *Compare*:
```powershell
python -m app.evals.avaliar --estrategia hibrida --langsmith
python -m app.evals.avaliar --estrategia hibrida --langsmith --modelo qwen2.5:7b
```

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
