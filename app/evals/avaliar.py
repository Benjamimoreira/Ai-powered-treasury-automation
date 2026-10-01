"""Avaliação automática das sugestões para casos ambíguos.

Corre o conjunto evals/ambiguos.json (ver scripts/construir_conjunto_
avaliacao.py) contra um fornecedor/modelo e uma estratégia, e mede:
- exatidão (a linha escolhida é a certa; "nenhuma serve" conta quando o
  esperado é null), global e por categoria;
- respostas válidas (JSON com um id que existe entre os candidatos) e ids
  inventados - o erro mais grave, uma sugestão para uma linha que não
  existe;
- latência (média e p95), tokens e custo estimado.

Com --limiar, sai com código 1 se a exatidão ficar abaixo - é a porta de
qualidade do CI (.github/workflows/ci.yml, job "avaliacao-llm").

    python -m app.evals.avaliar --estrategia hibrida --limiar 0.70
    python -m app.evals.avaliar --prompt v1 --max-casos 20
    python -m app.evals.avaliar --estrategia hibrida      # regras primeiro, LLM só se preciso
    python -m app.evals.avaliar --estrategia agente
"""
import argparse
import json
import os
import statistics
import sys
import time
from datetime import datetime

from app.services.llm_resolver import (
    VERSAO_PROMPT,
    chamar_llm_detalhado,
    interpretar_resposta,
    montar_prompt_de_dados,
)

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONJUNTO = os.path.join(RAIZ, "evals", "ambiguos.json")
PASTA_RESULTADOS = os.path.join(RAIZ, "evals", "resultados")


def carregar_casos(caminho: str = CONJUNTO, max_casos: int = None) -> list:
    with open(caminho, encoding="utf-8") as f:
        casos = json.load(f)["casos"]
    return casos[:max_casos] if max_casos else casos


def _resolver_sugestao(caso: dict, fornecedor: str, modelo: str, versao_prompt: str) -> dict:
    prompt = montar_prompt_de_dados(caso, versao_prompt)
    resposta = chamar_llm_detalhado(
        prompt, fornecedor, modelo, nome_span="avaliacao_sugestao", caso=caso["id"], versao_prompt=versao_prompt,
    )
    return {"texto": resposta.texto, "tokens_entrada": resposta.tokens_entrada, "tokens_saida": resposta.tokens_saida,
            "latencia_s": resposta.latencia_s, "custo_usd": resposta.custo_usd, "modelo": resposta.modelo,
            "chamadas_llm": 1, "decidido_por": "llm"}


def _resolver_hibrida(caso: dict, fornecedor: str, modelo: str, versao_prompt: str) -> dict:
    """Regras primeiro (app/services/resolucao_regras.py); o histórico só é
    lido se a triagem pelo texto não decidir, e o LLM só é chamado se as
    regras não decidirem - é o que corre em produção."""
    from app.services.resolucao_regras import decidir_sem_llm

    inicio = time.perf_counter()
    decisao = decidir_sem_llm(caso["movimento"], caso["candidatos"], lambda: caso.get("historico_entidade") or [])
    if decisao.decidiu:
        return {"texto": json.dumps({"linha_id": decisao.linha_id, "justificacao": decisao.motivo}),
                "tokens_entrada": 0, "tokens_saida": 0, "latencia_s": time.perf_counter() - inicio, "custo_usd": 0.0,
                "modelo": None, "chamadas_llm": 0, "decidido_por": f"regras ({decisao.fonte})"}
    return _resolver_sugestao(caso, fornecedor, modelo, versao_prompt)


def _resolver_agente(caso: dict, fornecedor: str, modelo: str, versao_prompt: str, usar_regras: bool = False) -> dict:
    from app.services.agente_ambiguos import ContextoCaso, investigar

    inicio = time.perf_counter()
    dossier = investigar(ContextoCaso.de_caso_avaliacao(caso), fornecedor=fornecedor, modelo=modelo,
                         usar_regras=usar_regras)
    return {
        "texto": json.dumps({"linha_id": dossier["linha_id_recomendada"], "justificacao": dossier["resumo"]}),
        "tokens_entrada": dossier["uso"]["tokens_entrada"], "tokens_saida": dossier["uso"]["tokens_saida"],
        "latencia_s": time.perf_counter() - inicio, "custo_usd": dossier["uso"]["custo_usd"],
        "modelo": dossier["uso"]["modelo"], "passos": dossier["passos"],
        "chamadas_llm": dossier["uso"].get("chamadas_llm", 0), "decidido_por": dossier.get("decidido_por", "llm"),
    }


def _resolver_agente_hibrido(caso: dict, fornecedor: str, modelo: str, versao_prompt: str) -> dict:
    return _resolver_agente(caso, fornecedor, modelo, versao_prompt, usar_regras=True)


ESTRATEGIAS = {
    "sugestao": _resolver_sugestao,
    "hibrida": _resolver_hibrida,
    "agente": _resolver_agente,
    "agente_hibrido": _resolver_agente_hibrido,
}


CAMPOS_PREVISAO = ("tokens_entrada", "tokens_saida", "latencia_s", "custo_usd", "modelo", "passos",
                   "chamadas_llm", "decidido_por")


def prever_caso(caso: dict, fornecedor: str, modelo: str = None, versao_prompt: str = VERSAO_PROMPT,
                estrategia: str = "sugestao") -> dict:
    """A previsão para um caso - sem ver a resposta esperada (é o "alvo" da
    experiência no LangSmith). Uma falha conta como resposta inválida, não
    pára a avaliação."""
    ids = [c["id"] for c in caso["candidatos"]]
    try:
        bruto = ESTRATEGIAS[estrategia](caso, fornecedor, modelo, versao_prompt)
        interpretado = interpretar_resposta(bruto["texto"], ids)
        erro = None
    except Exception as e:
        bruto, interpretado, erro = {}, {"valida": False, "linha_id": None, "justificacao": None}, str(e)
    return {"linha_id": interpretado["linha_id"], "valida": interpretado["valida"],
            "justificacao": interpretado["justificacao"], "erro": erro,
            **{k: bruto.get(k) for k in CAMPOS_PREVISAO}}


def _resultado(caso: dict, previsao: dict) -> dict:
    return {
        "caso": caso["id"], "categoria": caso["categoria"], "esperado": caso["esperado"],
        "obtido": previsao["linha_id"], "valida": previsao["valida"],
        "certa": previsao["valida"] and previsao["linha_id"] == caso["esperado"],
        "justificacao": previsao["justificacao"], "erro": previsao["erro"],
        **{k: previsao.get(k) for k in CAMPOS_PREVISAO},
    }


def _relatorio(resultados: list, fornecedor, modelo, versao_prompt, estrategia) -> dict:
    return {"config": {"fornecedor": fornecedor, "modelo": next((r["modelo"] for r in resultados if r["modelo"]), modelo),
                       "versao_prompt": versao_prompt, "estrategia": estrategia, "n_casos": len(resultados)},
            "metricas": metricas(resultados), "resultados": resultados}


def avaliar(casos: list, fornecedor: str, modelo: str = None, versao_prompt: str = VERSAO_PROMPT,
            estrategia: str = "sugestao", pausa_s: float = 0.0) -> dict:
    resultados = []
    for caso in casos:
        resultados.append(_resultado(caso, prever_caso(caso, fornecedor, modelo, versao_prompt, estrategia)))
        if pausa_s:
            time.sleep(pausa_s)
    return _relatorio(resultados, fornecedor, modelo, versao_prompt, estrategia)


def avaliar_no_langsmith(casos: list, caminho_conjunto: str, fornecedor: str, modelo: str = None,
                         versao_prompt: str = VERSAO_PROMPT, estrategia: str = "sugestao") -> dict:
    """Mesma avaliação, corrida como experiência no LangSmith (ver
    app/evals/langsmith_experiencias.py) - mesmas métricas e mesmo limiar."""
    from app.evals.langsmith_experiencias import correr

    config = {"fornecedor": fornecedor, "modelo": modelo, "versao_prompt": versao_prompt, "estrategia": estrategia}
    resultados = correr(casos, caminho_conjunto,
                        lambda caso: prever_caso(caso, fornecedor, modelo, versao_prompt, estrategia), config)
    return _relatorio(resultados, fornecedor, modelo, versao_prompt, estrategia)


def _p95(valores: list) -> float:
    if not valores:
        return 0.0
    ordenados = sorted(valores)
    return ordenados[min(len(ordenados) - 1, int(round(0.95 * (len(ordenados) - 1))))]


def metricas(resultados: list) -> dict:
    n = len(resultados) or 1
    latencias = [r["latencia_s"] for r in resultados if r.get("latencia_s") is not None]
    por_categoria = {}
    for cat in sorted({r["categoria"] for r in resultados}):
        da_cat = [r for r in resultados if r["categoria"] == cat]
        por_categoria[cat] = {"n": len(da_cat), "exatidao": sum(r["certa"] for r in da_cat) / len(da_cat)}
    sem_llm = [r for r in resultados if (r.get("decidido_por") or "").startswith("regras")]
    com_llm = [r for r in resultados if r not in sem_llm]
    return {
        "exatidao": sum(r["certa"] for r in resultados) / n,
        "chamadas_llm": sum(r.get("chamadas_llm") or 0 for r in resultados),
        "decididos_sem_llm": len(sem_llm) / n,
        "exatidao_sem_llm": (sum(r["certa"] for r in sem_llm) / len(sem_llm)) if sem_llm else None,
        "exatidao_com_llm": (sum(r["certa"] for r in com_llm) / len(com_llm)) if com_llm else None,
        "respostas_validas": sum(r["valida"] for r in resultados) / n,
        "erros_de_chamada": sum(1 for r in resultados if r["erro"]),
        "por_categoria": por_categoria,
        "latencia_media_s": statistics.mean(latencias) if latencias else 0.0,
        "latencia_p95_s": _p95(latencias),
        "tokens_entrada_medio": statistics.mean([r["tokens_entrada"] or 0 for r in resultados]) if resultados else 0,
        "tokens_saida_medio": statistics.mean([r["tokens_saida"] or 0 for r in resultados]) if resultados else 0,
        "custo_total_usd": sum(r["custo_usd"] or 0 for r in resultados),
    }


def resumo_texto(relatorio: dict) -> str:
    c, m = relatorio["config"], relatorio["metricas"]
    linhas = [
        f"{c['estrategia']} | {c['fornecedor']} / {c['modelo']} | prompt {c['versao_prompt']} | {c['n_casos']} casos",
        f"  exatidão {m['exatidao']:.1%}  ·  respostas válidas {m['respostas_validas']:.1%}  ·  erros de chamada {m['erros_de_chamada']}",
    ]
    linhas += [f"  - {cat}: {v['exatidao']:.1%} ({v['n']} casos)" for cat, v in m["por_categoria"].items()]
    if m.get("decididos_sem_llm"):
        linhas.append(
            f"  decididos sem LLM {m['decididos_sem_llm']:.1%} (exatidão {m['exatidao_sem_llm']:.1%})  ·  "
            f"com LLM: exatidão {m['exatidao_com_llm'] if m['exatidao_com_llm'] is not None else 0:.1%}"
        )
    linhas.append(f"  chamadas ao LLM: {m['chamadas_llm']}")
    linhas.append(
        f"  latência média {m['latencia_media_s']:.2f}s (p95 {m['latencia_p95_s']:.2f}s)  ·  "
        f"tokens {m['tokens_entrada_medio']:.0f} entrada / {m['tokens_saida_medio']:.0f} saída  ·  "
        f"custo {m['custo_total_usd']:.4f} USD"
    )
    return "\n".join(linhas)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fornecedor", default=None, help="ollama (o único suportado; omissão: LLM_FORNECEDOR ou ollama)")
    parser.add_argument("--modelo", default=None)
    parser.add_argument("--prompt", default=VERSAO_PROMPT, choices=["v1", "v2"])
    parser.add_argument("--estrategia", default="sugestao", choices=sorted(ESTRATEGIAS))
    parser.add_argument("--max-casos", type=int, default=None)
    parser.add_argument("--limiar", type=float, default=None, help="exatidão mínima (0-1); abaixo sai com código 1")
    parser.add_argument("--pausa", type=float, default=0.0, help="segundos entre casos (limites de pedidos)")
    parser.add_argument("--conjunto", default=CONJUNTO)
    parser.add_argument("--langsmith", action="store_true",
                        help="corre como experiência no LangSmith (precisa de LANGSMITH_API_KEY); "
                             "se o LangSmith falhar, avalia localmente na mesma")
    args = parser.parse_args(argv)

    from app.services.llm_resolver import fornecedor_por_omissao
    fornecedor = args.fornecedor or fornecedor_por_omissao()
    casos = carregar_casos(args.conjunto, args.max_casos)
    relatorio = None
    if args.langsmith:
        try:
            relatorio = avaliar_no_langsmith(casos, args.conjunto, fornecedor, args.modelo, args.prompt, args.estrategia)
        except Exception as e:  # a porta de qualidade nunca depende do LangSmith estar disponível
            print(f"  LangSmith indisponível ({e}) - a avaliar localmente.")
    if relatorio is None:
        relatorio = avaliar(casos, fornecedor, args.modelo, args.prompt, args.estrategia, args.pausa)
    relatorio["quando"] = datetime.now().isoformat(timespec="seconds")

    os.makedirs(PASTA_RESULTADOS, exist_ok=True)
    nome = f"{datetime.now():%Y%m%d-%H%M%S}_{args.estrategia}_{fornecedor}_{args.prompt}.json"
    with open(os.path.join(PASTA_RESULTADOS, nome), "w", encoding="utf-8") as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=1)
    print(resumo_texto(relatorio))
    print(f"  relatório: evals/resultados/{nome}")
    from app.services.llm_tracing import esvaziar
    esvaziar()

    if args.limiar is not None and relatorio["metricas"]["exatidao"] < args.limiar:
        print(f"FALHOU: exatidão {relatorio['metricas']['exatidao']:.1%} abaixo do limiar {args.limiar:.0%}")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
