import os

from sqlalchemy.orm import Session

from app.db.models import SaldoDiario
from app.services.reconciliador import abrir_workbook_com_retry, chave_empresa, nome_empresa_do_ficheiro


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


def consultar_saldo(db: Session, empresa: str, dia=None):
    """Consulta read-only: saldos guardados para uma empresa (por
    chave_empresa, ignora LDA/SA), opcionalmente filtrados por dia."""
    query = db.query(SaldoDiario)
    if dia is not None:
        query = query.filter(SaldoDiario.dia == dia)
    alvo = chave_empresa(empresa)
    return [s for s in query.all() if chave_empresa(s.entidade) == alvo]


def _ultimo_saldo_por_entidade(db: Session) -> dict:
    todos = db.query(SaldoDiario).order_by(SaldoDiario.dia).all()
    ultimo = {}
    for s in todos:
        ultimo[s.entidade] = s
    return ultimo


def saldo_total_geral(db: Session) -> dict:
    """Soma o último saldo conhecido de cada entidade - não é a soma de um
    dia específico, porque nem todos os dias têm leitura de todas as
    entidades (ex. contas sem movimento nesse dia)."""
    ultimo_por_entidade = _ultimo_saldo_por_entidade(db)

    total_contabilistico = sum(s.saldo_contabilistico or 0 for s in ultimo_por_entidade.values())
    total_disponivel = sum(s.saldo_disponivel or 0 for s in ultimo_por_entidade.values())
    return {
        "entidades": len(ultimo_por_entidade),
        "saldo_contabilistico_total": total_contabilistico,
        "saldo_disponivel_total": total_disponivel,
    }


def listar_saldos_atuais(db: Session):
    """Último saldo conhecido de cada entidade - para rankings/gráficos
    (ex. "quais as contas com mais saldo")."""
    return list(_ultimo_saldo_por_entidade(db).values())


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
