"""Lambda #2 do pipeline de documentos: valida/estrutura o texto extraído
pelo Textract (ou o erro de extração, se o Textract falhou) e grava o
resultado no DynamoDB, publicando um alerta SNS quando o documento não
fica com estado "processado".

Vive dentro de app/services/ (em vez de infra_aws/) para que o AWS SAM
empacote esta função a partir deste diretório - ver ValidarFunction em
infra_aws/template.yaml - e reaproveite `extrair_dados_do_texto` sem
duplicar a lógica de parsing já testada em tests/test_aws_pipeline.py.
"""
import os
import uuid
from datetime import datetime, timezone
from decimal import Decimal

import boto3

try:
    from aws_pipeline import extrair_dados_do_texto  # empacotado sozinho para a Lambda
except ImportError:
    from app.services.aws_pipeline import extrair_dados_do_texto  # import normal (app/testes)

TABLE_NAME = os.environ.get("TABLE_NAME", "")
SNS_TOPIC_ARN = os.environ.get("SNS_TOPIC_ARN", "")

_dynamodb_resource = None
_sns_client = None


def _get_table():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource.Table(TABLE_NAME)


def _get_sns():
    global _sns_client
    if _sns_client is None:
        _sns_client = boto3.client("sns")
    return _sns_client


def handler(event, context=None):
    bucket = event.get("bucket", "")
    key = event.get("key", "")
    texto = event.get("texto") or ""
    erro_extracao = event.get("erro_extracao")

    dados = extrair_dados_do_texto(texto, origem=key or "desconhecida")
    if erro_extracao:
        dados["status"] = "erro_extracao"
    if dados.get("valor") is not None:
        dados["valor"] = Decimal(str(dados["valor"]))

    item = {
        "id": str(uuid.uuid4()),
        "s3_bucket": bucket,
        "s3_key": key,
        "criado_em": datetime.now(timezone.utc).isoformat(),
        **dados,
    }

    _get_table().put_item(Item=item)

    if dados["status"] != "processado":
        _publicar_alerta(item, erro_extracao)

    return item


def _publicar_alerta(item: dict, erro_extracao) -> None:
    if not SNS_TOPIC_ARN:
        return

    assunto = (
        "Alerta: falha na extração de documento"
        if erro_extracao
        else "Alerta: documento com dados incompletos"
    )
    mensagem = (
        f"Documento: {item.get('s3_key')}\n"
        f"Estado: {item.get('status')}\n"
        f"Fornecedor: {item.get('fornecedor')}\n"
        f"Valor: {item.get('valor')}\n"
        f"Data: {item.get('data')}\n"
    )
    if erro_extracao:
        mensagem += f"Erro: {erro_extracao}\n"

    _get_sns().publish(TopicArn=SNS_TOPIC_ARN, Subject=assunto, Message=mensagem)
