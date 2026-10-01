"""Avaliação como experiência no LangSmith (python -m app.evals.avaliar --langsmith).

O conjunto de avaliação fica no LangSmith como dataset
("tesouraria-ambiguos-v<versão>", um exemplo por caso): o caso é o input e
a resposta esperada + categoria são a referência - o modelo nunca as vê.
Cada corrida é uma experiência ("<estratégia>-<modelo>") com três
avaliadores por caso (certa, resposta_valida, decidido_sem_llm) e os
traces do LLM/agente aninhados em cada caso - dá para comparar corridas
lado a lado e abrir cada caso falhado.

O conjunto é pseudonimizado (ver app/evals/pseudonimizar.py), por isso
pode ir para o LangSmith (região da UE - ver docs/GOVERNANCA_IA.md)."""
import json
import os

PREFIXO_DATASET = "tesouraria-ambiguos"


def _cliente():
    from langsmith import Client

    return Client()


def nome_dataset(caminho_conjunto: str) -> str:
    with open(caminho_conjunto, encoding="utf-8") as f:
        versao = json.load(f).get("versao", 1)
    return f"{PREFIXO_DATASET}-v{versao}"


def sincronizar_dataset(cliente, nome: str, casos: list) -> str:
    """Garante que o dataset tem todos os casos do conjunto (acrescenta só
    os que faltam, pelo id do caso - correr duas vezes não duplica). O
    nome tem a versão do conjunto, por isso um conjunto novo dá um dataset
    novo e as experiências antigas continuam comparáveis."""
    if cliente.has_dataset(dataset_name=nome):
        existentes = {(e.metadata or {}).get("caso") for e in cliente.list_examples(dataset_name=nome)}
    else:
        cliente.create_dataset(
            nome, description="Casos ambíguos (movimento bancário vs linhas do Mapa) com resposta conhecida, "
                              "pseudonimizados - ver docs/AVALIACAO_LLM.md.",
        )
        existentes = set()
    em_falta = [c for c in casos if c["id"] not in existentes]
    if em_falta:
        cliente.create_examples(dataset_name=nome, examples=[
            {
                "inputs": {"caso": {k: v for k, v in caso.items() if k not in ("esperado", "categoria")}},
                "outputs": {"esperado": caso["esperado"], "categoria": caso["categoria"]},
                "metadata": {"caso": caso["id"], "categoria": caso["categoria"]},
            }
            for caso in em_falta
        ])
    return nome


def _certa(outputs: dict, reference_outputs: dict) -> dict:
    return {"key": "certa", "score": int(bool(outputs.get("valida")) and outputs.get("linha_id") == reference_outputs["esperado"])}


def _resposta_valida(outputs: dict) -> dict:
    return {"key": "resposta_valida", "score": int(bool(outputs.get("valida")))}


def _decidido_sem_llm(outputs: dict) -> dict:
    return {"key": "decidido_sem_llm", "score": int((outputs.get("decidido_por") or "").startswith("regras"))}


def correr(casos: list, caminho_conjunto: str, prever, config: dict) -> list:
    """`prever(caso) -> dict` (linha_id, valida, justificacao, erro, tokens,
    chamadas_llm, decidido_por, ...) - o mesmo usado na avaliação local.
    Devolve a lista de resultados no formato de avaliar.avaliar, para as
    métricas e o limiar serem exatamente os mesmos."""
    cliente = _cliente()
    # o dataset tem sempre o conjunto inteiro, mesmo com --max-casos
    with open(caminho_conjunto, encoding="utf-8") as f:
        todos = json.load(f)["casos"]
    dataset = sincronizar_dataset(cliente, nome_dataset(caminho_conjunto), todos)
    ids_pedidos = {c["id"] for c in casos}

    def alvo(inputs: dict) -> dict:
        return prever(inputs["caso"])

    resultados_ls = cliente.evaluate(
        alvo,
        # com --max-casos, só os exemplos pedidos
        data=[e for e in cliente.list_examples(dataset_name=dataset) if (e.metadata or {}).get("caso") in ids_pedidos],
        evaluators=[_certa, _resposta_valida, _decidido_sem_llm],
        experiment_prefix=f"{config['estrategia']}-{config.get('modelo') or os.environ.get('OLLAMA_MODEL_ID', 'qwen2.5:3b')}",
        metadata=config,
        max_concurrency=1,  # um só Ollama local - em paralelo só ficava mais lento
    )
    resultados = []
    for linha in resultados_ls:
        saida = linha["run"].outputs or {}
        referencia = linha["example"].outputs or {}
        resultados.append({
            "caso": (linha["example"].metadata or {}).get("caso"),
            "categoria": referencia.get("categoria"), "esperado": referencia.get("esperado"),
            "obtido": saida.get("linha_id"), "valida": bool(saida.get("valida")),
            "certa": bool(saida.get("valida")) and saida.get("linha_id") == referencia.get("esperado"),
            "justificacao": saida.get("justificacao"), "erro": saida.get("erro") or linha["run"].error,
            **{k: saida.get(k) for k in ("tokens_entrada", "tokens_saida", "latencia_s", "custo_usd", "modelo",
                                          "passos", "chamadas_llm", "decidido_por")},
        })
    print(f"  LangSmith: experiência '{resultados_ls.experiment_name}' no dataset '{dataset}'")
    return resultados
