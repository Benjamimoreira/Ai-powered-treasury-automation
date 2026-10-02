"""Chatbot da dashboard: um "gestor" dos dados de tesouraria - explica o
que está a ser mostrado e vai buscar informação real através de tools,
nunca inventa números. Reutiliza o mcp_server.py já existente (mesmas
tools que um cliente MCP como o Claude Desktop veria) como fonte de
ferramentas, ligado via huggingface_hub.Agent ao mesmo modelo local
(Ollama) usado em llm_resolver.py - evita reinventar um loop de
tool-calling, e nenhum dado sai da máquina.

Só tools de leitura são permitidas (FERRAMENTAS_PERMITIDAS): o chatbot
nunca reconcilia nem resolve nada sozinho, isso continua a ser feito
manualmente pelo utilizador nas outras abas da dashboard."""
import os
import sys

from huggingface_hub import Agent
from huggingface_hub.inference._generated.types.chat_completion import ChatCompletionInputMessage

# ChatCompletionInputMessage declara tool_calls para qualquer role (default
# None), e o mcp_client da huggingface_hub usa-a também para as mensagens
# role="tool" - ficam sempre com "tool_calls": null no JSON enviado. Há
# servidores compatíveis com OpenAI que validam o schema de forma estrita e
# rejeitam esse campo em mensagens role="tool" (erro 400). Removemos a chave
# quando vem a None - inofensivo para qualquer backend, já que omitir o
# campo e tê-lo a null significam o mesmo.
_parse_obj_as_instance_original = ChatCompletionInputMessage.parse_obj_as_instance


def _parse_obj_as_instance_sem_tool_calls_nulo(data):
    instancia = _parse_obj_as_instance_original(data)
    if instancia.get("tool_calls") is None:
        instancia.pop("tool_calls", None)
    return instancia


ChatCompletionInputMessage.parse_obj_as_instance = classmethod(
    lambda cls, data: _parse_obj_as_instance_sem_tool_calls_nulo(data)
)

FERRAMENTAS_PERMITIDAS = [
    "consultar_saldo_tool",
    "movimentos_do_dia_tool",
    "auditoria_dia_tool",
    "listar_ambiguos_tool",
    "saldo_total_tool",
    "listar_saldos_tool",
    "listar_empresas_tool",
    "previsao_saldo_tool",
    "avaliar_previsao_tool",
    "ranking_risco_tool",
    "estado_scripts_tool",
    "faturas_recebidas_tool",
    "anomalias_do_dia_tool",
]

PROMPT_SISTEMA = """És o assistente da dashboard de tesouraria. O teu \
papel é explicar os dados apresentados e ir buscar informação real \
através das ferramentas disponíveis - nunca inventes números nem \
"lembres-te" de um valor sem o teres consultado.

Regras:
- Responde sempre em português de Portugal.
- Todos os valores são em euros (€), nunca noutra moeda. Copia os números \
exatamente como vêm das ferramentas, sem acrescentar nem tirar dígitos, e \
escreve-os no formato português: 233 203,52 €.
- A comparação de nomes de empresa nas ferramentas é exata (ignora \
LDA/SA mas não é parcial) - se não tiveres a certeza do nome completo \
de uma empresa, usa primeiro `listar_empresas_tool` para veres os \
nomes reais antes de chamares outra ferramenta com esse nome.
- Se uma ferramenta não devolver dados, diz isso claramente ao \
utilizador em vez de adivinhar ou inventar um valor.
- Só tens ferramentas de leitura. Nunca sugiras nem finjas que \
consegues reconciliar um dia ou resolver um caso ambíguo - isso só se \
faz manualmente nas abas "Reconciliação"/"Ambíguos" da dashboard.
- Previsões: usa `previsao_saldo_tool` (sem empresa = grupo inteiro). \
Apresenta sempre o saldo previsto junto com o intervalo provável - a \
previsão não adivinha movimentos grandes não planeados, e o intervalo \
mostra quanto o saldo costuma mexer. Os recebimentos do comercial são \
um cenário à parte, não a previsão. Se perguntarem quão fiável é, usa \
`avaliar_previsao_tool`.
"""

RAIZ_PROJETO = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
MCP_SERVER_PATH = os.path.join(RAIZ_PROJETO, "mcp_server.py")


def criar_agent() -> Agent:
    """Constrói o Agent ligado ao mcp_server.py local, restrito às tools
    de leitura, com o modelo local do Ollama (OLLAMA_URL/OLLAMA_MODEL_ID -
    os mesmos de llm_resolver)."""
    servers = [
        {
            "type": "stdio",
            "command": sys.executable,
            "args": [MCP_SERVER_PATH],
            "cwd": RAIZ_PROJETO,
            # sem isto o cliente MCP só passa PATH/HOME ao subprocesso: sem
            # DATABASE_URL o mcp_server.py abria um SQLite vazio em vez do
            # Postgres do container ("tabela saldos_diarios não existe")
            "env": dict(os.environ),
            "allowed_tools": FERRAMENTAS_PERMITIDAS,
        }
    ]

    from app.services.llm_resolver import FORNECEDORES

    config = FORNECEDORES["ollama"]
    return Agent(
        model=os.environ.get(config["modelo_env"]) or config["modelo"],
        base_url=os.environ.get(config["url_env"]) or config["url"],
        # o Ollama não pede chave, mas o cliente OpenAI exige uma
        api_key="ollama",
        servers=servers, prompt=PROMPT_SISTEMA,
    )


def acumular_resposta(eventos) -> dict:
    """Interpreta a sequência de eventos devolvida por agent.run(): junta
    os pedaços de texto da resposta final (as rondas em que o modelo só
    pede tools não emitem texto, por isso concatenar tudo dá exatamente
    o texto final) e regista o nome de cada tool chamada pelo caminho.
    Função pura e isolada precisamente para os testes poderem chamá-la
    com uma lista fake de eventos, sem precisar de subprocesso nem rede."""
    partes_resposta = []
    ferramentas_usadas = []

    for evento in eventos:
        if getattr(evento, "role", None) == "tool":
            nome = getattr(evento, "name", None)
            if nome:
                ferramentas_usadas.append(nome)
            continue

        choices = getattr(evento, "choices", None)
        if not choices:
            continue
        delta = choices[0].delta
        if delta and delta.content:
            partes_resposta.append(delta.content)

    return {
        "resposta": "".join(partes_resposta).strip(),
        "ferramentas_usadas": ferramentas_usadas,
    }


def _passos_ferramentas(mensagens: list) -> list:
    """[(nome, argumentos, resultado)] das ferramentas chamadas nas
    mensagens de uma pergunta: os argumentos vêm dos tool_calls do
    assistente, o resultado da mensagem role="tool" com o mesmo id."""
    argumentos = {}
    for m in mensagens:
        m = dict(m)
        for chamada in m.get("tool_calls") or []:
            chamada = dict(chamada)
            funcao = dict(chamada.get("function") or {})
            argumentos[chamada.get("id")] = funcao.get("arguments")
    passos = []
    for m in mensagens:
        m = dict(m)
        if m.get("role") == "tool":
            passos.append((m.get("name"), argumentos.get(m.get("tool_call_id")), str(m.get("content") or "")[:4000]))
    return passos


async def perguntar(agent: Agent, pergunta: str) -> dict:
    """Faz a pergunta ao agent (conversa acumulada em agent.messages) e
    devolve a resposta final + as tools usadas. Cada pergunta fica no
    LangSmith como um trace "assistente", com um passo por ferramenta MCP
    (nome, argumentos e resultado) - o cliente do Agent não passa pelo
    tracing de llm_tracing.span_llm."""
    from app.services.llm_tracing import registar_passo, run_cadeia

    inicio = len(agent.messages)
    with run_cadeia("assistente", {"pergunta": pergunta},
                    modelo=os.environ.get("OLLAMA_MODEL_ID", "qwen2.5:3b")) as run:
        eventos = [evento async for evento in agent.run(pergunta)]
        resultado = acumular_resposta(eventos)
        for nome, argumentos, conteudo in _passos_ferramentas(agent.messages[inicio:]):
            registar_passo(run, nome or "ferramenta", "tool", {"argumentos": argumentos}, {"resultado": conteudo})
        if run is not None:
            run.end(outputs=resultado)
    return resultado
