"""Servidor MCP que expõe a lógica de reconciliação de tesouraria como
"tools" que um agente LLM (Claude, etc.) pode chamar diretamente, sem
precisar de correr scripts manualmente ou saber os endpoints da API.

Correr com:
    mcp dev mcp_server.py          (modo de desenvolvimento, com inspector)
ou configurar no cliente MCP (ex. Claude Desktop) para correr:
    python mcp_server.py
"""
from datetime import date
from typing import Optional

from mcp.server.fastmcp import FastMCP

from app.db.session import SessionLocal
from app.services.reconciliador import (
    auditoria_dia,
    listar_movimentos_do_dia,
    reconciliar_dia,
    resolver_ambiguo,
)
from app.services.saldos import consultar_saldo as consultar_saldo_servico
from app.db.models import CasoAmbiguo

mcp = FastMCP("tesouraria")


@mcp.tool()
def reconciliar_dia_tool(dia: str) -> dict:
    """Corre a reconciliação de um dia (formato AAAA-MM-DD): tenta casar
    cada movimento bancário ainda não processado com uma linha do mapa de
    pagamentos/recebimentos. Devolve quantos foram casados/novos/ambíguos.
    É seguro chamar mais que uma vez - não reprocessa o que já está feito."""
    db = SessionLocal()
    try:
        return reconciliar_dia(db, date.fromisoformat(dia))
    finally:
        db.close()


@mcp.tool()
def auditoria_dia_tool(dia: str) -> dict:
    """Verificação read-only de um dia (formato AAAA-MM-DD): conta
    movimentos sem correspondência e linhas do mapa com previsto em aberto
    que ainda não bateram com nenhum movimento. Não altera nada."""
    db = SessionLocal()
    try:
        return auditoria_dia(db, date.fromisoformat(dia))
    finally:
        db.close()


@mcp.tool()
def movimentos_do_dia_tool(dia: str) -> list:
    """Lista cada movimento bancário de um dia (formato AAAA-MM-DD) com o
    seu estado atual (casado/novo/ambíguo, ou por processar). Só leitura -
    seguro chamar quantas vezes quiseres."""
    db = SessionLocal()
    try:
        return listar_movimentos_do_dia(db, date.fromisoformat(dia))
    finally:
        db.close()


@mcp.tool()
def consultar_saldo_tool(empresa: str, dia: Optional[str] = None) -> list:
    """Consulta os saldos guardados de uma empresa (nome completo ou
    parcial - a comparação ignora LDA/SA), opcionalmente filtrados por um
    dia (formato AAAA-MM-DD)."""
    db = SessionLocal()
    try:
        dia_obj = date.fromisoformat(dia) if dia else None
        resultados = consultar_saldo_servico(db, empresa, dia_obj)
        return [
            {
                "dia": s.dia.isoformat(),
                "entidade": s.entidade,
                "saldo_contabilistico": s.saldo_contabilistico,
                "saldo_disponivel": s.saldo_disponivel,
            }
            for s in resultados
        ]
    finally:
        db.close()


@mcp.tool()
def listar_ambiguos_tool() -> list:
    """Lista os casos ambíguos ainda por resolver (movimentos que bateram
    com mais que uma linha do mapa e precisam de decisão humana)."""
    db = SessionLocal()
    try:
        casos = db.query(CasoAmbiguo).filter(CasoAmbiguo.resolvido_por.is_(None)).all()
        return [
            {
                "id": c.id,
                "dia": c.dia.isoformat(),
                "empresa": c.empresa,
                "valor": c.valor,
                "candidatos": c.candidatos,
                "resolucao_sugerida": c.resolucao_sugerida,
                "justificacao_sugerida": c.justificacao_sugerida,
            }
            for c in casos
        ]
    finally:
        db.close()


@mcp.tool()
def resolver_ambiguo_tool(caso_id: int, linha_id: Optional[int], resolvido_por: str) -> dict:
    """Regista a decisão humana sobre um caso ambíguo: associa o
    movimento à linha do mapa escolhida (linha_id), ou marca como
    movimento novo se linha_id for None/null."""
    db = SessionLocal()
    try:
        caso = resolver_ambiguo(db, caso_id, linha_id, resolvido_por)
        return {
            "id": caso.id,
            "resolvido_por": caso.resolvido_por,
            "resolucao": caso.resolucao,
        }
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run()
