import os

from sqlalchemy.orm import Session

from app.db.models import SaldoDiario
from app.services.reconciliador import abrir_workbook_com_retry, nome_empresa_do_ficheiro


def parse_valor_eur(texto):
    """'141,24 EUR ' -> 141.24"""
    if texto is None:
        return None
    texto = str(texto).replace("EUR", "").strip()
    texto = texto.replace(".", "").replace(",", ".")
    try:
        return float(texto)
    except ValueError:
        return None


def ler_saldos_finais_do_dia(pasta_extratos: str):
    """Devolve {empresa_do_ficheiro: (saldo_contabilistico, saldo_disponivel)}
    lendo o topo de cada extrato .xlsx da pasta indicada."""
    if not os.path.isdir(pasta_extratos):
        return None

    saldos = {}
    for nome_ficheiro in sorted(os.listdir(pasta_extratos)):
        if not nome_ficheiro.lower().endswith(".xlsx"):
            continue
        caminho = os.path.join(pasta_extratos, nome_ficheiro)
        empresa = nome_empresa_do_ficheiro(caminho)
        wb = abrir_workbook_com_retry(caminho)
        ws = wb.active
        contabilistico = disponivel = None
        for r in range(1, 12):
            rotulo = ws[f"A{r}"].value
            if rotulo and "Saldo contabilístico" in str(rotulo):
                contabilistico = parse_valor_eur(ws[f"B{r}"].value)
            if rotulo and "Saldo disponível" in str(rotulo):
                disponivel = parse_valor_eur(ws[f"B{r}"].value)
        saldos[empresa] = (contabilistico, disponivel)
    return saldos


def registar_saldos_do_dia(db: Session, dia, pasta_extratos: str) -> int:
    """Lê os saldos finais de cada extrato da pasta e grava-os em
    saldos_diarios. Devolve o número de entidades registadas."""
    saldos = ler_saldos_finais_do_dia(pasta_extratos)
    if not saldos:
        return 0

    for entidade, (contabilistico, disponivel) in saldos.items():
        db.add(SaldoDiario(
            dia=dia,
            entidade=entidade,
            saldo_contabilistico=contabilistico,
            saldo_disponivel=disponivel,
        ))
    db.commit()
    return len(saldos)
