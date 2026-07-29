"""Testes dos handlers Lambda do pipeline AWS (infra_aws/), com boto3
completamente mockado - não fazem nenhuma chamada real à AWS.

As Lambdas #1 e #3 vivem fora do package `app` (infra_aws/lambdas/...),
por isso são carregadas diretamente do ficheiro em vez de importadas
como módulo normal.
"""
import importlib.util
import json
import sys
from decimal import Decimal
from pathlib import Path
from unittest.mock import MagicMock, patch

from app.services import aws_pipeline_lambda

REPO_ROOT = Path(__file__).resolve().parents[1]


def _carregar_modulo(nome: str, caminho: Path):
    spec = importlib.util.spec_from_file_location(nome, caminho)
    modulo = importlib.util.module_from_spec(spec)
    sys.modules[nome] = modulo
    spec.loader.exec_module(modulo)
    return modulo


textract_handler = _carregar_modulo(
    "textract_handler_app",
    REPO_ROOT / "infra_aws" / "lambdas" / "textract_handler" / "app.py",
)
consulta_handler = _carregar_modulo(
    "consulta_handler_app",
    REPO_ROOT / "infra_aws" / "lambdas" / "consulta_handler" / "app.py",
)


TEXTO_FATURA_VALIDA = """
Fatura eletrónica
Fornecedor: ACME Serviços, Lda.
Data: 29/07/2026
Nº Fatura: 2026-1001
Valor Total: 1.250,00 EUR
"""


# ---------------------------------------------------------------------------
# Lambda #2 - ValidarFunction (app/services/aws_pipeline_lambda.py)
# ---------------------------------------------------------------------------

def test_validar_handler_grava_documento_processado_no_dynamodb():
    mock_table = MagicMock()
    mock_sns = MagicMock()

    with patch.object(aws_pipeline_lambda, "_get_table", return_value=mock_table), \
         patch.object(aws_pipeline_lambda, "_get_sns", return_value=mock_sns):
        item = aws_pipeline_lambda.handler({
            "bucket": "raw-bucket",
            "key": "uploads/fatura1.png",
            "texto": TEXTO_FATURA_VALIDA,
        })

    assert item["status"] == "processado"
    assert item["valor"] == Decimal("1250.00")
    mock_table.put_item.assert_called_once()
    gravado = mock_table.put_item.call_args.kwargs["Item"]
    assert gravado["s3_key"] == "uploads/fatura1.png"
    assert isinstance(gravado["valor"], Decimal)
    mock_sns.publish.assert_not_called()


def test_validar_handler_publica_alerta_sns_quando_dados_incompletos():
    mock_table = MagicMock()
    mock_sns = MagicMock()

    with patch.object(aws_pipeline_lambda, "_get_table", return_value=mock_table), \
         patch.object(aws_pipeline_lambda, "_get_sns", return_value=mock_sns), \
         patch.object(aws_pipeline_lambda, "SNS_TOPIC_ARN", "arn:aws:sns:eu-west-1:123456789012:teste"):
        item = aws_pipeline_lambda.handler({
            "bucket": "raw-bucket",
            "key": "uploads/fatura2.png",
            "texto": "Fornecedor: ACME\nData: 29/07/2026",
        })

    assert item["status"] == "invalido"
    mock_sns.publish.assert_called_once()
    assert "incompletos" in mock_sns.publish.call_args.kwargs["Subject"]


def test_validar_handler_marca_erro_extracao_e_alerta():
    mock_table = MagicMock()
    mock_sns = MagicMock()

    with patch.object(aws_pipeline_lambda, "_get_table", return_value=mock_table), \
         patch.object(aws_pipeline_lambda, "_get_sns", return_value=mock_sns), \
         patch.object(aws_pipeline_lambda, "SNS_TOPIC_ARN", "arn:aws:sns:eu-west-1:123456789012:teste"):
        item = aws_pipeline_lambda.handler({
            "bucket": "raw-bucket",
            "key": "uploads/corrompido.pdf",
            "texto": "",
            "erro_extracao": "UnsupportedDocumentException",
        })

    assert item["status"] == "erro_extracao"
    mock_sns.publish.assert_called_once()
    assert "falha na extração" in mock_sns.publish.call_args.kwargs["Subject"]
    assert "UnsupportedDocumentException" in mock_sns.publish.call_args.kwargs["Message"]


# ---------------------------------------------------------------------------
# Lambda #1 - TextractFunction
# ---------------------------------------------------------------------------

def _evento_s3(bucket="raw-bucket", key="uploads/fatura1.png"):
    return {"Records": [{"s3": {"bucket": {"name": bucket}, "object": {"key": key}}}]}


def test_textract_handler_extrai_texto_e_invoca_validar():
    mock_textract = MagicMock()
    mock_textract.detect_document_text.return_value = {
        "Blocks": [
            {"BlockType": "LINE", "Text": "Fornecedor: ACME"},
            {"BlockType": "WORD", "Text": "ignorado"},
            {"BlockType": "LINE", "Text": "Valor Total: 10,00 EUR"},
        ]
    }
    mock_lambda = MagicMock()

    with patch.object(textract_handler, "_get_textract_client", return_value=mock_textract), \
         patch.object(textract_handler, "_get_lambda_client", return_value=mock_lambda):
        resultado = textract_handler.handler(_evento_s3())

    assert resultado["processados"][0]["texto"] == "Fornecedor: ACME\nValor Total: 10,00 EUR"
    mock_lambda.invoke.assert_called_once()
    kwargs = mock_lambda.invoke.call_args.kwargs
    assert kwargs["InvocationType"] == "Event"
    payload = json.loads(kwargs["Payload"])
    assert payload["bucket"] == "raw-bucket"
    assert payload["key"] == "uploads/fatura1.png"
    assert "erro_extracao" not in payload


def test_textract_handler_regista_erro_quando_textract_falha():
    mock_textract = MagicMock()
    mock_textract.detect_document_text.side_effect = Exception("UnsupportedDocumentException")
    mock_lambda = MagicMock()

    with patch.object(textract_handler, "_get_textract_client", return_value=mock_textract), \
         patch.object(textract_handler, "_get_lambda_client", return_value=mock_lambda):
        resultado = textract_handler.handler(_evento_s3(key="uploads/corrompido.pdf"))

    payload_resultado = resultado["processados"][0]
    assert payload_resultado["texto"] == ""
    assert "UnsupportedDocumentException" in payload_resultado["erro_extracao"]
    mock_lambda.invoke.assert_called_once()
    payload_enviado = json.loads(mock_lambda.invoke.call_args.kwargs["Payload"])
    assert "UnsupportedDocumentException" in payload_enviado["erro_extracao"]


# ---------------------------------------------------------------------------
# Lambda #3 - ConsultaFunction
# ---------------------------------------------------------------------------

def test_consulta_handler_lista_documentos_ordenados_por_data():
    mock_table = MagicMock()
    mock_table.scan.return_value = {
        "Items": [
            {"id": "1", "criado_em": "2026-07-28T10:00:00+00:00", "status": "processado"},
            {"id": "2", "criado_em": "2026-07-29T10:00:00+00:00", "status": "invalido"},
        ]
    }

    with patch.object(consulta_handler, "_get_table", return_value=mock_table):
        resposta = consulta_handler.handler({"pathParameters": None, "queryStringParameters": None})

    assert resposta["statusCode"] == 200
    corpo = json.loads(resposta["body"])
    assert corpo["total"] == 2
    assert corpo["documentos"][0]["id"] == "2"  # mais recente primeiro


def test_consulta_handler_filtra_por_status():
    mock_table = MagicMock()
    mock_table.scan.return_value = {
        "Items": [
            {"id": "1", "criado_em": "2026-07-28T10:00:00+00:00", "status": "processado"},
            {"id": "2", "criado_em": "2026-07-29T10:00:00+00:00", "status": "invalido"},
        ]
    }

    with patch.object(consulta_handler, "_get_table", return_value=mock_table):
        resposta = consulta_handler.handler({
            "pathParameters": None,
            "queryStringParameters": {"status": "invalido"},
        })

    corpo = json.loads(resposta["body"])
    assert corpo["total"] == 1
    assert corpo["documentos"][0]["id"] == "2"


def test_consulta_handler_devolve_404_quando_documento_nao_existe():
    mock_table = MagicMock()
    mock_table.get_item.return_value = {}

    with patch.object(consulta_handler, "_get_table", return_value=mock_table):
        resposta = consulta_handler.handler({"pathParameters": {"id": "inexistente"}})

    assert resposta["statusCode"] == 404
