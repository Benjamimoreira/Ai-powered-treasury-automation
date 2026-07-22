"""Cliente HTTP fino para a API de reconciliação - o dashboard nunca
acede à base de dados diretamente, só fala com a API (mesma separação
backend/frontend do roteiro)."""
import os
from typing import Optional

import requests

API_BASE_URL = os.environ.get("API_BASE_URL", "http://127.0.0.1:8000")


def atualizar_dados(dias_atras: int = 7) -> dict:
    r = requests.post(f"{API_BASE_URL}/atualizar-dados", params={"dias_atras": dias_atras}, timeout=120)
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


def listar_movimentos(dia: str) -> list:
    r = requests.get(f"{API_BASE_URL}/movimentos/{dia}")
    r.raise_for_status()
    return r.json()


def consultar_saldo(empresa: str, dia: Optional[str] = None) -> list:
    params = {"dia": dia} if dia else {}
    r = requests.get(f"{API_BASE_URL}/saldos/{empresa}", params=params)
    r.raise_for_status()
    return r.json()


def saldo_total() -> dict:
    r = requests.get(f"{API_BASE_URL}/saldo-total")
    r.raise_for_status()
    return r.json()


def listar_saldos_atuais() -> list:
    r = requests.get(f"{API_BASE_URL}/saldos")
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


def listar_anomalias(dia: str) -> list:
    r = requests.get(f"{API_BASE_URL}/anomalias/{dia}")
    r.raise_for_status()
    return r.json()


def listar_ambiguos() -> list:
    r = requests.get(f"{API_BASE_URL}/ambiguos")
    r.raise_for_status()
    return r.json()


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
