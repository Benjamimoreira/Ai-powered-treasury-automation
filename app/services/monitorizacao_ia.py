"""Métricas de qualidade da IA para o separador Monitorização.

Dois sítios onde a IA entra em produção, com feedback humano já embutido:
- Assistente da dashboard (POST /chat): cada pergunta fica em
  interacoes_assistente, com 👍/👎 de quem perguntou e as conversas que
  terminaram em erro (Ollama em baixo/ocupado, timeout);
- sugestões para casos ambíguos (llm_resolver.sugerir_resolucao): a
  sugestão fica em resolucao_sugerida e a decisão humana em resolucao, por
  isso aceitar/rejeitar é comparar as duas - sem tabela nova.
"""
import logging
import os
import statistics
from datetime import date, datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import (
    AnotacaoAssistente, AvaliacaoOnline, CasoAmbiguo, DossierAmbiguo, InteracaoAssistente, _utcnow_naive,
)

logger = logging.getLogger(__name__)

# justificacao_sugerida começa assim quando a sugestão veio das regras (ver
# llm_resolver.sugerir_resolucao) ou quando o LLM não devolveu JSON válido
PREFIXO_REGRAS = "[regra:"
PREFIXO_INVALIDA = "[resposta do LLM não veio em JSON válido]"
MAX_CARACTERES_GUARDADOS = 4000


def registar_interacao(
    db: Session, pergunta: str, *, resposta: Optional[str] = None, ferramentas_usadas: Optional[List[str]] = None,
    status: str = "ok", erro: Optional[str] = None, duracao_segundos: Optional[float] = None,
    modelo: Optional[str] = None, trace_id: Optional[str] = None,
    numeros_nao_verificados: Optional[List[str]] = None,
) -> Optional[int]:
    """Grava uma pergunta ao Assistente e devolve o id (para o 👍/👎). Nunca
    deixa uma falha a gravar partir a resposta ao utilizador - devolve None."""
    try:
        interacao = InteracaoAssistente(
            pergunta=pergunta[:MAX_CARACTERES_GUARDADOS],
            resposta=resposta[:MAX_CARACTERES_GUARDADOS] if resposta is not None else None,
            ferramentas_usadas=ferramentas_usadas or [], status=status, erro=erro,
            duracao_segundos=duracao_segundos, modelo=modelo, trace_id=trace_id,
            numeros_nao_verificados=numeros_nao_verificados or [],
        )
        db.add(interacao)
        db.commit()
        return interacao.id
    except Exception:
        db.rollback()
        logger.exception("não consegui registar a interação com o assistente")
        return None


def registar_feedback(db: Session, interacao_id: int, util: bool) -> Dict[str, Any]:
    interacao = db.get(InteracaoAssistente, interacao_id)
    if interacao is None:
        raise ValueError(f"Interação {interacao_id} não encontrada.")
    interacao.feedback = 1 if util else 0
    interacao.feedback_em = _utcnow_naive()
    db.commit()
    # o mesmo 👍/👎 no trace do Phoenix, ao lado do resto da conversa
    from app.services.phoenix_cliente import anotar_trace

    anotar_trace(interacao.trace_id, "feedback_utilizador", "HUMAN",
                 label="útil" if util else "não útil", score=float(interacao.feedback))
    return {"id": interacao.id, "feedback": interacao.feedback}


def _taxa(parte: int, total: int) -> Optional[float]:
    return round(parte / total, 4) if total else None


def _iso(dt: datetime) -> str:
    return dt.isoformat(timespec="seconds") + "Z"


def _metricas_assistente(db: Session, desde: datetime) -> Dict[str, Any]:
    interacoes = (
        db.query(InteracaoAssistente).filter(InteracaoAssistente.criado_em >= desde)
        .order_by(InteracaoAssistente.criado_em.desc()).all()
    )
    erros = [i for i in interacoes if i.status == "erro"]
    positivos = sum(1 for i in interacoes if i.feedback == 1)
    negativos = sum(1 for i in interacoes if i.feedback == 0)
    duracoes = sorted(i.duracao_segundos for i in interacoes if i.status == "ok" and i.duracao_segundos is not None)

    por_dia: Dict[str, Dict[str, int]] = {}
    for i in interacoes:
        dia = por_dia.setdefault(i.criado_em.date().isoformat(), {"perguntas": 0, "erros": 0})
        dia["perguntas"] += 1
        dia["erros"] += i.status == "erro"

    def resumo(i: InteracaoAssistente) -> Dict[str, Any]:
        return {"id": i.id, "criado_em": _iso(i.criado_em), "pergunta": i.pergunta, "resposta": i.resposta,
                "erro": i.erro, "ferramentas_usadas": i.ferramentas_usadas or [], "trace_id": i.trace_id,
                "numeros_nao_verificados": i.numeros_nao_verificados or []}

    return {
        "perguntas": len(interacoes),
        "erros": len(erros),
        "taxa_erro": _taxa(len(erros), len(interacoes)),
        "feedback_positivo": positivos,
        "feedback_negativo": negativos,
        "taxa_satisfacao": _taxa(positivos, positivos + negativos),
        "taxa_com_feedback": _taxa(positivos + negativos, len(interacoes)),
        "sem_ferramentas": sum(1 for i in interacoes if i.status == "ok" and not i.ferramentas_usadas),
        "com_numeros_nao_verificados": sum(1 for i in interacoes if i.numeros_nao_verificados),
        "respostas_nao_verificadas": [resumo(i) for i in interacoes if i.numeros_nao_verificados][:20],
        "duracao_media_s": round(statistics.mean(duracoes), 1) if duracoes else None,
        "duracao_p95_s": round(duracoes[int(0.95 * (len(duracoes) - 1))], 1) if duracoes else None,
        "por_dia": [{"dia": dia, **v} for dia, v in sorted(por_dia.items())],
        "respostas_negativas": [resumo(i) for i in interacoes if i.feedback == 0][:20],
        "erros_recentes": [resumo(i) for i in erros][:20],
    }


def _metricas_ambiguos(db: Session, desde: date) -> Dict[str, Any]:
    casos = db.query(CasoAmbiguo).filter(CasoAmbiguo.dia >= desde).all()
    resolvidos = [c for c in casos if c.resolvido_por]
    com_sugestao = [c for c in casos if c.resolucao_sugerida]
    por_regras = [c for c in com_sugestao if (c.justificacao_sugerida or "").startswith(PREFIXO_REGRAS)]
    invalidas = [c for c in casos if (c.justificacao_sugerida or "").startswith(PREFIXO_INVALIDA)]

    # aceitar/rejeitar = a decisão humana (resolucao) bate com a sugestão
    decididos_com_sugestao = [c for c in resolvidos if c.resolucao_sugerida]
    aceites = [c for c in decididos_com_sugestao if c.resolucao == c.resolucao_sugerida]
    aceites_regras = [c for c in aceites if (c.justificacao_sugerida or "").startswith(PREFIXO_REGRAS)]
    decididos_regras = [c for c in decididos_com_sugestao if (c.justificacao_sugerida or "").startswith(PREFIXO_REGRAS)]
    # "passado a humano": resolvido sem nenhuma sugestão utilizável
    sem_sugestao = [c for c in resolvidos if not c.resolucao_sugerida]

    ids_casos = [c.id for c in casos]
    dossiers = db.query(DossierAmbiguo).filter(DossierAmbiguo.caso_id.in_(ids_casos)).count() if ids_casos else 0

    return {
        "casos": len(casos),
        "resolvidos": len(resolvidos),
        "pendentes": len(casos) - len(resolvidos),
        "com_sugestao": len(com_sugestao),
        "sugestoes_por_regras": len(por_regras),
        "sugestoes_por_llm": len(com_sugestao) - len(por_regras),
        "taxa_sem_llm": _taxa(len(por_regras), len(com_sugestao)),
        "respostas_invalidas_llm": len(invalidas),
        "aceites": len(aceites),
        "rejeitadas": len(decididos_com_sugestao) - len(aceites),
        "taxa_aceitacao": _taxa(len(aceites), len(decididos_com_sugestao)),
        "taxa_aceitacao_regras": _taxa(len(aceites_regras), len(decididos_regras)),
        "taxa_aceitacao_llm": _taxa(len(aceites) - len(aceites_regras), len(decididos_com_sugestao) - len(decididos_regras)),
        "resolvidos_sem_sugestao": len(sem_sugestao),
        "taxa_passados_a_humano": _taxa(len(sem_sugestao), len(resolvidos)),
        "dossiers": dossiers,
    }


def metricas_ia(db: Session, dias: int = 30) -> Dict[str, Any]:
    agora = _utcnow_naive()
    desde = agora - timedelta(days=dias)
    return {
        "dias": dias,
        # o browser não resolve http://phoenix:6006 (nome interno do Docker) -
        # é o endereço por onde quem abre a dashboard chega ao Phoenix
        "phoenix_url": os.environ.get("PHOENIX_URL_PUBLICA") or None,
        "assistente": _metricas_assistente(db, desde),
        "ambiguos": _metricas_ambiguos(db, desde.date()),
    }


LABELS_ANOTACAO = ("correta", "incorreta", "alucinada", "incompleta")


def registar_avaliacao_online(db: Session, trace_id: str, avaliador: str, label: Optional[str],
                              score: Optional[float], explicacao: Optional[str], modelo: Optional[str]) -> None:
    db.add(AvaliacaoOnline(trace_id=trace_id, avaliador=avaliador, label=label, score=score,
                           explicacao=explicacao, modelo=modelo))
    db.commit()


def listar_para_rever(db: Session, limite: int = 50) -> List[Dict[str, Any]]:
    """Fila de revisão humana: respostas do Assistente ainda sem anotação que
    têm 👎, números não verificados pelo guardrail, ou que o juiz das
    avaliações online chumbou (score 0). Mais recentes primeiro."""
    anotadas = {i for (i,) in db.query(AnotacaoAssistente.interacao_id).distinct()}
    chumbadas: Dict[str, List[str]] = {}
    for a in db.query(AvaliacaoOnline).filter(AvaliacaoOnline.score == 0).all():
        chumbadas.setdefault(a.trace_id, []).append(f"juiz: {a.avaliador} = {a.label}")

    fila = []
    candidatas = (
        db.query(InteracaoAssistente).filter(InteracaoAssistente.status == "ok")
        .order_by(InteracaoAssistente.criado_em.desc()).limit(1000).all()
    )
    for i in candidatas:
        if i.id in anotadas:
            continue
        motivos = []
        if i.feedback == 0:
            motivos.append("👎 de quem perguntou")
        if i.numeros_nao_verificados:
            motivos.append("números não verificados: " + ", ".join(i.numeros_nao_verificados))
        motivos += chumbadas.get(i.trace_id or "", [])
        if motivos:
            fila.append({"id": i.id, "criado_em": _iso(i.criado_em), "pergunta": i.pergunta, "resposta": i.resposta,
                         "trace_id": i.trace_id, "motivos": motivos})
        if len(fila) >= limite:
            break
    return fila


def anotar_interacao(db: Session, interacao_id: int, label: str, score: Optional[float] = None,
                     notas: Optional[str] = None, resposta_esperada: Optional[str] = None,
                     autor: Optional[str] = None) -> Dict[str, Any]:
    interacao = db.get(InteracaoAssistente, interacao_id)
    if interacao is None:
        raise ValueError(f"Interação {interacao_id} não encontrada.")
    if label not in LABELS_ANOTACAO:
        raise ValueError(f"label tem de ser um de {LABELS_ANOTACAO}.")
    anotacao = AnotacaoAssistente(interacao_id=interacao_id, label=label, score=score, notas=notas,
                                  resposta_esperada=(resposta_esperada or None), autor=autor)
    db.add(anotacao)
    db.commit()

    from app.services.phoenix_cliente import anotar_trace

    anotar_trace(interacao.trace_id, "anotacao_humana", "HUMAN", label=label, score=score, explicacao=notas,
                 metadata={"resposta_esperada": resposta_esperada} if resposta_esperada else None)
    return {"id": anotacao.id, "interacao_id": interacao_id, "label": label}


# anotação humana -> a resposta é boa? (o 👍/👎 conta quando não há anotação)
_ANOTACAO_BOA = {"correta": True, "incorreta": False, "alucinada": False, "incompleta": False}
MIN_PARES_CALIBRACAO = 20


def _kappa(a: int, b: int, c: int, d: int) -> Optional[float]:
    """Kappa de Cohen da matriz [[a, b], [c, d]] (linhas = humano bom/mau,
    colunas = juiz bom/mau): concordância acima da que haveria por acaso."""
    n = a + b + c + d
    if not n:
        return None
    observada = (a + d) / n
    esperada = ((a + b) * (a + c) + (c + d) * (b + d)) / n ** 2
    return round((observada - esperada) / (1 - esperada), 3) if esperada < 1 else None


def calibracao_juiz(db: Session) -> Dict[str, Any]:
    """Concordância entre cada avaliador das avaliações online e as pessoas,
    nas respostas que têm os dois. Até haver MIN_PARES_CALIBRACAO pares, o
    juiz não está calibrado e os scores dele são só indicativos."""
    interacoes = db.query(InteracaoAssistente).filter(InteracaoAssistente.trace_id.isnot(None)).all()
    ultima_anotacao: Dict[int, AnotacaoAssistente] = {}
    for a in db.query(AnotacaoAssistente).order_by(AnotacaoAssistente.criado_em).all():
        ultima_anotacao[a.interacao_id] = a
    humano: Dict[str, bool] = {}
    for i in interacoes:
        if i.id in ultima_anotacao:
            humano[i.trace_id] = _ANOTACAO_BOA[ultima_anotacao[i.id].label]
        elif i.feedback is not None:
            humano[i.trace_id] = i.feedback == 1

    resultado = {}
    avaliacoes = db.query(AvaliacaoOnline).order_by(AvaliacaoOnline.criado_em).all()
    for avaliador in sorted({a.avaliador for a in avaliacoes}):
        juiz = {a.trace_id: (a.score or 0) >= 0.5 for a in avaliacoes if a.avaliador == avaliador}
        pares = [(humano[t], juiz[t]) for t in juiz if t in humano]
        a = sum(1 for h, j in pares if h and j)          # os dois: boa
        b = sum(1 for h, j in pares if h and not j)      # juiz chumba uma boa (falso alarme)
        c = sum(1 for h, j in pares if not h and j)      # juiz deixa passar uma má
        d = sum(1 for h, j in pares if not h and not j)  # os dois: má
        resultado[avaliador] = {
            "pares": len(pares),
            "concordancia": _taxa(a + d, len(pares)),
            "kappa": _kappa(a, b, c, d),
            "matriz": {"ambos_boa": a, "falso_alarme": b, "deixou_passar": c, "ambos_ma": d},
            # das respostas más para as pessoas, quantas o juiz apanhou
            "apanha_mas": _taxa(d, c + d),
            "calibrado": len(pares) >= MIN_PARES_CALIBRACAO,
        }
    return {"min_pares": MIN_PARES_CALIBRACAO, "avaliadores": resultado}
