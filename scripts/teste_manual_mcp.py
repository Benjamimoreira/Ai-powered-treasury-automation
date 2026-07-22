"""Teste manual (não faz parte da suite automática): liga-se a sério ao
mcp_server.py via protocolo MCP (stdio), como um agente LLM faria, para
confirmar que o servidor arranca e responde corretamente ao protocolo -
não só que as funções Python funcionam quando chamadas diretamente."""
import asyncio
import sys
from pathlib import Path

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client

PASTA_PROJETO = Path(__file__).resolve().parent.parent


async def main():
    parametros = StdioServerParameters(
        command=sys.executable,
        args=[str(PASTA_PROJETO / "mcp_server.py")],
        cwd=str(PASTA_PROJETO),
    )
    async with stdio_client(parametros) as (leitura, escrita):
        async with ClientSession(leitura, escrita) as sessao:
            await sessao.initialize()

            tools = await sessao.list_tools()
            print("Tools disponíveis:", [t.name for t in tools.tools])

            resultado = await sessao.call_tool("auditoria_dia_tool", {"dia": "2026-07-21"})
            print("auditoria_dia_tool(2026-07-21) ->", resultado.content[0].text)


if __name__ == "__main__":
    asyncio.run(main())
