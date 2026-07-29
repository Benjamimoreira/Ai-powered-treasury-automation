"""Cliente fino para o pipeline AWS de documentos (S3 + Textract + DynamoDB) -
ver infra_aws/. Separado de api_client.py porque fala com uma API Gateway
diferente da API FastAPI local, e faz upload direto para S3."""
import os
from typing import Optional

import boto3
import requests

AWS_API_URL = os.environ.get("AWS_API_URL", "").rstrip("/")
AWS_RAW_BUCKET = os.environ.get("AWS_RAW_BUCKET", "")
AWS_REGION = os.environ.get("AWS_REGION", "eu-west-1")


def pipeline_configurado() -> bool:
    return bool(AWS_API_URL and AWS_RAW_BUCKET)


def listar_documentos(status: Optional[str] = None) -> list:
    params = {"status": status} if status else {}
    r = requests.get(f"{AWS_API_URL}/documentos", params=params, timeout=30)
    r.raise_for_status()
    return r.json()["documentos"]


def enviar_documento(nome_ficheiro: str, conteudo: bytes) -> str:
    """Envia um ficheiro para o bucket S3 (prefixo uploads/), o que despoleta
    o pipeline Textract -> validação -> DynamoDB automaticamente."""
    s3 = boto3.client("s3", region_name=AWS_REGION)
    chave = f"uploads/{nome_ficheiro}"
    s3.put_object(Bucket=AWS_RAW_BUCKET, Key=chave, Body=conteudo)
    return chave
