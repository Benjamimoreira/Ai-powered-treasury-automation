"""
Reporta o resultado da corrida de extrair_faturas.py à API (mesma rede
Docker, API_TESOURARIA_URL="http://api:8000" por omissão) - cópia
reduzida de Ambiente de Trabalho/Fornecedores/monitorizacao_client.py
(só o que este container precisa: notificar()/monitorizar()).

Melhor esforço: nunca levanta exceção, mesmo que a API esteja em baixo.
"""

import os
import time
import traceback
from contextlib import contextmanager

import requests

API_URL = os.environ.get("API_TESOURARIA_URL", "http://api:8000")


def notificar(script, status, erro=None, log=None, duracao_segundos=None):
    try:
        requests.post(
            f"{API_URL.rstrip('/')}/monitorizacao/scripts/{script}/executar",
            json={
                "status": status,
                "erro": erro,
                "log": log or [],
                "duracao_segundos": duracao_segundos,
            },
            timeout=5,
        )
    except Exception:
        pass


@contextmanager
def monitorizar(script):
    inicio = time.monotonic()
    try:
        yield
    except SystemExit as e:
        duracao = time.monotonic() - inicio
        erro = str(e.code) if e.code not in (None, 0, "") else None
        notificar(script, "erro" if erro else "ok", erro=erro, duracao_segundos=duracao)
        raise
    except Exception:
        duracao = time.monotonic() - inicio
        notificar(script, "erro", erro=traceback.format_exc(), duracao_segundos=duracao)
        raise
    else:
        duracao = time.monotonic() - inicio
        notificar(script, "ok", duracao_segundos=duracao)
