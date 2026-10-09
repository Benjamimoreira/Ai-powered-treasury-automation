"""Destinatários do email do Mapa de Pagamentos e Recebimentos
(enviar_mapa_smtp.py, Treasury-Automation-PT2).

O Pedro Duarte recebe sempre por omissão: se a lista estiver vazia (tabela
nova, ou alguém removeu todos), volta a ter só ele - o email das 16:30
nunca fica sem destinatário.
"""
import re
from typing import List

from sqlalchemy.orm import Session

from app.db.models import DestinatarioMapa

DESTINATARIO_OMISSAO = "pduarte@vidor.pt"
PADRAO_EMAIL = re.compile(r"^[^@\s,;]+@[^@\s,;]+\.[A-Za-z]{2,}$")


def listar_destinatarios(db: Session) -> List[str]:
    if db.query(DestinatarioMapa).count() == 0:
        db.add(DestinatarioMapa(email=DESTINATARIO_OMISSAO))
        db.commit()
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
