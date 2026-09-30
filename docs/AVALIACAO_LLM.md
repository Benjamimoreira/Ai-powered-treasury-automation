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
