"""Promove respostas anotadas do Assistente ao golden dataset - fecha o
ciclo: 👎 / números não verificados / chumbo do juiz -> fila "para
rever" -> anotação humana com a resposta esperada -> caso de teste.

Para cada anotação ainda não promovida que tem resposta esperada:
- vai buscar ao Phoenix os resultados das ferramentas desse trace (os
  dados com que o Assistente respondeu), para o caso poder ser repetido
  sem depender do estado da base de dados (app/evals/avaliar_assistente.py);
- pseudonimiza pergunta, dados e resposta esperada (o mesmo
  Pseudonimizador do conjunto dos ambíguos - RGPD, ver
  docs/GOVERNANCA_IA.md), por isso o conjunto pode ir para o repositório;
- acrescenta o caso a evals/assistente.json e ao dataset
  "tesouraria-assistente" no Phoenix, e marca a anotação como promovida.

Rever o diff de evals/assistente.json antes do commit, como no conjunto
dos ambíguos: a pseudonimização reconhece nomes de uma lista, não todos.

    python -m app.evals.promover_golden
"""
import json
import os
from datetime import timedelta, timezone

from app.evals.avaliacao_online import agrupar_conversas
from app.evals.pseudonimizar import Pseudonimizador
from app.services import phoenix_cliente

RAIZ = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
CONJUNTO = os.path.join(RAIZ, "evals", "assistente.json")
NOME_DATASET = "tesouraria-assistente"
JANELA_MINUTOS = 15  # à volta da hora da interação, para encontrar os spans do trace


def carregar_conjunto(caminho: str = CONJUNTO) -> dict:
    if not os.path.exists(caminho):
        return {"versao": 1, "descricao": "Respostas do Assistente anotadas por uma pessoa, com a resposta "
                                         "esperada e os dados das ferramentas desse momento - pseudonimizado.",
                "casos": []}
    with open(caminho, encoding="utf-8") as f:
        return json.load(f)


def dados_do_trace(trace_id: str, criado_em) -> list:
    momento = criado_em.replace(tzinfo=timezone.utc)
    spans = phoenix_cliente.listar_spans((momento - timedelta(minutes=JANELA_MINUTOS)).isoformat(),
                                         (momento + timedelta(minutes=JANELA_MINUTOS)).isoformat(), limite=5000)
    conversas = [c for c in agrupar_conversas([s for s in spans if s["context"]["trace_id"] == trace_id])]
    return conversas[0]["dados"] if conversas else []


def caso_de_anotacao(anotacao, interacao, dados: list) -> dict:
    p = Pseudonimizador()  # um por caso: o mesmo nome dá o mesmo PESSOA_nn em todo o caso
    return {
        "id": f"assistente-{interacao.id}",
        "pergunta": p.texto(interacao.pergunta),
        "dados": [p.texto(d) for d in dados],
        "resposta_dada": p.texto(interacao.resposta or ""),
        "resposta_esperada": p.texto(anotacao.resposta_esperada),
        "label": anotacao.label,
        "notas": p.texto(anotacao.notas) if anotacao.notas else None,
    }


def promover(db, caminho: str = CONJUNTO) -> dict:
    from app.db.models import AnotacaoAssistente, InteracaoAssistente, _utcnow_naive

    pendentes = (
        db.query(AnotacaoAssistente, InteracaoAssistente)
        .join(InteracaoAssistente, AnotacaoAssistente.interacao_id == InteracaoAssistente.id)
        .filter(AnotacaoAssistente.promovida_em.is_(None), AnotacaoAssistente.resposta_esperada.isnot(None))
        .all()
    )
    conjunto = carregar_conjunto(caminho)
    existentes = {c["id"] for c in conjunto["casos"]}
    novos, sem_dados = [], 0
    for anotacao, interacao in pendentes:
        dados = dados_do_trace(interacao.trace_id, interacao.criado_em) if interacao.trace_id else []
        if not dados and interacao.ferramentas_usadas:
            sem_dados += 1  # o trace ainda não está (ou já não está) no Phoenix - tenta na próxima
            continue
        caso = caso_de_anotacao(anotacao, interacao, dados)
        if caso["id"] not in existentes:
            novos.append(caso)
        anotacao.promovida_em = _utcnow_naive()

    if novos:
        conjunto["casos"].extend(novos)
        with open(caminho, "w", encoding="utf-8") as f:
            json.dump(conjunto, f, ensure_ascii=False, indent=2)
        phoenix_cliente.enviar_dataset(
            NOME_DATASET, conjunto["descricao"],
            entradas=[{"pergunta": c["pergunta"], "dados": c["dados"]} for c in novos],
            saidas=[{"resposta_esperada": c["resposta_esperada"]} for c in novos],
            metadata=[{"caso": c["id"], "label": c["label"]} for c in novos],
        )
    db.commit()
    return {"promovidos": len(novos), "sem_dados_no_phoenix": sem_dados, "total_no_conjunto": len(conjunto["casos"])}


def main():
    from dotenv import load_dotenv

    from app.db.session import SessionLocal

    load_dotenv()
    db = SessionLocal()
    try:
        print(json.dumps(promover(db), ensure_ascii=False))
    finally:
        db.close()


if __name__ == "__main__":
    main()
