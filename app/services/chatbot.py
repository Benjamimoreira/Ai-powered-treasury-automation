"""Chatbot da dashboard: um "gestor" dos dados de tesouraria - explica o
que está a ser mostrado e vai buscar informação real através de tools,
nunca inventa números. Reutiliza o mcp_server.py já existente (mesmas
tools que um cliente MCP como o Claude Desktop veria) como fonte de
ferramentas, ligado via huggingface_hub.Agent ao mesmo modelo/router HF
já usado em llm_resolver.py - evita reinventar um loop de tool-calling.

Só tools de leitura são permitidas (FERRAMENTAS_PERMITIDAS): o chatbot
nunca reconcilia nem resolve nada sozinho, isso continua a ser feito
manualmente pelo utilizador nas outras abas da dashboard."""
import os
import sys

from huggingface_hub import Agent
from huggingface_hub.inference._generated.types.chat_completion import ChatCompletionInputMessage

# ChatCompletionInputMessage declara tool_calls para qualquer role (default
# None), e o mcp_client da huggingface_hub usa-a também para as mensagens
# role="tool" - ficam sempre com "tool_calls": null no JSON enviado. O
# router da HuggingFace tolera isso, mas a Groq (chamada diretamente via
# base_url em vez de provider="groq") valida o schema de forma estrita e
# rejeita esse campo em mensagens role="tool" com erro 400. Removemos a
# chave quando vem a None - inofensivo para qualquer backend, já que
# omitir o campo e tê-lo a null significam o mesmo (mensagem sem chamadas
# de ferramenta associadas).
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
    "anomalias_do_dia_tool",
]

PROMPT_SISTEMA = """És o assistente da dashboard de tesouraria. O teu \
papel é explicar os dados apresentados e ir buscar informação real \
através das ferramentas disponíveis - nunca inventes números nem \
"lembres-te" de um valor sem o teres consultado.

Regras:
- Responde sempre em português.
- A comparação de nomes de empresa nas ferramentas é exata (ignora \
LDA/SA mas não é parcial) - se não tiveres a certeza do nome completo \
de uma empresa, usa primeiro `listar_empresas_tool` para veres os \
nomes reais antes de chamares outra ferramenta com esse nome.
- Se uma ferramenta não devolver dados, diz isso claramente ao \
utilizador em vez de adivinhar ou inventar um valor.
- Só tens ferramentas de leitura. Nunca sugiras nem finjas que \
consegues reconciliar um dia ou resolver um caso ambíguo - isso só se \
faz manualmente nas abas "Reconciliação"/"Ambíguos" da dashboard.
"""

RAIZ_PROJETO = os.path.dirname(os.path.dirname(os.path.dirname(__file__)))
MCP_SERVER_PATH = os.path.join(RAIZ_PROJETO, "mcp_server.py")


def criar_agent() -> Agent:
    """Constrói o Agent ligado ao mcp_server.py local, restrito às tools
    de leitura. Usa o Groq (gratuito) se GROQ_API_KEY estiver definido;
    caso contrário cai para a HuggingFace (HF_TOKEN) - mesma lógica de
    llm_resolver.chamar_llm. Levanta RuntimeError se nenhum estiver
    definido."""
    servers = [
        {
            "type": "stdio",
            "command": sys.executable,
            "args": [MCP_SERVER_PATH],
            "cwd": RAIZ_PROJETO,
            "allowed_tools": FERRAMENTAS_PERMITIDAS,
        }
    ]

    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        # base_url directo à Groq - ver nota em llm_resolver.chamar_llm sobre
        # porque não usamos provider="groq" (catálogo da HF é mais limitado).
        modelo = os.environ.get("GROQ_MODEL_ID", "llama-3.1-8b-instant")
        return Agent(
            model=modelo, base_url="https://api.groq.com/openai/v1", api_key=groq_key,
            servers=servers, prompt=PROMPT_SISTEMA,
        )

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "Nem GROQ_API_KEY nem HF_TOKEN estão definidos - cria/edita o "
            "ficheiro .env (a partir de .env.example) com um dos dois."
        )
    modelo = os.environ.get("HF_MODEL_ID", "Qwen/Qwen2.5-72B-Instruct")
    return Agent(
        model=modelo, api_key=token,
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


async def perguntar(agent: Agent, pergunta: str) -> dict:
    """Faz a pergunta ao agent (conversa acumulada em agent.messages) e
    devolve a resposta final + as tools usadas."""
    eventos = [evento async for evento in agent.run(pergunta)]
    return acumular_resposta(eventos)
