# Governança e conformidade da IA (AI Act e RGPD)

Mapa curto do sistema face ao **Regulamento (UE) 2024/1689 (AI Act)** e ao **RGPD**. Serve para documentar o desenho e as salvaguardas. **Não é um parecer jurídico.** Antes de levar o sistema para produção fora deste contexto, estas conclusões devem ser validadas pelo encarregado de proteção de dados (DPO) e pelo jurídico.

Estado a 30/09/2026.

## 1. Que componentes usam IA

| Componente | O que faz | Técnica | Decide sozinho? |
|---|---|---|---|
| Sugestão para casos ambíguos ([llm_resolver.py](../app/services/llm_resolver.py)) | Propõe qual linha do Mapa corresponde a um movimento bancário | Primeiro regras sem LLM ([resolucao_regras.py](../app/services/resolucao_regras.py)); só o que sobra vai ao LLM **local** (Ollama, `qwen2.5:3b`) | **Não**: grava uma sugestão, e a pessoa resolve em `POST /ambiguos/{id}/resolver` |
| Agente de investigação ([agente_ambiguos.py](../app/services/agente_ambiguos.py)) | Recolhe provas e prepara um dossier com recomendação | LangGraph + as mesmas regras + LLM local | **Não**: grava um dossier "pendente de aprovação humana" |
| Assistente (separador *Assistente*, [chatbot.py](../app/services/chatbot.py)) | Responde a perguntas sobre os dados | LLM local + ferramentas MCP **só de leitura** | Não tem ferramentas de escrita |
| Deteção de anomalias ([anomalias.py](../app/services/anomalias.py)) | Assinala movimentos fora do padrão da conta | Isolation Forest | Só assinala |
| Previsão de saldo ([previsao_ancorada.py](../app/services/previsao_ancorada.py)) | Saldo previsto e intervalo de incerteza | Regras + simulação estatística | Só informa |

## 2. AI Act

### Nível de risco: risco mínimo, com obrigações de transparência

- **Nenhuma prática proibida** (art. 5.º).
- **Não é de risco elevado** (art. 6.º e Anexo III). O sistema reconcilia movimentos entre empresas do grupo e prevê saldos. Não avalia a solvabilidade nem a pontuação de crédito de pessoas singulares (Anexo III, 5.b), não decide sobre emprego nem sobre acesso a serviços essenciais, e não produz efeitos jurídicos sobre pessoas.
- O que se aplica:
  - **transparência** (art. 50.º, aplicável desde 2/8/2026), no Assistente;
  - **literacia em IA** (art. 4.º, desde 2/2/2025), para quem usa o sistema.

Somos *responsáveis pela implementação* (*deployers*) de um modelo de uso geral de terceiros (Qwen 2.5, da Alibaba, a correr localmente no Ollama), e não fornecedores desse modelo. As obrigações dos arts. 53.º e seguintes recaem sobre quem o fornece.

### Salvaguardas implementadas

| Requisito | Como está implementado | Onde |
|---|---|---|
| **Supervisão humana** | Nenhum componente resolve casos: o LLM sugere e o agente prepara, mas só `resolver_ambiguo` aplica, chamado por uma pessoa. O Assistente só tem ferramentas de leitura (as de escrita estão fora de `FERRAMENTAS_PERMITIDAS`). | [chatbot.py](../app/services/chatbot.py), [agente_ambiguos.py](../app/services/agente_ambiguos.py) (nó `verificar`) |
| **Guardas que não dependem do modelo** | Um id recomendado que não existe entre os candidatos é descartado. Confiança baixa obriga a revisão. A decisão fica sempre "pendente de aprovação humana". | `_verificar`, `interpretar_resposta` |
| **Registo das decisões** | Cada dossier fica em `dossiers_ambiguos`, com o modelo, o fornecedor, a data, a recomendação, a confiança e as provas. A resolução humana fica em `casos_ambiguos.resolvido_por`. Cada chamada ao LLM fica no Phoenix, com prompt, resposta, tokens, latência e custo. | [models.py](../app/db/models.py), [llm_tracing.py](../app/services/llm_tracing.py) |
| **Exatidão e robustez medidas** | Conjunto de avaliação com 60 casos e resposta conhecida. O CI falha e não há deploy se a exatidão descer abaixo do limiar. | [AVALIACAO_LLM.md](AVALIACAO_LLM.md), job `avaliacao-llm` no [ci.yml](../.github/workflows/ci.yml) |
| **Transparência (art. 50.º)** | O separador diz que é um "Assistente de IA", qual o modelo, que corre localmente, que só lê dados e que pode errar. | [dashboard/app.py](../dashboard/app.py), separador Assistente |
| **Menos decisões por IA** | As regras sem LLM decidem cerca de 36 % dos casos, com 99,7 % de precisão em dados reais; o LLM só entra quando as regras não chegam. Cada sugestão diz se veio de uma regra ou do LLM. | [resolucao_regras.py](../app/services/resolucao_regras.py) |

## 3. RGPD

### Dados tratados

| Dados | Pessoais? | Nota |
|---|---|---|
| Saldos e movimentos das empresas do grupo | Não, em regra (pessoas coletivas) | — |
| **Descritivos bancários** | **Sim, com frequência** | Nomes de clientes, arrendatários, colaboradores e reembolsos (ex. `TRF <nome>`), e telemóveis parciais do MB Way |
| Faturas recebidas | Às vezes | Fornecedores que são pessoas singulares (empresários em nome individual) e remetentes de email |
| Contratos de renda (Mapa de Rendas) | Sim | Nomes dos arrendatários e valor da renda |

**Base legal:**
- art. 6.º, n.º 1, al. c): obrigações legais contabilísticas e fiscais de conservar e conciliar movimentos;
- art. 6.º, n.º 1, al. f): interesse legítimo na gestão da tesouraria do grupo.

Não há categorias especiais de dados (art. 9.º) nem decisões automatizadas com efeitos sobre pessoas (art. 22.º), porque nenhuma decisão sobre uma pessoa é tomada pelo sistema.

### Salvaguardas implementadas

- **Minimização:**
  - o conjunto de avaliação versionado no repositório é **pseudonimizado** ([pseudonimizar.py](../app/evals/pseudonimizar.py)): nomes de pessoas passam a `PESSOA_nn`, e telemóveis, IBAN e NIF são mascarados;
  - o script que o gera lista o que fica depois de `TRF`/`TFI` para ser revisto;
  - o repositório é **privado**.
- **Sem envio a terceiros:** desde 30/09/2026 o LLM é **só local** (Ollama, nesta máquina). Os descritivos bancários não saem da máquina, e não há transferências internacionais nem subcontratantes de IA. A Groq (EUA), usada antes, foi retirada do código e da configuração. A comparação de qualidade, latência e custo que sustenta a escolha está em [AVALIACAO_LLM.md](AVALIACAO_LLM.md).
- **LangSmith (opcional):** se o tracing no LangSmith for ligado (`LANGSMITH_TRACING=true`), os prompts e as respostas passam a ir para um serviço externo. Para dados reais: usar a região da UE (`LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com`) e ter o DPA da LangChain. Com o conjunto de avaliação não há problema, porque está pseudonimizado.
- **Tracing local:** o Phoenix corre num container nesta máquina. Os prompts registados, que contêm descritivos, não vão para nenhum serviço externo.
- **Segredos:** as chaves ficam em `.env` (fora do git) e nos segredos do GitHub Actions. Nunca aparecem em logs.

## 4. Lacunas conhecidas (por ordem de prioridade)

1. **LangSmith com dados reais:** antes de o ligar à app em produção, confirmar a região da UE e o DPA. Em alternativa, pseudonimizar o prompt antes de o enviar: o `Pseudonimizador` já existe e pode ser aplicado em `chamar_llm_detalhado`. O Phoenix, que é local, não tem este problema.
2. **Portas abertas sem autenticação na rede local:** API (8000), dashboard (8501), Phoenix (6006) e logs (8080) ouvem em `0.0.0.0`. Se a máquina não estiver isolada, é preciso limitá-las a `127.0.0.1` ou pôr um proxy com login à frente.
3. **Retenção por definir:** os traces do Phoenix e os dossiers não têm prazo de apagamento. Proposta: 90 dias para os traces; os dossiers acompanham o prazo contabilístico do movimento.
4. **Histórico do git:** o repositório foi público até 30/09/2026, e o histórico contém pelo menos um nome de arrendatário com o valor da renda, num teste. Tornar o repositório privado limita o acesso, mas não apaga o que já pode ter sido copiado. É preciso avaliar se há obrigação de notificação e reescrever o histórico ou remover os dados.
5. **Literacia em IA (art. 4.º):** falta uma nota curta para quem usa a dashboard sobre o que a IA faz, os limites dela e porque a decisão é humana.
6. **Avaliação de impacto (AIPD):** não parece obrigatória (não há tratamento em grande escala de dados sensíveis), mas vale a pena registar esta análise formalmente no registo de atividades de tratamento.
