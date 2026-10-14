"""Autenticação da API por chave partilhada (API_TOKEN no .env).

A API fica na rede (o dashboard do servidor fala com ela pela porta 8000)
e tem endpoints que lançam scripts, deploys elevados no Windows e mudam
quem recebe o Mapa por email - sem chave, qualquer PC da rede o fazia.
Com API_TOKEN definido, todos os pedidos têm de trazer o cabeçalho
X-API-Key com esse valor, exceto:

  - o que os scripts agendados reportam (início, resultado, eventos,
    faturas recebidas, extratos prontos) - são muitos e compilados noutros
    repositórios (exes da CGD, Fornecedores); só acrescentam registos;
  - o PDF de uma fatura com link assinado (o browser abre-o diretamente,
    sem poder mandar cabeçalhos - ver assinar_pdf);
  - "/" (estado) e a documentação (/docs).

Sem API_TOKEN a API continua aberta, como antes, e avisa no arranque.
"""
import hashlib
import hmac
import os
import re
import time

CABECALHO = "X-API-Key"
VALIDADE_LINK_PDF_S = 12 * 3600

_ABERTOS = [
    ("GET", re.compile(r"/")),
    ("GET", re.compile(r"/(docs|redoc|openapi\.json)(/.*)?")),
    ("POST", re.compile(r"/monitorizacao/scripts/[^/]+/(inicio|executar|eventos)")),
    ("POST", re.compile(r"/faturas/recebidas")),
    ("POST", re.compile(r"/extratos/prontos")),
]
_PDF = re.compile(r"/faturas/recebidas/(\d+)/pdf")


def token() -> str:
    return os.environ.get("API_TOKEN", "").strip()


def _assinatura(chave: str, fatura_id: int, expira: int) -> str:
    return hmac.new(chave.encode(), f"pdf:{fatura_id}:{expira}".encode(), hashlib.sha256).hexdigest()


def assinar_pdf(fatura_id: int, chave: str, agora: float | None = None) -> str:
    """Query string (?expira=...&assinatura=...) que abre o PDF desta fatura
    durante VALIDADE_LINK_PDF_S, sem expor a chave da API ao browser."""
    expira = int((agora if agora is not None else time.time()) + VALIDADE_LINK_PDF_S)
    return f"expira={expira}&assinatura={_assinatura(chave, fatura_id, expira)}"


def _pdf_assinado(caminho: str, query, chave: str) -> bool:
    m = _PDF.fullmatch(caminho)
    if not m:
        return False
    try:
        expira = int(query.get("expira", ""))
    except ValueError:
        return False
    if expira < time.time():
        return False
    return hmac.compare_digest(query.get("assinatura", ""), _assinatura(chave, int(m.group(1)), expira))


def pedido_autorizado(metodo: str, caminho: str, chave_recebida: str | None, query) -> bool:
    chave = token()
    if not chave:
        return True
    if any(metodo == m and padrao.fullmatch(caminho) for m, padrao in _ABERTOS):
        return True
    if chave_recebida and hmac.compare_digest(chave_recebida.encode(), chave.encode()):
        return True
    return metodo == "GET" and _pdf_assinado(caminho, query, chave)
