"""Avaliação da recuperação (a parte "R" do RAG), sem LLM.

A exatidão final mistura duas coisas: o contexto que o LLM recebe e o que
ele faz com isso. Aqui mede-se só a primeira. Um item do histórico é
**relevante** para uma consulta se foi imputado no Mapa à mesma rubrica que
a resposta certa ("AGUAS DE GONDOMAR" -> "Aguas" quando a certa é "Aguas")
- a relevância sai dos pares reais, ninguém teve de etiquetar nada.

Dois conjuntos:
- recuperacao (omissão, evals/recuperacao.json - ver scripts/construir_
  conjunto_recuperacao.py): cada movimento real é uma consulta contra todo
  o histórico anterior da empresa - centenas de consultas;
- ambiguos (evals/ambiguos.json): os casos da avaliação do LLM, com o
  histórico de 25 itens que lá está - poucos casos avaliáveis, mas é
  exatamente o contexto que o LLM vê nessa avaliação.

Métricas, por método (app/services/rag_historico.py) e modelo de
embeddings:
- recall@k: fração das consultas com pelo menos um relevante nos k
  primeiros (k=6 é o que vai para o prompt);
- MRR: média de 1/posição do primeiro relevante;
- precisão@6: fração dos 6 itens enviados ao LLM que são relevantes -
  quanto do contexto é sinal e quanto é ruído.

Só contam as consultas com pelo menos um relevante no histórico (nas
outras nenhum método pode acertar).

    python -m app.evals.avaliar_recuperacao
    python -m app.evals.avaliar_recuperacao --conjunto ambiguos
    python -m app.evals.avaliar_recuperacao --modelos intfloat/multilingual-e5-small all-MiniLM-L6-v2
    python -m app.evals.avaliar_recuperacao --limiar-recall 0.90     # porta de qualidade
"""
import argparse
import json
import os
import statistics
import sys
from datetime import datetime

from app.evals.avaliar import CONJUNTO as CONJUNTO_AMBIGUOS
from app.evals.avaliar import PASTA_RESULTADOS, RAIZ
from app.services import rag_historico
from app.services.reconciliador import normalizar

CONJUNTOS = {"recuperacao": os.path.join(RAIZ, "evals", "recuperacao.json"), "ambiguos": CONJUNTO_AMBIGUOS}
KS = (1, 3, 6)
K_PROMPT = 6
# imputações que não dizem nada - não servem de rubrica certa (as mesmas
# que o construtor do conjunto dos ambíguos exclui)
RUBRICAS_VAZIAS = {"", "DESCONHECO", "DESCONHECIDO", "OUTROS", "NONE"}


def _rubrica(texto) -> str:
    return normalizar(texto or "")


def consultas_de_ambiguos(dados: dict) -> list:
    consultas = []
    for caso in dados["casos"]:
        certa = next((c for c in caso["candidatos"] if c["id"] == caso["esperado"]), None)
        if certa is None:
            continue  # "nenhuma serve" - não há rubrica certa
        consultas.append({"id": caso["id"], "consulta": caso["movimento"]["descricao"],
                          "historico": caso.get("historico_entidade") or [],
                          "alvo": _rubrica(certa.get("imputacao") or certa.get("descricao"))})
    return consultas


def consultas_de_recuperacao(dados: dict) -> list:
    consultas = []
    for empresa in dados["empresas"]:
        historico = empresa["historico"]
        for i, item in enumerate(historico):
            alvo = _rubrica(item["imputacao_no_mapa"])
            if alvo in RUBRICAS_VAZIAS:
                continue
            # só o que já tinha acontecido antes deste dia
            anteriores = [h for h in historico[:i] if h["dia"] < item["dia"]]
            consultas.append({"id": f"{empresa['empresa']}#{i}", "consulta": item["descricao"],
                              "historico": anteriores, "alvo": alvo})
    return consultas


def carregar_consultas(conjunto: str) -> list:
    caminho = CONJUNTOS.get(conjunto, conjunto)
    with open(caminho, encoding="utf-8") as f:
        dados = json.load(f)
    return consultas_de_ambiguos(dados) if "casos" in dados else consultas_de_recuperacao(dados)


def relevantes(consulta: dict) -> set:
    return {i for i, h in enumerate(consulta["historico"]) if _rubrica(h.get("imputacao_no_mapa")) == consulta["alvo"]}


def avaliar_metodo(consultas: list, metodo: str, modelo: str = None) -> dict:
    posicoes, precisoes = [], []
    for c in consultas:
        rel = relevantes(c)
        if not rel:
            continue
        ordem = rag_historico.ordenar(c["consulta"], c["historico"], metodo, modelo)
        posicoes.append(next(p for p, i in enumerate(ordem, start=1) if i in rel))
        topo = ordem[:K_PROMPT]
        precisoes.append(sum(i in rel for i in topo) / len(topo))
    n = len(posicoes) or 1
    return {
        "metodo": metodo, "modelo": modelo if metodo in ("denso", "hibrido") else None,
        "consultas_avaliadas": len(posicoes),
        **{f"recall@{k}": sum(p <= k for p in posicoes) / n for k in KS},
        "mrr": sum(1 / p for p in posicoes) / n,
        f"precisao@{K_PROMPT}": statistics.mean(precisoes) if precisoes else 0.0,
    }


def avaliar(consultas: list, metodos=rag_historico.METODOS, modelos=(rag_historico.MODELO_EMBEDDINGS,)) -> list:
    return [
        avaliar_metodo(consultas, metodo, modelo)
        for metodo in metodos
        for modelo in (modelos if metodo in ("denso", "hibrido") else (None,))
    ]


def tabela(resultados: list, consultas: list) -> str:
    avaliadas = resultados[0]["consultas_avaliadas"] if resultados else 0
    linhas = [
        f"{avaliadas} consultas com pelo menos um relevante no histórico "
        f"(de {len(consultas)}; nas outras nenhum método pode acertar)",
        f"{'método':<10} {'modelo':<44} " + " ".join(f"{'R@' + str(k):>6}" for k in KS) + f" {'MRR':>6} {'P@6':>6}",
    ]
    for r in resultados:
        linhas.append(
            f"{r['metodo']:<10} {(r['modelo'] or '-'):<44} "
            + " ".join(f"{r[f'recall@{k}']:>6.1%}" for k in KS)
            + f" {r['mrr']:>6.3f} {r[f'precisao@{K_PROMPT}']:>6.1%}"
        )
    return "\n".join(linhas)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--conjunto", default="recuperacao", help="recuperacao, ambiguos ou um caminho")
    parser.add_argument("--metodos", nargs="+", default=list(rag_historico.METODOS), choices=rag_historico.METODOS)
    parser.add_argument("--modelos", nargs="+", default=[rag_historico.MODELO_EMBEDDINGS])
    parser.add_argument("--limiar-recall", type=float, default=None,
                        help=f"recall@{K_PROMPT} mínimo do método em produção ({rag_historico.METODO_POR_OMISSAO}); "
                             "abaixo sai com código 1")
    args = parser.parse_args(argv)

    consultas = carregar_consultas(args.conjunto)
    resultados = avaliar(consultas, args.metodos, args.modelos)
    print(tabela(resultados, consultas))

    os.makedirs(PASTA_RESULTADOS, exist_ok=True)
    nome = f"{datetime.now():%Y%m%d-%H%M%S}_recuperacao_{os.path.splitext(os.path.basename(args.conjunto))[0]}.json"
    with open(os.path.join(PASTA_RESULTADOS, nome), "w", encoding="utf-8") as f:
        json.dump({"quando": datetime.now().isoformat(timespec="seconds"), "conjunto": args.conjunto,
                   "resultados": resultados}, f, ensure_ascii=False, indent=1)
    print(f"  relatório: evals/resultados/{nome}")

    if args.limiar_recall is not None:
        alvo = next((r for r in resultados if r["metodo"] == rag_historico.METODO_POR_OMISSAO), None)
        if alvo is None or alvo[f"recall@{K_PROMPT}"] < args.limiar_recall:
            obtido = f"{alvo[f'recall@{K_PROMPT}']:.1%}" if alvo else "não medido"
            print(f"FALHOU: recall@{K_PROMPT} de {rag_historico.METODO_POR_OMISSAO} {obtido} "
                  f"abaixo do limiar {args.limiar_recall:.0%}")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
