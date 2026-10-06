"""Avaliação como experiência no Phoenix (python -m app.evals.avaliar --phoenix).

O conjunto de avaliação fica no Phoenix como dataset
("tesouraria-ambiguos-v<versão>", um exemplo por caso): o caso é o input,
a resposta esperada e a categoria são a referência - o modelo nunca as vê.
Cada corrida é uma experiência ("<estratégia>-<modelo>") com três
avaliações por caso (certa, resposta_valida, decidido_sem_llm), e cada
run aponta para o trace do caso (o LLM e, na estratégia agente, o grafo
nó a nó). Em Datasets -> tesouraria-ambiguos-v2 -> Experiments dá para
comparar corridas (ex. qwen2.5:3b vs 7b) lado a lado e abrir cada caso
falhado.

Substitui as experiências no LangSmith (langsmith_experiencias.py): a
observabilidade dos LLMs é só no Phoenix desde 06/10/2026 - local, por
isso nem o conjunto (já pseudonimizado) sai da máquina."""
import json
import os
from datetime import datetime, timezone
from typing import Callable, List

from app.services import phoenix_cliente

PREFIXO_DATASET = "tesouraria-ambiguos"


def nome_dataset(caminho_conjunto: str) -> str:
    with open(caminho_conjunto, encoding="utf-8") as f:
        versao = json.load(f).get("versao", 1)
    return f"{PREFIXO_DATASET}-v{versao}"


def _agora() -> str:
    return datetime.now(timezone.utc).isoformat()


def sincronizar_dataset(nome: str, casos: list) -> str:
    """Garante que o dataset tem todos os casos (acrescenta só os que
    faltam, pelo id do caso - correr duas vezes não duplica). Devolve o id."""
    dataset_id = phoenix_cliente.obter_dataset_id(nome)
    existentes = set()
    if dataset_id:
        existentes = {(e.get("metadata") or {}).get("caso") for e in phoenix_cliente.listar_exemplos(dataset_id)}
    em_falta = [c for c in casos if c["id"] not in existentes]
    if em_falta:
        phoenix_cliente.enviar_dataset(
            nome, "Casos ambíguos (movimento bancário vs linhas do Mapa) com resposta conhecida, "
                  "pseudonimizados - ver docs/AVALIACAO_LLM.md.",
            entradas=[{"caso": {k: v for k, v in c.items() if k not in ("esperado", "categoria")}} for c in em_falta],
            saidas=[{"esperado": c["esperado"], "categoria": c["categoria"]} for c in em_falta],
            metadata=[{"caso": c["id"], "categoria": c["categoria"]} for c in em_falta],
        )
        dataset_id = phoenix_cliente.obter_dataset_id(nome)
    return dataset_id


def correr(casos: list, caminho_conjunto: str, prever: Callable[[dict], dict], config: dict) -> List[dict]:
    """`prever(caso) -> dict` (linha_id, valida, decidido_por, ...) - o mesmo
    da avaliação local. Devolve as previsões pela ordem de `casos`, para as
    métricas e o limiar serem exatamente os mesmos."""
    from app.services.llm_tracing import run_cadeia

    with open(caminho_conjunto, encoding="utf-8") as f:
        todos = json.load(f)["casos"]  # o dataset tem sempre o conjunto inteiro, mesmo com --max-casos
    dataset = nome_dataset(caminho_conjunto)
    dataset_id = sincronizar_dataset(dataset, todos)
    exemplo_por_caso = {(e.get("metadata") or {}).get("caso"): e["id"] for e in phoenix_cliente.listar_exemplos(dataset_id)}

    modelo = config.get("modelo") or os.environ.get("OLLAMA_MODEL_ID", "qwen2.5:3b")
    metadata = {**{k: v for k, v in config.items() if v is not None}, "modelo": modelo}
    if os.environ.get("IMAGE_TAG"):  # na porta de qualidade do deploy: o commit da imagem avaliada
        metadata["imagem"] = os.environ["IMAGE_TAG"][:7]
    experiencia_id = phoenix_cliente.criar_experiencia(dataset_id, f"{config['estrategia']}-{modelo}", metadata)
    previsoes = []
    for caso in casos:
        inicio = _agora()
        # um trace por caso, para o run da experiência apontar para ele
        with run_cadeia("caso_avaliacao", {"caso": caso["id"]}, estrategia=config["estrategia"], modelo=modelo) as cadeia:
            previsao = prever(caso)
            if cadeia is not None:
                cadeia.terminar(previsao)
        fim = _agora()
        previsoes.append(previsao)

        run_id = phoenix_cliente.registar_run(
            experiencia_id, exemplo_por_caso[caso["id"]], previsao, inicio, fim,
            trace_id=cadeia.trace_id if cadeia is not None else None, erro=previsao.get("erro"),
        )
        certa = bool(previsao["valida"]) and previsao["linha_id"] == caso["esperado"]
        for nome, score in (("certa", certa), ("resposta_valida", bool(previsao["valida"])),
                            ("decidido_sem_llm", (previsao.get("decidido_por") or "").startswith("regras"))):
            phoenix_cliente.avaliar_run(run_id, nome, float(score), inicio, fim)

    url = (os.environ.get("PHOENIX_URL_PUBLICA") or phoenix_cliente.url_base() or "").rstrip("/")
    print(f"  Phoenix: experiência '{config['estrategia']}-{modelo}' no dataset '{dataset}' - {url}/datasets/{dataset_id}/experiments")
    return previsoes
