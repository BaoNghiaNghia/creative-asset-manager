#!/bin/sh
# Disposable test container, existing image only; no production database or volumes.
set -eu
: "${CAM_P2C_TEST_PYTHON:?path to isolated test Python required}"
: "${CAM_P2C_TEST_HARNESS:?path to isolated test harness required}"
export CAM_P2C_TEST_PASSWORD
CAM_P2C_TEST_PASSWORD="$("$CAM_P2C_TEST_PYTHON" -c 'import secrets; print(secrets.token_urlsafe(30))')"
export POSTGRES_PASSWORD="$CAM_P2C_TEST_PASSWORD"
cid=""
cleanup() {
    if [ -n "$cid" ]; then
        docker rm -f "$cid" >/dev/null
        echo "Disposable PostgreSQL container removed"
    fi
}
trap cleanup EXIT
trap 'exit 130' INT
trap 'exit 143' TERM
cid="$(docker run --rm -d --pull=never --memory=384m --cpus=1 \
    --label cam.purpose=image-orchestrator-p2c-isolated \
    --tmpfs /var/lib/postgresql/data:rw,size=192m \
    --tmpfs /var/run/postgresql:rw,size=8m \
    --publish 127.0.0.1::5432 \
    -e POSTGRES_USER=cam_p2c_test -e POSTGRES_DB=cam_p2c_isolated \
    -e POSTGRES_PASSWORD postgres:16.4)"
# Read only this newly created container's loopback port; don't print credentials.
CAM_P2C_TEST_PORT="$(docker port "$cid" 5432/tcp | sed -n 's/^127\.0\.0\.1://p')"
export CAM_P2C_TEST_PORT
case "$CAM_P2C_TEST_PORT" in ''|5432|*[!0-9]*) exit 1;; esac
unset POSTGRES_PASSWORD
i=0
until docker exec "$cid" pg_isready -U cam_p2c_test -d cam_p2c_isolated -h 127.0.0.1 >/dev/null 2>&1; do
    i=$((i+1))
    [ "$i" -lt 40 ] || { echo "Disposable PostgreSQL startup failed"; exit 1; }
    sleep 1
done
"$CAM_P2C_TEST_PYTHON" -I "$CAM_P2C_TEST_HARNESS" --postgres
