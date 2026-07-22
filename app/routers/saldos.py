from datetime import date
from typing import List, Optional

from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.db.models import SaldoDiario
from app.db.session import get_db
from app.models import AtualizarSaldosRequest, SaldoOut
from app.services.reconciliador import chave_empresa
from app.services.saldos import registar_saldos_do_dia

router = APIRouter()


@router.get("/saldos/{empresa}", response_model=List[SaldoOut])
def consultar_saldo(empresa: str, dia: Optional[date] = None, db: Session = Depends(get_db)):
    query = db.query(SaldoDiario)
    if dia is not None:
        query = query.filter(SaldoDiario.dia == dia)
    alvo = chave_empresa(empresa)
    return [s for s in query.all() if chave_empresa(s.entidade) == alvo]


@router.post("/saldos/atualizar/{dia}")
def atualizar_saldos(dia: date, pedido: AtualizarSaldosRequest, db: Session = Depends(get_db)):
    entidades_registadas = registar_saldos_do_dia(db, dia, pedido.pasta_extratos)
    return {"dia": dia, "entidades_registadas": entidades_registadas}
