#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/mnt/disk2/desify/creative-asset-manager"
ENV_FILE="/etc/creative-asset-manager/production.env"
COMPOSE_FILE="$ROOT/infrastructure/docker/docker-compose.prod.yml"

cd "$ROOT"

export BUILD_COMMIT="$(git rev-parse HEAD)"
export CAM_PRODUCTION_ENV_FILE="$ENV_FILE"

compose() {
  docker compose \
    --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" \
    "$@"
}

echo "Deploy commit: $BUILD_COMMIT"

case "${1:-}" in
  config)
    compose config --quiet
    ;;

  build)
    compose build --pull api
    ;;

  migrate)
    compose --profile migration run --rm migrate
    ;;

  up)
    compose up -d elasticsearch api worker
    ;;

  deploy)
    compose config --quiet
    compose build --pull api
    compose --profile migration run --rm migrate
    compose up -d elasticsearch api worker
    compose ps
    ;;

  restart)
    compose restart api worker
    ;;

  ps)
    compose ps
    ;;

  logs)
    compose logs --tail=200 -f api worker elasticsearch
    ;;

  *)
    echo "Usage: $0 {config|build|migrate|up|deploy|restart|ps|logs}" >&2
    exit 1
    ;;
esac
