# Pipeline AWS de documentos (Textract)

Infraestrutura serverless (AWS SAM) para o fluxo:

```
Upload (dashboard ou pasta)
  -> S3 (bucket "raw")
  -> Lambda #1 (Textract)
  -> Lambda #2 (valida/estrutura, reaproveita app/services/aws_pipeline.py)
  -> DynamoDB
       -> SNS (alerta por email se status != "processado")
  -> API Gateway + Lambda #3 (consulta)
  -> Dashboard (Streamlit)
```

Isto é código de infraestrutura para **deploy manual teu** — nada aqui é
executado automaticamente; precisas de conta AWS e AWS CLI/SAM CLI
configurados na tua máquina.

## Pré-requisitos

- Conta AWS com permissões para criar S3, Lambda, DynamoDB, SNS, API Gateway
  e roles IAM (para uma conta pessoal/sandbox, `AdministratorAccess` é o mais simples).
- [AWS CLI](https://docs.aws.amazon.com/cli/latest/userguide/getting-started-install.html)
  configurado (`aws configure`).
- [AWS SAM CLI](https://docs.aws.amazon.com/serverless-application-model/latest/developerguide/install-sam-cli.html) instalado.
- Docker (opcional — só é preciso se quiseres `sam build --use-container`;
  como as 3 Lambdas são só Python + boto3, o build local sem container costuma chegar).

## Deploy

```bash
cd infra_aws
sam build
sam deploy --guided
```

No modo `--guided` vais escolher: nome da stack, região, e o parâmetro
`AlertEmail` (o email que recebe os alertas SNS — podes deixar em branco e
subscrever o tópico mais tarde na consola). As respostas ficam gravadas em
`samconfig.toml` para deploys seguintes (`sam deploy` sem `--guided`).

Se `AlertEmail` for preenchido, a AWS envia um email de confirmação de
subscrição SNS — tens de clicar em "Confirm subscription" para começar a
receber alertas.

No fim do deploy, os `Outputs` mostram:

- `ApiUrl` — usar como `AWS_API_URL` no `.env` do dashboard.
- `RawBucketName` — usar como `AWS_RAW_BUCKET` no `.env` do dashboard.

## Testar manualmente

```bash
# substitui pelo bucket real do Output RawBucketName
aws s3 cp fatura_exemplo.png s3://<RawBucketName>/uploads/fatura_exemplo.png

# passados alguns segundos (Textract + Lambdas):
curl "<ApiUrl>/documentos"
```

O documento tem de ser uma imagem (PNG/JPEG) ou PDF de uma página, com
linhas de texto reconhecíveis pelo parser existente em
`app/services/aws_pipeline.py` (`Fornecedor:`, `Data:`, `Nº Fatura:`,
`Valor Total:`) — é o mesmo parser (e os mesmos testes) usados pelo
endpoint local `/aws/processar-documento`.

## Ligar ao dashboard

No `.env` da raiz do projeto (usado pelo Streamlit):

```
AWS_API_URL=https://xxxxx.execute-api.eu-west-1.amazonaws.com/Prod
AWS_RAW_BUCKET=tesouraria-documentos-raw-...
AWS_REGION=eu-west-1
```

O separador "Documentos (AWS)" do dashboard fica escondido/desativado
enquanto estas variáveis não estiverem definidas. O upload feito a partir
do dashboard usa as credenciais AWS locais (as mesmas do `aws configure`)
para escrever diretamente no bucket S3.

## Limitações conhecidas (por ser um projeto de portefólio)

- Lambda #1 usa `Textract.detect_document_text` (só texto/linhas, sem
  `AnalyzeDocument` FORMS/TABLES) — suficiente para o parser determinístico
  atual, mas menos robusto a layouts de fatura muito diferentes.
- `detect_document_text` síncrono só processa imagens e PDFs de uma
  página; documentos multi-página precisariam da API assíncrona do
  Textract (`StartDocumentTextDetection` + notificação SNS de conclusão).
- Sem autenticação na API Gateway (endpoint público) — para um cenário
  real, adicionar API key ou um autorizador (Cognito/IAM).

## Remover tudo

```bash
sam delete
```
