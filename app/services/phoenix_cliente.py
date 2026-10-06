"""Cliente mínimo da API REST do Arize Phoenix (self-hosted, sem login).

Para o que o tracing por OpenTelemetry (llm_tracing.py) não cobre:
- anotar um trace já enviado: 👍/👎 de quem perguntou (HUMAN), o
  guardrail de números (CODE), o juiz das avaliações online (LLM);
- ler os spans de um período, para as avaliações online
  (app/evals/avaliacao_online.py);
- criar/atualizar um dataset (golden dataset do Assistente).

O endereço vem de PHOENIX_URL (do ponto de vista da API - no Docker,
http://phoenix:6006) ou, sem ele, de PHOENIX_COLLECTOR_ENDPOINT sem o
/v1/traces. Sem nenhum dos dois, as funções não fazem nada - tal como o
tracing, o Phoenix nunca pode partir a app."""
import logging
import os
import threading
import time
from typing import Any, Dict, List, Optional

import httpx

logger = logging.getLogger(__name__)
TIMEOUT_S = 10
# um trace acabado de criar só chega ao Phoenix quando o BatchSpanProcessor
# o exporta (até ~5 s depois) - até lá, anotá-lo dá 404
TENTATIVAS_TRACE_NOVO = 6
ESPERA_TRACE_NOVO_S = 2


def url_base() -> Optional[str]:
    url = os.environ.get("PHOENIX_URL")
    if not url:
        coletor = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT") or ""
        url = coletor.removesuffix("/v1/traces") if coletor else None
    return url.rstrip("/") if url else None


def nome_projeto() -> str:
    return os.environ.get("PHOENIX_PROJECT_NAME", "tesouraria")


def anotar_trace(trace_id: Optional[str], nome: str, tipo: str, *, label: Optional[str] = None,
                 score: Optional[float] = None, explicacao: Optional[str] = None,
                 metadata: Optional[Dict[str, Any]] = None, trace_novo: bool = False) -> bool:
    """Grava (ou atualiza - o identificador é fixo por nome) uma anotação
    num trace. tipo = HUMAN | CODE | LLM. Devolve False se não conseguiu.

    trace_novo=True para um trace que ainda pode não ter chegado ao Phoenix
    (ex. o guardrail, no fim da própria resposta): a anotação vai numa
    thread de fundo que repete enquanto o Phoenix responder 404, para não
    atrasar a resposta ao utilizador; devolve True (posta em fila)."""
    base = url_base()
    if not base or not trace_id:
        return False
    corpo = {"data": [{
        "trace_id": trace_id, "name": nome, "annotator_kind": tipo, "identifier": nome,
        "result": {"label": label, "score": score, "explanation": explicacao},
        "metadata": metadata or {},
    }]}
    if trace_novo:
        threading.Thread(target=_enviar_anotacao, args=(base, trace_id, corpo, TENTATIVAS_TRACE_NOVO),
                         daemon=True).start()
        return True
    return _enviar_anotacao(base, trace_id, corpo, 1)


def _enviar_anotacao(base: str, trace_id: str, corpo: dict, tentativas: int) -> bool:
    for tentativa in range(tentativas):
        try:
            resposta = httpx.post(f"{base}/v1/trace_annotations", params={"sync": "true"}, json=corpo,
                                  timeout=TIMEOUT_S)
            if resposta.status_code == 404 and tentativa < tentativas - 1:
                time.sleep(ESPERA_TRACE_NOVO_S)  # o trace ainda não foi exportado
                continue
            resposta.raise_for_status()
            return True
        except Exception:
            if tentativa == tentativas - 1:
                logger.warning("não consegui anotar o trace %s no Phoenix", trace_id, exc_info=True)
    return False


def listar_spans(desde_iso: str, ate_iso: Optional[str] = None, limite: int = 1000) -> List[Dict[str, Any]]:
    """Spans do projeto entre duas datas (ISO 8601, UTC), todas as páginas."""
    base = url_base()
    if not base:
        return []
    params: Dict[str, Any] = {"start_time": desde_iso, "limit": min(limite, 1000)}
    if ate_iso:
        params["end_time"] = ate_iso
    spans: List[Dict[str, Any]] = []
    while True:
        resposta = httpx.get(f"{base}/v1/projects/{nome_projeto()}/spans", params=params, timeout=30)
        if resposta.status_code == 404:  # o projeto ainda não tem nenhum trace
            return []
        resposta.raise_for_status()
        corpo = resposta.json()
        spans.extend(corpo.get("data", []))
        cursor = corpo.get("next_cursor")
        if not cursor or len(spans) >= limite:
            return spans[:limite]
        params["cursor"] = cursor


def enviar_dataset(nome: str, descricao: str, entradas: List[dict], saidas: List[dict],
                   metadata: List[dict]) -> Optional[str]:
    """Cria o dataset ou acrescenta-lhe exemplos (action=append se já existir).
    Devolve o id do dataset, ou None se o Phoenix não estiver ligado."""
    base = url_base()
    if not base or not entradas:
        return None
    existe = httpx.get(f"{base}/v1/datasets", params={"name": nome}, timeout=TIMEOUT_S)
    existe.raise_for_status()
    acao = "append" if existe.json().get("data") else "create"
    resposta = httpx.post(f"{base}/v1/datasets/upload", params={"sync": "true"}, timeout=30, json={
        "action": acao, "name": nome, "description": descricao,
        "inputs": entradas, "outputs": saidas, "metadata": metadata,
    })
    resposta.raise_for_status()
    return resposta.json().get("data", {}).get("dataset_id")
