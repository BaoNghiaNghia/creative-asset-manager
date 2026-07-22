#!/usr/bin/env bash
# Interactive first-administrator bootstrap for local and VPS installations.
set -Eeuo pipefail

DEFAULT_REASON="Initial administrator bootstrap"
ENVIRONMENT=""
PROJECT_ROOT=""
ENV_FILE=""
VENV_DIR=""
PROVIDER=""
SUBJECT=""
TENANT_SLUG=""
TENANT_NAME=""
REASON="$DEFAULT_REASON"
ASSIGN_PLATFORM_ADMIN=false
ASSUME_YES=false

cleanup() {
  unset IDENTITIES_JSON REFERENCE_JSON DRY_RUN_JSON APPLY_JSON VERIFY_JSON SUBJECT
}

on_error() {
  local status=$?
  trap - ERR
  printf 'Administrator setup failed (exit %s). No secrets were printed.\n' "$status" >&2
  exit "$status"
}

trap cleanup EXIT
trap on_error ERR

usage() {
  cat <<'EOF'
Usage: scripts/setup-admin.sh [options]

  --environment local|production
  --project-root PATH
  --env-file PATH
  --venv PATH
  --provider google|microsoft
  --subject SUBJECT
  --tenant-slug SLUG
  --tenant-name NAME
  --reason TEXT
  --platform-admin
  --yes
EOF
}

while (($#)); do
  case "$1" in
    --environment) ENVIRONMENT="${2:?missing environment}"; shift 2 ;;
    --project-root) PROJECT_ROOT="${2:?missing project root}"; shift 2 ;;
    --env-file) ENV_FILE="${2:?missing environment file}"; shift 2 ;;
    --venv) VENV_DIR="${2:?missing virtualenv}"; shift 2 ;;
    --provider) PROVIDER="${2:?missing provider}"; shift 2 ;;
    --subject) SUBJECT="${2:?missing subject}"; shift 2 ;;
    --tenant-slug) TENANT_SLUG="${2:?missing tenant slug}"; shift 2 ;;
    --tenant-name) TENANT_NAME="${2:?missing tenant name}"; shift 2 ;;
    --reason) REASON="${2:?missing reason}"; shift 2 ;;
    --platform-admin) ASSIGN_PLATFORM_ADMIN=true; shift ;;
    --yes) ASSUME_YES=true; shift ;;
    -h|--help) usage; exit 0 ;;
    *) printf 'Unknown option: %s\n' "$1" >&2; usage >&2; exit 2 ;;
  esac
done

CURRENT_USER="$(id -un)"
if [[ -z "$ENVIRONMENT" ]]; then
  case "$CURRENT_USER" in
    baonghia) ENVIRONMENT="local" ;;
    desify) ENVIRONMENT="production" ;;
    *)
      printf 'User %s is not a recognized deployment user; pass --environment.\n' "$CURRENT_USER" >&2
      exit 2
      ;;
  esac
fi
if [[ "$ENVIRONMENT" != "local" && "$ENVIRONMENT" != "production" ]]; then
  printf '%s\n' "--environment must be local or production" >&2
  exit 2
fi

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
if [[ -z "$PROJECT_ROOT" ]]; then
  if [[ "$ENVIRONMENT" == "production" ]]; then
    PROJECT_ROOT="/opt/creative-asset-manager/current"
  else
    PROJECT_ROOT="$(cd "$SCRIPT_DIR/.." && pwd)"
  fi
fi
if [[ -z "$ENV_FILE" ]]; then
  if [[ "$ENVIRONMENT" == "production" ]]; then
    ENV_FILE="/etc/creative-asset-manager/production.env"
  else
    for candidate in \
      "$PROJECT_ROOT/.env.local" \
      "$PROJECT_ROOT/.env" \
      "$PROJECT_ROOT/apps/api/.env"
    do
      if [[ -f "$candidate" ]]; then
        ENV_FILE="$candidate"
        break
      fi
    done
    ENV_FILE="${ENV_FILE:-$PROJECT_ROOT/.env}"
  fi
fi
if [[ -z "$VENV_DIR" ]]; then
  if [[ "$ENVIRONMENT" == "production" ]]; then
    VENV_DIR="/opt/creative-asset-manager/venv"
  elif [[ -x "$PROJECT_ROOT/apps/api/.venv/bin/python" ]]; then
    VENV_DIR="$PROJECT_ROOT/apps/api/.venv"
  else
    VENV_DIR="$PROJECT_ROOT/.venv"
  fi
fi

if [[ ! -d "$PROJECT_ROOT/apps/api" ]]; then
  printf 'API project directory does not exist: %s\n' "$PROJECT_ROOT/apps/api" >&2
  exit 1
fi
if [[ ! -f "$ENV_FILE" ]]; then
  printf 'Environment file does not exist: %s\n' "$ENV_FILE" >&2
  if [[ "$ENVIRONMENT" == "local" ]]; then
    printf '%s\n' "Checked .env.local, .env, and apps/api/.env under the project root." >&2
  fi
  exit 1
fi
if [[ ! -d "$VENV_DIR" || ! -x "$VENV_DIR/bin/python" ]]; then
  printf 'Python virtualenv is unavailable: %s\n' "$VENV_DIR" >&2
  exit 1
fi

set -a
# The selected deployment environment file is operator-controlled.
. "$ENV_FILE"
set +a

PYTHON="$VENV_DIR/bin/python"
cd "$PROJECT_ROOT/apps/api"

printf 'Environment: %s\n' "$ENVIRONMENT"
printf 'Project root: %s\n' "$PROJECT_ROOT"
printf 'Checking database, Alembic revision, RBAC and legacy authorization...\n'
PREFLIGHT_JSON="$("$PYTHON" -m app.operations.auth_cli check-admin-setup --environment "$ENVIRONMENT")"
PREFLIGHT_JSON="$PREFLIGHT_JSON" "$PYTHON" -c '
import json, os
d=json.loads(os.environ["PREFLIGHT_JSON"])
assert d["database_reachable"] and d["rbac_enabled"]
assert not d["legacy_authorization_enabled"]
print("Preflight passed; Alembic head: " + d["alembic_head"])
'

if [[ "$ENVIRONMENT" == "production" && "$ASSUME_YES" == true ]]; then
  if [[ -z "$PROVIDER" || -z "$SUBJECT" || -z "$TENANT_SLUG" || -z "$TENANT_NAME" ]]; then
    printf '%s\n' "Production --yes requires --provider, --subject, --tenant-slug and --tenant-name." >&2
    exit 2
  fi
fi
if [[ -n "$PROVIDER" && "$PROVIDER" != "google" && "$PROVIDER" != "microsoft" ]]; then
  printf '%s\n' "--provider must be google or microsoft" >&2
  exit 2
fi
if [[ -n "$PROVIDER" && -z "$SUBJECT" ]] || [[ -z "$PROVIDER" && -n "$SUBJECT" ]]; then
  printf '%s\n' "--provider and --subject must be provided together" >&2
  exit 2
fi

IDENTITY_ARGS=(list-identities)
if [[ -n "$PROVIDER" ]]; then
  IDENTITY_ARGS+=(--provider "$PROVIDER" --subject "$SUBJECT")
fi
IDENTITIES_JSON="$("$PYTHON" -m app.operations.auth_cli "${IDENTITY_ARGS[@]}")"
IDENTITIES_JSON="$IDENTITIES_JSON" "$PYTHON" -c '
import json, os
rows=json.loads(os.environ["IDENTITIES_JSON"])["identities"]
if not rows:
    raise SystemExit("No matching application identities found. Sign in once, then rerun setup.")
print("Available application identities:")
for index, row in enumerate(rows, 1):
    print("  {}. {} | {} | {} | {}".format(index, row["provider"], row["masked_email"] or "-", row["subject_short"], row["user_status"]))
'
IDENTITY_COUNT="$(IDENTITIES_JSON="$IDENTITIES_JSON" "$PYTHON" -c 'import json,os; print(len(json.loads(os.environ["IDENTITIES_JSON"])["identities"]))')"
if [[ -n "$PROVIDER" ]]; then
  [[ "$IDENTITY_COUNT" == "1" ]] || { printf 'Expected one matching identity, found %s.\n' "$IDENTITY_COUNT" >&2; exit 1; }
  IDENTITY_INDEX=1
elif [[ "$ASSUME_YES" == true ]]; then
  printf '%s\n' "--yes requires --provider and --subject" >&2
  exit 2
else
  read -r -p "Select identity number: " IDENTITY_INDEX
fi
if [[ ! "$IDENTITY_INDEX" =~ ^[0-9]+$ ]] || ((IDENTITY_INDEX < 1 || IDENTITY_INDEX > IDENTITY_COUNT)); then
  printf '%s\n' "Invalid identity selection." >&2
  exit 2
fi

IDENTITY_ID="$(IDENTITIES_JSON="$IDENTITIES_JSON" IDENTITY_INDEX="$IDENTITY_INDEX" "$PYTHON" -c '
import json,os
row=json.loads(os.environ["IDENTITIES_JSON"])["identities"][int(os.environ["IDENTITY_INDEX"])-1]
if row["user_status"] != "active":
    raise SystemExit("Selected application user is disabled or suspended.")
print(row["identity_id"])
')"
REFERENCE_JSON="$("$PYTHON" -m app.operations.auth_cli resolve-identity-reference --identity-id "$IDENTITY_ID")"
PROVIDER="$(REFERENCE_JSON="$REFERENCE_JSON" "$PYTHON" -c 'import json,os; print(json.loads(os.environ["REFERENCE_JSON"])["provider"])')"
SUBJECT="$(REFERENCE_JSON="$REFERENCE_JSON" "$PYTHON" -c 'import json,os; print(json.loads(os.environ["REFERENCE_JSON"])["subject"])')"

if [[ -z "$TENANT_SLUG" ]]; then
  [[ "$ASSUME_YES" != true ]] || { printf '%s\n' "--yes requires --tenant-slug" >&2; exit 2; }
  read -r -p "Tenant slug: " TENANT_SLUG
fi
if [[ -z "$TENANT_NAME" ]]; then
  [[ "$ASSUME_YES" != true ]] || { printf '%s\n' "--yes requires --tenant-name" >&2; exit 2; }
  read -r -p "Tenant name: " TENANT_NAME
fi

BOOTSTRAP_ARGS=(--provider "$PROVIDER" --subject "$SUBJECT" --tenant-slug "$TENANT_SLUG" --tenant-name "$TENANT_NAME" --reason "$REASON")
printf '%s\n' "Running mandatory dry-run..."
DRY_RUN_JSON="$("$PYTHON" -m app.operations.auth_cli bootstrap-access "${BOOTSTRAP_ARGS[@]}" --dry-run)"
DRY_RUN_JSON="$DRY_RUN_JSON" "$PYTHON" -c '
import json,os
d=json.loads(os.environ["DRY_RUN_JSON"])
print("Planned changes:")
print("  tenant created: " + ("yes" if d["tenant_created"] else "already exists"))
print("  membership created: " + ("yes" if d["membership_created"] else "already exists"))
print("  permissions to seed: " + str(d["permissions_created"]))
print("  roles to seed: " + str(d["roles_created"]))
print("  role grants to seed: " + str(d["role_permissions_created"]))
'

if [[ "$ASSUME_YES" != true ]]; then
  read -r -p "Apply tenant administrator setup? Type YES to continue: " CONFIRM
  if [[ "$CONFIRM" != "YES" ]]; then
    printf '%s\n' "No changes applied."
    exit 0
  fi
fi
APPLY_JSON="$("$PYTHON" -m app.operations.auth_cli bootstrap-access "${BOOTSTRAP_ARGS[@]}" --confirm)"
USER_ID="$(APPLY_JSON="$APPLY_JSON" "$PYTHON" -c 'import json,os; print(json.loads(os.environ["APPLY_JSON"])["user_id"])')"
TENANT_ID="$(APPLY_JSON="$APPLY_JSON" "$PYTHON" -c 'import json,os; print(json.loads(os.environ["APPLY_JSON"])["tenant_id"])')"

if [[ "$ASSIGN_PLATFORM_ADMIN" == true ]]; then
  if [[ "$ASSUME_YES" != true ]]; then
    read -r -p "Grant platform administrator separately? Type PLATFORM ADMIN to continue: " PLATFORM_CONFIRM
    if [[ "$PLATFORM_CONFIRM" != "PLATFORM ADMIN" ]]; then
      printf '%s\n' "Platform administrator grant skipped."
      ASSIGN_PLATFORM_ADMIN=false
    fi
  fi
  if [[ "$ASSIGN_PLATFORM_ADMIN" == true ]]; then
    "$PYTHON" -m app.operations.auth_cli grant-platform-admin --provider "$PROVIDER" --subject "$SUBJECT" --granted-by-user-id "$USER_ID" --reason "$REASON" --confirm >/dev/null
  fi
fi

VERIFY_ARGS=(--provider "$PROVIDER" --subject "$SUBJECT" --tenant-id "$TENANT_ID")
if [[ "$ASSIGN_PLATFORM_ADMIN" == true ]]; then
  VERIFY_ARGS+=(--expect-platform-admin)
fi
VERIFY_JSON="$("$PYTHON" -m app.operations.auth_cli verify-bootstrap-access "${VERIFY_ARGS[@]}")"
ENVIRONMENT="$ENVIRONMENT" VERIFY_JSON="$VERIFY_JSON" "$PYTHON" -c '
import json,os
d=json.loads(os.environ["VERIFY_JSON"])
print("")
print("Administrator setup verified")
print("  environment: " + os.environ["ENVIRONMENT"])
print("  application user ID: " + d["user_id"])
print("  tenant: " + d["tenant_id"] + " (" + d["tenant_slug"] + ")")
print("  assigned roles: " + ", ".join(d["roles"]))
print("  platform admin: " + ("yes" if d["platform_admin"] else "no"))
print("Next step: logout and login again.")
'
