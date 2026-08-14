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

require_deployment_user() {
  local expected="$1"
  local override="${2:-}"
  local current
  current="$(id -un)"
  if [[ "$current" == "$expected" ]]; then
    return
  fi
  if [[ -z "$override" || "$override" != "$current" ]]; then
    deploy_die "Run as $expected or pass --allow-user $current explicitly."
  fi
  printf 'Deployment user override accepted for %s.\n' "$current"
}

verify_frontend_dist() {
  local dist="$1"
  [[ -f "$dist/index.html" ]] || deploy_die "Frontend dist is missing index.html: $dist"
  [[ -f "$dist/build-info.json" ]] || deploy_die "Frontend build marker is missing: $dist/build-info.json"
  if ! find "$dist/assets" -type f -print -quit 2>/dev/null | grep -q .; then
    deploy_die "Frontend dist has no generated assets."
  fi
}

scan_frontend_dist() {
  local dist="$1"
  verify_frontend_dist "$dist"
  local pattern
  # A bare library error message can legitimately mention localhost.  Detect
  # actual local endpoints while retaining fail-closed checks for loopback IPs,
  # database URLs, credential names, and token-shaped values.
  pattern='(https?:)?//localhost([/:]|$)|localhost:[0-9]+|127[.]0[.]0[.]1|(postgresql|postgres|mysql|mariadb|mongodb)([+][A-Za-z0-9_-]+)?://|DATABASE_URL|GEMINI_API_KEY|OPENAI_API_KEY|GOOGLE_CLIENT_SECRET|MICROSOFT_CLIENT_SECRET|(OAUTH_(ACCESS_|REFRESH_)?|ACCESS_|REFRESH_)TOKEN|BEGIN (RSA |EC |OPENSSH )?PRIVATE KEY|(^|[^A-Za-z0-9])(sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|ya29[.][A-Za-z0-9_-]{20,}|gh[pousr]_[A-Za-z0-9]{20,}|xox[baprs]-[A-Za-z0-9-]{10,}|Bearer[[:space:]]+[A-Za-z0-9._~-]{20,})'
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

validate_production_env_file() {
  local env_file="$1"
  [[ -f "$env_file" ]] || deploy_die "Production env file is missing."
  local mode
  mode="$(stat -c '%a' "$env_file")"
  if [[ "$mode" != "600" && "$mode" != "640" ]]; then
    deploy_die "Production env must have mode 0600 or 0640."
  fi
  if LC_ALL=C grep -qE 'CHANGE_ME|REPLACE_[A-Z0-9_]+|<[^>]+>' "$env_file"; then
    deploy_die "Production env still contains a placeholder."
  fi

  local app_env public_url database_url cookie_secure persistent_auth
  local dev_tenant legacy_admin api_docs
  app_env="$(read_env_value "$env_file" APP_ENV)"
  public_url="$(read_env_value "$env_file" PUBLIC_APP_URL)"
  database_url="$(read_env_value "$env_file" DATABASE_URL)"
  cookie_secure="$(read_env_value "$env_file" AUTH_COOKIE_SECURE)"
  persistent_auth="$(read_env_value "$env_file" PERSISTENT_AUTH_ENABLED)"
  dev_tenant="$(read_env_value "$env_file" DEVELOPMENT_PERSONAL_TENANT_ENABLED)"
  legacy_admin="$(read_env_value "$env_file" AUTH_PROCESSING_ADMIN_ALLOWLIST_COMPAT_ENABLED)"
  api_docs="$(read_env_value "$env_file" API_DOCS_ENABLED)"

  [[ "$app_env" == "production" ]] || deploy_die "APP_ENV must be production."
  [[ "$public_url" == https://* ]] || deploy_die "PUBLIC_APP_URL must use HTTPS."
  if [[ "$database_url" != postgresql+psycopg://*host.docker.internal:5432/* ]]; then
    deploy_die "DATABASE_URL must use native PostgreSQL through host.docker.internal."
  fi
  [[ "$database_url" != *sqlite* ]] || deploy_die "SQLite is forbidden in production."
  [[ "$cookie_secure" == "true" ]] || deploy_die "Secure cookies must be enabled."
  [[ "$persistent_auth" == "true" ]] || deploy_die "Persistent RBAC authentication must be enabled."
  [[ "$dev_tenant" == "false" ]] || deploy_die "Development tenant bootstrap must be disabled."
  [[ "$legacy_admin" == "false" ]] || deploy_die "Legacy admin allowlist compatibility must be disabled."
  [[ "$api_docs" == "false" ]] || deploy_die "API documentation must be disabled in production."
}

safe_release_id() {
  [[ "$1" =~ ^[0-9a-f]{7,40}$ ]] || deploy_die "Release must be a Git commit SHA."
}

version_matches_commit() {
  local url="$1"
  local commit="$2"
  local response
  response="$(curl --fail --silent --show-error --max-time 5 "$url")" || return 1
  printf '%s' "$response" | grep -Eq '"commit"[[:space:]]*:[[:space:]]*"'"$commit"'"'
}

wait_for_api_release() {
  local base_url="$1"
  local commit="$2"
  local attempts="${3:-40}"
  local attempt
  for ((attempt=1; attempt<=attempts; attempt++)); do
    if curl --fail --silent --max-time 5 "$base_url/live" >/dev/null &&
      curl --fail --silent --max-time 5 "$base_url/ready" >/dev/null &&
      version_matches_commit "$base_url/version" "$commit"; then
      return
    fi
    sleep 2
  done
  deploy_die "API readiness failed; API did not become live, ready and version-matched."
}
