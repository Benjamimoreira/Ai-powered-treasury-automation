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
from app.services.faturas import listar_faturas
from app.services.monitorizacao import listar_scripts
from app.services.previsao import listar_ranking_risco
from app.services.previsao_ancorada import backtest_previsao_ancorada, prever_saldo_ancorado
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
    que ainda não bateram com nenhum movimento. Não altera nada. Devolve só
    o resumo (contagens e somas) - para a lista detalhada de movimentos usa
    movimentos_do_dia_tool."""
    db = SessionLocal()
    try:
        resultado = auditoria_dia(db, date.fromisoformat(dia))
        return {
            "sem_match_fwd": resultado["sem_match_fwd"],
            "sem_match_rev": resultado["sem_match_rev"],
            "soma_extrato": resultado["soma_extrato"],
            "soma_mapa": resultado["soma_mapa"],
            "diferenca_extrato_mapa": resultado["diferenca_extrato_mapa"],
        }
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


MAX_FLUXOS_MCP = 25


def _fim_de_cada_semana(serie: list) -> list:
    """Último ponto de cada semana (domingo) e o último do horizonte - uma
    série de 180 pontos diários não cabe no contexto do modelo do
    Assistente e não acrescenta nada a uma resposta de gestão."""
    pontos = [p for p in serie if date.fromisoformat(p["dia"]).weekday() == 6]
    if serie and (not pontos or pontos[-1]["dia"] != serie[-1]["dia"]):
        pontos.append(serie[-1])
    return [{"dia": p["dia"], "valor": round(p["valor"], 2)} for p in pontos]


@mcp.tool()
def previsao_saldo_tool(empresa: Optional[str] = None, dias: int = 30) -> dict:
    """Previsão do saldo nos próximos `dias` dias (máx. 180) - a mesma do
    separador Forecast da dashboard. Sem `empresa`, o grupo inteiro; com
    `empresa`, o nome exato (ver listar_empresas_tool).

    Método: saldo de hoje + fluxos já conhecidos (rendas, recorrentes,
    linhas do Mapa com data futura), sem extrapolar tendências. O
    `intervalo_provavel` (80%) diz quanto o saldo costuma mexer. O
    `saldo_com_recebimentos_comercial` soma à parte os sinais/reforços/
    escrituras do índice comercial - não entra no `saldo_previsto` porque
    as saídas grandes não estão marcadas em lado nenhum. Ver
    avaliar_previsao_tool para quão fiável é."""
    db = SessionLocal()
    try:
        r = prever_saldo_ancorado(db, empresa, dias)
        banda = r["banda_incerteza"]
        com_comercial = r["previsao_com_comercial"]
        fluxos = r["fluxos_conhecidos_previstos"]
        totais_por_fonte = {}
        for f in fluxos:
            total = totais_por_fonte.setdefault(f["fonte"], {"n": 0, "total": 0.0})
            total["n"] += 1
            total["total"] = round(total["total"] + f["valor"], 2)
        return {
            "entidade": empresa or "Grupo (todas as contas)",
            "saldo_atual": round(r["saldo_partida"], 2),
            "dia_saldo_atual": r["dia_partida"],
            "dias": len(r["previsao"]),
            "saldo_previsto": round(r["previsao"][-1]["valor"], 2),
            "intervalo_provavel": (
                {"minimo": round(banda["baixa"][-1]["valor"], 2), "maximo": round(banda["alta"][-1]["valor"], 2)}
                if banda else None
            ),
            "saldo_com_recebimentos_comercial": round(com_comercial[-1]["valor"], 2) if com_comercial else None,
            "previsao_por_semana": _fim_de_cada_semana(r["previsao"]),
            "fluxos_conhecidos_por_tipo": totais_por_fonte,
            # os maiores, por dia - a lista inteira a 180 dias do grupo tem
            # centenas de linhas e não cabe no contexto do Assistente
            "maiores_fluxos_conhecidos": sorted(
                (
                    {k: f[k] for k in ("dia", "valor", "fonte", "empresa", "descricao")}
                    for f in sorted(fluxos, key=lambda f: -abs(f["valor"]))[:MAX_FLUXOS_MCP]
                ),
                key=lambda f: f["dia"],
            ),
        }
    finally:
        db.close()


@mcp.tool()
def avaliar_previsao_tool(empresa: Optional[str] = None, dias: int = 30) -> dict:
    """Quão fiável é a previsão de previsao_saldo_tool: repete-a a partir de
    até 20 datas passadas (uma por semana), só com os dados desse dia, e
    compara com o saldo real `dias` dias depois - ao lado do palpite "o
    saldo fica igual". Sem `empresa`, o grupo inteiro. Demora ~10 s."""
    db = SessionLocal()
    try:
        b = backtest_previsao_ancorada(db, dias, empresa=empresa)
        return {
            "entidade": empresa or "Grupo (todas as contas)",
            "dias": dias,
            "cortes_avaliados": len(b["cortes"]),
            "erro_medio_previsao": b["erro_medio_modelo"],
            "erro_medio_saldo_fica_igual": b["erro_medio_sem_alteracao"],
            "erro_medio_com_recebimentos_comercial": b["erro_medio_com_comercial"],
            "real_dentro_do_intervalo": b["cobertura_banda"],
        }
    finally:
        db.close()


@mcp.tool()
def ranking_risco_tool(dias: int = 30) -> list:
    """Todas as empresas por risco de liquidez (crítico primeiro): zona
    atual e prevista a `dias` dias, saldo atual e previsto, ritmo de caixa
    dos últimos 30 dias e autonomia. Crítico = o saldo cobre menos de 1
    semana de despesa média; alerta = menos de 1 mês. Demora ~40 s."""
    db = SessionLocal()
    try:
        campos = (
            "empresa", "zona_atual", "zona_prevista", "dia_risco", "saldo_atual", "saldo_previsto_fim",
            "taxa_diaria_liquida", "dias_autonomia_tendencia", "dias_autonomia_despesa",
        )
        return [{c: r[c] for c in campos} for r in listar_ranking_risco(db, dias)]
    finally:
        db.close()


@mcp.tool()
def estado_scripts_tool() -> list:
    """Estado dos scripts de automação agendados (preenchimento do Mapa,
    Mapa de Saldos, recolha de faturas, ...): último resultado, hora da
    última execução, último erro e se está atrasado face à hora esperada."""
    db = SessionLocal()
    try:
        return listar_scripts(db)
    finally:
        db.close()


@mcp.tool()
def faturas_recebidas_tool(
    pesquisa: Optional[str] = None, desde: Optional[str] = None, ate: Optional[str] = None, limite: int = 50,
) -> list:
    """Faturas recebidas em faturas@vidor.pt. `pesquisa` procura em empresa,
    fornecedor, NIF, assunto, remetente ou valor, em qualquer dia; `desde`/
    `ate` (AAAA-MM-DD) limitam o período. Mais recentes primeiro, no
    máximo `limite` (até 200)."""
    db = SessionLocal()
    try:
        faturas = listar_faturas(
            db,
            desde=date.fromisoformat(desde) if desde else None,
            ate=date.fromisoformat(ate) if ate else None,
            pesquisa=pesquisa or None,
            limit=max(1, min(int(limite), 200)),
        )
        from app.db.models import FaturaRecebida
        from app.services.faturas import normalizar_fornecedores

        nomes = normalizar_fornecedores(faturas, db.query(FaturaRecebida).all(), listar_empresas(db))
        return [
            {
                "dia": f.dia.isoformat(), "hora": f.hora, "empresa": f.empresa, "fornecedor": nome,
                "nif_fornecedor": f.nif_fornecedor, "valor_fatura": f.valor_fatura, "assunto": f.assunto,
                "tem_pdf": bool(f.pdf_relativo),
            }
            for f, (nome, _) in zip(faturas, nomes)
        ]
    finally:
        db.close()


@mcp.tool()
def movimentos_empresa_tool(empresa: str, desde: str, ate: str) -> dict:
    """Movimentos bancários de UMA empresa num período (desde/ate em
    AAAA-MM-DD): total recebido, total pago, líquido, nº de movimentos e os
    10 maiores. Responde a "quanto pagou/recebeu a empresa X em agosto".
    O nome pode ser abreviado ("J. Pinto", "Habiserve Invest."); se servir
    a várias empresas, devolve as candidatas para escolher."""
    from app.services.previsao_ancorada import _nome_abreviado_corresponde
    from app.services.reconciliador import chave_empresa, listar_movimentos_da_empresa

    db = SessionLocal()
    try:
        empresas = listar_empresas(db)
        exatas = [e for e in empresas if chave_empresa(e) == chave_empresa(empresa)]
        candidatas = exatas or [e for e in empresas if _nome_abreviado_corresponde(empresa, e)]
        if not candidatas:
            return {"erro": f"Nenhuma empresa corresponde a '{empresa}'.", "empresas": empresas}
        if len(candidatas) > 1:
            # nunca escolher em silêncio ("Habiserve" são 4 empresas)
            return {"erro": f"'{empresa}' corresponde a várias empresas - pergunta qual.", "candidatas": candidatas}
        nome = candidatas[0]
        inicio, fim = date.fromisoformat(desde), date.fromisoformat(ate)
        movimentos = [m for m in listar_movimentos_da_empresa(db, nome) if inicio <= date.fromisoformat(m["dia"]) <= fim]
        recebido = sum(m["valor"] for m in movimentos if m["valor"] > 0)
        pago = -sum(m["valor"] for m in movimentos if m["valor"] < 0)
        return {
            "empresa": nome, "desde": desde, "ate": ate, "n_movimentos": len(movimentos),
            "total_recebido": round(recebido, 2), "total_pago": round(pago, 2), "liquido": round(recebido - pago, 2),
            "maiores_movimentos": sorted(
                ({"dia": m["dia"], "descricao": m["descricao"], "valor": round(m["valor"], 2)} for m in movimentos),
                key=lambda m: -abs(m["valor"]),
            )[:10],
        }
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
