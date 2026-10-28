"""Código SMS da CGD introduzido no dashboard do servidor.

Quando o banco pede o código SMS Token, os exes da CGD mostram um popup -
mas só na sessão Windows de quem os corre (agendador), que quase nunca
está a olhar. Por isso também abrem aqui um pedido: o dashboard mostra-o
a qualquer utilizador, que escreve o código recebido no telemóvel, e o exe
vem buscá-lo (monitorizacao_cgd.py, no repositório script-extratos). Vale
o primeiro que chegar, popup ou dashboard.

Fica só em memória (uma API, um processo): um código só serve durante uns
minutos e um reinício da API apenas faz a extração falhar como faria sem
resposta. O id é escolhido pelo exe (32 hexadecimais aleatórios) e serve de
segredo: só o exe e quem tem a chave da API (lista dos pendentes) o sabem -
é por isso que o exe pode ir buscar o código sem X-API-Key.
"""
import re
import threading
import time
from datetime import datetime, timezone
from typing import Any, Dict, List, Optional

# Os exes desistem aos 4 min (SMS_ESPERA_MAX_S); um pedido esquecido (exe
# morto) deixa de aparecer no dashboard pouco depois.
VALIDADE_PEDIDO_S = 5 * 60
# Um código entregue e não levantado não fica guardado para sempre.
VALIDADE_CODIGO_S = 2 * 60
ID_VALIDO = re.compile(r"[0-9a-f]{32}")
CODIGO_VALIDO = re.compile(r"\d{1,8}")

_pedidos: Dict[str, Dict[str, Any]] = {}
_lock = threading.Lock()


def _limpar(agora: float) -> None:
    for pedido_id, pedido in list(_pedidos.items()):
        limite = pedido["codigo_em"] + VALIDADE_CODIGO_S if pedido["codigo"] else pedido["criado"] + VALIDADE_PEDIDO_S
        if agora > limite:
            del _pedidos[pedido_id]


def abrir_pedido(pedido_id: str, script: str, descricao: Optional[str] = None,
                 agora: Optional[float] = None) -> Dict[str, Any]:
    if not ID_VALIDO.fullmatch(pedido_id):
        raise ValueError("Id do pedido inválido (32 hexadecimais)")
    agora = agora if agora is not None else time.time()
    with _lock:
        _limpar(agora)
        _pedidos[pedido_id] = {"script": script[:80], "descricao": (descricao or "")[:200],
                               "criado": agora, "codigo": None, "codigo_em": None}
    return {"id": pedido_id, "expira_em_s": VALIDADE_PEDIDO_S}


def listar_pendentes(agora: Optional[float] = None) -> List[Dict[str, Any]]:
    """Pedidos à espera de código, mais antigos primeiro (o código nunca sai daqui)."""
    agora = agora if agora is not None else time.time()
    with _lock:
        _limpar(agora)
        pendentes = [(pid, p) for pid, p in _pedidos.items() if not p["codigo"]]
    return [{
        "id": pid,
        "script": p["script"],
        "descricao": p["descricao"],
        "criado_em": datetime.fromtimestamp(p["criado"], tz=timezone.utc).isoformat(),
        "segundos_restantes": max(0, int(p["criado"] + VALIDADE_PEDIDO_S - agora)),
    } for pid, p in sorted(pendentes, key=lambda item: item[1]["criado"])]


def entregar_codigo(pedido_id: str, codigo: str, agora: Optional[float] = None) -> bool:
    """Dashboard: grava o código. False se o pedido já não existe ou já tem código."""
    codigo = (codigo or "").strip()
    if not CODIGO_VALIDO.fullmatch(codigo):
        raise ValueError("Código inválido (só dígitos, até 8)")
    agora = agora if agora is not None else time.time()
    with _lock:
        _limpar(agora)
        pedido = _pedidos.get(pedido_id)
        if pedido is None or pedido["codigo"]:
            return False
        pedido["codigo"] = codigo
        pedido["codigo_em"] = agora
    return True


def levantar_codigo(pedido_id: str, agora: Optional[float] = None) -> Optional[str]:
    """Exe: devolve o código uma vez e fecha o pedido; None se ainda não há.
    KeyError se o pedido não existe (expirou ou já foi levantado)."""
    agora = agora if agora is not None else time.time()
    with _lock:
        _limpar(agora)
        pedido = _pedidos[pedido_id]
        if not pedido["codigo"]:
            return None
        del _pedidos[pedido_id]
        return pedido["codigo"]


def fechar_pedido(pedido_id: str) -> None:
    """Exe: já não precisa (respondido no popup, cancelado ou desistiu)."""
    with _lock:
        _pedidos.pop(pedido_id, None)


def limpar_tudo() -> None:
    """Testes."""
    with _lock:
        _pedidos.clear()
