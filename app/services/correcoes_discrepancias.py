"""Correções SEGURAS das discrepâncias no Mapa de Pagamentos e Recebimentos
(pedido 19/10/2026: corrigir todas as discrepâncias na mesma corrida, só o
que é seguro) - o que o corrigir_discrepancias.py (PT2) aplica, a meio do
deploy "corrigir_discrepancias" (scripts/deploy_windows.py).

A única correção feita sozinha é mudar de dia uma linha do Mapa que está
noutro dia que o movimento do banco (ver destino_discrepancia.py), e só
quando não há dúvida:
- o par é único nos dois sentidos: só um movimento sem linha com esse valor
  e só uma linha sem movimento com esse valor, na janela da discrepância;
- a linha já tem o valor recebido/pago e não tem Valor Previsto (não foi
  planeada à mão);
- a descrição da linha é o descritivo do banco (foi o preencher que a
  escreveu, não alguém à mão);
- os dois dias são do mesmo mês (o mesmo ficheiro do Mapa).
Tudo o resto (linha em falta, valor diferente, ambíguo, editado à mão)
fica na lista de discrepâncias para alguém decidir.
"""
import re
import unicodedata
from datetime import date, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from app.db.models import LinhaMapa, MovimentoBancario
from app.services.calendario import folha_do_movimento
from app.services.destino_discrepancia import JANELA_DIAS, _da_empresa, _ddmm
from app.services.discrepancias_saldos import listar_discrepancias
from app.services.reconciliador import TOLERANCIA_VALOR


def _normalizar(texto: Optional[str]) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^A-Z0-9]", "", texto.upper())


def descricao_do_banco(descricao_linha: Optional[str], descricao_movimento: str) -> bool:
    """A linha tem o descritivo do banco (o preencher copia-o; o extrato
    pode vir truncado num dos lados) - pelo menos 4 caracteres em comum."""
    linha, movimento = _normalizar(descricao_linha), _normalizar(descricao_movimento)
    if len(linha) < 4 or len(movimento) < 4:
        return False
    return linha == movimento or linha.startswith(movimento) or movimento.startswith(linha)


def movimentos_de_linha(db: Session, dia: date, empresa: str, janela: int = JANELA_DIAS) -> List[Dict[str, Any]]:
    """Linhas do Mapa a mudar de dia para a discrepância (dia, empresa) -
    lista vazia se não houver nenhuma sem dúvida."""
    inicio, fim = dia - timedelta(days=janela), dia + timedelta(days=janela)
    movimentos = [m for m in db.query(MovimentoBancario)
                  .filter(MovimentoBancario.dia >= inicio, MovimentoBancario.dia <= fim)
                  .order_by(MovimentoBancario.dia, MovimentoBancario.id).all()
                  if _da_empresa(m.empresa, empresa)]
    pagas = [l for l in db.query(LinhaMapa)
             .filter(LinhaMapa.dia >= inicio, LinhaMapa.dia <= fim, LinhaMapa.pago.isnot(None))
             .order_by(LinhaMapa.dia, LinhaMapa.linha).all()
             if _da_empresa(l.empresa, empresa)]
    folha = {m.id: folha_do_movimento(m.dia) for m in movimentos}

    # o que já bate certo na mesma folha sai (como em destino_discrepancia)
    sobram_m = {m.id: m for m in movimentos}
    sobram_l = {l.id: l for l in pagas}
    for d in sorted(set(folha.values()) | {l.dia for l in pagas}):
        usadas = set()
        for m in [m for m in sobram_m.values() if folha[m.id] == d]:
            l = next((l for l in sobram_l.values() if l.dia == d and l.id not in usadas
                      and abs(l.pago - m.valor) < TOLERANCIA_VALOR), None)
            if l is not None:
                usadas.add(l.id)
                del sobram_m[m.id], sobram_l[l.id]

    mover = []
    for m in sobram_m.values():
        linhas = [l for l in sobram_l.values() if l.dia != folha[m.id] and abs(l.pago - m.valor) < TOLERANCIA_VALOR]
        if len(linhas) != 1:
            continue  # nenhuma (falta lançar) ou várias (ambíguo)
        l = linhas[0]
        outros_m = [o for o in sobram_m.values() if folha[o.id] != l.dia and abs(o.valor - l.pago) < TOLERANCIA_VALOR]
        destino = folha[m.id]
        if (len(outros_m) != 1 or dia not in (l.dia, destino) or l.previsto is not None
                or (l.dia.year, l.dia.month) != (destino.year, destino.month)
                or not descricao_do_banco(l.descricao, m.descricao)):
            continue
        mover.append({
            "tipo": l.tipo, "empresa": l.empresa, "descricao": l.descricao,
            # na folha os valores são positivos (a BD guarda os pagamentos negativos)
            "valor": round(abs(l.pago), 2),
            "dia_origem": l.dia.isoformat(), "linha_origem": l.linha, "dia_destino": destino.isoformat(),
            "motivo": f"no extrato a {_ddmm(m.dia)} ({m.descricao})",
            "discrepancia": {"dia": dia.isoformat(), "empresa": empresa},
        })
    return mover


def correcoes_seguras(db: Session, discrepancias: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """As linhas a mudar de dia para estas discrepâncias ({dia, empresa}),
    sem repetir a mesma linha."""
    vistas, resultado = set(), []
    for d in discrepancias:
        if not d.get("dia") or not d.get("empresa") or d["empresa"] == "-":
            continue
        for mov in movimentos_de_linha(db, date.fromisoformat(d["dia"]), d["empresa"]):
            chave = (mov["dia_origem"], mov["tipo"], mov["linha_origem"])
            if chave not in vistas:
                vistas.add(chave)
                resultado.append(mov)
    return resultado


def discrepancias_atuais(db: Session, dias: int = 8) -> List[Dict[str, Any]]:
    """As discrepâncias com dia e empresa dos últimos `dias` (sem hoje: o
    preencher ainda mexe na folha de hoje)."""
    hoje = date.today()
    return [d for d in listar_discrepancias(db, dias=dias + 1)
            if d.get("dia") and d["empresa"] != "-"
            and hoje - timedelta(days=dias) <= date.fromisoformat(d["dia"]) < hoje]
