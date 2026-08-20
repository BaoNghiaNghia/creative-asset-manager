#!/usr/bin/env bash
set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"

usage() {
  cat <<"USAGE"
Usage: deploy-cam-frontend.sh [--commit] [--push] [--message TEXT]

Builds and validates the committed frontend artifact through the canonical
build-frontend-release.sh workflow. It does not connect to or deploy a VPS.
Use scripts/deploy-vps.sh for a reviewed full production release.
USAGE
}

case "${1:-}" in
  -h|--help)
    usage
    exit 0
    ;;
esac

exec "$SCRIPT_DIR/build-frontend-release.sh" "$@"
