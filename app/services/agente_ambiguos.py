"""Agente de investigação de casos ambíguos (LangGraph).

A sugestão simples (llm_resolver.sugerir_resolucao) faz uma chamada com o
que já está no caso. O agente investiga antes de recomendar, como faria
quem está na tesouraria: vê como movimentos parecidos desta empresa foram
imputados, procura movimentos com o mesmo descritivo e faturas recebidas
com o mesmo valor, e se as provas não chegarem pede histórico mais longo.
No fim prepara um dossier (recomendação, confiança, provas, alertas) para
a pessoa decidir.

    decidir_sem_llm --(decidiu)--> verificar -> fim
          \\--(não)--> recolher_provas -> analisar --(precisa de mais?)--> alargar_historico -> analisar
                                                     \\-> verificar -> fim

O primeiro passo são regras baratas (app/services/resolucao_regras.py): a
triagem pelo texto não consulta nada; o histórico só é lido se a triagem
não decidir; e o LLM só é chamado se as regras não decidirem (empate,
sinais contraditórios ou nenhum sinal).

Nunca resolve o caso: "verificar" são regras fixas (o id recomendado tem de
existir entre os candidatos, confiança baixa obriga a revisão, a decisão
fica sempre pendente de aprovação humana) - é aqui que fica a supervisão
humana do AI Act (ver docs/GOVERNANCA_IA.md).

As ferramentas do agente vêm de um ContextoCaso, com duas implementações:
a base de dados real (de_bd) e um caso do conjunto de avaliação
(de_caso_avaliacao) - assim o agente é avaliado exatamente com o mesmo
código que corre em produção (app/evals/avaliar.py --estrategia agente)."""
import json
import re
from dataclasses import dataclass, field
from datetime import timedelta
from typing import Callable, Optional, TypedDict

from langgraph.graph import END, START, StateGraph

from app.services.llm_resolver import chamar_llm_detalhado, interpretar_resposta
from app.services.llm_tracing import custo_estimado
from app.services.resolucao_regras import decidir_sem_llm, palavras_significativas

N_HISTORICO = 6
N_HISTORICO_ALARGADO = 25
N_PARECIDOS = 5
MAX_RONDAS = 2
CONFIANCAS = ("alta", "media", "baixa")
_palavras = palavras_significativas


# ---------------------------------------------------------------------------
# Ferramentas (o que o agente pode consultar)
# ---------------------------------------------------------------------------


@dataclass
class ContextoCaso:
    movimento: dict
    candidatos: list
    historico: Callable[[int], list]
    movimentos_parecidos: Callable[[str, int], list]
    faturas: Callable[[], list] = field(default=lambda: [])

    @classmethod
    def de_caso_avaliacao(cls, caso: dict) -> "ContextoCaso":
        historico = caso.get("historico_entidade") or []
        parecidos = caso.get("movimentos_parecidos") or []
        return cls(caso["movimento"], caso["candidatos"], lambda n: historico[-n:], lambda descricao, n: parecidos[-n:])

    @classmethod
    def de_bd(cls, db, caso) -> "ContextoCaso":
        from app.db.models import FaturaRecebida, LinhaMapa, MovimentoBancario
        from app.services.llm_resolver import historico_da_entidade, pares_movimento_linha
        from app.services.reconciliador import chave_empresa

        mov = db.get(MovimentoBancario, caso.movimento_id)
        movimento = {"dia": caso.dia.isoformat(), "empresa": caso.empresa, "valor": caso.valor,
                     "descricao": mov.descricao if mov else ""}
        candidatos = [
            {"id": l.id, "linha": l.linha, "tipo": l.tipo, "empresa": l.empresa, "previsto": l.previsto,
             "imputacao": l.imputacao, "descricao": l.descricao}
            for l in db.query(LinhaMapa).filter(LinhaMapa.id.in_(caso.candidatos or [])).all()
        ]
        alvo = chave_empresa(caso.empresa)

        def parecidos(descricao, n):
            palavras = _palavras(descricao)
            return [
                {"dia": m.dia.isoformat(), "descricao": m.descricao, "valor": m.valor,
                 "imputacao_no_mapa": l.imputacao or l.descricao}
                for l, m in sorted(pares_movimento_linha(db, ate=caso.dia), key=lambda p: p[1].dia)
                if chave_empresa(m.empresa) == alvo and m.dia < caso.dia and palavras & _palavras(m.descricao)
            ][-n:]

        def faturas():
            if caso.valor >= 0:
                return []  # faturas recebidas são de compras - só explicam pagamentos
            valor = abs(caso.valor)
            resultado = []
            for f in db.query(FaturaRecebida).filter(
                FaturaRecebida.dia >= caso.dia - timedelta(days=45), FaturaRecebida.dia <= caso.dia,
            ).all():
                try:
                    v = float(str(f.valor_fatura).replace("€", "").replace(".", "").replace(",", ".").strip())
                except ValueError:
                    continue
                if abs(v - valor) <= 0.01 * valor:
                    resultado.append({"dia": f.dia.isoformat(), "fornecedor": f.fornecedor, "empresa": f.empresa,
                                      "valor_fatura": f.valor_fatura, "assunto": f.assunto})
            return resultado[:5]

        return cls(movimento, candidatos, lambda n: historico_da_entidade(db, caso.empresa, caso.dia, n),
                   parecidos, faturas)


# ---------------------------------------------------------------------------
# Grafo
# ---------------------------------------------------------------------------


class Estado(TypedDict, total=False):
    contexto: ContextoCaso
    fornecedor: Optional[str]
    modelo: Optional[str]
    usar_regras: bool
    decisao_regras: dict
    provas: dict
    ronda: int
    analise: dict
    passos: list
    uso: dict
    dossier: dict


def _uso_vazio() -> dict:
    return {"tokens_entrada": 0, "tokens_saida": 0, "modelo": None, "chamadas_llm": 0}


def _decidir_sem_llm(estado: Estado) -> dict:
    ctx = estado["contexto"]
    if not estado.get("usar_regras", True):
        return {"passos": [], "uso": _uso_vazio(), "decisao_regras": {"decidiu": False}}
    decisao = decidir_sem_llm(ctx.movimento, ctx.candidatos, lambda: ctx.historico(N_HISTORICO_ALARGADO))
    registo = {"decidiu": decisao.decidiu, "linha_id": decisao.linha_id, "fonte": decisao.fonte,
               "motivo": decisao.motivo, "sinais": decisao.sinais}
    passo = (f"decidir_sem_llm: {'decidiu' if decisao.decidiu else 'não decidiu'} ({decisao.fonte or '-'}) - {decisao.motivo}"
             + ("" if decisao.consultou_historico else " [histórico não consultado]"))
    atualizacao = {"decisao_regras": registo, "passos": [passo], "uso": _uso_vazio()}
    if decisao.decidiu:
        atualizacao["analise"] = {"linha_id": decisao.linha_id, "confianca": "alta",
                                  "justificacao": f"[regra: {decisao.fonte}] {decisao.motivo}"}
        atualizacao["provas"] = {"historico_entidade": [], "movimentos_parecidos": [], "faturas_mesmo_valor": [],
                                 "regras": registo}
    return atualizacao


def _regras_decidiram(estado: Estado) -> str:
    return "verificar" if estado["decisao_regras"].get("decidiu") else "recolher_provas"


def _recolher_provas(estado: Estado) -> dict:
    ctx = estado["contexto"]
    provas = {
        "historico_entidade": ctx.historico(N_HISTORICO),
        "movimentos_parecidos": ctx.movimentos_parecidos(ctx.movimento["descricao"], N_PARECIDOS),
        "faturas_mesmo_valor": ctx.faturas(),
    }
    passo = (f"recolher_provas: {len(provas['historico_entidade'])} movimentos no histórico, "
             f"{len(provas['movimentos_parecidos'])} parecidos, {len(provas['faturas_mesmo_valor'])} faturas")
    return {"provas": provas, "ronda": 0, "passos": estado["passos"] + [passo]}


def _texto_provas(provas: dict) -> str:
    def lista(itens, formato):
        return "\n".join(formato(i) for i in itens) or "(nada encontrado)"

    return (
        "Histórico desta empresa (descritivo do banco -> imputação no Mapa):\n"
        + lista(provas["historico_entidade"], lambda h: f"- \"{h['descricao']}\" ({h['valor']:.2f}) -> \"{h['imputacao_no_mapa']}\"")
        + "\n\nMovimentos anteriores com descritivo parecido ao deste:\n"
        + lista(provas["movimentos_parecidos"], lambda h: f"- {h['dia']} \"{h['descricao']}\" -> \"{h['imputacao_no_mapa']}\"")
        + "\n\nFaturas recebidas com o mesmo valor (últimos 45 dias):\n"
        + lista(provas["faturas_mesmo_valor"], lambda f: f"- {f['dia']} {f['fornecedor']} ({f['valor_fatura']}): {f['assunto']}")
    )


def _analisar(estado: Estado) -> dict:
    ctx, provas = estado["contexto"], estado["provas"]
    mov = ctx.movimento
    candidatos = "\n".join(
        f"- [id {c['id']}] {c['tipo']}, imputação: {c.get('imputacao') or '(vazia)'}, descrição: {c.get('descricao') or '(vazia)'}"
        for c in ctx.candidatos
    )
    pode_pedir = estado["ronda"] < MAX_RONDAS - 1
    # fora da f-string: o Python 3.11 (CI e imagem Docker) não aceita
    # barras invertidas dentro das expressões {...} de uma f-string
    regra_pedir_mais = (
        '- Se precisares de mais histórico desta empresa para decidir, responde precisa_de: "historico_alargado".'
        if pode_pedir else ""
    )
    campo_pedir_mais = ', "precisa_de": null' if pode_pedir else ""
    prompt = f"""És um analista de tesouraria a investigar um movimento bancário que \
bate com várias linhas do Mapa de Pagamentos e Recebimentos (mesma empresa, \
mesmo valor). Usa as provas para decidir qual linha corresponde ao \
movimento, ou se nenhuma corresponde (movimento novo).

Movimento: {mov['empresa']} | dia {mov['dia']} | descritivo do banco: "{mov['descricao']}" | {mov['valor']:.2f} EUR

Linhas candidatas:
{candidatos}

{_texto_provas(provas)}

Regras:
- Escolhe uma linha só se as provas a ligarem ao descritivo do movimento \
(mesma entidade, ou o histórico mostra este descritivo imputado assim).
- Se nenhuma linha tiver ligação ao movimento, responde linha_id null.
{regra_pedir_mais}

Responde APENAS com JSON:
{{"linha_id": <id ou null>, "confianca": "alta|media|baixa", "justificacao": "<curta, cita as provas>"{campo_pedir_mais}}}
"""
    resposta = chamar_llm_detalhado(prompt, estado.get("fornecedor"), estado.get("modelo"),
                                    nome_span="agente_analisar", formato_json=True, ronda=estado["ronda"])
    try:
        analise = json.loads(re.search(r"\{.*\}", resposta.texto, re.DOTALL).group(0))
    except (AttributeError, json.JSONDecodeError):
        analise = {"linha_id": None, "confianca": "baixa", "justificacao": resposta.texto, "invalida": True}
    uso = dict(estado["uso"])
    uso["tokens_entrada"] += resposta.tokens_entrada
    uso["tokens_saida"] += resposta.tokens_saida
    uso["chamadas_llm"] = uso.get("chamadas_llm", 0) + 1
    uso["modelo"] = resposta.modelo
    uso["fornecedor"] = resposta.fornecedor
    passo = f"analisar (ronda {estado['ronda'] + 1}): linha {analise.get('linha_id')}, confiança {analise.get('confianca')}"
    if analise.get("precisa_de"):
        passo += f", pediu {analise['precisa_de']}"
    return {"analise": analise, "uso": uso, "passos": estado["passos"] + [passo]}


def _precisa_de_mais(estado: Estado) -> str:
    if estado["analise"].get("precisa_de") == "historico_alargado" and estado["ronda"] < MAX_RONDAS - 1:
        return "alargar_historico"
    return "verificar"


def _alargar_historico(estado: Estado) -> dict:
    ctx = estado["contexto"]
    provas = dict(estado["provas"])
    provas["historico_entidade"] = ctx.historico(N_HISTORICO_ALARGADO)
    provas["movimentos_parecidos"] = ctx.movimentos_parecidos(ctx.movimento["descricao"], N_PARECIDOS * 2)
    return {"provas": provas, "ronda": estado["ronda"] + 1,
            "passos": estado["passos"] + [f"alargar_historico: {len(provas['historico_entidade'])} movimentos"]}


def _verificar(estado: Estado) -> dict:
    """Regras fixas, sem LLM: é aqui que se garante que o dossier nunca
    recomenda uma linha que não existe e que a decisão fica humana."""
    ctx, analise = estado["contexto"], estado["analise"]
    ids = [c["id"] for c in ctx.candidatos]
    interpretado = interpretar_resposta(json.dumps({"linha_id": analise.get("linha_id"),
                                                    "justificacao": analise.get("justificacao")}), ids)
    confianca = analise.get("confianca") if analise.get("confianca") in CONFIANCAS else "baixa"
    alertas = []
    if analise.get("invalida"):
        alertas.append("A resposta do modelo não veio em JSON válido - sem recomendação.")
    elif not interpretado["valida"]:
        alertas.append(f"O modelo recomendou um id que não está entre os candidatos ({analise.get('linha_id')}) - descartado.")
        confianca = "baixa"
    decidiu_por_regras = (estado.get("decisao_regras") or {}).get("decidiu")
    if not decidiu_por_regras and not estado["provas"]["historico_entidade"] and not estado["provas"]["movimentos_parecidos"]:
        alertas.append("Sem histórico desta empresa - a recomendação assenta só nos textos das linhas.")
    if confianca == "baixa":
        alertas.append("Confiança baixa: revisão humana obrigatória antes de resolver.")

    uso = dict(estado["uso"])
    uso["custo_usd"] = custo_estimado(uso.get("modelo") or "", uso["tokens_entrada"], uso["tokens_saida"])
    dossier = {
        "linha_id_recomendada": interpretado["linha_id"],
        "confianca": confianca,
        "resumo": analise.get("justificacao"),
        "candidatos": ctx.candidatos,
        "provas": estado["provas"],
        "alertas": alertas,
        "passos": estado["passos"] + ["verificar: regras fixas aplicadas"],
        "uso": uso,
        # o agente prepara; quem decide é a pessoa (POST /ambiguos/{id}/resolver)
        "decidido_por": f"regras ({estado['decisao_regras']['fonte']})" if decidiu_por_regras else "llm",
        "decisao": "pendente de aprovação humana",
    }
    return {"dossier": dossier}


def construir_grafo():
    grafo = StateGraph(Estado)
    grafo.add_node("decidir_sem_llm", _decidir_sem_llm)
    grafo.add_node("recolher_provas", _recolher_provas)
    grafo.add_node("analisar", _analisar)
    grafo.add_node("alargar_historico", _alargar_historico)
    grafo.add_node("verificar", _verificar)
    grafo.add_edge(START, "decidir_sem_llm")
    grafo.add_conditional_edges("decidir_sem_llm", _regras_decidiram, ["verificar", "recolher_provas"])
    grafo.add_edge("recolher_provas", "analisar")
    grafo.add_conditional_edges("analisar", _precisa_de_mais, ["alargar_historico", "verificar"])
    grafo.add_edge("alargar_historico", "analisar")
    grafo.add_edge("verificar", END)
    return grafo.compile()


_GRAFO = None


def investigar(contexto: ContextoCaso, fornecedor: str = None, modelo: str = None, usar_regras: bool = True) -> dict:
    """`usar_regras=False` salta as regras e vai sempre ao LLM (só para a
    avaliação comparar as duas abordagens)."""
    global _GRAFO
    if _GRAFO is None:
        _GRAFO = construir_grafo()
    estado = _GRAFO.invoke({"contexto": contexto, "fornecedor": fornecedor, "modelo": modelo, "usar_regras": usar_regras},
                           config={"run_name": "agente_ambiguos", "metadata": {"usar_regras": usar_regras}})
    dossier = estado["dossier"]
    return {**dossier, "passos": dossier["passos"]}


def investigar_caso(db, caso_id: int, fornecedor: str = None, modelo: str = None):
    """Investiga um caso ambíguo real e grava o dossier (DossierAmbiguo).
    Não mexe no caso - a resolução continua a ser humana."""
    from app.db.models import CasoAmbiguo, DossierAmbiguo

    caso = db.get(CasoAmbiguo, caso_id)
    if caso is None:
        raise ValueError(f"Caso ambíguo {caso_id} não encontrado.")
    dossier = investigar(ContextoCaso.de_bd(db, caso), fornecedor, modelo)
    registo = DossierAmbiguo(
        caso_id=caso.id, fornecedor=dossier["uso"].get("fornecedor") or fornecedor or "",
        modelo=dossier["uso"].get("modelo") or "", linha_id_recomendada=dossier["linha_id_recomendada"],
        confianca=dossier["confianca"], dossier=json.loads(json.dumps(dossier, default=str)),
    )
    db.add(registo)
    db.commit()
    db.refresh(registo)
    return registo
