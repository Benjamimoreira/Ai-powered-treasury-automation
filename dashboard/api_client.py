"""Cliente HTTP fino para a API de reconciliação - o dashboard nunca
acede à base de dados diretamente, só fala com a API (mesma separação
backend/frontend do roteiro)."""
import os
from typing import Optional

import requests

API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")
# URL que o BROWSER do utilizador consegue alcançar (não o container) -
# dentro do Docker, API_BASE_URL é "http://api:8000" (rede interna), mas
# isso não existe fora do Docker. Usada só para montar links clicáveis
# (ex. abrir PDF de uma fatura), nunca para pedidos feitos pelo próprio
# dashboard.
API_PUBLIC_URL = os.environ.get("API_PUBLIC_URL", "http://127.0.0.1:8000")


def atualizar_dados(dias_atras: int = 7) -> dict:
    r = requests.post(f"{API_BASE_URL}/atualizar-dados", params={"dias_atras": dias_atras}, timeout=120)
    r.raise_for_status()
    return r.json()


def atualizar_dados_do_dia(dia: str) -> dict:
    r = requests.post(f"{API_BASE_URL}/atualizar-dados/{dia}", timeout=60)
    r.raise_for_status()
    return r.json()


def reconciliar_dia(dia: str) -> dict:
    r = requests.post(f"{API_BASE_URL}/reconciliar/{dia}")
    r.raise_for_status()
    return r.json()


def auditoria(dia: str) -> dict:
    r = requests.get(f"{API_BASE_URL}/auditoria/{dia}")
    r.raise_for_status()
    return r.json()


def registar_auditoria(dia: str) -> dict:
    """Como auditoria(), mas fica gravada em auditorias_dia (histórico)."""
    r = requests.post(f"{API_BASE_URL}/auditoria/{dia}/registar", timeout=60)
    r.raise_for_status()
    return r.json()


def auditoria_geral(dias_atras: int = 31) -> dict:
    r = requests.post(f"{API_BASE_URL}/auditoria/geral", params={"dias_atras": dias_atras}, timeout=300)
    r.raise_for_status()
    return r.json()


def historico_auditorias(limit: int = 100) -> dict:
    r = requests.get(f"{API_BASE_URL}/auditoria/historico", params={"limit": limit})
    r.raise_for_status()
    return r.json()


def listar_movimentos(dia: str) -> list:
    r = requests.get(f"{API_BASE_URL}/movimentos/{dia}")
    r.raise_for_status()
    return r.json()


def consultar_saldo(empresa: str, dia: Optional[str] = None) -> list:
    params = {"dia": dia} if dia else {}
    r = requests.get(f"{API_BASE_URL}/saldos/{empresa}", params=params)
    r.raise_for_status()
    return r.json()


def saldo_total(dia: Optional[str] = None) -> dict:
    params = {"dia": dia} if dia else {}
    r = requests.get(f"{API_BASE_URL}/saldo-total", params=params)
    r.raise_for_status()
    return r.json()


def listar_saldos_atuais(dia: Optional[str] = None) -> list:
    params = {"dia": dia} if dia else {}
    r = requests.get(f"{API_BASE_URL}/saldos", params=params)
    r.raise_for_status()
    return r.json()


def mapa_saldos(dia: Optional[str] = None) -> list:
    params = {"dia": dia} if dia else {}
    r = requests.get(f"{API_BASE_URL}/saldos/mapa", params=params)
    r.raise_for_status()
    return r.json()


def listar_empresas() -> list:
    r = requests.get(f"{API_BASE_URL}/empresas")
    r.raise_for_status()
    return r.json()


def historico_movimentos(empresa: str) -> list:
    r = requests.get(f"{API_BASE_URL}/movimentos/empresa/{empresa}")
    r.raise_for_status()
    return r.json()


def resumo_diario() -> list:
    r = requests.get(f"{API_BASE_URL}/movimentos/resumo-diario")
    r.raise_for_status()
    return r.json()


def analise_imputacoes(empresa: Optional[str] = None, dia_inicio: Optional[str] = None, dia_fim: Optional[str] = None) -> dict:
    params = {}
    if empresa:
        params["empresa"] = empresa
    if dia_inicio:
        params["dia_inicio"] = dia_inicio
    if dia_fim:
        params["dia_fim"] = dia_fim
    r = requests.get(f"{API_BASE_URL}/analise/imputacoes", params=params)
    r.raise_for_status()
    return r.json()


def listar_linhas_imputacao(empresa: Optional[str] = None, dia_inicio: Optional[str] = None, dia_fim: Optional[str] = None) -> list:
    params = {}
    if empresa:
        params["empresa"] = empresa
    if dia_inicio:
        params["dia_inicio"] = dia_inicio
    if dia_fim:
        params["dia_fim"] = dia_fim
    r = requests.get(f"{API_BASE_URL}/analise/imputacoes/linhas", params=params)
    r.raise_for_status()
    return r.json().get("linhas", [])


def previsao_saldo(empresa: str, dias: int = 7) -> dict:
    r = requests.get(f"{API_BASE_URL}/previsao/saldo/{empresa}", params={"dias": dias})
    r.raise_for_status()
    return r.json()


def avaliar_previsao(empresa: str, dias_teste: int = 5) -> dict:
    r = requests.get(f"{API_BASE_URL}/previsao/avaliacao/{empresa}", params={"dias_teste": dias_teste})
    r.raise_for_status()
    return r.json()


def previsao_saldo_total(dias: int = 7) -> dict:
    r = requests.get(f"{API_BASE_URL}/previsao/saldo-total", params={"dias": dias})
    r.raise_for_status()
    return r.json()


def previsao_saldo_total_cashflow(dias: int = 30) -> dict:
    r = requests.get(f"{API_BASE_URL}/previsao/saldo-total-cashflow", params={"dias": dias})
    r.raise_for_status()
    return r.json()


def backtest_saldo_total(dias: int = 30, cortes: int = 6) -> dict:
    r = requests.get(
        f"{API_BASE_URL}/previsao/saldo-total-backtest", params={"dias": dias, "cortes": cortes}, timeout=600,
    )
    r.raise_for_status()
    return r.json()


def previsao_ancorada(empresa: Optional[str] = None, dias: int = 30) -> dict:
    params = {"dias": dias}
    if empresa:
        params["empresa"] = empresa
    r = requests.get(f"{API_BASE_URL}/previsao/ancorada", params=params, timeout=120)
    r.raise_for_status()
    return r.json()


def previsao_risco_liquidez(dias: int = 30) -> list:
    r = requests.get(f"{API_BASE_URL}/previsao/risco-liquidez", params={"dias": dias}, timeout=300)
    r.raise_for_status()
    return r.json()


def backtest_previsao_ancorada(empresa: Optional[str] = None, dias: int = 30, cortes: int = 20) -> dict:
    params = {"dias": dias, "cortes": cortes}
    if empresa:
        params["empresa"] = empresa
    r = requests.get(f"{API_BASE_URL}/previsao/ancorada-backtest", params=params, timeout=600)
    r.raise_for_status()
    return r.json()


def avaliar_previsao_saldo_total(dias_teste: int = 5) -> dict:
    r = requests.get(f"{API_BASE_URL}/previsao/saldo-total-avaliacao", params={"dias_teste": dias_teste})
    r.raise_for_status()
    return r.json()


def previsao_risco(empresa: str, dias: int = 7) -> dict:
    r = requests.get(f"{API_BASE_URL}/previsao/risco/{empresa}", params={"dias": dias})
    r.raise_for_status()
    return r.json()


def previsao_risco_ranking(dias: int = 30) -> list:
    r = requests.get(f"{API_BASE_URL}/previsao/risco-ranking", params={"dias": dias})
    r.raise_for_status()
    return r.json()


def saldo_serie_total() -> list:
    r = requests.get(f"{API_BASE_URL}/saldos/serie-total")
    r.raise_for_status()
    return r.json().get("serie", [])


def previsao_cashflow(empresa: Optional[str] = None, dias: int = 7) -> dict:
    url = f"{API_BASE_URL}/previsao/cashflow/{empresa}" if empresa else f"{API_BASE_URL}/previsao/cashflow"
    r = requests.get(url, params={"dias": dias})
    r.raise_for_status()
    return r.json()


def avaliar_previsao_cashflow(empresa: Optional[str] = None, dias_teste: int = 5) -> dict:
    params = {"dias_teste": dias_teste}
    if empresa:
        params["empresa"] = empresa
    r = requests.get(f"{API_BASE_URL}/previsao/cashflow-avaliacao", params=params)
    r.raise_for_status()
    return r.json()


def perguntar_chat(pergunta: str) -> dict:
    # o modelo local em CPU leva 1-3 min por pergunta (2+ rondas com
    # ferramentas) - com 120 s a dashboard desistia antes da resposta
    r = requests.post(f"{API_BASE_URL}/chat", json={"pergunta": pergunta}, timeout=300)
    r.raise_for_status()
    return r.json()


def reiniciar_chat() -> dict:
    r = requests.post(f"{API_BASE_URL}/chat/reset")
    r.raise_for_status()
    return r.json()


def listar_ambiguos() -> list:
    r = requests.get(f"{API_BASE_URL}/ambiguos")
    r.raise_for_status()
    return r.json()


def listar_monitorizacao_scripts() -> dict:
    r = requests.get(f"{API_BASE_URL}/monitorizacao/scripts")
    r.raise_for_status()
    return r.json()


def correr_script(script: str) -> dict:
    r = requests.post(f"{API_BASE_URL}/monitorizacao/scripts/{script}/correr", timeout=10)
    r.raise_for_status()
    return r.json()


def listar_monitorizacao_logs(limit: int = 50, dia: str = None) -> dict:
    params = {"limit": limit}
    if dia:
        params["dia"] = dia
    r = requests.get(f"{API_BASE_URL}/monitorizacao/logs", params=params)
    r.raise_for_status()
    return r.json()


def listar_monitorizacao_eventos(limit: int = 50, script: Optional[str] = None) -> dict:
    """Eventos de log reportados em tempo real, a meio de corridas ainda a
    decorrer (ver _HandlerEventoDashboard em monitorizacao_client.py) -
    diferente de listar_monitorizacao_logs(), que só tem o resultado final
    de corridas já terminadas."""
    params = {"limit": limit}
    if script:
        params["script"] = script
    r = requests.get(f"{API_BASE_URL}/monitorizacao/eventos", params=params)
    r.raise_for_status()
    return r.json()


def registrar_execucao_script(script: str, status: str, erro: Optional[str] = None, log: Optional[list] = None, duracao_segundos: Optional[float] = None) -> dict:
    r = requests.post(
        f"{API_BASE_URL}/monitorizacao/scripts/{script}/executar",
        json={
            "status": status,
            "erro": erro,
            "log": log or [],
            "duracao_segundos": duracao_segundos,
        },
        timeout=120,
    )
    r.raise_for_status()
    return r.json()


def listar_faturas_recebidas(
    dia: Optional[str] = None,
    desde: Optional[str] = None,
    ate: Optional[str] = None,
    pesquisa: Optional[str] = None,
    limit: int = 200,
) -> list:
    params = {"limit": limit}
    if dia:
        params["dia"] = dia
    if desde:
        params["desde"] = desde
    if ate:
        params["ate"] = ate
    if pesquisa:
        params["pesquisa"] = pesquisa
    r = requests.get(f"{API_BASE_URL}/faturas/recebidas", params=params)
    r.raise_for_status()
    return r.json()


def url_pdf_fatura(fatura_id: int) -> str:
    """URL pública (não a interna do Docker) do PDF de uma fatura, para
    usar num link clicável na dashboard."""
    return f"{API_PUBLIC_URL}/faturas/recebidas/{fatura_id}/pdf"


def sugerir_ambiguo(caso_id: int) -> dict:
    r = requests.post(f"{API_BASE_URL}/ambiguos/{caso_id}/sugerir")
    r.raise_for_status()
    return r.json()


def resolver_ambiguo(caso_id: int, linha_id: Optional[int], resolvido_por: str) -> dict:
    r = requests.post(
        f"{API_BASE_URL}/ambiguos/{caso_id}/resolver",
        json={"linha_id": linha_id, "resolvido_por": resolvido_por},
    )
    r.raise_for_status()
    return r.json()


def listar_cpcv_escrituras(dia_inicio: Optional[str] = None, dia_fim: Optional[str] = None) -> list:
    params = {}
    if dia_inicio:
        params["dia_inicio"] = dia_inicio
    if dia_fim:
        params["dia_fim"] = dia_fim
    r = requests.get(f"{API_BASE_URL}/comercial/cpcv", params=params)
    r.raise_for_status()
    return r.json().get("linhas", [])


def espaco_fracao_por_ref() -> dict:
    r = requests.get(f"{API_BASE_URL}/comercial/espaco-fracao-por-ref")
    r.raise_for_status()
    return r.json().get("por_ref", {})
