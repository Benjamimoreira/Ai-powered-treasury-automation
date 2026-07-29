import re
from typing import Any, Dict


def extrair_dados_do_texto(texto: str, origem: str = "desconhecida") -> Dict[str, Any]:
    """Extrai campos básicos de um documento textual para o fluxo AWS.

    Este módulo implementa uma primeira versão simples e determinística,
    suficiente para demonstrar a arquitetura proposta e permitir testes.
    """
    texto_limpo = (texto or "").strip()

    fornecedor = None
    numero_documento = None
    data = None
    valor = None

    if texto_limpo:
        for linha in texto_limpo.splitlines():
            linha_limpa = linha.strip()
            if not linha_limpa:
                continue

            if linha_limpa.lower().startswith("fornecedor:"):
                fornecedor = linha_limpa.split(":", 1)[1].strip()
            elif linha_limpa.lower().startswith("nº fatura") or linha_limpa.lower().startswith("numero fatura") or linha_limpa.lower().startswith("nº documento"):
                numero_documento = linha_limpa.split(":", 1)[1].strip()
            elif linha_limpa.lower().startswith("data:"):
                data = linha_limpa.split(":", 1)[1].strip()
            elif linha_limpa.lower().startswith("valor") or linha_limpa.lower().startswith("valor total"):
                valor_match = re.search(r"([\d.]+,\d{2})", linha_limpa)
                if valor_match:
                    valor_texto = valor_match.group(1).replace(".", "").replace(",", ".")
                    valor = float(valor_texto)

    if data:
        try:
            data_obj = re.search(r"(\d{4})[-/](\d{2})[-/](\d{2})", data)
            if data_obj:
                data = f"{data_obj.group(1)}-{data_obj.group(2)}-{data_obj.group(3)}"
            else:
                data_obj = re.search(r"(\d{2})/(\d{2})/(\d{4})", data)
                if data_obj:
                    data = f"{data_obj.group(3)}-{data_obj.group(2)}-{data_obj.group(1)}"
        except Exception:
            data = None

    status = "processado" if fornecedor and data and valor and numero_documento else "invalido"

    return {
        "origem": origem,
        "fornecedor": fornecedor,
        "numero_documento": numero_documento,
        "data": data,
        "valor": valor,
        "status": status,
    }
