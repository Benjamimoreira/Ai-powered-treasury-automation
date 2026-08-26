#!/bin/sh
set -eu

DATE=$(date +%F)
DEST="/archive/$DATE"
mkdir -p "$DEST"

# Copia os .log atuais (api, dashboard, scripts) para o arquivo do dia -
# como estes ficheiros crescem por append (nunca são recriados), copiar o
# estado atual em cada corrida horária é suficiente; não há -newermt no
# find do busybox (Alpine), por isso não filtramos por data de modificação.
for src in /logs-app /logs-scripts /logs-scripts-faturas; do
    if [ -d "$src" ]; then
        find "$src" -maxdepth 1 -name '*.log' -exec cp {} "$DEST/" \;
    fi
done

# Extrai só as linhas de erro/exceção para um ficheiro à parte, fácil de consultar
: > "$DEST/errors.log"
for f in "$DEST"/*.log; do
    [ -f "$f" ] || continue
    [ "$(basename "$f")" = "errors.log" ] && continue
    grep -iE 'error|exception|traceback|critical' "$f" >> "$DEST/errors.log" 2>/dev/null || true
done

# Compacta o dia anterior (já fechado) e remove a pasta original - o date
# do busybox não entende "yesterday" nem -v-1d (BSD), só timestamps @epoch
YESTERDAY=$(date -d @$(($(date +%s) - 86400)) +%F)
if [ -d "/archive/$YESTERDAY" ] && [ ! -f "/archive/$YESTERDAY.tar.gz" ]; then
    tar -czf "/archive/$YESTERDAY.tar.gz" -C /archive "$YESTERDAY"
    rm -rf "/archive/$YESTERDAY"
fi

echo "[$(date '+%F %T')] Arquivo de logs atualizado em $DEST"
