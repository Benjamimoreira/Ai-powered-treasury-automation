from app.services.aws_pipeline import extrair_dados_do_texto


def test_extrair_dados_do_texto_identifica_fornecedor_valor_data_e_numero():
    texto = """
    Fatura eletrónica
    Fornecedor: ACME Serviços, Lda.
    Data: 29/07/2026
    Nº Fatura: 2026-1001
    Valor Total: 1.250,00 EUR
    """

    resultado = extrair_dados_do_texto(texto, origem="teste")

    assert resultado["fornecedor"] == "ACME Serviços, Lda."
    assert resultado["valor"] == 1250.0
    assert resultado["data"] == "2026-07-29"
    assert resultado["numero_documento"] == "2026-1001"
    assert resultado["status"] == "processado"


def test_extrair_dados_do_texto_sem_valor_retorna_status_invalido():
    texto = """
    Fatura eletrónica
    Fornecedor: ACME Serviços, Lda.
    Data: 29/07/2026
    """

    resultado = extrair_dados_do_texto(texto, origem="teste")

    assert resultado["status"] == "invalido"
    assert resultado["valor"] is None
