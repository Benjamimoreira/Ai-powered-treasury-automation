import json
import os
import re
import time
from collections import defaultdict
from dataclasses import dataclass

import httpx
from dotenv import load_dotenv
from sqlalchemy.orm import Session

from app.db.models import CasoAmbiguo, LinhaMapa, MovimentoBancario
from app.services.llm_tracing import custo_estimado, span_llm

load_dotenv()

TOP_K_EXEMPLOS = 3
N_HISTORICO_ENTIDADE = 6

# Só o modelo local (Ollama, API compatível com OpenAI): nenhum dado sai da
# máquina. As APIs externas (Groq, HuggingFace) foram retiradas em set/2026
# - comparação e motivos em docs/AVALIACAO_LLM.md e docs/GOVERNANCA_IA.md.
# O dicionário mantém a forma de "vários fornecedores" para se poder
# acrescentar outro modelo local (ex. vLLM) sem mexer no resto.
FORNECEDORES = {
    "ollama": {
        "url_env": "OLLAMA_URL", "url": "http://localhost:11434/v1",
        "chave": None, "modelo_env": "OLLAMA_MODEL_ID", "modelo": "qwen2.5:3b",
    },
}
TENTATIVAS_LIMITE_PEDIDOS = 5

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
    norma_b = sum(x * x for x in b) ** 0.5
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


# ---------------------------------------------------------------------------
# Histórico da entidade: como é que movimentos parecidos desta empresa
# foram imputados no Mapa no passado
# ---------------------------------------------------------------------------


def pares_movimento_linha(db: Session, ate=None) -> list:
    """(linha do Mapa paga, movimento bancário) em que a linha tem um único
    movimento do mesmo dia, valor e empresa - a correspondência conhecida.
    Base do histórico da entidade e do conjunto de avaliação."""
    from app.services.reconciliador import empresa_do_mapa_corresponde

    movimentos = defaultdict(list)
    for m in db.query(MovimentoBancario).all():
        if ate is None or m.dia <= ate:
            movimentos[(m.dia, round(abs(m.valor), 2))].append(m)
    pares = []
    for linha in db.query(LinhaMapa).filter(LinhaMapa.pago.isnot(None)).all():
        if not linha.pago or (ate is not None and linha.dia > ate):
            continue
        candidatos = [
            m for m in movimentos.get((linha.dia, round(abs(linha.pago), 2)), [])
            if (m.valor > 0) == (linha.pago > 0) and empresa_do_mapa_corresponde(linha.empresa, m.empresa)
        ]
        if len(candidatos) == 1:
            pares.append((linha, candidatos[0]))
    return pares


def historico_da_entidade(db: Session, empresa: str, antes_de, n: int = N_HISTORICO_ENTIDADE) -> list:
    from app.services.reconciliador import chave_empresa

    alvo = chave_empresa(empresa)
    pares = sorted(
        (p for p in pares_movimento_linha(db) if chave_empresa(p[1].empresa) == alvo and p[1].dia < antes_de),
        key=lambda p: p[1].dia,
    )[-n:]
    return [
        {
            "dia": m.dia.isoformat(), "descricao": m.descricao, "valor": m.valor,
            "imputacao_no_mapa": l.imputacao or l.descricao,
        }
        for l, m in pares
    ]


# ---------------------------------------------------------------------------
# Prompt - construído a partir de dados neutros (dict), para a produção e a
# avaliação (app/evals/avaliar.py) usarem exatamente o mesmo texto
# ---------------------------------------------------------------------------

# v1: o prompt original (só empresa + valor do movimento) - mantido para a
# avaliação medir a melhoria. v2: descritivo do movimento, descrição das
# linhas e histórico da entidade - é o que está em produção.
VERSAO_PROMPT = "v2"


def dados_do_caso(db: Session, caso: CasoAmbiguo, candidatos: list, exemplos: list) -> dict:
    movimento = db.get(MovimentoBancario, caso.movimento_id)
    return {
        "movimento": {
            "dia": caso.dia.isoformat(), "empresa": caso.empresa, "valor": caso.valor,
            "descricao": movimento.descricao if movimento else "",
        },
        "candidatos": [
            {
                "id": l.id, "linha": l.linha, "tipo": l.tipo, "empresa": l.empresa,
                "previsto": l.previsto, "imputacao": l.imputacao, "descricao": l.descricao,
            }
            for l in candidatos
        ],
        "historico_entidade": historico_da_entidade(db, caso.empresa, caso.dia) if movimento else [],
        "exemplos_resolvidos": [
            {"caso": texto_do_caso(db, c), "resolucao": c.resolucao, "similaridade": sim} for c, sim in exemplos
        ],
    }


def _texto_candidato(c: dict, versao: str) -> str:
    base = (
        f"linha {c['linha']} ({c['tipo']}): empresa {c['empresa']}, "
        f"previsto {c['previsto']}, imputação: {c.get('imputacao') or '(vazia)'}"
    )
    if versao == "v1":
        return f"- [id {c['id']}] {base}"
    return f"- [id {c['id']}] {base}, descrição: {c.get('descricao') or '(vazia)'}"


def montar_prompt_de_dados(dados: dict, versao: str = VERSAO_PROMPT) -> str:
    mov = dados["movimento"]
    linhas_candidatas = "\n".join(_texto_candidato(c, versao) for c in dados["candidatos"])
    exemplos = dados.get("exemplos_resolvidos") or []
    exemplos_texto = "\n".join(
        f"- Caso: {e['caso']} -> resolvido como: {e['resolucao']} (similaridade {e['similaridade']:.2f})"
        for e in exemplos
    ) or "(sem casos parecidos resolvidos anteriormente)"

    if versao == "v1":
        movimento_texto = f"{mov['empresa']} | valor {mov['valor']:.2f} EUR"
        historico_texto = ""
    else:
        movimento_texto = (
            f"{mov['empresa']} | dia {mov['dia']} | descritivo do banco: \"{mov['descricao']}\" | "
            f"valor {mov['valor']:.2f} EUR"
        )
        historico = (dados.get("historico_entidade") or [])[-N_HISTORICO_ENTIDADE:]
        historico_texto = "\n\nComo movimentos anteriores desta empresa foram imputados no Mapa:\n" + (
            "\n".join(
                f"- \"{h['descricao']}\" ({h['valor']:.2f} EUR) -> imputação \"{h['imputacao_no_mapa']}\""
                for h in historico
            ) or "(sem histórico)"
        )

    return f"""Tens um movimento bancário ambíguo para reconciliar com o \
Mapa de Pagamentos e Recebimentos: há mais que uma linha do mapa que bate \
com a mesma empresa e o mesmo valor, e é preciso escolher a linha correta \
(ou concluir que nenhuma serve e é um movimento novo).

Movimento a reconciliar: {movimento_texto}

Linhas candidatas:
{linhas_candidatas}{historico_texto}

Casos parecidos já resolvidos por um humano no passado (para te ajudar a \
perceber o padrão de decisão):
{exemplos_texto}

Responde APENAS com um JSON, sem mais texto nenhum, no formato:
{{"linha_id": <id da linha escolhida, ou null se nenhuma servir>, "justificacao": "<explicação curta>"}}
"""


def montar_prompt(db: Session, caso: CasoAmbiguo, candidatos: list, exemplos: list) -> str:
    return montar_prompt_de_dados(dados_do_caso(db, caso, candidatos, exemplos))


# ---------------------------------------------------------------------------
# Chamada ao LLM
# ---------------------------------------------------------------------------


@dataclass
class RespostaLLM:
    texto: str
    fornecedor: str
    modelo: str
    tokens_entrada: int
    tokens_saida: int
    latencia_s: float

    @property
    def custo_usd(self) -> float:
        return custo_estimado(self.modelo, self.tokens_entrada, self.tokens_saida)


def fornecedor_por_omissao() -> str:
    escolhido = os.environ.get("LLM_FORNECEDOR") or "ollama"
    if escolhido not in FORNECEDORES:
        raise RuntimeError(
            f"LLM_FORNECEDOR='{escolhido}' não é suportado - só {sorted(FORNECEDORES)} (modelo local)."
        )
    return escolhido


def chamar_llm_detalhado(
    prompt: str, fornecedor: str = None, modelo: str = None, nome_span: str = "sugestao_ambiguo",
    formato_json: bool = True, **atributos,
) -> RespostaLLM:
    """Chamada ao LLM local (Ollama, API compatível com OpenAI), com
    tokens e latência - para a avaliação e para o tracing (Phoenix/
    LangSmith). Repete com espera se o servidor devolver 429 (ocupado).
    `formato_json` pede o modo JSON nativo
    (response_format) - sem ele os modelos pequenos partem o JSON com aspas
    dentro da justificação."""
    fornecedor = fornecedor or fornecedor_por_omissao()
    if fornecedor not in FORNECEDORES:
        raise RuntimeError(f"Fornecedor de LLM '{fornecedor}' não é suportado - só {sorted(FORNECEDORES)}.")
    config = FORNECEDORES[fornecedor]
    url = os.environ.get(config.get("url_env") or "", "") or config["url"]
    modelo = modelo or os.environ.get(config["modelo_env"]) or config["modelo"]
    cabecalhos = {"Content-Type": "application/json", "User-Agent": "api-tesouraria/1.0"}
    if config["chave"]:
        chave = os.environ.get(config["chave"])
        if not chave:
            raise RuntimeError(f"{config['chave']} não está definido - necessário para o fornecedor '{fornecedor}'.")
        cabecalhos["Authorization"] = f"Bearer {chave}"
    mensagens = [{"role": "user", "content": prompt}]

    with span_llm(nome_span, fornecedor, modelo, mensagens, **atributos) as registo:
        inicio = time.perf_counter()
        for tentativa in range(TENTATIVAS_LIMITE_PEDIDOS):
            try:
                resposta = httpx.post(
                    f"{url.rstrip('/')}/chat/completions", headers=cabecalhos,
                    json={
                        "model": modelo, "messages": mensagens, "temperature": 0,
                        **({"response_format": {"type": "json_object"}} if formato_json else {}),
                    },
                    timeout=300.0,
                )
            except httpx.ConnectError as erro:
                registo.registar_erro(erro)
                raise RuntimeError(
                    f"Não foi possível ligar ao Ollama em {url} - está a correr? (ícone na barra de tarefas, "
                    f"ou 'ollama serve'; e o modelo {modelo} descarregado: 'ollama pull {modelo}')"
                ) from erro
            if resposta.status_code != 429 or tentativa == TENTATIVAS_LIMITE_PEDIDOS - 1:
                break
            time.sleep(float(resposta.headers.get("retry-after") or 2 ** (tentativa + 2)))
        try:
            resposta.raise_for_status()
        except httpx.HTTPStatusError as erro:
            registo.registar_erro(erro)
            raise
        corpo = resposta.json()
        uso = corpo.get("usage") or {}
        resultado = RespostaLLM(
            texto=corpo["choices"][0]["message"]["content"] or "",
            fornecedor=fornecedor, modelo=modelo,
            tokens_entrada=uso.get("prompt_tokens", 0), tokens_saida=uso.get("completion_tokens", 0),
            latencia_s=time.perf_counter() - inicio,
        )
        registo.registar_resposta(resultado.texto, resultado.tokens_entrada, resultado.tokens_saida)
    return resultado


def chamar_llm(prompt: str) -> str:
    """Chamada isolada ao LLM (só o texto). Isolada numa função própria
    para os testes poderem substituir isto por uma resposta falsa, sem
    fazer chamadas de rede nem gastar créditos."""
    return chamar_llm_detalhado(prompt).texto


def _extrair_json(texto: str):
    match = re.search(r"\{.*\}", texto, re.DOTALL)
    if not match:
        raise ValueError(f"Resposta do LLM não contém JSON reconhecível: {texto!r}")
    return json.loads(match.group(0))


def interpretar_resposta(texto: str, ids_candidatos: list) -> dict:
    """{"valida", "linha_id", "justificacao"} - válida quando é JSON e o
    linha_id é um dos candidatos (ou null). Um id inventado conta como
    inválido: é exatamente o erro que não se pode deixar passar."""
    try:
        resultado = _extrair_json(texto)
    except (ValueError, json.JSONDecodeError):
        return {"valida": False, "linha_id": None, "justificacao": texto}
    linha_id = resultado.get("linha_id")
    if isinstance(linha_id, str) and linha_id.strip().isdigit():
        linha_id = int(linha_id)
    valida = linha_id is None or linha_id in ids_candidatos
    return {"valida": valida, "linha_id": linha_id if valida else None, "justificacao": resultado.get("justificacao")}


def sugerir_resolucao(db: Session, caso_id: int) -> CasoAmbiguo:
    """Gera uma proposta de resolução para um caso ambíguo. Primeiro as
    regras (app/services/resolucao_regras.py): a triagem pelo texto não lê
    nada, o histórico só é lido se a triagem não decidir, e o LLM (com RAG
    sobre casos parecidos já resolvidos) só é chamado se as regras não
    decidirem. Nunca aplica a proposta - só a grava em resolucao_sugerida/
    justificacao_sugerida, para confirmação humana via resolver_ambiguo/
    POST /ambiguos/{id}/resolver."""
    from app.services.resolucao_regras import decidir_sem_llm

    caso = db.get(CasoAmbiguo, caso_id)
    if caso is None:
        raise ValueError(f"Caso ambíguo {caso_id} não encontrado.")

    candidatos = db.query(LinhaMapa).filter(LinhaMapa.id.in_(caso.candidatos or [])).all()
    movimento = db.get(MovimentoBancario, caso.movimento_id)
    decisao = decidir_sem_llm(
        {"descricao": movimento.descricao if movimento else ""},
        [{"id": l.id, "imputacao": l.imputacao, "descricao": l.descricao} for l in candidatos],
        lambda: historico_da_entidade(db, caso.empresa, caso.dia, 25),
    )
    if decisao.decidiu:
        caso.resolucao_sugerida = f"linha_id={decisao.linha_id}"
        caso.justificacao_sugerida = f"[regra: {decisao.fonte}, sem LLM] {decisao.motivo}"
        db.commit()
        return caso

    exemplos = casos_resolvidos_semelhantes(db, caso)
    prompt = montar_prompt(db, caso, candidatos, exemplos)
    resposta_texto = chamar_llm(prompt)

    resultado = interpretar_resposta(resposta_texto, [c.id for c in candidatos])
    if resultado["valida"]:
        linha_id = resultado["linha_id"]
        caso.resolucao_sugerida = f"linha_id={linha_id}" if linha_id is not None else "novo"
        caso.justificacao_sugerida = resultado["justificacao"]
    else:
        caso.resolucao_sugerida = None
        caso.justificacao_sugerida = f"[resposta do LLM não veio em JSON válido] {resposta_texto}"

    db.commit()
    return caso
