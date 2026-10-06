"""Avaliações online: as respostas reais do Assistente, avaliadas depois de
acontecerem, com o resultado escrito no Phoenix como anotação do trace.

É a camada assíncrona da avaliação (ver docs/AVALIACAO_LLM.md): a
offline (app/evals/avaliar.py) mede antes do deploy contra o golden
dataset; o guardrail de números (guardrail_numeros.py) corre em cada
resposta; esta corre sobre uma amostra do que já foi
respondido (agendada às 10:00, 14:00 e 18:00, cada corrida com as 4 h
anteriores), com avaliadores caros demais para correr em linha:

- fundamentacao (LLM as a judge): a resposta é suportada pelos resultados
  das ferramentas, ou afirma coisas que não estão lá? - o template de
  "hallucination" do Phoenix, adaptado;
- relevancia (LLM as a judge): a resposta responde à pergunta feita? - o
  template de "QA correctness" sem referência;
- nli (opcional, --nli): um classificador pequeno (SLM, encoder tipo BERT,
  multilingue) de inferência - os resultados das ferramentas implicam a
  resposta? Não depende de nenhum LLM, por isso serve para calibrar o juiz.

O juiz é um modelo maior do que o avaliado (AVALIACAO_JUIZ_MODELO, por
omissão qwen2.5:7b, contra o qwen2.5:3b do Assistente): um modelo a julgar
as próprias respostas é um juiz enviesado. Antes de confiar nos números,
calibrar o juiz contra as anotações humanas (👍/👎 e anotações manuais no
Phoenix) - a concordância entre os dois diz quanto vale o juiz.

Cada corrida fica em execucoes_scripts como "avaliacao_online", por isso
aparece na Monitorização como os outros scripts agendados.

    python -m app.evals.avaliacao_online --horas 4       # agendado às 10:00, 14:00 e 18:00
    python -m app.evals.avaliacao_online --horas 24 --max-traces 50 --nli
"""
import argparse
import json
import os
import sys
from datetime import datetime, timedelta, timezone
from typing import Dict, List, Optional

from app.services import phoenix_cliente

NOME_SPAN_RAIZ = "assistente"
MODELO_JUIZ = "qwen2.5:7b"
MODELO_NLI = "MoritzLaurer/mDeBERTa-v3-base-xnli-multilingual-nli-2mil7"
MAX_CARACTERES_CONTEXTO = 6000

PROMPT_FUNDAMENTACAO = """És um avaliador. Recebes uma pergunta feita a um assistente de \
tesouraria, os DADOS que as ferramentas lhe devolveram e a RESPOSTA que ele deu.

Decide se a RESPOSTA é suportada pelos DADOS:
- "fundamentada": tudo o que a resposta afirma (valores, datas, nomes, \
conclusões) está nos dados ou decorre diretamente deles (ex. uma soma);
- "alucinada": a resposta afirma algo que não está nos dados ou os contradiz.
Se não houver dados e a resposta afirmar factos sobre a tesouraria, é "alucinada".

[PERGUNTA]
{pergunta}

[DADOS]
{dados}

[RESPOSTA]
{resposta}

Responde só com JSON: {{"veredicto": "fundamentada" | "alucinada", "explicacao": "<uma frase>"}}"""

PROMPT_RELEVANCIA = """És um avaliador. Recebes uma pergunta feita a um assistente de \
tesouraria e a RESPOSTA que ele deu.

Decide se a RESPOSTA responde à PERGUNTA:
- "relevante": responde ao que foi perguntado (mesmo que diga que não há dados);
- "irrelevante": fala de outra coisa, fica a meio, ou devolve texto técnico \
(ex. uma chamada a ferramenta) em vez de uma resposta.

[PERGUNTA]
{pergunta}

[RESPOSTA]
{resposta}

Responde só com JSON: {{"veredicto": "relevante" | "irrelevante", "explicacao": "<uma frase>"}}"""

AVALIADORES_LLM = {
    # nome da anotação -> (prompt, veredicto bom)
    "fundamentacao": (PROMPT_FUNDAMENTACAO, "fundamentada"),
    "relevancia": (PROMPT_RELEVANCIA, "relevante"),
}


def _valor(texto: Optional[str], chave: str) -> str:
    """input.value/output.value são JSON ({"pergunta": ...}); devolve o campo
    ou o texto tal como veio."""
    if not texto:
        return ""
    try:
        return str(json.loads(texto).get(chave, texto))
    except (ValueError, AttributeError):
        return texto


def agrupar_conversas(spans: List[dict]) -> List[dict]:
    """[{trace_id, pergunta, resposta, dados}] - uma por trace "assistente",
    com os resultados das ferramentas (spans TOOL do mesmo trace) como dados."""
    por_trace: Dict[str, dict] = {}
    for s in spans:
        trace_id = s["context"]["trace_id"]
        conversa = por_trace.setdefault(trace_id, {"trace_id": trace_id, "dados": []})
        atributos = s.get("attributes") or {}
        if s["name"] == NOME_SPAN_RAIZ and not s.get("parent_id"):
            conversa["pergunta"] = _valor(atributos.get("input.value"), "pergunta")
            conversa["resposta"] = _valor(atributos.get("output.value"), "resposta")
            conversa["inicio"] = s.get("start_time")
        elif s.get("span_kind") == "TOOL":
            conversa["dados"].append(f"{s['name']}: {_valor(atributos.get('output.value'), 'resultado')}")
    # só conversas completas (a raiz pode estar fora da janela, ou a resposta falhou)
    return sorted((c for c in por_trace.values() if c.get("resposta")), key=lambda c: c.get("inicio") or "")


def julgar(conversa: dict, nome: str, modelo: str) -> dict:
    from app.services.llm_resolver import _extrair_json, chamar_llm_detalhado

    prompt, bom = AVALIADORES_LLM[nome]
    dados = "\n".join(conversa["dados"])[:MAX_CARACTERES_CONTEXTO] or "(nenhuma ferramenta foi chamada)"
    resposta = chamar_llm_detalhado(
        prompt.format(pergunta=conversa.get("pergunta", ""), dados=dados, resposta=conversa["resposta"]),
        modelo=modelo, nome_span=f"juiz_{nome}", trace_avaliado=conversa["trace_id"],
    )
    veredicto = (_extrair_json(resposta.texto) or {})
    label = str(veredicto.get("veredicto", "")).strip().lower() or "inválido"
    return {"label": label, "score": 1.0 if label == bom else 0.0, "explicacao": veredicto.get("explicacao")}


def _carregar_nli(modelo: str):
    from sentence_transformers import CrossEncoder

    return CrossEncoder(modelo)


def classificar_nli(classificador, conversa: dict) -> Optional[dict]:
    """Probabilidade de os dados implicarem a resposta (entailment). Sem
    dados não há premissa - devolve None."""
    premissa = "\n".join(conversa["dados"])[:MAX_CARACTERES_CONTEXTO]
    if not premissa:
        return None
    import numpy as np

    logits = classificador.predict([(premissa, conversa["resposta"])])[0]
    probabilidades = np.exp(logits - np.max(logits)) / np.exp(logits - np.max(logits)).sum()
    rotulos = [classificador.model.config.id2label[i].lower() for i in range(len(probabilidades))]
    p_implica = float(probabilidades[rotulos.index("entailment")])
    return {"label": rotulos[int(np.argmax(probabilidades))], "score": round(p_implica, 4), "explicacao": None}


def avaliar(horas: float = 4, max_traces: int = 20, modelo_juiz: str = None, usar_nli: bool = False,
            log: Optional[list] = None, db=None) -> dict:
    """Avalia as conversas da janela e escreve cada resultado no Phoenix
    (anotação do trace) e, com `db`, em avaliacoes_online (fila "para rever")."""
    log = log if log is not None else []
    modelo_juiz = modelo_juiz or os.environ.get("AVALIACAO_JUIZ_MODELO", MODELO_JUIZ)
    if not phoenix_cliente.url_base():
        raise RuntimeError("Phoenix não configurado - define PHOENIX_URL ou PHOENIX_COLLECTOR_ENDPOINT.")

    agora = datetime.now(timezone.utc)
    spans = phoenix_cliente.listar_spans((agora - timedelta(hours=horas)).isoformat(), agora.isoformat(), limite=5000)
    conversas = agrupar_conversas(spans)[-max_traces:]
    log.append(f"{len(conversas)} conversa(s) do Assistente nas últimas {horas:g} h (juiz: {modelo_juiz})")

    classificador = _carregar_nli(os.environ.get("AVALIACAO_NLI_MODELO", MODELO_NLI)) if usar_nli else None
    resumo = {nome: [] for nome in AVALIADORES_LLM}
    if usar_nli:
        resumo["nli"] = []

    for conversa in conversas:
        resultados = {}
        for nome in AVALIADORES_LLM:
            try:
                resultados[nome] = julgar(conversa, nome, modelo_juiz)
            except Exception as e:  # um caso que falha não pode parar a corrida
                log.append(f"[AVISO] {nome} falhou no trace {conversa['trace_id']}: {e}")
        if classificador is not None:
            nli = classificar_nli(classificador, conversa)
            if nli:
                resultados["nli"] = nli
        for nome, r in resultados.items():
            tipo = "CODE" if nome == "nli" else "LLM"
            phoenix_cliente.anotar_trace(conversa["trace_id"], nome, tipo, label=r["label"], score=r["score"],
                                         explicacao=r["explicacao"], metadata={"modelo": modelo_juiz if tipo == "LLM" else None})
            resumo[nome].append(r["score"])
            if db is not None:
                from app.services.monitorizacao_ia import registar_avaliacao_online

                registar_avaliacao_online(db, conversa["trace_id"], nome, r["label"], r["score"], r["explicacao"],
                                          modelo_juiz if tipo == "LLM" else os.environ.get("AVALIACAO_NLI_MODELO", MODELO_NLI))

    medias = {nome: round(sum(v) / len(v), 3) if v else None for nome, v in resumo.items()}
    log.append("médias: " + ", ".join(f"{n}={m}" for n, m in medias.items()))
    return {"conversas": len(conversas), "medias": medias}


def main(argv=None):
    from dotenv import load_dotenv

    from app.db.session import SessionLocal
    from app.services.llm_tracing import esvaziar
    from app.services.monitorizacao import monitorizar_execucao

    load_dotenv()
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--horas", type=float, default=4, help="janela a avaliar, a contar de agora")
    parser.add_argument("--max-traces", type=int, default=20, help="amostra máxima (as mais recentes)")
    parser.add_argument("--modelo-juiz", default=None)
    parser.add_argument("--nli", action="store_true", help="também o classificador NLI (descarrega o modelo na 1.ª vez)")
    args = parser.parse_args(argv)

    db = SessionLocal()
    try:
        with monitorizar_execucao(db, "avaliacao_online") as log:
            resultado = avaliar(args.horas, args.max_traces, args.modelo_juiz, args.nli, log, db=db)
        print(json.dumps(resultado, ensure_ascii=False))
    finally:
        db.close()
        esvaziar()


if __name__ == "__main__":
    sys.exit(main())
