#!/bin/sh
set -eu

REMOTE_PATH="${KNOWLEDGE_RCLONE_REMOTE:-lcdash-drive:Central Squared CAD/pdf}"
TARGET_PATH="${KNOWLEDGE_SYNC_TARGET:-/knowledge/centralsquare}"
INTERVAL="${KNOWLEDGE_SYNC_INTERVAL_SECONDS:-900}"
STATUS_FILE="${TARGET_PATH}/.drive-sync-status"

mkdir -p "$TARGET_PATH"

while true; do
    started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if rclone copy "$REMOTE_PATH" "$TARGET_PATH" \
        --include "*.pdf" \
        --checksum \
        --metadata \
        --create-empty-src-dirs \
        --log-level INFO; then
        completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        {
            echo "status=complete"
            echo "remote=$REMOTE_PATH"
            echo "started_at=$started_at"
            echo "completed_at=$completed_at"
        } > "$STATUS_FILE"
    else
        completed_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
        {
            echo "status=failed"
            echo "remote=$REMOTE_PATH"
            echo "started_at=$started_at"
            echo "completed_at=$completed_at"
        } > "$STATUS_FILE"
    fi
    sleep "$INTERVAL"
done
