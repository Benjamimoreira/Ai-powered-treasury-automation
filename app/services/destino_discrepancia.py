"""Para onde foi o valor de uma discrepância (Monitorização > Discrepâncias
de saldos: fim do botão "Atualizar" e botão "Detalhe").

Uma discrepância Extrato ≠ Mapa num dia quase sempre é um movimento que
está num dia no extrato do banco e noutro no Mapa (ex. "Trf Mbway -374,50 €"
no extrato a 10/10 e no Mapa a 12/10), ou que só está de um dos lados. Aqui
casam-se, para a empresa e uma janela de dias à volta do dia, os
movimentos bancários com as linhas do Mapa pelo valor - primeiro no mesmo
dia, depois noutro - e devolvem-se só os que sobram no dia da discrepância,
cada um com a explicação (onde está do outro lado, ou que falta).
"""
from datetime import date, timedelta
from typing import Any, Dict, List

from sqlalchemy.orm import Session

from app.db.models import LinhaMapa, MovimentoBancario
from app.services.calendario import folha_do_movimento
from app.services.reconciliador import TOLERANCIA_VALOR, chave_empresa, empresa_do_mapa_corresponde

JANELA_DIAS = 6


def _da_empresa(nome: str, empresa: str) -> bool:
    """O Mapa usa nomes curtos ("Palavra") e o extrato a designação completa
    ("PALAVRADICIONAL,LDA"); a discrepância pode vir com qualquer um."""
    return (chave_empresa(nome) == chave_empresa(empresa)
            or empresa_do_mapa_corresponde(nome, empresa) or empresa_do_mapa_corresponde(empresa, nome))


def _euros(valor: float) -> str:
    texto = f"{valor:,.2f}".replace(",", " ").replace(".", ",").replace(" ", ".")
    return f"{texto} €"


def _ddmm(dia: date) -> str:
    return dia.strftime("%d/%m")


def _texto_linha(l: LinhaMapa) -> str:
    partes = [f"linha {l.linha}", l.descricao or "sem descrição", l.imputacao or "sem imputação"]
    if l.pago is None:
        partes.append("só previsto, sem pago")
    return " · ".join(partes)


def _casar(movimentos: List[MovimentoBancario], linhas: List[LinhaMapa], valor_linha) -> List[tuple]:
    """Pares (movimento, linha) com o mesmo valor, cada um usado uma vez."""
    pares, usadas = [], set()
    for m in movimentos:
        for l in linhas:
            if l.id not in usadas and abs(valor_linha(l) - m.valor) < TOLERANCIA_VALOR:
                pares.append((m, l))
                usadas.add(l.id)
                break
    return pares


def destino_discrepancia(db: Session, dia: date, empresa: str, janela: int = JANELA_DIAS) -> Dict[str, Any]:
    inicio, fim = dia - timedelta(days=janela), dia + timedelta(days=janela)
    movimentos = [m for m in db.query(MovimentoBancario)
                  .filter(MovimentoBancario.dia >= inicio, MovimentoBancario.dia <= fim)
                  .order_by(MovimentoBancario.dia, MovimentoBancario.id).all()
                  if _da_empresa(m.empresa, empresa)]
    linhas = [l for l in db.query(LinhaMapa)
              .filter(LinhaMapa.dia >= inicio, LinhaMapa.dia <= fim)
              .order_by(LinhaMapa.dia, LinhaMapa.linha).all()
              if _da_empresa(l.empresa, empresa) and (l.pago is not None or l.previsto is not None)]
    pagas = [l for l in linhas if l.pago is not None]

    # em que folha do Mapa fica cada movimento: os de sábado, domingo ou
    # feriado vão para o dia útil seguinte (calendario.py - regra do preencher)
    folha = {m.id: folha_do_movimento(m.dia) for m in movimentos}

    # 1) na mesma folha: o que bate certo (pago = valor do extrato)
    sobram_m = {m.id: m for m in movimentos}
    sobram_l = {l.id: l for l in pagas}
    for d in sorted(set(folha.values()) | {l.dia for l in pagas}):
        for m, l in _casar([m for m in sobram_m.values() if folha[m.id] == d],
                           [l for l in sobram_l.values() if l.dia == d], lambda l: l.pago):
            del sobram_m[m.id], sobram_l[l.id]

    itens: List[Dict[str, Any]] = []
    # 2) o que sobra no dia: procurar o mesmo valor noutro dia (o mais perto)
    perto = lambda x: (abs((x.dia - dia).days), x.dia)

    def no_extrato(m) -> str:
        transporte = f", vai para a folha de {_ddmm(folha[m.id])}" if folha[m.id] != m.dia else ""
        return f"{_ddmm(m.dia)} ({m.descricao}{transporte})"

    for m in [m for m in sobram_m.values() if folha[m.id] == dia]:
        candidatas = sorted([l for l in sobram_l.values() if l.dia != dia] +
                            [l for l in linhas if l.pago is None and l.previsto is not None], key=perto)
        par = next((l for l in candidatas if abs((l.pago if l.pago is not None else l.previsto) - m.valor)
                    < TOLERANCIA_VALOR), None)
        item = {"lado": "extrato", "dia": m.dia.isoformat(), "valor": m.valor, "descricao": m.descricao,
                "imputacao": None, "linha": None}
        if par is not None:
            sobram_l.pop(par.id, None)
            item.update(estado="noutro_dia", outro_dia=par.dia.isoformat(), explicacao=(
                f"{_euros(m.valor)} no extrato a {no_extrato(m)} — no Mapa está a "
                f"{_ddmm(par.dia)} ({_texto_linha(par)})"))
        else:
            item.update(estado="em_falta", outro_dia=None, explicacao=(
                f"{_euros(m.valor)} no extrato a {no_extrato(m)} — não está no Mapa "
                f"de {_ddmm(inicio)} a {_ddmm(fim)} (falta lançar)"))
        itens.append(item)
    for l in [l for l in sobram_l.values() if l.dia == dia]:
        par = next((m for m in sorted(sobram_m.values(), key=perto)
                    if folha[m.id] != dia and abs(m.valor - l.pago) < TOLERANCIA_VALOR), None)
        item = {"lado": "mapa", "dia": l.dia.isoformat(), "valor": l.pago, "descricao": l.descricao,
                "imputacao": l.imputacao, "linha": l.linha}
        if par is not None:
            sobram_m.pop(par.id, None)
            item.update(estado="noutro_dia", outro_dia=par.dia.isoformat(), explicacao=(
                f"{_euros(l.pago)} no Mapa a {_ddmm(l.dia)} ({_texto_linha(l)}) — no extrato está a "
                f"{no_extrato(par)}"))
        else:
            item.update(estado="em_falta", outro_dia=None, explicacao=(
                f"{_euros(l.pago)} no Mapa a {_ddmm(l.dia)} ({_texto_linha(l)}) — não aparece no "
                f"extrato de {_ddmm(inicio)} a {_ddmm(fim)}"))
        itens.append(item)

    soma_extrato = sum(m.valor for m in movimentos if folha[m.id] == dia)
    soma_mapa = sum(l.pago for l in pagas if l.dia == dia)
    return {
        "dia": dia.isoformat(), "empresa": empresa,
        "janela": {"inicio": inicio.isoformat(), "fim": fim.isoformat()},
        "soma_extrato": round(soma_extrato, 2), "soma_mapa": round(soma_mapa, 2),
        "diferenca": round(soma_extrato - soma_mapa, 2),
        "itens": itens,
        # só há lançamentos noutro dia: voltar a extrair não resolve - é o
        # Mapa que tem de mudar de dia (ou o banco lançou noutra data)
        "so_noutro_dia": bool(itens) and all(i["estado"] == "noutro_dia" for i in itens),
    }
