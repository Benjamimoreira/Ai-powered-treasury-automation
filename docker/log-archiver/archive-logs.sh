#!/bin/sh
set -eu

DATE=$(date +%F)
DEST="/archive/$DATE"
mkdir -p "$DEST"

# Copia todos os .log de hoje (api, dashboard, scripts) para o arquivo do dia
for src in /logs-app /logs-scripts; do
    if [ -d "$src" ]; then
        find "$src" -maxdepth 1 -name '*.log' -newermt "$DATE 00:00:00" -exec cp {} "$DEST/" \;
    fi
done

# Extrai só as linhas de erro/exceção para um ficheiro à parte, fácil de consultar
: > "$DEST/errors.log"
for f in "$DEST"/*.log; do
    [ -f "$f" ] || continue
    [ "$(basename "$f")" = "errors.log" ] && continue
    grep -iE 'error|exception|traceback|critical' "$f" >> "$DEST/errors.log" 2>/dev/null || true
done

# Compacta o dia anterior (já fechado) e remove a pasta original
YESTERDAY=$(date -d 'yesterday' +%F 2>/dev/null || date -v-1d +%F)
if [ -d "/archive/$YESTERDAY" ] && [ ! -f "/archive/$YESTERDAY.tar.gz" ]; then
    tar -czf "/archive/$YESTERDAY.tar.gz" -C /archive "$YESTERDAY"
    rm -rf "/archive/$YESTERDAY"
fi

echo "[$(date '+%F %T')] Arquivo de logs atualizado em $DEST"
