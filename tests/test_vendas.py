"""Casamento da agenda do índice comercial com os recebimentos do Mapa (vendas.casar_pagamentos)."""
from datetime import date

from app.services.vendas import casar_pagamentos

HOJE = date(2026, 10, 6)


def _p(tipo, dia, valor):
    return {"tipo": tipo, "dia": dia, "valor": valor}


def test_reforco_recebido_casa_pela_ref_mesmo_com_data_e_valor_diferentes():
    # ALVA Lote 1 A: sinal em março (só no índice), reforço marcado a 24/09 e recebido nesse dia
    agenda = [_p("Sinal (CPCV)", date(2026, 3, 24), 41_000), _p("Reforço de sinal", date(2026, 9, 24), 41_000),
              _p("Reforço de sinal", date(2027, 3, 24), 41_000)]
    casados, sobras = casar_pagamentos(agenda, [(date(2026, 9, 24), 41_000.0)], HOJE)

    assert [p["estado"] for p in casados] == ["recebido (índice)", "recebido", "agendado"]
    assert casados[1]["recebido_em"] == "2026-09-24"
    assert sobras == []


def test_pagamento_com_data_passada_sem_entrada_fica_por_confirmar():
    agenda = [_p("Sinal (CPCV)", date(2026, 4, 22), 5_500), _p("Escritura", date(2026, 5, 8), 49_500)]
    casados, _ = casar_pagamentos(agenda, [], HOJE)
    assert [p["estado"] for p in casados] == ["recebido (índice)", "por confirmar"]


def test_recebimento_so_casa_dentro_da_tolerancia_e_uma_vez():
    agenda = [_p("Reforço de sinal", date(2026, 9, 1), 40_000), _p("Reforço de sinal", date(2026, 9, 2), 40_000)]
    recebimentos = [(date(2026, 9, 10), 40_500.0),   # +1,25%: casa
                    (date(2026, 12, 1), 40_000.0)]   # 90 dias depois: fora da janela
    casados, sobras = casar_pagamentos(agenda, recebimentos, HOJE)

    assert [p["estado"] for p in casados] == ["recebido", "por confirmar"]
    assert sobras == [(date(2026, 12, 1), 40_000.0)]  # sobra: conta no recebido do negócio, fora da agenda


def test_valor_muito_diferente_nao_casa():
    casados, sobras = casar_pagamentos([_p("Escritura", date(2026, 9, 1), 100_000)],
                                       [(date(2026, 9, 1), 39_000.0)], HOJE)
    assert casados[0]["estado"] == "por confirmar"
    assert sobras == [(date(2026, 9, 1), 39_000.0)]
