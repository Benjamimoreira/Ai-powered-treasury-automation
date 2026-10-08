#!/usr/bin/env bash
# Deploy num runner self-hosted Linux - o mesmo que os passos "cmd" do job
# deploy em .github/workflows/ci.yml fazem num runner Windows: puxar as
# imagens novas, portas de qualidade do RAG e do LLM (só quando muda algo
# que afeta o LLM, ou em "Run workflow") e reiniciar os containers.
#
# Variáveis (definidas pelo ci.yml): IMAGE_PREFIX, IMAGE_TAG, EVENTO
# (github.event_name), ANTES (github.event.before), LIMIAR_RECALL,
# LIMIAR_EXATIDAO. Corre na pasta de trabalho do runner, onde está o .env.
set -euo pipefail

echo "== Diagnóstico"
whoami
docker version --format "cliente {{.Client.Version}} / servidor {{.Server.Version}}"
docker compose version
if [ ! -f .env ]; then
  echo "ERRO: falta o .env em $PWD - ver docs/MIGRACAO_SERVIDOR.md" >&2
  exit 1
fi
docker compose config --quiet

echo "== Puxar imagens novas"
docker compose pull --quiet

echo "== Mudou algo que afeta o LLM?"
correr=false
if [ "${EVENTO:-}" = "workflow_dispatch" ]; then
  correr=true
else
  alterados=$(git -c safe.directory='*' diff --name-only "${ANTES:-HEAD~1}" HEAD 2>/dev/null \
    || git -c safe.directory='*' diff --name-only HEAD~1 HEAD)
  echo "Ficheiros alterados:"; echo "$alterados"
  # <<< em vez de echo | grep -q: com pipefail, o grep -q a sair cedo fazia o echo falhar (SIGPIPE)
  if grep -Eq '^(app/services/(llm_resolver|llm_tracing|agente_ambiguos|resolucao_regras|rag_historico|indice_vetorial)\.py|app/evals/|evals/(ambiguos|recuperacao)\.json|requirements\.txt|Dockerfile|\.github/workflows/ci\.yml|deploy/deploy\.sh)' <<< "$alterados"; then
    correr=true
  else
    echo "Nada que afete o LLM mudou - avaliação não corre."
  fi
fi

if [ "$correr" = true ]; then
  echo "== Porta de qualidade da recuperação (sem LLM, imagem nova)"
  timeout 15m docker run --rm "$IMAGE_PREFIX/api:$IMAGE_TAG" \
    python -m app.evals.avaliar_recuperacao --metodos recencia denso --limiar-recall "${LIMIAR_RECALL:-0.75}"

  echo "== Porta de qualidade do LLM (Ollama local, imagem nova)"
  curl -sf -o /dev/null http://localhost:11434/api/tags \
    || { echo "ERRO: o Ollama não está a correr nesta máquina" >&2; exit 1; }
  # --add-host: em Linux o host.docker.internal não existe por omissão. O
  # Ollama tem de ouvir para lá do 127.0.0.1 (OLLAMA_HOST=0.0.0.0) para o
  # container lhe chegar - ver docs/MIGRACAO_SERVIDOR.md.
  timeout 90m docker run --rm --add-host=host.docker.internal:host-gateway --env-file .env \
    -e LLM_FORNECEDOR=ollama -e OLLAMA_URL=http://host.docker.internal:11434/v1 -e OLLAMA_MODEL_ID=qwen2.5:3b \
    -e PHOENIX_COLLECTOR_ENDPOINT=http://host.docker.internal:6006/v1/traces -e PHOENIX_URL_PUBLICA=http://localhost:6006 \
    -e LANGSMITH_TRACING=false -e IMAGE_TAG="$IMAGE_TAG" "$IMAGE_PREFIX/api:$IMAGE_TAG" \
    python -m app.evals.avaliar --estrategia hibrida --phoenix --limiar "${LIMIAR_EXATIDAO:-0.65}"
fi

echo "== Reiniciar containers"
docker compose up -d --no-build --remove-orphans

echo "== Limpar imagens antigas"
docker image prune -f
