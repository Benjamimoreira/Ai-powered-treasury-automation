import os
import re
import shutil
import tempfile
import time
import unicodedata

import openpyxl
from sqlalchemy.orm import Session

from app.db.models import AuditoriaDia, CasoAmbiguo, LinhaMapa, MovimentoBancario, Reconciliacao
from app.services.monitorizacao import _isoformat

TOLERANCIA_VALOR = 0.01

PALAVRAS_IGNORAR = {"LDA", "SA", "DE", "DA", "DO", "E"}


def abrir_workbook_com_retry(caminho, tentativas=5, espera=1.0):
    """Abre um .xlsx com openpyxl, tentando novamente em caso de
    PermissionError transitório (ficheiros sincronizados no OneDrive,
    antivírus a indexar, ficheiro aberto no Excel). Lê a partir de uma
    cópia temporária para não depender de o ficheiro original ficar
    livre a meio da tentativa."""
    ultimo_erro = None
    for tentativa in range(tentativas):
        try:
            with tempfile.TemporaryDirectory() as tmp:
                destino = os.path.join(tmp, os.path.basename(caminho))
                shutil.copyfile(caminho, destino)
                return openpyxl.load_workbook(destino, data_only=True)
        except PermissionError as e:
            ultimo_erro = e
            if tentativa < tentativas - 1:
                time.sleep(espera)
    raise PermissionError(
        f"Não consegui ler '{caminho}' depois de {tentativas} tentativas "
        f"(ficheiro continua bloqueado)."
    ) from ultimo_erro


def ler_movimentos_do_extrato(caminho):
    wb = abrir_workbook_com_retry(caminho)
    ws = wb.active

    linha_cabecalho = None
    for row in ws.iter_rows(min_row=1, max_row=20):
        if row[0].value and str(row[0].value).strip().startswith("Data mov"):
            linha_cabecalho = row[0].row
            break
    if linha_cabecalho is None:
        return []

    movimentos = []
    for row in ws.iter_rows(min_row=linha_cabecalho + 1):
        data_mov, _data_valor, descricao, montante = row[0].value, row[1].value, row[2].value, row[3].value
        if data_mov is None and descricao is None:
            break
        if montante is None:
            continue
        valor = float(str(montante).strip().replace(".", "").replace(",", "."))
        movimentos.append({
            "descricao": str(descricao).strip() if descricao else "",
            "valor": valor,
        })
    return movimentos


def remover_acentos(texto):
    nfkd = unicodedata.normalize("NFKD", texto)
    return "".join(c for c in nfkd if not unicodedata.combining(c))


def normalizar(texto):
    texto = remover_acentos(str(texto)).upper()
    return re.sub(r"[^A-Z0-9]", "", texto)


def nome_empresa_do_ficheiro(caminho):
    nome = os.path.splitext(os.path.basename(caminho))[0]
    nome = re.sub(r"^\d{2}-\d{2}-\d{4}_", "", nome)
    return nome.strip()


def _partes_significativas(nome_completo) -> list:
    partes = [p for p in re.split(r"[\s,]+", str(nome_completo).strip()) if p]
    return [p for p in partes if normalizar(p) not in PALAVRAS_IGNORAR]


def chave_empresa(nome_completo):
    """Chave normalizada do nome da empresa sem palavras tipo LDA/SA, para
    que 'ANCORA APOGEU,LDA' e 'Ancora Apogeu' (sem sigla) batam certo."""
    return normalizar(" ".join(_partes_significativas(nome_completo)))


_ABREVIATURA_SGPS = "SOCIEDADE GESTORA PARTICIPACOES SOCIAIS"


def _chave_ampla(nome) -> str:
    """Como chave_empresa(), mas expande a sigla legal "SGPS" (Sociedade
    Gestora de Participações Sociais) antes de normalizar - só usada para
    aproximar os códigos curtos do Mapa (linhas_mapa.empresa, ex. "Vidor
    SGPS") da designação social completa (movimentos_bancarios.empresa /
    saldos_diarios.entidade, ex. "VIDOR SOCIEDADE GESTORA PARTICIPACOES
    SOCIAIS,SA"). Nunca usada na reconciliação banco vs. Mapa
    (chave_empresa, já validada) para não lhe mudar o comportamento."""
    nome = re.sub(r"\bSGPS\b", _ABREVIATURA_SGPS, str(nome), flags=re.IGNORECASE)
    return chave_empresa(nome)


def empresa_do_mapa_corresponde(nome_mapa: str, nome_canonico: str) -> bool:
    """True se `nome_mapa` (texto livre de linhas_mapa.empresa - ex.
    "H.C.C", "Vidor SGPS", "Palavra", "Viduarte") pode razoavelmente
    designar `nome_canonico` (designação social completa, de
    movimentos_bancarios.empresa / saldos_diarios.entidade - a mesma lista
    de empresas usada na aba Saldos): por igualdade direta, por
    `nome_mapa` ser um prefixo/abreviação por corte do nome completo (ex.
    "Viduarte" de "VIDUARTE INDUSTRIA CONSTRUCAO CIVIL,LDA") ou por serem
    as iniciais de cada palavra significativa (ex. "H.C.C" para "Habiserve
    Construções Centro")."""
    chave_mapa = _chave_ampla(nome_mapa)
    if not chave_mapa:
        return False
    chave_canonico = _chave_ampla(nome_canonico)
    if chave_mapa == chave_canonico or chave_canonico.startswith(chave_mapa):
        return True
    iniciais = "".join(p[0] for p in _partes_significativas(nome_canonico))
    return chave_mapa == normalizar(iniciais)


def importar_extrato_para_bd(db: Session, caminho: str, dia, empresa: str) -> int:
    """Lê um ficheiro de extrato (.xlsx) e grava cada movimento como uma
    linha em movimentos_bancarios. Devolve o número de movimentos
    inseridos. Não faz deteção de duplicados (isso é Fase 2 -
    reconciliação); esta função só migra os dados brutos do Excel para
    a base de dados."""
    movimentos = ler_movimentos_do_extrato(caminho)
    for mov in movimentos:
        db.add(MovimentoBancario(
            dia=dia,
            empresa=empresa,
            descricao=mov["descricao"],
            valor=mov["valor"],
            ficheiro_origem=os.path.basename(caminho),
        ))
    db.commit()
    return len(movimentos)


def _linha_bate_com_movimento(linha: LinhaMapa, movimento: MovimentoBancario) -> bool:
    if linha.previsto is None or linha.pago is not None:
        return False
    return (
        chave_empresa(linha.empresa) == chave_empresa(movimento.empresa)
        and abs(linha.previsto - movimento.valor) < TOLERANCIA_VALOR
    )


def reconciliar_dia(db: Session, dia) -> dict:
    """Corre a reconciliação de um dia: tenta casar cada movimento bancário
    ainda não processado com uma linha do mapa (linhas_mapa) do mesmo dia,
    pela empresa (chave_empresa) e pelo valor (com tolerância). Grava o
    resultado em Reconciliacao (e CasoAmbiguo quando há mais que uma linha
    candidata). Correr duas vezes para o mesmo dia não duplica trabalho -
    só processa movimentos que ainda não têm nenhuma Reconciliacao."""
    movimentos = (
        db.query(MovimentoBancario)
        .filter(MovimentoBancario.dia == dia)
        .filter(~MovimentoBancario.reconciliacoes.any())
        .all()
    )
    linhas = db.query(LinhaMapa).filter(LinhaMapa.dia == dia).all()

    casados = novos = ambiguos = 0
    linhas_ja_usadas = set()

    for movimento in movimentos:
        candidatos = [
            linha for linha in linhas
            if linha.id not in linhas_ja_usadas and _linha_bate_com_movimento(linha, movimento)
        ]

        if len(candidatos) == 1:
            linha = candidatos[0]
            linha.pago = movimento.valor
            linhas_ja_usadas.add(linha.id)
            db.add(Reconciliacao(movimento_id=movimento.id, linha_id=linha.id, tipo_match="exato"))
            casados += 1
        elif len(candidatos) > 1:
            db.add(Reconciliacao(movimento_id=movimento.id, linha_id=None, tipo_match="ambiguo"))
            db.add(CasoAmbiguo(
                movimento_id=movimento.id,
                dia=dia,
                empresa=movimento.empresa,
                valor=movimento.valor,
                candidatos=[linha.id for linha in candidatos],
            ))
            ambiguos += 1
        else:
            db.add(Reconciliacao(movimento_id=movimento.id, linha_id=None, tipo_match="novo"))
            novos += 1

    db.commit()
    return {"casados": casados, "novos": novos, "ambiguos": ambiguos}


def listar_movimentos_do_dia(db: Session, dia):
    """Consulta read-only: devolve cada movimento do dia com o seu estado
    atual (casado/novo/ambíguo, ou por processar se reconciliar_dia ainda
    não correu para ele). Nunca escreve nada - dá para chamar quantas
    vezes quiseres sem duplicar ou reprocessar nada."""
    movimentos = db.query(MovimentoBancario).filter(MovimentoBancario.dia == dia).all()

    resultado = []
    for movimento in movimentos:
        reconciliacao = (
            db.query(Reconciliacao).filter(Reconciliacao.movimento_id == movimento.id).first()
        )
        linha = db.get(LinhaMapa, reconciliacao.linha_id) if reconciliacao and reconciliacao.linha_id else None
        resultado.append({
            "id": movimento.id,
            "empresa": movimento.empresa,
            "descricao": movimento.descricao,
            "valor": movimento.valor,
            "tipo_match": reconciliacao.tipo_match if reconciliacao else None,
            "linha_id": linha.id if linha else None,
            "linha_imputacao": linha.imputacao if linha else None,
        })
    return resultado


def listar_empresas(db: Session):
    """Lista as empresas distintas com movimentos importados - usada para
    preencher um seletor no dashboard, em vez de o utilizador ter de
    adivinhar/escrever o nome exato."""
    linhas = db.query(MovimentoBancario.empresa).distinct().order_by(MovimentoBancario.empresa).all()
    return [empresa for (empresa,) in linhas]


def listar_movimentos_da_empresa(db: Session, empresa: str):
    """Consulta read-only: todos os movimentos de uma empresa (qualquer
    dia), ordenados por dia - para analisar o histórico/fluxo de uma conta
    ao longo do tempo."""
    alvo = chave_empresa(empresa)
    movimentos = db.query(MovimentoBancario).order_by(MovimentoBancario.dia).all()
    return [
        {"id": m.id, "dia": m.dia.isoformat(), "descricao": m.descricao, "valor": m.valor}
        for m in movimentos
        if chave_empresa(m.empresa) == alvo
    ]


def resumo_diario(db: Session):
    """Totais de recebimentos (valor >= 0) e pagamentos (valor < 0, em
    módulo) por dia, somados por todas as empresas - para o gráfico de
    fluxo mensal da Visão Geral."""
    movimentos = db.query(MovimentoBancario).order_by(MovimentoBancario.dia).all()
    por_dia = {}
    for m in movimentos:
        totais = por_dia.setdefault(m.dia, {"recebimentos": 0.0, "pagamentos": 0.0})
        if m.valor >= 0:
            totais["recebimentos"] += m.valor
        else:
            totais["pagamentos"] += -m.valor
    return [
        {"dia": dia, "recebimentos": totais["recebimentos"], "pagamentos": totais["pagamentos"]}
        for dia, totais in sorted(por_dia.items())
    ]


def _linhas_mapa_filtradas(db: Session, empresa: str = None, dia_inicio=None, dia_fim=None) -> list:
    """Linhas do Mapa já confirmadas no extrato (pago preenchido) no
    período/empresa pedidos - base comum de analise_imputacoes() (somas
    por categoria, para o gráfico circular) e listar_linhas_imputacao()
    (linhas em detalhe, para a tabela por baixo do gráfico).

    `empresa`, quando dado, é a designação social completa (a mesma lista
    da aba Saldos - ver listar_empresas()); linhas_mapa.empresa usa
    códigos curtos/abreviados (ex. "H.C.C"), por isso a comparação usa
    empresa_do_mapa_corresponde() em vez de igualdade direta."""
    query = db.query(LinhaMapa).filter(LinhaMapa.pago.isnot(None))
    if dia_inicio is not None:
        query = query.filter(LinhaMapa.dia >= dia_inicio)
    if dia_fim is not None:
        query = query.filter(LinhaMapa.dia <= dia_fim)
    linhas = query.order_by(LinhaMapa.dia.desc()).all()

    if empresa:
        linhas = [l for l in linhas if empresa_do_mapa_corresponde(l.empresa, empresa)]
    return linhas


def analise_imputacoes(db: Session, empresa: str = None, dia_inicio=None, dia_fim=None) -> dict:
    """Soma o valor pago (real, já sinalizado por importar_linhas - ver
    mapa_importer.py) de cada linha do Mapa agrupado por imputação e por
    tipo (recebimento/pagamento), para o gráfico circular "Análise de
    Extratos" - quanto de cada categoria pesa no total recebido/pago no
    período. Só considera linhas com pago preenchido (movimento já
    confirmado no extrato, não só previsto)."""
    linhas = _linhas_mapa_filtradas(db, empresa=empresa, dia_inicio=dia_inicio, dia_fim=dia_fim)

    somas = {"recebimento": {}, "pagamento": {}}
    for l in linhas:
        categoria = l.imputacao or "(sem imputação)"
        somas[l.tipo][categoria] = somas[l.tipo].get(categoria, 0.0) + abs(l.pago)

    def _ordenado(por_categoria: dict) -> list:
        return [
            {"imputacao": categoria, "valor": valor}
            for categoria, valor in sorted(por_categoria.items(), key=lambda item: item[1], reverse=True)
        ]

    return {
        "recebimentos": _ordenado(somas["recebimento"]),
        "pagamentos": _ordenado(somas["pagamento"]),
    }


def listar_linhas_imputacao(db: Session, empresa: str = None, dia_inicio=None, dia_fim=None) -> list:
    """Linhas do Mapa em detalhe (uma por movimento, não agregada) para a
    tabela de extratos por baixo do gráfico circular de "Análise de
    Extratos" - o dashboard usa `imputacao` de cada linha para pintá-la
    com a mesma cor da fatia do gráfico a que pertence. `previsto` vem
    também (sem sinal alterado, já vem sinalizado do mapa_importer.py) -
    usado pela tabela de CPCVs/Escrituras do início da aba (previsto =
    "valor tabelado/proposto" no vocabulário comercial, pago = "valor
    recebido")."""
    linhas = _linhas_mapa_filtradas(db, empresa=empresa, dia_inicio=dia_inicio, dia_fim=dia_fim)
    return [
        {
            "dia": l.dia.isoformat(),
            "empresa": l.empresa,
            "tipo": l.tipo,
            "imputacao": l.imputacao or "(sem imputação)",
            "previsto": l.previsto,
            "valor": abs(l.pago),
        }
        for l in linhas
    ]


def auditoria_dia(db: Session, dia) -> dict:
    """Verificação read-only (não grava nada): identifica movimentos sem
    correspondência numa linha do mapa (sem_match_fwd - algo que devia
    estar preenchido no Mapa e não está) e linhas do mapa com previsto
    ainda em aberto (sem pago) que não correspondem a nenhum movimento
    (sem_match_rev - algo preenchido no Mapa sem confirmação no extrato
    real). Devolve também a lista detalhada de cada um, com a empresa/
    ficheiro de origem do movimento, para o dashboard (aba Monitorização
    > Auditoria) mostrar exatamente o que falta/sobra e de onde veio.
    Linhas com previsto E pago já preenchidos (resolvidas antes de existir
    esta API) ficam de fora - já não são "previstos por bater", são
    histórico. Independente de reconciliar_dia já ter corrido."""
    movimentos = db.query(MovimentoBancario).filter(MovimentoBancario.dia == dia).all()
    linhas_abertas = db.query(LinhaMapa).filter(
        LinhaMapa.dia == dia, LinhaMapa.previsto.isnot(None), LinhaMapa.pago.is_(None),
    ).all()
    linhas_todas = db.query(LinhaMapa).filter(LinhaMapa.dia == dia).all()

    movimentos_sem_match = [
        movimento for movimento in movimentos
        if not any(_linha_bate_com_movimento(linha, movimento) for linha in linhas_abertas)
    ]
    linhas_sem_match = [
        linha for linha in linhas_abertas
        if not any(_linha_bate_com_movimento(linha, movimento) for movimento in movimentos)
    ]

    movimentos_dia_out = [
        {
            "empresa": m.empresa,
            "descricao": m.descricao,
            "valor": m.valor,
            "ficheiro_origem": m.ficheiro_origem,
        }
        for m in movimentos
    ]
    # soma_extrato: total real dos movimentos bancários desse dia (fonte:
    # extratos). soma_mapa: total do que já ficou confirmado ("real"/pago)
    # nas linhas do Mapa desse dia, com o mesmo sinal (ver mapa_importer -
    # pagamentos negativos, recebimentos positivos), para as duas somas
    # serem diretamente comparáveis. Uma diferença != 0 sinaliza movimento
    # do extrato ainda não refletido no Mapa (ou vice-versa).
    soma_extrato = sum(m.valor for m in movimentos)
    soma_mapa = sum(l.pago for l in linhas_todas if l.pago is not None)

    return {
        "sem_match_fwd": len(movimentos_sem_match),
        "sem_match_rev": len(linhas_sem_match),
        "movimentos_sem_match": [
            {
                "empresa": m.empresa,
                "descricao": m.descricao,
                "valor": m.valor,
                "ficheiro_origem": m.ficheiro_origem,
            }
            for m in movimentos_sem_match
        ],
        "linhas_sem_match": [
            {
                "linha": l.linha,
                "empresa": l.empresa,
                "previsto": l.previsto,
                "imputacao": l.imputacao,
            }
            for l in linhas_sem_match
        ],
        "movimentos_dia": movimentos_dia_out,
        "soma_extrato": soma_extrato,
        "soma_mapa": soma_mapa,
        "diferenca_extrato_mapa": soma_extrato - soma_mapa,
    }


def registar_auditoria_dia(db: Session, dia) -> dict:
    """Corre auditoria_dia e grava o resultado em auditorias_dia (uma
    linha nova por pedido, nunca substitui a anterior) - fica um histórico
    consultável mesmo depois de os dados de origem mudarem, ao contrário
    de auditoria_dia sozinho (só leitura, recalcula sempre na hora). Chamada
    apenas pelo botão "Auditar este dia"/"Auditoria geral" do dashboard -
    não há atualmente nenhuma chamada automática a partir de scripts
    externos (ex. preencher_mapa.py, que corre noutro repositório); se essa
    integração vier a existir, tem de ser feita explicitamente lá."""
    resultado = auditoria_dia(db, dia)
    db.add(AuditoriaDia(
        dia=dia,
        sem_match_fwd=resultado["sem_match_fwd"],
        sem_match_rev=resultado["sem_match_rev"],
        soma_extrato=resultado["soma_extrato"],
        soma_mapa=resultado["soma_mapa"],
        diferenca=resultado["diferenca_extrato_mapa"],
        movimentos_sem_match=resultado["movimentos_sem_match"],
        linhas_sem_match=resultado["linhas_sem_match"],
    ))
    db.commit()
    return resultado


def listar_historico_auditorias(db: Session, limit: int = 100) -> list:
    """Últimas auditorias registadas (mais recente primeiro) - histórico
    persistente, ao contrário do resultado ao vivo de auditoria_dia."""
    registos = (
        db.query(AuditoriaDia)
        .order_by(AuditoriaDia.timestamp.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "dia": r.dia.isoformat(),
            "timestamp": _isoformat(r.timestamp),
            "sem_match_fwd": r.sem_match_fwd,
            "sem_match_rev": r.sem_match_rev,
            "soma_extrato": r.soma_extrato,
            "soma_mapa": r.soma_mapa,
            "diferenca": r.diferenca,
        }
        for r in registos
    ]


def resolver_ambiguo(db: Session, caso_id: int, linha_id, resolvido_por: str) -> CasoAmbiguo:
    """Regista a decisão humana sobre um caso ambíguo: associa o movimento
    à linha escolhida (ou marca como 'novo' se linha_id for None)."""
    caso = db.get(CasoAmbiguo, caso_id)
    if caso is None:
        raise ValueError(f"Caso ambíguo {caso_id} não encontrado.")

    reconciliacao = (
        db.query(Reconciliacao)
        .filter(Reconciliacao.movimento_id == caso.movimento_id, Reconciliacao.tipo_match == "ambiguo")
        .first()
    )

    if linha_id is not None:
        linha = db.get(LinhaMapa, linha_id)
        if linha is None:
            raise ValueError(f"Linha {linha_id} não encontrada.")
        linha.pago = caso.valor
        caso.resolucao = f"linha_id={linha_id}"
        if reconciliacao is not None:
            reconciliacao.linha_id = linha_id
            reconciliacao.tipo_match = "exato"
    else:
        caso.resolucao = "novo"
        if reconciliacao is not None:
            reconciliacao.tipo_match = "novo"

    caso.resolvido_por = resolvido_por
    db.commit()
    return caso
