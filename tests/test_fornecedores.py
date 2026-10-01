from types import SimpleNamespace

from app.services.faturas import (
    ENCAMINHADO_INTERNAMENTE,
    fornecedor_do_remetente,
    limpar_fornecedor,
    normalizar_fornecedores,
    palavras_do_grupo,
)

GRUPO = ["PALAVRADICIONAL,LDA", "HABISERVE INVESTIMENTOS IMOBILIARIOS,LDA", "FELIZARDO CORREIA,LDA"]


def _fatura(fornecedor=None, nif=None, remetente=None):
    return SimpleNamespace(fornecedor=fornecedor, nif_fornecedor=nif, remetente=remetente)


def test_limpar_fornecedor_tira_prefixos_e_sufixos():
    grupo = palavras_do_grupo(GRUPO)
    assert limpar_fornecedor("ATILA - A. Trigueira & Irmão, Lda. | NOTA DE ENCOMENDA", grupo) == "ATILA - A. Trigueira & Irmão, Lda."
    assert limpar_fornecedor("PRIO ENERGY, S.A.", grupo) == "PRIO ENERGY, S.A."


def test_limpar_fornecedor_rejeita_lixo_moradas_e_o_cliente():
    grupo = palavras_do_grupo(GRUPO)
    for lixo in ("do titular IBAN", "Payment method Paid", "Documento", "Pro forma | NOTA DE ENCOMENDA",
                 "Silvia Oliveira Rua Ilha dos Amores, 53B, 1990-371, Lisboa,", "Praça Manuel Guedes"):
        assert limpar_fornecedor(lixo, grupo) is None, lixo
    # o cliente (empresa do grupo) não é o fornecedor
    assert limpar_fornecedor("PCI Creative Science Park, Vía do Palavradicional Lda", grupo) is None
    # mas um apelido que só aparece mais à frente no nome de uma empresa do grupo não é rejeitado
    assert limpar_fornecedor("Correia & Filhos, Lda", grupo) == "Correia & Filhos, Lda"


def test_fornecedor_do_remetente():
    assert fornecedor_do_remetente("faturaedp@edp.pt") == "EDP"
    assert fornecedor_do_remetente("no-reply@hello.galp.com") == "Galp"
    assert fornecedor_do_remetente("luis.neves@vidor.pt") == ENCAMINHADO_INTERNAMENTE
    assert fornecedor_do_remetente("alguem@gmail.com") is None


def test_normalizar_usa_o_nif_antes_do_remetente():
    faturas = [
        _fatura("Axiomas & Demandas - Lda", "519391020", "luis.neves@vidor.pt"),
        _fatura("Documento", "519391020", "luis.neves@vidor.pt"),  # lixo, mas o NIF já é conhecido
        _fatura(None, None, "invoices@miio.pt"),
        _fatura(None, None, "rcarrito@vidor.pt"),
    ]

    resultado = normalizar_fornecedores(faturas, faturas, GRUPO)

    assert resultado == [
        ("Axiomas & Demandas - Lda", "extraido"),
        ("Axiomas & Demandas - Lda", "nif"),
        ("Miio", "remetente"),
        (ENCAMINHADO_INTERNAMENTE, "remetente"),
    ]
