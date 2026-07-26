#!/bin/sh
set -eu

REMOTE_PATH="${MINDSHARE_KNOWLEDGE_RCLONE_REMOTE:-lcdash-drive:Mindshare Documents}"
TARGET_PATH="${MINDSHARE_KNOWLEDGE_SYNC_TARGET:-/knowledge/mindshare}"
INTERVAL="${MINDSHARE_KNOWLEDGE_SYNC_INTERVAL_SECONDS:-900}"
STATUS_FILE="${TARGET_PATH}/.drive-sync-status"

mkdir -p "$TARGET_PATH"

while true; do
    started_at="$(date -u +%Y-%m-%dT%H:%M:%SZ)"
    if rclone copy "$REMOTE_PATH" "$TARGET_PATH" \
        --include "*.pdf" \
        --include "*.PDF" \
        --include "*.docx" \
        --include "*.DOCX" \
        --include "*.txt" \
        --include "*.md" \
        --include "*.cfg" \
        --include "*.ini" \
        --include "*.conf" \
        --include "*.json" \
        --include "*.xml" \
        --include "*.csv" \
        --include "*.yaml" \
        --include "*.yml" \
        --exclude "*password*" \
        --exclude "*credential*" \
        --exclude "*secret*" \
        --exclude "*.env" \
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
