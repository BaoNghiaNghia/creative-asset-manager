#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
PROJECT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
RESTART=false

usage() {
  cat <<"USAGE"
Usage: cam-rebuild-backend.sh [--project-root PATH] [--env-file PATH] [--restart]

Builds the API backend image from a clean checkout using the protected
production environment file. It never runs migrations or deploys a frontend.
Pass --restart only after the image build has been reviewed.
USAGE
}

while (($#)); do
  case "$1" in
    --project-root)
      PROJECT_ROOT="${2:?missing project root}"
      shift 2
      ;;
    --env-file)
      ENV_FILE="${2:?missing environment file}"
      shift 2
      ;;
    --restart)
      RESTART=true
      shift
      ;;
    -h|--help)
      usage
      exit 0
      ;;
    *)
      printf "Unknown option: %s
" "$1" >&2
      exit 2
      ;;
  esac
done

PROJECT_ROOT="$(realpath -- "$PROJECT_ROOT")"
COMPOSE_FILE="$PROJECT_ROOT/infrastructure/docker/docker-compose.prod.yml"
[[ -d "$PROJECT_ROOT/.git" ]] || { printf "Repository not found.
" >&2; exit 1; }
[[ -f "$ENV_FILE" ]] || { printf "Production environment file is missing.
" >&2; exit 1; }
[[ -f "$COMPOSE_FILE" ]] || { printf "Production compose file is missing.
" >&2; exit 1; }
command -v docker >/dev/null 2>&1 || { printf "Docker is required.
" >&2; exit 1; }
[[ -z "$(git -C "$PROJECT_ROOT" status --porcelain --untracked-files=normal)" ]] || { printf "Refusing backend rebuild from a dirty checkout.
" >&2; exit 1; }

declare -a compose=(docker compose --env-file "$ENV_FILE" -f "$COMPOSE_FILE")
"${compose[@]}" config --quiet
export BUILD_COMMIT="$(git -C "$PROJECT_ROOT" rev-parse HEAD)"
printf "Building backend image for commit %s.
" "$BUILD_COMMIT"
"${compose[@]}" build api

if [[ "$RESTART" == true ]]; then
  printf "Restarting API and worker with the reviewed image.
"
  "${compose[@]}" up -d --no-build api worker
else
  printf "Backend image built; services were not restarted.
"
fi
