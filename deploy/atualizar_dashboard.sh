#!/usr/bin/env bash
# Servidor Linux: mantém o dashboard igual à última imagem publicada pelo CI.
#
# Na pasta com o docker-compose.dashboard.yml e o .env (API_PRODUCAO_URL):
#   chmod +x atualizar_dashboard.sh
#   crontab -e  ->  */10 * * * * /caminho/para/atualizar_dashboard.sh
#
# "up -d" só recria o contentor se a imagem mudou; sem imagem nova não faz nada.
set -euo pipefail
cd "$(dirname "$0")"

exec 9>/tmp/tesouraria-dashboard.lock
flock -n 9 || exit 0     # a volta anterior ainda está a puxar a imagem

compose="docker compose -f docker-compose.dashboard.yml"
antes=$($compose images -q dashboard 2>/dev/null || true)

$compose pull -q
$compose up -d --remove-orphans

depois=$($compose images -q dashboard)
if [ "$antes" != "$depois" ]; then
    echo "[$(date '+%F %T')] dashboard atualizado para ${depois:0:12}" >> atualizar_dashboard.log
    docker image prune -f > /dev/null
fi
