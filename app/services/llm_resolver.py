import json
import os
import re

import httpx
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.db.models import CasoAmbiguo, LinhaMapa, MovimentoBancario

load_dotenv()

HF_CHAT_COMPLETIONS_URL = "https://router.huggingface.co/v1/chat/completions"
TOP_K_EXEMPLOS = 3

_modelo_embeddings = None


def _carregar_modelo_embeddings():
    """Carrega o modelo de embeddings (sentence-transformers) só na primeira
    vez que é preciso - evita o custo de arranque (e o download do modelo)
    em código que nunca chega a usar a camada de RAG."""
    global _modelo_embeddings
    if _modelo_embeddings is None:
        from sentence_transformers import SentenceTransformer
        _modelo_embeddings = SentenceTransformer("all-MiniLM-L6-v2")
    return _modelo_embeddings


def obter_embeddings(textos):
    """Isolado numa função própria para os testes poderem substituir isto
    por uma versão falsa e determinística, sem carregar o modelo real nem
    depender de rede/GPU."""
    modelo = _carregar_modelo_embeddings()
    return modelo.encode(textos, normalize_embeddings=True).tolist()


def _similaridade_cosseno(a, b):
    produto = sum(x * y for x, y in zip(a, b))
    norma_a = sum(x * x for x in a) ** 0.5
    norma_b = sum(y * y for y in b) ** 0.5
    if norma_a == 0 or norma_b == 0:
        return 0.0
    return produto / (norma_a * norma_b)


def texto_do_caso(db: Session, caso: CasoAmbiguo) -> str:
    movimento = db.get(MovimentoBancario, caso.movimento_id)
    descricao = movimento.descricao if movimento else ""
    return f"{caso.empresa} | {descricao} | {caso.valor:.2f} EUR"


def texto_da_linha(linha: LinhaMapa) -> str:
    return (
        f"linha {linha.linha} ({linha.tipo}): empresa {linha.empresa}, "
        f"previsto {linha.previsto}, imputação: {linha.imputacao or '(vazia)'}"
    )


def casos_resolvidos_semelhantes(db: Session, caso: CasoAmbiguo, top_k: int = TOP_K_EXEMPLOS):
    """Devolve até top_k casos ambíguos já resolvidos por um humano no
    passado, ordenados por semelhança (embeddings) com o caso atual. Esta
    é a parte "RAG": procurar exemplos parecidos já resolvidos, para dar
    ao LLM contexto sobre como este tipo de caso costuma ser decidido."""
    resolvidos = (
        db.query(CasoAmbiguo)
        .filter(CasoAmbiguo.resolvido_por.isnot(None), CasoAmbiguo.id != caso.id)
        .all()
    )
    if not resolvidos:
        return []

    textos = [texto_do_caso(db, caso)] + [texto_do_caso(db, c) for c in resolvidos]
    embeddings = obter_embeddings(textos)
    embedding_alvo, embeddings_resolvidos = embeddings[0], embeddings[1:]

    pontuados = [
        (c, _similaridade_cosseno(embedding_alvo, emb))
        for c, emb in zip(resolvidos, embeddings_resolvidos)
    ]
    pontuados.sort(key=lambda par: par[1], reverse=True)
    return pontuados[:top_k]


def montar_prompt(db: Session, caso: CasoAmbiguo, candidatos: list, exemplos: list) -> str:
    linhas_candidatas = "\n".join(f"- {texto_da_linha(linha)}" for linha in candidatos)

    if exemplos:
        exemplos_texto = "\n".join(
            f"- Caso: {texto_do_caso(db, c)} -> resolvido como: {c.resolucao} "
            f"(similaridade {sim:.2f})"
            for c, sim in exemplos
        )
    else:
        exemplos_texto = "(sem casos parecidos resolvidos anteriormente)"

    return f"""Tens um movimento bancário ambíguo para reconciliar com o \
Mapa de Pagamentos e Recebimentos: há mais que uma linha do mapa que bate \
com a mesma empresa e o mesmo valor, e é preciso escolher a linha correta \
(ou concluir que nenhuma serve e é um movimento novo).

Movimento a reconciliar: {caso.empresa} | valor {caso.valor:.2f} EUR

Linhas candidatas:
{linhas_candidatas}

Casos parecidos já resolvidos por um humano no passado (para te ajudar a \
perceber o padrão de decisão):
{exemplos_texto}

Responde APENAS com um JSON, sem mais texto nenhum, no formato:
{{"linha_id": <id da linha escolhida, ou null se nenhuma servir>, "justificacao": "<explicação curta>"}}
"""


def chamar_llm(prompt: str) -> str:
    """Chamada isolada ao LLM. Usa o Groq (gratuito) se GROQ_API_KEY
    estiver definido; caso contrário cai para a HuggingFace Inference
    Providers (HF_TOKEN). Isolada numa função própria para os testes
    poderem substituir isto por uma resposta falsa, sem fazer chamadas de
    rede nem gastar créditos."""
    groq_key = os.environ.get("GROQ_API_KEY")
    if groq_key:
        from huggingface_hub import InferenceClient

        # base_url aponta directamente para a Groq (em vez de provider="groq",
        # que só encaminha modelos que a HuggingFace tenha no seu catálogo
        # próprio - a maioria dos modelos leves da Groq não está lá listada).
        # Assim usamos o nome nativo da Groq e o limite de tokens/minuto mais
        # alto dos modelos pequenos.
        modelo = os.environ.get("GROQ_MODEL_ID", "llama-3.1-8b-instant")
        cliente = InferenceClient(base_url="https://api.groq.com/openai/v1", api_key=groq_key)
        resposta = cliente.chat_completion(
            messages=[{"role": "user", "content": prompt}], model=modelo,
        )
        return resposta.choices[0].message.content

    token = os.environ.get("HF_TOKEN")
    if not token:
        raise RuntimeError(
            "Nem GROQ_API_KEY nem HF_TOKEN estão definidos - cria/edita o "
            "ficheiro .env (a partir de .env.example) com um dos dois."
        )
    modelo = os.environ.get("HF_MODEL_ID", "Qwen/Qwen2.5-72B-Instruct")

    resposta = httpx.post(
        HF_CHAT_COMPLETIONS_URL,
        headers={"Authorization": f"Bearer {token}"},
        json={"model": modelo, "messages": [{"role": "user", "content": prompt}]},
        timeout=30.0,
    )
    resposta.raise_for_status()
    return resposta.json()["choices"][0]["message"]["content"]


def _extrair_json(texto: str):
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if not match:
        raise ValueError(f"Resposta do LLM não contém JSON reconhecível: {texto!r}")
    return json.loads(match.group(0))


def sugerir_resolucao(db: Session, caso_id: int) -> CasoAmbiguo:
    """Gera uma proposta de resolução para um caso ambíguo, usando RAG
    (casos parecidos já resolvidos) + LLM. Nunca aplica a proposta - só a
    grava em resolucao_sugerida/justificacao_sugerida, para confirmação
    humana via resolver_ambiguo/POST /ambiguos/{id}/resolver."""
    caso = db.get(CasoAmbiguo, caso_id)
    if caso is None:
        raise ValueError(f"Caso ambíguo {caso_id} não encontrado.")

    candidatos = db.query(LinhaMapa).filter(LinhaMapa.id.in_(caso.candidatos or [])).all()
    exemplos = casos_resolvidos_semelhantes(db, caso)
    prompt = montar_prompt(db, caso, candidatos, exemplos)
    resposta_texto = chamar_llm(prompt)

    try:
        resultado = _extrair_json(resposta_texto)
        linha_id = resultado.get("linha_id")
        caso.resolucao_sugerida = f"linha_id={linha_id}" if linha_id is not None else "novo"
        caso.justificacao_sugerida = resultado.get("justificacao")
    except (ValueError, json.JSONDecodeError):
        caso.resolucao_sugerida = None
        caso.justificacao_sugerida = f"[resposta do LLM não veio em JSON válido] {resposta_texto}"

    db.commit()
    return caso
