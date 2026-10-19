"""Destinatários do email do Mapa de Pagamentos e Recebimentos
(enviar_mapa_smtp.py, Treasury-Automation-PT2).

O Mapa vai só para quem está nesta lista (pedido 19/10/2026 - antes, com a
lista vazia, voltava a pôr pduarte@vidor.pt sozinho). Lista vazia = o
enviar_mapa_smtp não envia e reporta erro; o dashboard não deixa remover o
último.
"""
import re
from typing import List

from sqlalchemy.orm import Session

from app.db.models import DestinatarioMapa

PADRAO_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")


def listar_destinatarios(db: Session) -> List[str]:
    return [d.email for d in db.query(DestinatarioMapa).order_by(DestinatarioMapa.id).all()]


def adicionar_destinatario(db: Session, email: str) -> List[str]:
    email = (email or "").strip().lower()
    if not PADRAO_EMAIL.match(email):
        raise ValueError(f"Email inválido: {email or '(vazio)'}")
    atuais = listar_destinatarios(db)
    if email not in atuais:
        db.add(DestinatarioMapa(email=email))
        db.commit()
    return listar_destinatarios(db)


def remover_destinatario(db: Session, email: str) -> List[str]:
    email = (email or "").strip().lower()
    db.query(DestinatarioMapa).filter(DestinatarioMapa.email == email).delete()
    db.commit()
    return listar_destinatarios(db)
