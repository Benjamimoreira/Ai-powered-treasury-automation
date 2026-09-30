FROM python:3.11-slim

WORKDIR /app

COPY requirements.txt .
# PyTorch só-CPU (o container não tem GPU) - sem isto o sentence-transformers
# puxa a versão com CUDA e a imagem passa de ~2 GB para ~8 GB.
RUN pip install --no-cache-dir torch --index-url https://download.pytorch.org/whl/cpu \
    && pip install --no-cache-dir -r requirements.txt

COPY app/ app/
COPY scripts/ scripts/
COPY dashboard/ dashboard/
COPY .streamlit/ .streamlit/
# o Assistente (app/services/chatbot.py) arranca o servidor MCP como
# subprocesso - sem ele no container o separador Assistente não funciona
COPY mcp_server.py .
# conjunto de avaliação do LLM - a porta de qualidade do deploy corre-o
# dentro da imagem nova (ver .github/workflows/ci.yml)
COPY evals/ambiguos.json evals/ambiguos.json

EXPOSE 8000
EXPOSE 8501

CMD ["sh", "-c", "python scripts/criar_tabelas.py && uvicorn app.main:app --host 0.0.0.0 --port 8000"]
