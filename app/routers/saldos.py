from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.session import get_db
from app.models import AtualizarSaldosRequest, SaldoOut, SaldoTotalOut
from app.services.saldos import consultar_saldo as consultar_saldo_servico
from app.services.saldos import registar_saldos_do_dia, saldo_total_geral

router = APIRouter()


@router.get("/saldo-total", response_model=SaldoTotalOut)
def saldo_total(db: Session = Depends(get_db)):
    """Soma o último saldo conhecido de cada entidade - visão geral, não
    de um único dia (nem todos os dias têm leitura de todas as contas)."""
    return saldo_total_geral(db)


@router.get("/saldos/{empresa}", response_model=List[SaldoOut])
def consultar_saldo(empresa: str, dia: Optional[date] = None, db: Session = Depends(get_db)):
    return consultar_saldo_servico(db, empresa, dia)


@router.post("/saldos/atualizar/{dia}")
def atualizar_saldos(dia: date, pedido: AtualizarSaldosRequest, db: Session = Depends(get_db)):
    entidades_registadas = registar_saldos_do_dia(db, dia, pedido.pasta_extratos)
    return {"dia": dia, "entidades_registadas": entidades_registadas}
