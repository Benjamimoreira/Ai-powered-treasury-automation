"""Deteção de anomalias em movimentos bancários (Fase 3.5 do roteiro):
ML clássico (Isolation Forest), não LLM - cada empresa tem o seu próprio
padrão de valores (não faz sentido comparar o valor de uma empresa
pequena com o de uma grande), por isso o modelo é treinado por empresa,
sobre o histórico completo, e só depois aplicado aos movimentos do dia
pedido."""
from sqlalchemy.orm import Session

from app.db.models import MovimentoBancario

MIN_AMOSTRAS_HISTORICO = 10


def detetar_anomalias_do_dia(db: Session, dia, contaminacao: float = 0.1):
    """Devolve os movimentos do dia cujo valor foge do padrão habitual da
    própria empresa. Empresas com histórico insuficiente (< 10 movimentos
    no total) são ignoradas - não há padrão suficiente para comparar."""
    from sklearn.ensemble import IsolationForest

    movimentos_do_dia = db.query(MovimentoBancario).filter(MovimentoBancario.dia == dia).all()

    por_empresa = {}
    for m in movimentos_do_dia:
        por_empresa.setdefault(m.empresa, []).append(m)

    resultado = []
    for empresa, movimentos_da_empresa_no_dia in por_empresa.items():
        historico = db.query(MovimentoBancario).filter(MovimentoBancario.empresa == empresa).all()
        if len(historico) < MIN_AMOSTRAS_HISTORICO:
            continue

        valores_historico = [[m.valor] for m in historico]
        modelo = IsolationForest(contamination=contaminacao, random_state=42)
        modelo.fit(valores_historico)

        valores_do_dia = [[m.valor] for m in movimentos_da_empresa_no_dia]
        previsoes = modelo.predict(valores_do_dia)

        for movimento, previsao in zip(movimentos_da_empresa_no_dia, previsoes):
            if previsao == -1:
                resultado.append({
                    "id": movimento.id,
                    "dia": movimento.dia,
                    "empresa": movimento.empresa,
                    "descricao": movimento.descricao,
                    "valor": movimento.valor,
                })
    return resultado
