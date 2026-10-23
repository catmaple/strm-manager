#!/bin/sh
set -eu

DATA_DIR=/data
mkdir -p "$DATA_DIR/backups" "$DATA_DIR/logs" /media/movies /media/tv

if [ ! -f "$DATA_DIR/config.json" ]; then
    cp /opt/strm-manager/config.defaults.json "$DATA_DIR/config.json"
fi
chmod 600 "$DATA_DIR/config.json"

link_state() {
    src="$1"
    target="$2"
    if [ -L "$src" ] && [ "$(readlink "$src")" = "$target" ]; then
        return 0
    fi
    if [ -e "$src" ] || [ -L "$src" ]; then
        rm -rf "$src"
    fi
    ln -s "$target" "$src"
}

for state in crawl_state.json state.json health.json stats_snapshot.json; do
    link_state "/opt/m3u/$state" "$DATA_DIR/$state"
done
link_state /opt/m3u/config.json "$DATA_DIR/config.json"

exec python /opt/strm-manager/app.py
