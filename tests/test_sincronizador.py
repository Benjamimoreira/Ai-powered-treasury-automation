from datetime import datetime, timezone

import requests

from app import sincronizador


class _Resposta:
    def __init__(self, corpo):
        self._corpo = corpo

    def raise_for_status(self):
        pass

    def json(self):
        return self._corpo


def _api_falsa(monkeypatch, corpo_sync=None, falha=None):
    reportados = []

    def post(url, json=None, params=None, timeout=None):
        if url.endswith("/atualizar-dados"):
            if falha:
                raise falha
            return _Resposta(corpo_sync)
        reportados.append(json)
        return _Resposta({})

    monkeypatch.setattr(sincronizador.requests, "post", post)
    return reportados


def test_so_reporta_quando_ha_dados_novos(monkeypatch):
    reportados = _api_falsa(monkeypatch, {"dias_com_movimentos_novos": [], "dias_com_saldos_novos": [],
                                          "dias_com_mapa_novo": [], "erros": []})
    sincronizador.sincronizar()
    assert reportados == []

    reportados = _api_falsa(monkeypatch, {"dias_com_movimentos_novos": ["2026-10-08"],
                                          "dias_com_saldos_novos": ["2026-10-08"], "erros": []})
    sincronizador.sincronizar()
    assert [r["status"] for r in reportados] == ["ok"]
    assert reportados[0]["log"] == ["movimentos novos: 2026-10-08", "saldos novos: 2026-10-08"]


def test_reporta_erros_da_importacao_e_api_em_baixo(monkeypatch):
    reportados = _api_falsa(monkeypatch, {"erros": ["saldos 2026-10-08: ficheiro corrompido"]})
    sincronizador.sincronizar()
    assert reportados[0]["status"] == "warning"
    assert "ficheiro corrompido" in reportados[0]["erro"]

    reportados = _api_falsa(monkeypatch, falha=requests.ConnectionError("API em baixo"))
    assert "erro" in sincronizador.sincronizar()
    assert reportados[0]["status"] == "erro"


def test_horario_em_hora_de_lisboa():
    # 06:30 UTC = 07:30 em Lisboa (verão) - já dentro
    assert sincronizador.dentro_do_horario(datetime(2026, 10, 8, 6, 30, tzinfo=timezone.utc))
    assert not sincronizador.dentro_do_horario(datetime(2026, 10, 8, 5, 30, tzinfo=timezone.utc))
    assert not sincronizador.dentro_do_horario(datetime(2026, 10, 8, 20, 0, tzinfo=timezone.utc))
