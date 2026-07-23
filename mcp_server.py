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
from app.services.anomalias import detetar_anomalias_do_dia
from app.services.previsao import avaliar_modelos, prever_saldo
from app.services.reconciliador import (
    auditoria_dia,
    listar_empresas,
    listar_movimentos_do_dia,
    reconciliar_dia,
    resolver_ambiguo,
)
from app.services.saldos import consultar_saldo as consultar_saldo_servico
from app.services.saldos import listar_saldos_atuais, saldo_total_geral
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


@mcp.tool()
def listar_empresas_tool() -> list:
    """Lista os nomes exatos (tal como guardados na base de dados) de
    todas as empresas com movimentos importados. A comparação de nomes
    usada por outras tools (ex. consultar_saldo_tool) ignora LDA/SA mas
    exige o resto do nome exato - usa esta tool primeiro sempre que não
    tiveres a certeza do nome completo de uma empresa."""
    db = SessionLocal()
    try:
        return listar_empresas(db)
    finally:
        db.close()


@mcp.tool()
def saldo_total_tool() -> dict:
    """Soma o último saldo conhecido de cada entidade - visão geral,
    não de um único dia (nem todos os dias têm leitura de todas as
    contas)."""
    db = SessionLocal()
    try:
        return saldo_total_geral(db)
    finally:
        db.close()


@mcp.tool()
def listar_saldos_tool() -> list:
    """Último saldo conhecido de cada entidade - para rankings/
    comparações entre contas."""
    db = SessionLocal()
    try:
        return [
            {
                "dia": s.dia.isoformat(),
                "entidade": s.entidade,
                "saldo_contabilistico": s.saldo_contabilistico,
                "saldo_disponivel": s.saldo_disponivel,
            }
            for s in listar_saldos_atuais(db)
        ]
    finally:
        db.close()


@mcp.tool()
def previsao_saldo_tool(empresa: str, dias: int = 7) -> dict:
    """Previsão do saldo contabilístico dos próximos dias de uma
    empresa (nome exato - ver listar_empresas_tool), com vários
    modelos de séries temporais para comparação lado a lado."""
    db = SessionLocal()
    try:
        return prever_saldo(db, empresa, dias)
    finally:
        db.close()


@mcp.tool()
def avaliar_previsao_tool(empresa: str, dias_teste: int = 5) -> dict:
    """Avaliação treino/teste dos modelos de previsão de saldo de uma
    empresa (nome exato - ver listar_empresas_tool): retira os últimos
    `dias_teste` dias, treina cada modelo só com o resto, e compara com
    o valor real (RMSE) - responde a "qual modelo acerta mais"."""
    db = SessionLocal()
    try:
        return avaliar_modelos(db, empresa, dias_teste)
    finally:
        db.close()


@mcp.tool()
def anomalias_do_dia_tool(dia: str) -> list:
    """Lista os movimentos bancários de um dia (formato AAAA-MM-DD)
    cujo valor foge do padrão habitual da própria empresa (deteção via
    Isolation Forest, por empresa). Empresas com histórico insuficiente
    são ignoradas."""
    db = SessionLocal()
    try:
        resultado = detetar_anomalias_do_dia(db, date.fromisoformat(dia))
        return [
            {**item, "dia": item["dia"].isoformat()}
            for item in resultado
        ]
    finally:
        db.close()


if __name__ == "__main__":
    mcp.run()
