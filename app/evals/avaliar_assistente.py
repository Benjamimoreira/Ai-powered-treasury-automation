"""Avaliação offline do Assistente contra o seu golden dataset
(evals/assistente.json, construído por app/evals/promover_golden.py).

Cada caso é repetido em "replay": o modelo recebe o prompt de sistema do
Assistente, os resultados das ferramentas gravados nesse momento e a
pergunta, e responde. Assim o teste não depende do estado da base de
dados nem do servidor MCP - mede o passo que mais erra, que é responder
bem a partir dos dados. Por caso:
- guardrail de números: os valores da resposta vêm dos dados?
- juiz (modelo maior): a resposta diz o mesmo que a resposta esperada?

Com --limiar, sai com código 1 se a taxa de respostas certas ficar abaixo
- a mesma porta de qualidade que app/evals/avaliar.py faz para os
ambíguos.

    python -m app.evals.avaliar_assistente
    python -m app.evals.avaliar_assistente --modelo qwen2.5:7b --limiar 0.7
"""
import argparse
import json
import os
import sys
from datetime import datetime

from app.evals.promover_golden import CONJUNTO, carregar_conjunto
from app.services.guardrail_numeros import verificar

PASTA_RESULTADOS = os.path.join(os.path.dirname(CONJUNTO), "resultados")

PROMPT_JUIZ = """És um avaliador. Recebes uma pergunta feita a um assistente de \
tesouraria, a RESPOSTA ESPERADA (escrita por uma pessoa que conhece os dados) \
e a RESPOSTA OBTIDA.

Decide se a RESPOSTA OBTIDA está certa face à esperada:
- "certa": dá a mesma informação essencial (os mesmos valores, a mesma \
conclusão); pode estar escrita de outra forma ou ter mais detalhe;
- "errada": falta a informação essencial, os valores diferem, ou contradiz a esperada.

[PERGUNTA]
{pergunta}

[RESPOSTA ESPERADA]
{esperada}

[RESPOSTA OBTIDA]
{obtida}

Responde só com JSON: {{"veredicto": "certa" | "errada", "explicacao": "<uma frase>"}}"""


def responder(caso: dict, modelo: str = None) -> dict:
    from app.services.chatbot import PROMPT_SISTEMA
    from app.services.llm_resolver import chamar_llm_detalhado

    dados = "\n".join(caso["dados"]) or "(nenhuma ferramenta devolveu dados)"
    prompt = (f"{PROMPT_SISTEMA}\n\nResultados das ferramentas que já consultaste:\n{dados}\n\n"
              f"Pergunta: {caso['pergunta']}")
    r = chamar_llm_detalhado(prompt, modelo=modelo, nome_span="avaliacao_assistente", formato_json=False,
                             caso=caso["id"])
    return {"texto": r.texto, "latencia_s": r.latencia_s, "modelo": r.modelo}


def julgar(caso: dict, obtida: str, modelo_juiz: str) -> dict:
    from app.services.llm_resolver import _extrair_json, chamar_llm_detalhado

    r = chamar_llm_detalhado(
        PROMPT_JUIZ.format(pergunta=caso["pergunta"], esperada=caso["resposta_esperada"], obtida=obtida),
        modelo=modelo_juiz, nome_span="juiz_assistente", caso=caso["id"],
    )
    veredicto = _extrair_json(r.texto) or {}
    return {"certa": str(veredicto.get("veredicto", "")).lower() == "certa", "explicacao": veredicto.get("explicacao")}


def avaliar(casos: list, modelo: str = None, modelo_juiz: str = None) -> dict:
    from app.evals.avaliacao_online import MODELO_JUIZ

    modelo_juiz = modelo_juiz or os.environ.get("AVALIACAO_JUIZ_MODELO", MODELO_JUIZ)
    resultados = []
    for caso in casos:
        obtida = responder(caso, modelo)
        guardrail = verificar(obtida["texto"], caso["dados"])
        juiz = julgar(caso, obtida["texto"], modelo_juiz)
        resultados.append({"caso": caso["id"], "resposta": obtida["texto"], "latencia_s": obtida["latencia_s"],
                           "modelo": obtida["modelo"], "certa": juiz["certa"], "explicacao_juiz": juiz["explicacao"],
                           "numeros_nao_verificados": guardrail["nao_verificados"]})
    n = len(resultados) or 1
    return {
        "config": {"modelo": next((r["modelo"] for r in resultados if r["modelo"]), modelo), "juiz": modelo_juiz,
                   "n_casos": len(resultados)},
        "metricas": {
            "certas": sum(r["certa"] for r in resultados) / n,
            "com_numeros_nao_verificados": sum(1 for r in resultados if r["numeros_nao_verificados"]) / n,
            "latencia_media_s": sum(r["latencia_s"] or 0 for r in resultados) / n,
        },
        "resultados": resultados,
    }


def main(argv=None):
    from dotenv import load_dotenv

    from app.services.llm_tracing import esvaziar

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--modelo", default=None, help="modelo avaliado (omissão: o do Assistente)")
    parser.add_argument("--modelo-juiz", default=None)
    parser.add_argument("--limiar", type=float, default=None, help="taxa mínima de respostas certas (0-1)")
    parser.add_argument("--conjunto", default=CONJUNTO)
    args = parser.parse_args(argv)

    casos = carregar_conjunto(args.conjunto)["casos"]
    if not casos:
        print("O golden dataset do Assistente ainda está vazio - anota respostas na Monitorização e corre "
              "python -m app.evals.promover_golden.")
        return 0
    relatorio = avaliar(casos, args.modelo, args.modelo_juiz)
    esvaziar()
    os.makedirs(PASTA_RESULTADOS, exist_ok=True)
    caminho = os.path.join(PASTA_RESULTADOS, f"{datetime.now():%Y%m%d-%H%M%S}_assistente_{relatorio['config']['modelo']}.json".replace(":", "-"))
    with open(caminho, "w", encoding="utf-8") as f:
        json.dump(relatorio, f, ensure_ascii=False, indent=2)
    m = relatorio["metricas"]
    print(f"{relatorio['config']['n_casos']} casos | {relatorio['config']['modelo']} (juiz {relatorio['config']['juiz']})\n"
          f"  certas {m['certas']:.1%} · com números não verificados {m['com_numeros_nao_verificados']:.1%} · "
          f"latência média {m['latencia_media_s']:.1f}s\n  relatório: {caminho}")
    if args.limiar is not None and m["certas"] < args.limiar:
        print(f"ABAIXO DO LIMIAR ({args.limiar:.0%})")
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
