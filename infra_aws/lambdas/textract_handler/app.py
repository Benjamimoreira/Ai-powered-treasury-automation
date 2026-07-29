"""Lambda #1 do pipeline de documentos: disparada por upload em S3
(prefixo uploads/), chama o Textract para extrair o texto e invoca a
Lambda de validação (ValidarFunction) com o resultado.

Erros do Textract (ficheiro corrompido, formato não suportado, etc.) não
interrompem o pipeline: são passados adiante para a Lambda de validação
como `erro_extracao`, para que fiquem registados no DynamoDB e gerem
alerta SNS da mesma forma que um documento com dados incompletos.
"""
import json
import os
import urllib.parse

import boto3

VALIDAR_FUNCTION_NAME = os.environ.get("VALIDAR_FUNCTION_NAME", "")

_textract_client = None
_lambda_client = None


def _get_textract_client():
    global _textract_client
    if _textract_client is None:
        _textract_client = boto3.client("textract")
    return _textract_client


def _get_lambda_client():
    global _lambda_client
    if _lambda_client is None:
        _lambda_client = boto3.client("lambda")
    return _lambda_client


def handler(event, context=None):
    processados = []
    for record in event.get("Records", []):
        bucket = record["s3"]["bucket"]["name"]
        key = urllib.parse.unquote_plus(record["s3"]["object"]["key"])
        processados.append(processar_documento(bucket, key))
    return {"processados": processados}


def processar_documento(bucket: str, key: str) -> dict:
    payload = {"bucket": bucket, "key": key}

    try:
        resposta = _get_textract_client().detect_document_text(
            Document={"S3Object": {"Bucket": bucket, "Name": key}}
        )
        linhas = [
            bloco["Text"]
            for bloco in resposta.get("Blocks", [])
            if bloco.get("BlockType") == "LINE"
        ]
        payload["texto"] = "\n".join(linhas)
    except Exception as exc:
        payload["texto"] = ""
        payload["erro_extracao"] = str(exc)

    _get_lambda_client().invoke(
        FunctionName=VALIDAR_FUNCTION_NAME,
        InvocationType="Event",
        Payload=json.dumps(payload).encode("utf-8"),
    )
    return payload
