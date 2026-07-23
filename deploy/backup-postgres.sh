#!/bin/sh
set -eu

BACKUP_DIR=/backups/postgresql
RETENTION_DAYS="${POSTGRES_BACKUP_RETENTION_DAYS:-30}"
STAMP="$(date -u +%Y%m%dT%H%M%SZ)"
TARGET="${BACKUP_DIR}/lcdash-${STAMP}.sql.gz"

mkdir -p "${BACKUP_DIR}"
export PGPASSWORD="$(cat /run/secrets/postgres_password)"

pg_dump \
    --host=postgres \
    --username="${POSTGRES_USER}" \
    --dbname="${POSTGRES_DB}" \
    --no-owner \
    --no-privileges \
    | gzip -9 > "${TARGET}"

chmod 600 "${TARGET}"
find "${BACKUP_DIR}" -type f -name 'lcdash-*.sql.gz' -mtime "+${RETENTION_DAYS}" -delete
echo "Created ${TARGET}"
