"""Lambda #3 do pipeline de documentos: endpoint HTTP (API Gateway) para
consultar os documentos estruturados guardados no DynamoDB pela Lambda de
validação.

GET /documentos            -> lista todos (opcionalmente filtrados por ?status=)
GET /documentos/{id}       -> um documento específico
"""
import json
import os

import boto3

TABLE_NAME = os.environ.get("TABLE_NAME", "")

_dynamodb_resource = None


def _get_table():
    global _dynamodb_resource
    if _dynamodb_resource is None:
        _dynamodb_resource = boto3.resource("dynamodb")
    return _dynamodb_resource.Table(TABLE_NAME)


def handler(event, context=None):
    path_params = event.get("pathParameters") or {}
    documento_id = path_params.get("id")

    if documento_id:
        return _consultar_um(documento_id)
    return _consultar_todos((event.get("queryStringParameters") or {}).get("status"))


def _consultar_um(documento_id: str) -> dict:
    resposta = _get_table().get_item(Key={"id": documento_id})
    item = resposta.get("Item")
    if not item:
        return _resposta(404, {"erro": "documento não encontrado"})
    return _resposta(200, item)


def _consultar_todos(status_filtro: str) -> dict:
    table = _get_table()
    resposta = table.scan()
    itens = list(resposta.get("Items", []))
    while "LastEvaluatedKey" in resposta:
        resposta = table.scan(ExclusiveStartKey=resposta["LastEvaluatedKey"])
        itens.extend(resposta.get("Items", []))

    if status_filtro:
        itens = [item for item in itens if item.get("status") == status_filtro]

    itens.sort(key=lambda item: item.get("criado_em", ""), reverse=True)

    return _resposta(200, {"documentos": itens, "total": len(itens)})


def _resposta(status_code: int, corpo) -> dict:
    return {
        "statusCode": status_code,
        "headers": {"Content-Type": "application/json"},
        "body": json.dumps(corpo, default=str),
    }
