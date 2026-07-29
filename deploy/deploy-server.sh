#!/bin/sh
set -eu

ARCHIVE="${1:-}"
CURRENT_DIR=/srv/lcdash-platform/current
PLATFORM_DIR=/srv/lcdash-platform
COMPOSE_FILE=deploy/compose.yaml

if [ -z "${ARCHIVE}" ] || [ ! -f "${ARCHIVE}" ]; then
    echo "Deployment archive was not found."
    exit 1
fi

mkdir -p "${PLATFORM_DIR}/releases"
STAGING_DIR="$(mktemp -d "${PLATFORM_DIR}/releases/lcdash-release.XXXXXX")"
PREVIOUS_DIR="${PLATFORM_DIR}/previous"

cleanup_staging() {
    if [ -d "${STAGING_DIR}" ]; then
        rm -rf "${STAGING_DIR}"
    fi
}

trap cleanup_staging EXIT

tar -xzf "${ARCHIVE}" -C "${STAGING_DIR}"

test -f "${STAGING_DIR}/Dockerfile"
test -f "${STAGING_DIR}/${COMPOSE_FILE}"
test -f "${STAGING_DIR}/app/main.py"

cd "${STAGING_DIR}"
docker compose -f "${COMPOSE_FILE}" config --quiet

had_previous_release=0
if [ -d "${CURRENT_DIR}" ]; then
    had_previous_release=1
    if [ -e "${PREVIOUS_DIR}" ]; then
        rm -rf "${PREVIOUS_DIR}"
    fi
    cd "${CURRENT_DIR}"
    docker compose -f "${COMPOSE_FILE}" down
    mv "${CURRENT_DIR}" "${PREVIOUS_DIR}"
fi

mv "${STAGING_DIR}" "${CURRENT_DIR}"
STAGING_DIR=

deployment_failed=0

cd "${CURRENT_DIR}"
if ! docker compose -f "${COMPOSE_FILE}" up -d --build; then
    deployment_failed=1
fi

if [ "${deployment_failed}" -eq 0 ]; then
    attempt=0
    while [ "${attempt}" -lt 30 ]; do
        lcdash_status="$(curl -sS -o /dev/null -w '%{http_code}' \
            http://127.0.0.1:8010/dashboard 2>/dev/null || true)"
        webui_status="$(curl -sS -o /dev/null -w '%{http_code}' \
            http://127.0.0.1:3000/ 2>/dev/null || true)"

        if [ "${lcdash_status}" = "200" ] && [ "${webui_status}" = "200" ]; then
            break
        fi

        attempt=$((attempt + 1))
        sleep 2
    done

    if [ "${lcdash_status:-}" != "200" ] || [ "${webui_status:-}" != "200" ]; then
        deployment_failed=1
    fi
fi

if [ "${deployment_failed}" -ne 0 ]; then
    echo "Deployment health check failed."
    docker compose -f "${COMPOSE_FILE}" down || true
    rm -rf "${CURRENT_DIR}"
    if [ "${had_previous_release}" -eq 1 ] && [ -d "${PREVIOUS_DIR}" ]; then
        echo "Restoring the previous release."
        mv "${PREVIOUS_DIR}" "${CURRENT_DIR}"
        cd "${CURRENT_DIR}"
        docker compose -f "${COMPOSE_FILE}" up -d --build
    else
        echo "No previous release exists; the failed first deployment was removed."
    fi
    rm -f "${ARCHIVE}"
    exit 1
fi

if [ -e "${PREVIOUS_DIR}" ]; then
    rm -rf "${PREVIOUS_DIR}"
fi
rm -f "${ARCHIVE}"

echo "LCDash deployment completed successfully."
docker compose -f "${COMPOSE_FILE}" ps
