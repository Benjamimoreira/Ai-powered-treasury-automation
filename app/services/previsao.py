"""Previsão de saldos (Fase 3.5 do roteiro): 3 modelos diferentes de
séries temporais para comparar, sobre o histórico de saldos_diarios de
uma conta. ML clássico, não LLM - séries numéricas não precisam de um
modelo de linguagem."""
from datetime import timedelta

import numpy as np
from sklearn.linear_model import LinearRegression
from sqlalchemy.orm import Session
from statsmodels.tsa.holtwinters import Holt

from app.db.models import SaldoDiario
from app.services.reconciliador import chave_empresa

MIN_PONTOS = 5
JANELA_MEDIA_MOVEL = 5


def _historico_saldo(db: Session, empresa: str):
    alvo = chave_empresa(empresa)
    todos = db.query(SaldoDiario).order_by(SaldoDiario.dia).all()
    return [
        s for s in todos
        if chave_empresa(s.entidade) == alvo and s.saldo_contabilistico is not None
    ]


def _prever_linear(n_pontos: int, valores: list, n_futuro: int) -> list:
    """Regressão linear sobre o índice do dia - tendência simples."""
    x = np.arange(n_pontos).reshape(-1, 1)
    y = np.array(valores)
    modelo = LinearRegression()
    modelo.fit(x, y)
    x_futuro = np.arange(n_pontos, n_pontos + n_futuro).reshape(-1, 1)
    return modelo.predict(x_futuro).tolist()


def _prever_media_movel(valores: list, n_futuro: int) -> list:
    """Baseline simples: média dos últimos dias, repetida (não capta
    tendência, só o nível recente)."""
    janela = valores[-JANELA_MEDIA_MOVEL:] if len(valores) >= JANELA_MEDIA_MOVEL else valores
    media = sum(janela) / len(janela)
    return [media] * n_futuro


def _prever_suavizacao_exponencial(valores: list, n_futuro: int) -> list:
    """Suavização exponencial de Holt (nível + tendência) - método
    clássico de séries temporais, reage mais depressa a mudanças recentes
    do que a regressão linear sobre todo o histórico."""
    modelo = Holt(valores, initialization_method="estimated").fit()
    return modelo.forecast(n_futuro).tolist()


def prever_saldo(db: Session, empresa: str, dias_futuro: int = 7) -> dict:
    """Prevê o saldo contabilístico dos próximos dias com 3 modelos, para
    comparação lado a lado. Levanta ValueError se não houver histórico
    suficiente (a conta é demasiado recente para qualquer modelo ser
    fiável)."""
    historico = _historico_saldo(db, empresa)
    if len(historico) < MIN_PONTOS:
        raise ValueError(
            f"Histórico insuficiente para prever saldo de '{empresa}' "
            f"({len(historico)} pontos, mínimo {MIN_PONTOS})."
        )

    dias = [h.dia for h in historico]
    valores = [h.saldo_contabilistico for h in historico]
    ultimo_dia = dias[-1]
    dias_futuros = [ultimo_dia + timedelta(days=i + 1) for i in range(dias_futuro)]

    previsoes_por_modelo = {
        "regressao_linear": _prever_linear(len(valores), valores, dias_futuro),
        "media_movel": _prever_media_movel(valores, dias_futuro),
        "suavizacao_exponencial": _prever_suavizacao_exponencial(valores, dias_futuro),
    }

    return {
        "historico": [{"dia": d.isoformat(), "valor": v} for d, v in zip(dias, valores)],
        "previsao": {
            modelo: [
                {"dia": d.isoformat(), "valor": v}
                for d, v in zip(dias_futuros, valores_previstos)
            ]
            for modelo, valores_previstos in previsoes_por_modelo.items()
        },
    }
