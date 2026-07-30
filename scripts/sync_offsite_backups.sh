#!/bin/sh
set -eu

SOURCE_PATH="${OFFSITE_BACKUP_SOURCE:-/backups}"
REMOTE_PATH="${OFFSITE_BACKUP_REMOTE:-lcdash-backup:server-227}"
INTERVAL="${OFFSITE_BACKUP_INTERVAL_SECONDS:-86400}"
STATUS_FILE="${SOURCE_PATH}/.offsite-sync-status"

while true; do
    started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if rclone copy "$SOURCE_PATH" "$REMOTE_PATH" \
        --filter "+ /postgresql/**" \
        --filter "+ /openwebui-*.tar.gz" \
        --filter "+ /knowledge-*.tar.gz" \
        --filter "- **" \
        --min-size 1M \
        --min-age 5m \
        --checksum \
        --metadata \
        --create-empty-src-dirs \
        --transfers 2 \
        --checkers 4 \
        --log-level INFO \
        && rclone copy "$SOURCE_PATH" "$REMOTE_PATH" \
        --filter "+ /jack-*.json" \
        --filter "+ /recovery-*.txt" \
        --filter "- **" \
        --checksum \
        --metadata \
        --transfers 2 \
        --checkers 4 \
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
