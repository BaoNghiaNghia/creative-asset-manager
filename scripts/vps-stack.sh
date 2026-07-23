#!/usr/bin/env bash
set -Eeuo pipefail

ROOT="/mnt/disk2/desify/creative-asset-manager"
ENV_FILE="/etc/creative-asset-manager/production.env"
COMPOSE_FILE="$ROOT/infrastructure/docker/docker-compose.prod.yml"

cd "$ROOT"

die() {
  echo "ERROR: $*" >&2
  exit 1
}

require_file() {
  [[ -f "$1" ]] || die "Missing required file: $1"
}

require_command() {
  command -v "$1" >/dev/null 2>&1 ||
    die "Required command not found: $1"
}

require_command git
require_command docker

require_file "$ENV_FILE"
require_file "$COMPOSE_FILE"

docker compose version >/dev/null 2>&1 ||
  die "Docker Compose plugin is unavailable"

if ! git rev-parse --is-inside-work-tree >/dev/null 2>&1; then
  die "$ROOT is not a Git repository"
fi

# Refuse deployment from a modified source tree.
if [[ -n "$(git status --porcelain)" ]]; then
  echo "Refusing operation: Git working tree is not clean." >&2
  git status --short >&2
  exit 1
fi

# Always derive the release commit from the currently checked-out source.
export BUILD_COMMIT
BUILD_COMMIT="$(git rev-parse HEAD)"

export CAM_PRODUCTION_ENV_FILE="$ENV_FILE"

compose() {
  docker compose \
    --env-file "$ENV_FILE" \
    -f "$COMPOSE_FILE" \
    "$@"
}

show_release() {
  echo "Repository:    $ROOT"
  echo "Branch:        $(git branch --show-current)"
  echo "Build commit:  $BUILD_COMMIT"
  echo "Environment:   $ENV_FILE"
  echo "Compose file:  $COMPOSE_FILE"
}

validate_env_format() {
  local invalid_lines

  invalid_lines="$(
    awk '
      /^[[:space:]]*$/ { next }
      /^[[:space:]]*#/ { next }
      /^[A-Za-z_][A-Za-z0-9_]*=/ { next }
      { print NR ": " $0 }
    ' "$ENV_FILE"
  )"

  if [[ -n "$invalid_lines" ]]; then
    echo "Invalid lines found in $ENV_FILE:" >&2
    echo "$invalid_lines" >&2
    exit 1
  fi

  local unresolved_placeholders

  unresolved_placeholders="$(
    awk '
      /^[[:space:]]*#/ { next }
      /^[[:space:]]*$/ { next }
      /REPLACE_|assets[.]example[.]com/ {
        print NR ": " $0
      }
    ' "$ENV_FILE"
  )"

  if [[ -n "$unresolved_placeholders" ]]; then
    echo "Unresolved placeholder found in $ENV_FILE:" >&2
    echo "$unresolved_placeholders" >&2
    exit 1
  fi
}

validate_config() {
  validate_env_format
  compose config --quiet
}

wait_for_elasticsearch() {
  local attempt

  for attempt in $(seq 1 30); do
    if curl \
      --fail \
      --silent \
      --show-error \
      "http://127.0.0.1:9200/_cluster/health?wait_for_status=yellow&timeout=3s" \
      >/dev/null 2>&1
    then
      echo "Elasticsearch is ready."
      return 0
    fi

    sleep 2
  done

  echo "Elasticsearch did not become ready." >&2
  compose logs --tail=100 elasticsearch >&2 || true
  return 1
}

wait_for_api() {
  local attempt
  local trusted_host

  trusted_host="$(
    awk -F= '
      $1 == "TRUSTED_HOSTS" {
        value=$0
        sub(/^[^=]*=/, "", value)
        split(value, hosts, ",")
        print hosts[1]
        exit
      }
    ' "$ENV_FILE"
  )"

  [[ -n "$trusted_host" ]] ||
    die "TRUSTED_HOSTS is missing from $ENV_FILE"

  for attempt in $(seq 1 30); do
    if curl \
      --fail \
      --silent \
      --show-error \
      -H "Host: $trusted_host" \
      "http://127.0.0.1:8000/ready" \
      >/dev/null 2>&1
    then
      echo "API is ready."
      return 0
    fi

    sleep 2
  done

  echo "API did not become ready." >&2
  compose logs --tail=150 api >&2 || true
  return 1
}

case "${1:-}" in
  config)
    show_release
    validate_config
    echo "Compose configuration is valid."
    ;;

  build)
    show_release
    validate_config
    compose build --pull api
    ;;

  elasticsearch)
    show_release
    validate_config
    compose up -d elasticsearch
    wait_for_elasticsearch
    ;;

  migrate)
    show_release
    validate_config
    compose --profile migration run --rm migrate
    ;;

  up)
    show_release
    validate_config
    compose up -d elasticsearch api worker
    wait_for_elasticsearch
    wait_for_api
    compose ps
    ;;

  deploy)
    show_release
    validate_config

    echo
    echo "Building backend image..."
    compose build --pull api

    echo
    echo "Starting Elasticsearch..."
    compose up -d elasticsearch
    wait_for_elasticsearch

    echo
    echo "Running database migrations..."
    compose --profile migration run --rm migrate

    echo
    echo "Starting API and worker..."
    compose up -d api worker
    wait_for_api

    echo
    echo "Deployment completed."
    compose ps
    ;;

  restart)
    show_release
    validate_config
    compose restart api worker
    wait_for_api
    compose ps
    ;;

  stop)
    show_release
    compose stop api worker elasticsearch
    ;;

  down)
    show_release
    compose down
    ;;

  ps|status)
    show_release
    compose ps
    ;;

  logs)
    compose logs --tail=200 -f api worker elasticsearch
    ;;

  api-logs)
    compose logs --tail=200 -f api
    ;;

  worker-logs)
    compose logs --tail=200 -f worker
    ;;

  version)
    show_release
    ;;

  *)
    cat >&2 <<USAGE
Usage:
  $0 config
  $0 build
  $0 elasticsearch
  $0 migrate
  $0 up
  $0 deploy
  $0 restart
  $0 stop
  $0 down
  $0 ps
  $0 logs
  $0 api-logs
  $0 worker-logs
  $0 version
USAGE
    exit 1
    ;;
esac
