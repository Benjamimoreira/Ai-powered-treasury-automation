"""Previsões acrescentadas à mão no dashboard (Predição > Acrescentar
previsão): uma linha de Valor Previsto para a folha de um dia do Mapa de
Pagamentos e Recebimentos. A API só as guarda (PrevisaoManual) e pede o
aplicar_previsoes_manuais.py ao agente do Windows (PT2), que as escreve no
Mapa e devolve o estado de cada uma - ver app/db/models.py:PrevisaoManual."""
from datetime import date, datetime, timezone
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import LinhaMapa, PrevisaoManual

SCRIPT = "aplicar_previsoes_manuais"
TIPOS = ("recebimento", "pagamento")
POR_APLICAR = ("pendente", "erro")


def _dict(p: PrevisaoManual) -> Dict[str, Any]:
    return {
        "id": p.id,
        "dia": p.dia.isoformat(),
        "tipo": p.tipo,
        "descricao": p.descricao,
        "valor": p.valor,
        "imputacao": p.imputacao,
        "estado": p.estado,
        "erro": p.erro,
        "criado_em": p.criado_em.isoformat(timespec="seconds") + "Z",
        "aplicada_em": p.aplicada_em.isoformat(timespec="seconds") + "Z" if p.aplicada_em else None,
    }


def criar(db: Session, dia: date, tipo: str, descricao: str, valor: float, imputacao: str) -> Dict[str, Any]:
    descricao, imputacao = descricao.strip(), imputacao.strip()
    if tipo not in TIPOS:
        raise ValueError(f"Tipo inválido: {tipo} (recebimento ou pagamento)")
    if not descricao:
        raise ValueError("A descrição é obrigatória")
    if not imputacao:
        raise ValueError("A imputação é obrigatória")
    if valor <= 0:
        raise ValueError("O valor previsto tem de ser maior que zero")
    previsao = PrevisaoManual(dia=dia, tipo=tipo, descricao=descricao, valor=round(valor, 2), imputacao=imputacao)
    db.add(previsao)
    db.commit()
    db.refresh(previsao)
    return _dict(previsao)


def listar(db: Session, por_aplicar: bool = False, limit: int = 50) -> List[Dict[str, Any]]:
    """Mais recentes primeiro; com por_aplicar, só as pendentes e as que deram
    erro (o script volta a tentá-las), mais antigas primeiro."""
    q = db.query(PrevisaoManual)
    if por_aplicar:
        q = q.filter(PrevisaoManual.estado.in_(POR_APLICAR)).order_by(PrevisaoManual.id)
    else:
        q = q.order_by(PrevisaoManual.id.desc())
    return [_dict(p) for p in q.limit(limit).all()]


def marcar(db: Session, previsao_id: int, estado: str, erro: Optional[str] = None) -> Optional[Dict[str, Any]]:
    """O script marca a linha "aplicada" (escrita no Mapa) ou "erro". None se
    não existe ou já estava aplicada."""
    previsao = db.get(PrevisaoManual, previsao_id)
    if previsao is None or previsao.estado == "aplicada":
        return None
    previsao.estado = estado
    previsao.erro = erro if estado == "erro" else None
    if estado == "aplicada":
        previsao.aplicada_em = datetime.now(timezone.utc).replace(tzinfo=None)
    db.commit()
    db.refresh(previsao)
    return _dict(previsao)


def apagar(db: Session, previsao_id: int) -> bool:
    """Só as que ainda não chegaram ao Mapa - uma aplicada apaga-se no Excel."""
    previsao = db.get(PrevisaoManual, previsao_id)
    if previsao is None or previsao.estado == "aplicada":
        return False
    db.delete(previsao)
    db.commit()
    return True


def imputacoes(db: Session, tipo: Optional[str] = None) -> List[str]:
    """Imputações já usadas no Mapa (e nas previsões manuais), por ordem
    alfabética - para a caixa de escolha do dashboard."""
    q = db.query(LinhaMapa.imputacao).filter(LinhaMapa.imputacao.isnot(None))
    m = db.query(PrevisaoManual.imputacao)
    if tipo:
        q = q.filter(LinhaMapa.tipo == tipo)
        m = m.filter(PrevisaoManual.tipo == tipo)
    valores = {str(v).strip() for (v,) in q.distinct().all()} | {v.strip() for (v,) in m.distinct().all()}
    return sorted((v for v in valores if v), key=str.casefold)
