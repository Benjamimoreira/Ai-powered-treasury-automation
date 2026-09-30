"""Tracing das chamadas a LLMs: Arize Phoenix (OpenTelemetry) e/ou LangSmith.

Cada chamada fica registada com modelo, fornecedor, mensagens, resposta,
tokens de entrada/saída e custo estimado; a latência vem da duração.

Dois destinos, cada um ligado de forma independente:
- Phoenix, quando PHOENIX_COLLECTOR_ENDPOINT está definido (ex.
  http://phoenix:6006/v1/traces no docker-compose). Convenções
  OpenInference, as que o Phoenix mostra nativamente. Um só container, e
  os traces não saem da máquina;
- LangSmith, quando LANGSMITH_TRACING=true e LANGSMITH_API_KEY estão
  definidos. O grafo do agente (LangGraph) é rastreado pelo próprio
  LangGraph; aqui entram as chamadas diretas ao LLM (run_type "llm", com
  usage_metadata e ls_model_name para o LangSmith calcular o custo), que
  ficam aninhadas no run do agente quando são feitas dentro dele.
  Atenção RGPD: o LangSmith é um serviço externo - usar a região da UE
  (LANGSMITH_ENDPOINT=https://eu.api.smith.langchain.com) e ver
  docs/GOVERNANCA_IA.md.

Sem nenhum dos dois, ou sem as bibliotecas, span_llm() não faz nada - o
tracing nunca pode partir uma chamada."""
import json
import os
from contextlib import ExitStack, contextmanager

# Preço por milhão de tokens (entrada, saída), em USD, para APIs pagas.
# Vazio: só se usa o modelo local (Ollama), que não tem custo por pedido -
# o custo dele é a máquina (ver docs/AVALIACAO_LLM.md). Fica a tabela para
# o tracing mostrar custo se um dia se voltar a usar uma API externa.
PRECOS_POR_MILHAO = {}

_tracer = None
_iniciado = False


def custo_estimado(modelo: str, tokens_entrada: int, tokens_saida: int) -> float:
    entrada, saida = PRECOS_POR_MILHAO.get(modelo, (0.0, 0.0))
    return (tokens_entrada * entrada + tokens_saida * saida) / 1_000_000


def langsmith_ativo() -> bool:
    return os.environ.get("LANGSMITH_TRACING", "").lower() == "true" and bool(os.environ.get("LANGSMITH_API_KEY"))


def _obter_tracer():
    global _tracer, _iniciado
    if _iniciado:
        return _tracer
    _iniciado = True
    endpoint = os.environ.get("PHOENIX_COLLECTOR_ENDPOINT")
    if not endpoint:
        return None
    try:
        from opentelemetry import trace
        from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
        from opentelemetry.sdk.resources import Resource
        from opentelemetry.sdk.trace import TracerProvider
        from opentelemetry.sdk.trace.export import BatchSpanProcessor
    except ImportError:
        return None
    provider = TracerProvider(resource=Resource.create({
        "service.name": "api-tesouraria",
        # o Phoenix agrupa os traces por projeto
        "openinference.project.name": os.environ.get("PHOENIX_PROJECT_NAME", "tesouraria"),
    }))
    provider.add_span_processor(BatchSpanProcessor(OTLPSpanExporter(endpoint=endpoint)))
    trace.set_tracer_provider(provider)
    _tracer = trace.get_tracer("app.services.llm")
    return _tracer


def _abrir_span_phoenix(pilha: ExitStack, nome, fornecedor, modelo, mensagens, atributos):
    tracer = _obter_tracer()
    if tracer is None:
        return None
    span = pilha.enter_context(tracer.start_as_current_span(nome))
    span.set_attribute("openinference.span.kind", "LLM")
    span.set_attribute("llm.provider", fornecedor)
    span.set_attribute("llm.model_name", modelo)
    span.set_attribute("input.value", json.dumps(mensagens, ensure_ascii=False))
    span.set_attribute("input.mime_type", "application/json")
    for i, m in enumerate(mensagens):
        span.set_attribute(f"llm.input_messages.{i}.message.role", m.get("role", ""))
        span.set_attribute(f"llm.input_messages.{i}.message.content", str(m.get("content", "")))
    for chave, valor in atributos.items():
        if valor is not None:
            span.set_attribute(f"tesouraria.{chave}", valor)
    return span


def _abrir_run_langsmith(pilha: ExitStack, nome, fornecedor, modelo, mensagens, atributos):
    if not langsmith_ativo():
        return None
    try:
        from langsmith.run_helpers import trace
    except ImportError:
        return None
    metadata = {"ls_provider": fornecedor, "ls_model_name": modelo,
                **{k: v for k, v in atributos.items() if v is not None}}
    return pilha.enter_context(trace(
        nome, run_type="llm", inputs={"messages": mensagens}, metadata=metadata,
        project_name=os.environ.get("LANGSMITH_PROJECT", "tesouraria"),
    ))


class _Registo:
    def __init__(self, span, run_langsmith, modelo):
        self._span, self._run, self._modelo = span, run_langsmith, modelo

    def registar_resposta(self, texto: str, tokens_entrada: int, tokens_saida: int):
        entrada, saida = tokens_entrada or 0, tokens_saida or 0
        if self._span is not None:
            s = self._span
            s.set_attribute("output.value", texto or "")
            s.set_attribute("llm.output_messages.0.message.role", "assistant")
            s.set_attribute("llm.output_messages.0.message.content", texto or "")
            s.set_attribute("llm.token_count.prompt", entrada)
            s.set_attribute("llm.token_count.completion", saida)
            s.set_attribute("llm.token_count.total", entrada + saida)
            s.set_attribute("llm.cost.total", custo_estimado(self._modelo, entrada, saida))
        if self._run is not None:
            self._run.end(outputs={
                "choices": [{"message": {"role": "assistant", "content": texto or ""}}],
                "usage_metadata": {"input_tokens": entrada, "output_tokens": saida, "total_tokens": entrada + saida},
            })

    def registar_erro(self, erro: Exception):
        if self._span is not None:
            self._span.record_exception(erro)
        if self._run is not None:
            self._run.end(error=str(erro))


@contextmanager
def span_llm(nome: str, fornecedor: str, modelo: str, mensagens: list, **atributos):
    """Envolve uma chamada ao LLM num span (Phoenix) e/ou num run
    (LangSmith). Devolve um objeto com registar_resposta(texto,
    tokens_entrada, tokens_saida) e registar_erro(erro), para o chamador
    preencher o resultado quando o tiver."""
    with ExitStack() as pilha:
        run = _abrir_run_langsmith(pilha, nome, fornecedor, modelo, mensagens, atributos)
        span = _abrir_span_phoenix(pilha, nome, fornecedor, modelo, mensagens, atributos)
        yield _Registo(span, run, modelo)
