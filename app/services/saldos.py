import os

from sqlalchemy import func
from sqlalchemy.orm import Session, aliased

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


def _ultimas_leituras_por_entidade(db: Session, ate_dia=None, n: int = 1) -> dict:
    """Últimas `n` leituras conhecidas de cada entidade (mais recente
    primeiro) - até `ate_dia` inclusive, se indicado (para responder "como
    estava o saldo neste dia", já que nem todas as contas têm leitura em
    todos os dias). Sem `ate_dia`, usa mesmo as mais recentes de sempre.

    Usa ROW_NUMBER() em vez de carregar a tabela toda para memória e
    filtrar em Python - esta tabela só cresce (uma leitura por
    entidade/dia, todos os dias, para sempre) e antes disto era varrida
    por inteiro em cada consulta ao mapa de saldos."""
    linha = (
        func.row_number()
        .over(partition_by=SaldoDiario.entidade, order_by=SaldoDiario.dia.desc())
        .label("linha")
    )
    query = db.query(SaldoDiario, linha)
    if ate_dia is not None:
        query = query.filter(SaldoDiario.dia <= ate_dia)
    subquery = query.subquery()
    SaldoNumerado = aliased(SaldoDiario, subquery)

    leituras = (
        db.query(SaldoNumerado)
        .filter(subquery.c.linha <= n)
        .order_by(SaldoNumerado.entidade, subquery.c.linha)
        .all()
    )

    # Reagrupa por chave_empresa() - a mesma normalização usada em
    # consultar_saldo() - para não separar o histórico de uma entidade cujo
    # nome varia ligeiramente entre extratos (ex. "LDA" vs sem sigla); o
    # ROW_NUMBER() acima particiona pelo nome bruto, por isso duas variantes
    # da mesma empresa chegam aqui como grupos distintos.
    por_chave = {}
    for s in leituras:
        por_chave.setdefault(chave_empresa(s.entidade), []).append(s)

    por_entidade = {}
    for leituras_chave in por_chave.values():
        leituras_chave.sort(key=lambda s: s.dia, reverse=True)
        nome_exibicao = leituras_chave[0].entidade
        por_entidade[nome_exibicao] = leituras_chave[:n]
    return por_entidade


def _ultimo_saldo_por_entidade(db: Session, ate_dia=None) -> dict:
    """Última leitura conhecida de cada entidade - até `ate_dia` inclusive,
    se indicado. Sem `ate_dia`, devolve mesmo a mais recente de sempre."""
    return {
        entidade: leituras[0]
        for entidade, leituras in _ultimas_leituras_por_entidade(db, ate_dia=ate_dia, n=1).items()
    }


def saldo_total_geral(db: Session, dia=None) -> dict:
    """Soma o último saldo conhecido de cada entidade até `dia` (ou o mais
    recente de sempre, sem `dia`) - não é a soma das leituras desse dia
    exato, porque nem todos os dias têm leitura de todas as entidades (ex.
    contas sem movimento nesse dia)."""
    ultimo_por_entidade = _ultimo_saldo_por_entidade(db, ate_dia=dia)

    total_contabilistico = sum(s.saldo_contabilistico or 0 for s in ultimo_por_entidade.values())
    total_disponivel = sum(s.saldo_disponivel or 0 for s in ultimo_por_entidade.values())
    return {
        "entidades": len(ultimo_por_entidade),
        "saldo_contabilistico_total": total_contabilistico,
        "saldo_disponivel_total": total_disponivel,
    }


def listar_saldos_atuais(db: Session, dia=None):
    """Último saldo conhecido de cada entidade até `dia` (ou o mais recente
    de sempre, sem `dia`) - para rankings/gráficos (ex. "quais as contas
    com mais saldo")."""
    return list(_ultimo_saldo_por_entidade(db, ate_dia=dia).values())


def mapa_saldos(db: Session, dia=None) -> list:
    """Saldo de cada entidade até `dia` (ou o mais recente de sempre, sem
    `dia`), lado a lado com a leitura anterior dessa mesma entidade -
    para mostrar de relance quem teve entrada/saída de dinheiro (seta +
    variação %). "Anterior" é a leitura conhecida imediatamente antes,
    não necessariamente o dia de calendário anterior, porque nem todas as
    contas têm leitura todos os dias."""
    por_entidade = _ultimas_leituras_por_entidade(db, ate_dia=dia, n=2)

    def variacao(atual_valor, anterior_valor):
        if atual_valor is None or anterior_valor is None:
            return None, None
        delta = atual_valor - anterior_valor
        pct = (delta / abs(anterior_valor) * 100) if anterior_valor != 0 else None
        return delta, pct

    resultado = []
    for entidade, leituras in por_entidade.items():
        atual = leituras[0]
        anterior = leituras[1] if len(leituras) > 1 else None

        var_contabilistico, var_pct_contabilistico = variacao(
            atual.saldo_contabilistico, anterior.saldo_contabilistico if anterior else None
        )
        var_disponivel, var_pct_disponivel = variacao(
            atual.saldo_disponivel, anterior.saldo_disponivel if anterior else None
        )

        resultado.append({
            "entidade": entidade,
            "dia": atual.dia,
            "saldo_contabilistico": atual.saldo_contabilistico,
            "saldo_disponivel": atual.saldo_disponivel,
            "dia_anterior": anterior.dia if anterior else None,
            "saldo_contabilistico_anterior": anterior.saldo_contabilistico if anterior else None,
            "saldo_disponivel_anterior": anterior.saldo_disponivel if anterior else None,
            "variacao_contabilistico": var_contabilistico,
            "variacao_disponivel": var_disponivel,
            "variacao_pct_contabilistico": var_pct_contabilistico,
            "variacao_pct_disponivel": var_pct_disponivel,
        })

    resultado.sort(key=lambda r: r["entidade"])
    return resultado


def _serie_saldo_total_bruta(db: Session) -> list:
    """Núcleo de serie_saldo_total(): mesma lógica (soma da última leitura
    conhecida de cada entidade, dia a dia), mas devolve `dia` como objeto
    `date` em vez de string ISO - para uso interno (ex. previsao.py, que
    precisa de objetos `date` para somar `timedelta`), reaproveitado por
    serie_saldo_total() para a resposta da API."""
    leituras = db.query(SaldoDiario).order_by(SaldoDiario.dia).all()
    if not leituras:
        return []

    ultimo_contabilistico: dict = {}
    ultimo_disponivel: dict = {}
    resultado = []
    dia_atual = leituras[0].dia

    def _ponto(dia):
        return {
            "dia": dia,
            "saldo_contabilistico_total": sum(v or 0 for v in ultimo_contabilistico.values()),
            "saldo_disponivel_total": sum(v or 0 for v in ultimo_disponivel.values()),
        }

    for s in leituras:
        if s.dia != dia_atual:
            resultado.append(_ponto(dia_atual))
            dia_atual = s.dia
        chave = chave_empresa(s.entidade)
        ultimo_contabilistico[chave] = s.saldo_contabilistico
        ultimo_disponivel[chave] = s.saldo_disponivel

    resultado.append(_ponto(dia_atual))
    return resultado


def serie_saldo_total(db: Session) -> list:
    """Evolução do saldo total (soma de todas as entidades) ao longo do
    tempo - para o gráfico "saldo bancário de todas as contas juntas" da
    Visão Geral. Para cada dia com pelo menos uma leitura, soma a última
    leitura conhecida de CADA entidade até esse dia (mesma lógica de
    saldo_total_geral, aplicada a todos os dias de uma vez) - uma conta
    sem movimento nesse dia entra com o saldo que já tinha, não com zero.

    Percorre as leituras uma única vez, ordenadas por dia (já vêm
    agrupadas por dia da própria ordenação) - O(leituras), evita repetir
    a consulta por dia que saldo_total_geral faz para um único dia."""
    return [
        {**ponto, "dia": ponto["dia"].isoformat()}
        for ponto in _serie_saldo_total_bruta(db)
    ]


def registar_saldos_do_dia(db: Session, dia, pasta_extratos: str) -> int:
    """Lê os saldos finais de cada extrato da pasta e grava-os em
    saldos_diarios. Devolve o número de entidades novas ou alteradas.

    Uma linha por (dia, entidade): se a entidade já tem saldo nesse dia,
    atualiza-o quando o extrato tem outro valor, em vez de duplicar
    (confirmado em produção: 30 entidades duplicadas no dia 23/07,
    distorcendo a média móvel da previsão de saldos) ou de o ignorar. O CGD
    publica uma versão provisória do dia às 14:00 e substitui a pasta pela
    versão final na manhã seguinte - um saldo provisório nunca era
    corrigido se a sincronização não corresse precisamente no dia a seguir
    (ex. sexta/sábado vistos só na segunda: confirmado em 06/10/2026, com o
    total de 02/10 igual ao de 01/10 e o de 06/10 igual ao de 05/10)."""
    saldos = ler_saldos_finais_do_dia(pasta_extratos)
    if not saldos:
        return 0

    existentes = {}
    for s in db.query(SaldoDiario).filter(SaldoDiario.dia == dia):
        existentes.setdefault(s.entidade, s)

    alteradas = 0
    for entidade, (contabilistico, disponivel) in saldos.items():
        atual = existentes.get(entidade)
        if atual is None:
            db.add(SaldoDiario(
                dia=dia,
                entidade=entidade,
                saldo_contabilistico=contabilistico,
                saldo_disponivel=disponivel,
            ))
            alteradas += 1
        elif (atual.saldo_contabilistico, atual.saldo_disponivel) != (contabilistico, disponivel):
            atual.saldo_contabilistico = contabilistico
            atual.saldo_disponivel = disponivel
            alteradas += 1
    db.commit()
    return alteradas
