#!/usr/bin/env bash
# Shared deployment checks. Callers must enable strict mode themselves.

deploy_die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}

require_command() {
  command -v "$1" >/dev/null 2>&1 || deploy_die "Required command is unavailable: $1"
}

require_non_root() {
  local uid
  uid="$(id -u)"
  [[ "$uid" != "0" ]] || deploy_die "This script must not run as root."
}

verify_frontend_dist() {
  local dist="$1"
  [[ -f "$dist/index.html" ]] || deploy_die "Frontend dist is missing index.html: $dist"
  [[ -f "$dist/build-meta.json" ]] || deploy_die "Frontend build marker is missing: $dist/build-meta.json"
  find "$dist/assets" -type f -print -quit 2>/dev/null | grep -q . \
    || deploy_die "Frontend dist has no generated assets."
}

scan_frontend_dist() {
  local dist="$1"
  verify_frontend_dist "$dist"
  local pattern
  pattern='localhost|127[.]0[.]0[.]1|GEMINI_API_KEY|OPENAI_API_KEY|GOOGLE_CLIENT_SECRET|DATABASE_URL|BEGIN PRIVATE KEY|(^|[^A-Za-z0-9])(sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,})'
  if LC_ALL=C grep -RInaE --binary-files=without-match "$pattern" "$dist"; then
    deploy_die "Forbidden local or secret-like value found in frontend dist."
  fi
  if find "$dist" -type f -name '*.map' -print -quit | grep -q .; then
    deploy_die "Frontend source maps are disabled for committed production builds."
  fi
}

atomic_symlink() {
  local target="$1"
  local link="$2"
  local temporary="$link.new.$$"
  ln -s "$target" "$temporary"
  mv -Tf "$temporary" "$link"
}

read_env_value() {
  local env_file="$1"
  local name="$2"
  awk -F= -v key="$name" '
    $0 !~ /^[[:space:]]*#/ && $1 == key {
      value=substr($0, index($0, "=")+1)
      gsub(/^[[:space:]]+|[[:space:]]+$/, "", value)
      gsub(/^["'"'"']|["'"'"']$/, "", value)
      print value
      exit
    }
  ' "$env_file"
}

safe_release_id() {
  [[ "$1" =~ ^[0-9a-f]{7,40}$ ]] || deploy_die "Release must be a Git commit SHA."
}
