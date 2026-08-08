#!/bin/sh
set -eu

secret_file="${1:-/srv/lcdash-platform/secrets/centralsquare_webhook_secret}"
base_url="${2:-http://127.0.0.1:8010}"

secret="$(cat "$secret_file")"
payload='{"CFSNumber":"WEBHOOK-DEPLOYMENT-TEST","Status":"Open"}'

first="$(
    curl --silent --show-error --fail \
        --user "lcdash:$secret" \
        --header "Content-Type: application/json" \
        --data "$payload" \
        "$base_url/api/integrations/centralsquare/webhooks/cfs"
)"

second="$(
    curl --silent --show-error --fail \
        --user "lcdash:$secret" \
        --header "Content-Type: application/json" \
        --data "$payload" \
        "$base_url/api/integrations/centralsquare/webhooks/cfs"
)"

health="$(
    curl --silent --show-error --fail "$base_url/health"
)"

metadata_count="$(
    docker exec lcdash-postgres \
        psql -U lcdash_user -d lcdash -Atc \
        "SELECT count(*) FROM lcdash_realtime.webhook_events WHERE source = 'cfs';"
)"

metadata_columns="$(
    docker exec lcdash-postgres \
        psql -U lcdash_user -d lcdash -Atc \
        "SELECT string_agg(column_name, ',' ORDER BY ordinal_position)
         FROM information_schema.columns
         WHERE table_schema = 'lcdash_realtime'
           AND table_name = 'webhook_events';"
)"

printf 'First delivery: %s\n' "$first"
printf 'Duplicate delivery: %s\n' "$second"
printf 'Health: %s\n' "$health"
printf 'Stored metadata rows: %s\n' "$metadata_count"
printf 'Metadata columns: %s\n' "$metadata_columns"
