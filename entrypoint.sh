#!/bin/sh
set -eu

DATA_DIR=/data
mkdir -p "$DATA_DIR/backups" "$DATA_DIR/logs" /media/movies /media/tv

if [ ! -f "$DATA_DIR/config.json" ]; then
    cp /opt/strm-manager/config.defaults.json "$DATA_DIR/config.json"
fi
chmod 600 "$DATA_DIR/config.json"

for state in crawl_state.json state.json health.json stats_snapshot.json; do
    if [ ! -e "/opt/m3u/$state" ]; then
        ln -s "$DATA_DIR/$state" "/opt/m3u/$state"
    fi
done
if [ ! -e /opt/m3u/config.json ]; then
    ln -s "$DATA_DIR/config.json" /opt/m3u/config.json
fi

exec python /opt/strm-manager/app.py
