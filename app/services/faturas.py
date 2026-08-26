"""Faturas recebidas em faturas@vidor.pt, reportadas pelo
recolher_faturas_recebidas.py (pasta "Fornecedores", fora do Docker) via
POST /faturas/recebidas - guarda na BD o mesmo que já vai para o Excel
mensal, para a dashboard mostrar sem precisar de abrir o ficheiro."""
from datetime import date
from typing import List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.db.models import FaturaRecebida
from app.models import FaturaRecebidaIn


def registar_faturas(db: Session, linhas: List[FaturaRecebidaIn]) -> dict:
    novas = duplicadas = 0
    vistos_neste_pedido = set()
    for linha in linhas:
        # duas verificações: já na BD (pedidos anteriores) e já visto neste
        # mesmo pedido (o mesmo email pode aparecer 2x no lote, ex. num
        # backfill a partir do Excel - sem isto, a 2ª linha só falhava na
        # BD com "unique constraint" em vez de ser tratada como duplicada)
        if linha.outlook_id in vistos_neste_pedido:
            duplicadas += 1
            continue
        existe = db.query(FaturaRecebida).filter(FaturaRecebida.outlook_id == linha.outlook_id).first()
        if existe:
            duplicadas += 1
            continue
        vistos_neste_pedido.add(linha.outlook_id)
        db.add(FaturaRecebida(
            outlook_id=linha.outlook_id,
            dia=linha.data_recebido.date(),
            hora=linha.data_recebido.strftime("%H:%M"),
            remetente=linha.remetente,
            assunto=linha.assunto,
            motivo=linha.motivo,
            empresa=linha.empresa,
            fornecedor=linha.fornecedor,
            nif_fornecedor=linha.nif_fornecedor,
            n_anexos_pdf=linha.n_anexos_pdf,
            debito=linha.debito,
            credito=linha.credito,
            saldo=linha.saldo,
            valor_fatura=linha.valor_fatura,
            pdf_relativo=linha.pdf_relativo,
        ))
        novas += 1
    db.commit()
    return {"novas": novas, "duplicadas": duplicadas}


def obter_fatura(db: Session, fatura_id: int) -> Optional[FaturaRecebida]:
    return db.query(FaturaRecebida).filter(FaturaRecebida.id == fatura_id).first()


def listar_faturas(db: Session, dia: Optional[date] = None, pesquisa: Optional[str] = None, limit: int = 200) -> list:
    query = db.query(FaturaRecebida)
    if dia:
        query = query.filter(FaturaRecebida.dia == dia)
    if pesquisa:
        # pesquisa em qualquer dia (não só na janela recente do "limit") -
        # por isso é um filtro à parte, não um contains() sobre o que já
        # foi carregado no browser
        termo = f"%{pesquisa}%"
        query = query.filter(
            or_(
                FaturaRecebida.empresa.ilike(termo),
                FaturaRecebida.fornecedor.ilike(termo),
                FaturaRecebida.nif_fornecedor.ilike(termo),
                FaturaRecebida.assunto.ilike(termo),
                FaturaRecebida.remetente.ilike(termo),
                FaturaRecebida.valor_fatura.ilike(termo),
            )
        )
    return (
        query.order_by(FaturaRecebida.dia.desc(), FaturaRecebida.hora.desc())
        .limit(limit)
        .all()
    )
