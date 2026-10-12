"""Dias úteis do Mapa de Pagamentos e Recebimentos - a mesma regra do
preencher (tesouraria-preenchimento/calendario.py, pedido de 06/10/2026):
os movimentos do extrato de um sábado, domingo ou feriado não ficam na
folha desse dia, passam para a do dia útil seguinte. Sem isto a auditoria
via, por exemplo, uma transferência de sábado como "a mais" no extrato de
sábado e "em falta" no extrato de segunda (12/10/2026: Palavra, 3294,52 €).
Têm de mudar as duas ao mesmo tempo.
"""
from datetime import date, timedelta
from functools import lru_cache
from typing import List

# só a partir desta data (até setembro/2026 os movimentos de fim de semana
# estão nas folhas do próprio dia)
DATA_INICIO_TRANSPORTE = date(2026, 10, 1)

# feriados que não são nacionais (ex. municipal) - também no preencher
FERIADOS_EXTRA: set = set()


def _pascoa(ano: int) -> date:
    a = ano % 19
    b, c = divmod(ano, 100)
    d, e = divmod(b, 4)
    f = (b + 8) // 25
    g = (b - f + 1) // 3
    h = (19 * a + b - d - g + 15) % 30
    i, k = divmod(c, 4)
    l = (32 + 2 * e + 2 * i - h - k) % 7
    m = (a + 11 * h + 22 * l) // 451
    mes, dia = divmod(h + l - 7 * m + 114, 31)
    return date(ano, mes, dia + 1)


@lru_cache(maxsize=None)
def feriados_nacionais(ano: int) -> frozenset:
    pascoa = _pascoa(ano)
    return frozenset({
        date(ano, 1, 1), pascoa - timedelta(days=2), pascoa, date(ano, 4, 25), date(ano, 5, 1),
        pascoa + timedelta(days=60), date(ano, 6, 10), date(ano, 8, 15), date(ano, 10, 5),
        date(ano, 11, 1), date(ano, 12, 1), date(ano, 12, 8), date(ano, 12, 25),
    })


def dia_util(dia: date) -> bool:
    return dia.weekday() < 5 and dia not in feriados_nacionais(dia.year) and dia not in FERIADOS_EXTRA


def extrato_transportado(dia: date) -> bool:
    """True se os movimentos do extrato deste dia vão para a folha do dia útil seguinte."""
    return dia >= DATA_INICIO_TRANSPORTE and not dia_util(dia)


def proximo_dia_util(dia: date) -> date:
    seguinte = dia + timedelta(days=1)
    while not dia_util(seguinte):
        seguinte += timedelta(days=1)
    return seguinte


def folha_do_movimento(dia: date) -> date:
    """Em que folha (dia) do Mapa fica um movimento do extrato deste dia."""
    return proximo_dia_util(dia) if extrato_transportado(dia) else dia


def dias_do_extrato_na_folha(dia: date) -> List[date]:
    """Os dias do extrato cujos movimentos estão na folha `dia` do Mapa: o
    próprio dia e os não úteis seguidos antes dele (ex. segunda: sábado,
    domingo, segunda); vazio para um sábado (os dele vão para a segunda)."""
    if extrato_transportado(dia):
        return []
    dias = [dia]
    anterior = dia - timedelta(days=1)
    while extrato_transportado(anterior):
        dias.insert(0, anterior)
        anterior -= timedelta(days=1)
    return dias
