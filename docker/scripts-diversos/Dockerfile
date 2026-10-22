# Scripts diversos (pasta "scripts diversos" do Ambiente de Trabalho), em modo
# linha de comando - corre sob pedido:
#   docker compose run --rm scripts-diversos pdf_depesas.py /pdfs/ficheiro.pdf
# (a pasta PDFS_RAIZ_DOCKER do .env é montada em /pdfs).
FROM python:3.11-slim

ENV PYTHONUNBUFFERED=1 PYTHONIOENCODING=utf-8

WORKDIR /app

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY pdf_depesas.py ./

ENTRYPOINT ["python"]
CMD ["-c", "print('Uso: docker compose run --rm scripts-diversos pdf_depesas.py /pdfs/ficheiro.pdf [/pdfs/pasta_destino]')"]
