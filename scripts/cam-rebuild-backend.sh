#!/usr/bin/env bash

# Refuse to be sourced so strict shell options never leak into the caller's
# interactive SSH session.
if [[ "${BASH_SOURCE[0]}" != "$0" ]]; then
  printf 'ERROR: Do not source this script. Run it as: sudo %s\n' \
    "${BASH_SOURCE[0]}" >&2
  return 2
fi

set -Eeuo pipefail

SCRIPT_DIR="$(CDPATH= cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)"
CHECKOUT_ROOT="$(CDPATH= cd -- "$SCRIPT_DIR/.." && pwd)"

SOURCE_DIR="${CAM_SOURCE_DIR:-$CHECKOUT_ROOT}"
ENV_FILE="${CAM_PRODUCTION_ENV_FILE:-/etc/creative-asset-manager/production.env}"
APP_ROOT="${CAM_APP_ROOT:-/opt/creative-asset-manager}"

KEEP_RELEASES="${CAM_BACKEND_RELEASE_KEEP:-4}"
KEEP_LOGS="${CAM_BACKEND_DEPLOY_LOG_KEEP:-20}"

LOCK_FILE="${CAM_BACKEND_DEPLOY_LOCK_FILE:-/run/lock/creative-asset-manager-backend-deploy.lock}"

LOG_DIR="${CAM_BACKEND_LOG_DIR:-/var/log/creative-asset-manager}"

IMAGE_WORKER_HEALTH_PORT="${CAM_IMAGE_WORKER_HEALTH_PORT:-8081}"
VIDEO_WORKER_HEALTH_PORT="${CAM_VIDEO_WORKER_HEALTH_PORT:-8082}"

REF=""

ROLLBACK=false
NO_MIGRATE=false
NO_RESTART=false
ALLOW_DIRTY=false
CLEANUP_RELEASES=true

STAGE=""
TARGET=""
LOG_FILE=""

START_TS="$(date +%s)"
CURRENT_PROGRESS=0
CURRENT_STAGE="startup"


usage() {
  cat <<'USAGE'
Usage:
  sudo scripts/cam-rebuild-backend.sh [options]

Options:
  --commit SHA
      Deploy the checked-out commit SHA.

  --source-dir PATH
      Source checkout to deploy from.

  --no-migrate
      Skip Alembic migration.

  --no-restart
      Activate the new release without restarting API/workers.

  --rollback
      Activate the recorded previous release.

  --allow-dirty
      Allow deployment from a dirty Git checkout.

  --keep-releases N
      Keep approximately N backend releases.
      Default: 4

  --keep-logs N
      Keep the N newest backend deployment logs.
      Default: 20

  --no-cleanup
      Skip pre-deploy and post-deploy disk cleanup.

  -h, --help
      Show this help.

Creates and activates an immutable native systemd backend release.

Docker is never used for API or workers.
USAGE
}


die() {
  printf 'ERROR: %s\n' "$*" >&2
  exit 1
}


require() {
  command -v "$1" >/dev/null 2>&1 \
    || die "Required command is unavailable: $1"
}


elapsed() {
  local now
  local delta

  now="$(date +%s)"
  delta=$((now - START_TS))

  printf '%02d:%02d' \
    "$((delta / 60))" \
    "$((delta % 60))"
}


progress() {
  local percent="$1"

  shift

  CURRENT_PROGRESS="$percent"
  CURRENT_STAGE="$*"

  printf '[%s] [%3d%%] [%s] %s\n' \
    "$(date '+%H:%M:%S')" \
    "$percent" \
    "$(elapsed)" \
    "$*"
}


info() {
  printf '[%s] [INFO] [%s] %s\n' \
    "$(date '+%H:%M:%S')" \
    "$(elapsed)" \
    "$*"
}


warn() {
  printf '[%s] [WARN] [%s] %s\n' \
    "$(date '+%H:%M:%S')" \
    "$(elapsed)" \
    "$*" >&2
}


on_error() {
  local exit_code="$1"
  local line="$2"
  local command="$3"

  # Do not let an error inside the error reporter hide the original error.
  set +e

  printf '\n' >&2

  printf '[%s] [ERROR] Backend deployment failed\n' \
    "$(date '+%H:%M:%S')" >&2

  printf '  progress : %s%%\n' \
    "$CURRENT_PROGRESS" >&2

  printf '  stage    : %s\n' \
    "$CURRENT_STAGE" >&2

  printf '  line     : %s\n' \
    "$line" >&2

  printf '  command  : %s\n' \
    "$command" >&2

  printf '  exit     : %s\n' \
    "$exit_code" >&2

  printf '  elapsed  : %s\n' \
    "$(elapsed)" >&2

  if [[ -n "${LOG_FILE:-}" ]]; then
    printf '  log      : %s\n' \
      "$LOG_FILE" >&2
  fi

  return "$exit_code"
}


cleanup_stage() {
  local candidate="${STAGE:-}"
  local releases_root="${RELEASES:-}"

  [[ -n "$candidate" && -n "$releases_root" ]] \
    || return 0

  [[ -e "$candidate" ]] \
    || return 0

  case "$candidate" in
    "$releases_root"/.*.new.*)

      warn "Removing incomplete staging directory: $candidate"

      rm -rf -- "$candidate" \
        || true
      ;;
  esac
}


trap 'on_error "$?" "$LINENO" "$BASH_COMMAND"' ERR
trap cleanup_stage EXIT


#
# ==========================================================
# CLI ARGUMENTS
# ==========================================================
#

while (($#)); do

  case "$1" in

    --commit)

      [[ $# -ge 2 ]] \
        || die "--commit requires SHA"

      REF="$2"

      shift 2
      ;;

    --source-dir)

      [[ $# -ge 2 ]] \
        || die "--source-dir requires PATH"

      SOURCE_DIR="$2"

      shift 2
      ;;

    --no-migrate)

      NO_MIGRATE=true

      shift
      ;;

    --no-restart)

      NO_RESTART=true

      shift
      ;;

    --rollback)

      ROLLBACK=true

      shift
      ;;

    --allow-dirty)

      ALLOW_DIRTY=true

      shift
      ;;

    --keep-releases)

      [[ $# -ge 2 ]] \
        || die "--keep-releases requires N"

      KEEP_RELEASES="$2"

      shift 2
      ;;

    --no-cleanup)

      CLEANUP_RELEASES=false

      shift
      ;;

    --keep-logs)

      [[ $# -ge 2 ]] \
        || die "--keep-logs requires N"

      KEEP_LOGS="$2"

      shift 2
      ;;

    -h|--help)

      usage

      exit 0
      ;;

    *)

      die "Unknown option: $1"
      ;;

  esac

done


#
# ==========================================================
# BASIC VALIDATION
# ==========================================================
#

[[ "$KEEP_RELEASES" =~ ^[0-9]+$ ]] \
  || die "--keep-releases must be an integer."

((KEEP_RELEASES >= 2)) \
  || die "--keep-releases must be at least 2."

[[ "$KEEP_LOGS" =~ ^[0-9]+$ ]] \
  || die "--keep-logs must be an integer."

((KEEP_LOGS >= 1)) \
  || die "--keep-logs must be at least 1."


if [[ ! -d "$SOURCE_DIR/.git" ]]; then
  SOURCE_DIR="$CHECKOUT_ROOT"
fi


SOURCE_DIR="$(realpath -- "$SOURCE_DIR")"


for command in \
  git \
  rsync \
  python3 \
  systemctl \
  curl \
  realpath \
  readlink \
  flock \
  runuser \
  install \
  stat \
  sort \
  awk \
  du \
  tee \
  tr \
  wc \
  date \
  dirname \
  df \
  sleep
do

  require "$command"

done


[[ $EUID -eq 0 ]] \
  || die "Run as root to install releases and native systemd units."


[[ -f "$ENV_FILE" ]] \
  || die "Production environment file is missing."


[[ -f "$SOURCE_DIR/deploy/tools/production_env.py" ]] \
  || die "production_env.py is missing."


#
# ==========================================================
# PATHS
# ==========================================================
#

RELEASES="$APP_ROOT/releases"
CURRENT="$APP_ROOT/current"
PREVIOUS="$APP_ROOT/previous"


#
# ==========================================================
# LOGGING
# ==========================================================
#

install \
  -d \
  -o root \
  -g root \
  -m 0750 \
  "$LOG_DIR"


LOG_FILE="$LOG_DIR/backend-deploy-$(date '+%Y%m%d-%H%M%S').log"


touch "$LOG_FILE"

chmod 0640 "$LOG_FILE"


# Preserve the caller's terminal streams before mirroring deployment output.
# Keeping the terminal file descriptors explicit avoids losing progress/error
# output when the script is run through sudo, SSH, or another wrapper.
exec 3>&1 4>&2

# Mirror stdout + stderr to:
#
# 1. current terminal
# 2. deployment log
#
exec > >(tee -a "$LOG_FILE" >&3) 2> >(tee -a "$LOG_FILE" >&4)


progress 5 "Validating deployment environment"


info "Source directory: $SOURCE_DIR"
info "Application root: $APP_ROOT"
info "Production env: $ENV_FILE"
info "Release retention: $KEEP_RELEASES"
info "Deployment log retention: $KEEP_LOGS"
info "Deployment log: $LOG_FILE"


#
# ==========================================================
# DEPLOYMENT LOCK
# ==========================================================
#

progress 10 "Acquiring deployment lock"


install \
  -d \
  -o root \
  -g root \
  -m 0755 \
  "$(dirname -- "$LOCK_FILE")"


exec 9>"$LOCK_FILE"


flock -n 9 \
  || die "Another backend deployment is already running."


info "Deployment lock acquired"


#
# ==========================================================
# RELEASE ACTIVATION
# ==========================================================
#

activate_release() {
  local target="$1"
  local old=""

  [[ -d "$target/apps/api" ]] \
    || die "Release API directory is missing: $target"


  [[ -x "$target/apps/api/.venv/bin/python" ]] \
    || die "Release Python virtual environment is invalid: $target"


  if [[ -L "$CURRENT" ]]; then

    old="$(
      readlink -f -- "$CURRENT" \
        2>/dev/null \
        || true
    )"

  fi


  #
  # Preserve old CURRENT as PREVIOUS.
  #
  if [[ -n "$old" && "$old" != "$target" ]]; then

    ln -s \
      -- "$old" \
      "$PREVIOUS.new"


    mv \
      -Tf \
      -- "$PREVIOUS.new" \
      "$PREVIOUS"

  fi


  #
  # Atomic CURRENT switch.
  #
  ln -s \
    -- "$target" \
    "$CURRENT.new"


  mv \
    -Tf \
    -- "$CURRENT.new" \
    "$CURRENT"
}


#
# Convert any path inside:
#
# /opt/creative-asset-manager/releases/<SHA>/...
#
# to:
#
# /opt/creative-asset-manager/releases/<SHA>
#
release_from_path() {
  local path="${1:-}"
  local relative=""
  local sha=""

  [[ -n "$path" ]] \
    || return 1


  case "$path" in

    "$RELEASES"/*)

      relative="${path#"$RELEASES"/}"

      sha="${relative%%/*}"
      ;;

    *)

      return 1
      ;;

  esac


  [[ "$sha" =~ ^[0-9a-fA-F]{40}(-[A-Za-z0-9][A-Za-z0-9._-]*)?$ ]] \
    || return 1


  [[ -d "$RELEASES/$sha" ]] \
    || return 1


  [[ ! -L "$RELEASES/$sha" ]] \
    || return 1


  printf '%s\n' \
    "$RELEASES/$sha"
}


#
# ==========================================================
# RELEASE CLEANUP
# ==========================================================
#

cleanup_old_releases() {
  local current_target=""
  local previous_target=""

  local service=""
  local pid=""
  local cwd=""
  local running_release=""

  local release=""
  local base=""
  local stage=""

  local size_kb=0
  local removed=0
  local freed_kb=0

  local protected_count=0
  local optional_slots=0
  local optional_kept=0

  local -a ordered_releases=()

  declare -A protected=()


  if ! $CLEANUP_RELEASES; then

    info "Backend release cleanup skipped (--no-cleanup)"

    return 0

  fi


  [[ -d "$RELEASES" ]] \
    || return 0


  # A failed or interrupted deploy can leave a complete source tree and
  # virtualenv under .<release>.new.<pid>. The global deploy lock guarantees
  # that no other deployment can own one while this cleanup is running.
  for stage in "$RELEASES"/.*.new.*; do

    [[ -d "$stage" && ! -L "$stage" ]] \
      || continue


    base="${stage##*/}"


    [[ "$base" =~ ^\.[0-9a-fA-F]{40}(-[A-Za-z0-9][A-Za-z0-9._-]*)?\.new\.[0-9]+$ ]] \
      || continue


    [[ -z "${STAGE:-}" || "$stage" != "$STAGE" ]] \
      || continue


    size_kb="$(
      du -sk -- "$stage" \
        2>/dev/null \
        | awk '{print $1}' \
        || true
    )"


    [[ "$size_kb" =~ ^[0-9]+$ ]] \
      || size_kb=0


    info \
      "Removing incomplete backend stage: $base (~$((size_kb / 1024)) MiB)"


    rm -rf -- "$stage"


    freed_kb=$((freed_kb + size_kb))

  done


  protect_release() {
    local candidate="${1:-}"
    local normalized=""

    [[ -n "$candidate" ]] \
      || return 0


    normalized="$(
      release_from_path "$candidate" \
        2>/dev/null \
        || true
    )"


    [[ -n "$normalized" ]] \
      || return 0


    protected["$normalized"]=1
  }


  #
  # Protect CURRENT.
  #
  if [[ -L "$CURRENT" ]]; then

    current_target="$(
      readlink -f -- "$CURRENT" \
        2>/dev/null \
        || true
    )"

    protect_release "$current_target"

  fi


  #
  # Protect PREVIOUS rollback release.
  #
  if [[ -L "$PREVIOUS" ]]; then

    previous_target="$(
      readlink -f -- "$PREVIOUS" \
        2>/dev/null \
        || true
    )"

    protect_release "$previous_target"

  fi


  # If the requested release already exists, never remove it during the
  # pre-deploy cleanup pass.
  protect_release "${TARGET:-}"


  #
  # Protect releases that running services are still using.
  #
  # This is important with:
  #
  #   --no-restart
  #
  # because CURRENT may already point at a new release while an
  # existing process still runs from an older release.
  #
  for service in \
    creative-asset-manager-api.service \
    creative-asset-manager-image-worker.service \
    creative-asset-manager-video-worker.service
  do

    pid="$(
      systemctl show "$service" \
        -p MainPID \
        --value \
        2>/dev/null \
        || true
    )"


    [[ "$pid" =~ ^[1-9][0-9]*$ ]] \
      || continue


    [[ -e "/proc/$pid/cwd" ]] \
      || continue


    cwd="$(
      readlink -f -- "/proc/$pid/cwd" \
        2>/dev/null \
        || true
    )"


    running_release="$(
      release_from_path "$cwd" \
        2>/dev/null \
        || true
    )"


    if [[ -n "$running_release" ]]; then

      protected["$running_release"]=1

    fi

  done


  protected_count="${#protected[@]}"


  optional_slots=$((KEEP_RELEASES - protected_count))


  if ((optional_slots < 0)); then
    optional_slots=0
  fi


  #
  # Sort immutable releases newest -> oldest.
  #
  while IFS= read -r release; do

    [[ -n "$release" ]] \
      && ordered_releases+=("$release")

  done < <(

    for release in "$RELEASES"/*; do

      [[ -d "$release" ]] \
        || continue


      [[ ! -L "$release" ]] \
        || continue


      base="${release##*/}"


      [[ "$base" =~ ^[0-9a-fA-F]{40}(-[A-Za-z0-9][A-Za-z0-9._-]*)?$ ]] \
        || continue


      printf '%s\t%s\n' \
        "$(stat -c '%Y' -- "$release")" \
        "$release"

    done \
      | sort -nr \
      | awk -F '\t' '{print $2}'

  )


  #
  # Cleanup.
  #
  for release in "${ordered_releases[@]}"; do


    if [[ -n "${protected[$release]+x}" ]]; then

      info \
        "Keeping protected backend release: ${release##*/}"

      continue

    fi


    if ((optional_kept < optional_slots)); then

      optional_kept=$((optional_kept + 1))


      info \
        "Keeping recent backend release: ${release##*/}"

      continue

    fi


    size_kb="$(
      du -sk -- "$release" \
        2>/dev/null \
        | awk '{print $1}' \
        || true
    )"


    [[ "$size_kb" =~ ^[0-9]+$ ]] \
      || size_kb=0


    info \
      "Removing old backend release: ${release##*/} (~$((size_kb / 1024)) MiB)"


    rm -rf -- "$release"


    freed_kb=$((freed_kb + size_kb))


    removed=$((removed + 1))

  done


  info \
    "Backend release cleanup complete: removed=$removed freed~=$((freed_kb / 1024))MiB keep=$KEEP_RELEASES protected=$protected_count"
}


cleanup_deploy_logs() {
  local log=""
  local kept=0
  local removed=0
  local -a ordered_logs=()


  [[ -d "$LOG_DIR" ]] \
    || return 0


  while IFS= read -r log; do

    [[ -n "$log" ]] \
      && ordered_logs+=("$log")

  done < <(

    for log in "$LOG_DIR"/backend-deploy-*.log; do

      [[ -f "$log" && ! -L "$log" ]] \
        || continue


      printf '%s\t%s\n' \
        "$(stat -c '%Y' -- "$log")" \
        "$log"

    done \
      | sort -nr \
      | awk -F '\t' '{print $2}'

  )


  for log in "${ordered_logs[@]}"; do

    if [[ "$log" == "$LOG_FILE" ]] || ((kept < KEEP_LOGS)); then

      kept=$((kept + 1))

      continue

    fi


    info "Removing old backend deployment log: ${log##*/}"


    rm -f -- "$log"


    removed=$((removed + 1))

  done


  info \
    "Backend deployment log cleanup complete: removed=$removed keep=$KEEP_LOGS"
}


report_disk_usage() {
  local label="$1"
  local stats=""


  stats="$(
    df -Pk -- "$APP_ROOT" \
      | awk 'NR == 2 {printf "used=%dMiB available=%dMiB capacity=%s", $3 / 1024, $4 / 1024, $5}'
  )"


  info "$label disk usage: $stats"
}


cleanup_backend_disk() {
  local phase="$1"


  if ! $CLEANUP_RELEASES; then

    info "$phase disk cleanup skipped (--no-cleanup)"

    return 0

  fi


  report_disk_usage "$phase before cleanup"


  cleanup_old_releases
  cleanup_deploy_logs


  report_disk_usage "$phase after cleanup"
}


#
# ==========================================================
# HEALTH CHECKS
# ==========================================================
#

trusted_host() {

  python3 -c \
    "import sys; \
sys.path.insert(0, \"$SOURCE_DIR/deploy/tools\"); \
from production_env import parse_environment_file; \
print(parse_environment_file(__import__(\"pathlib\").Path(\"$ENV_FILE\"))[\"TRUSTED_HOSTS\"].split(\",\")[0].strip())"

}


wait_for_endpoint() {
  local label="$1"

  shift

  local attempt


  for attempt in {1..30}; do

    if curl \
      --fail \
      --silent \
      --show-error \
      --max-time 5 \
      "$@" \
      >/dev/null 2>&1
    then

      info "$label is healthy (attempt $attempt/30)"

      return 0

    fi


    info "Waiting for $label (attempt $attempt/30)"

    sleep 1

  done


  die "$label did not become healthy within 30 seconds."
}


verify_services() {
  local host
  local service
  local endpoint
  local port


  host="$(trusted_host)"


  #
  # systemd service state
  #
  for service in \
    creative-asset-manager-api.service \
    creative-asset-manager-image-worker.service \
    creative-asset-manager-video-worker.service
  do

    systemctl is-active \
      --quiet \
      "$service" \
      || die "$service is not active."

  done


  for service in \
    creative-asset-manager-inventory-v41-snapshot.timer \
    creative-asset-manager-inventory-v41-reconcile.timer
  do

    systemctl is-enabled \
      --quiet \
      "$service" \
      || die "$service is not enabled."


    systemctl is-active \
      --quiet \
      "$service" \
      || die "$service is not active."

  done


  #
  # Legacy all-role worker must not run.
  #
  if systemctl is-active \
    --quiet \
    creative-asset-manager-worker.service
  then

    die "Legacy all-role worker must be inactive."

  fi


  #
  # API health.
  #
  for endpoint in \
    live \
    ready \
    version
  do

    wait_for_endpoint \
      "API $endpoint" \
      -H "Host: $host" \
      "http://127.0.0.1:8000/$endpoint"

  done


  #
  # Image + Video worker health.
  #
  for port in \
    "$IMAGE_WORKER_HEALTH_PORT" \
    "$VIDEO_WORKER_HEALTH_PORT"
  do

    for endpoint in \
      live \
      health \
      ready
    do

      wait_for_endpoint \
        "worker $port $endpoint" \
        "http://127.0.0.1:$port/$endpoint"

    done

  done
}


restart_services() {

  info "Restarting API"

  systemctl restart \
    creative-asset-manager-api.service


  info "Restarting Image worker"

  systemctl restart \
    creative-asset-manager-image-worker.service


  info "Restarting Video worker"

  systemctl restart \
    creative-asset-manager-video-worker.service


  info "Waiting for backend health checks"

  verify_services


  info "All backend services are healthy"
}


#
# ==========================================================
# ROLLBACK
# ==========================================================
#

if $ROLLBACK; then

  progress 15 \
    "Preparing backend rollback"


  [[ -L "$PREVIOUS" ]] \
    || die "No previous backend release is recorded."


  ROLLBACK_TARGET="$(
    readlink -f -- "$PREVIOUS" \
      2>/dev/null \
      || true
  )"


  [[ -n "$ROLLBACK_TARGET" ]] \
    || die "Previous backend release target is invalid."


  info \
    "Rollback target: $ROLLBACK_TARGET"


  progress 25 \
    "Running pre-rollback disk cleanup"


  TARGET="$ROLLBACK_TARGET"


  cleanup_backend_disk "Pre-rollback"


  progress 50 \
    "Activating previous backend release"


  activate_release \
    "$ROLLBACK_TARGET"


  if ! $NO_RESTART; then

    progress 75 \
      "Restarting backend services after rollback"


    restart_services

  else

    info \
      "Service restart skipped (--no-restart)"

  fi


  progress 90 \
    "Cleaning obsolete backend releases"


  cleanup_backend_disk "Post-rollback"


  progress 100 \
    "Backend rollback completed successfully"


  printf '\n'


  info \
    "Rollback target: $ROLLBACK_TARGET"


  info \
    "Elapsed: $(elapsed)"


  info \
    "Log: $LOG_FILE"


  info \
    "PostgreSQL was not downgraded"


  exit 0

fi


#
# ==========================================================
# NORMAL DEPLOYMENT
# ==========================================================
#

progress 15 \
  "Resolving Git commit"


#
# Dirty checkout protection.
#
if ! $ALLOW_DIRTY; then

  [[ -z "$(
    git -C "$SOURCE_DIR" \
      status \
      --porcelain \
      --untracked-files=normal
  )" ]] \
    || die "Refusing deployment from a dirty checkout."

fi


#
# Resolve commit.
#
COMMIT="$(
  git -C "$SOURCE_DIR" \
    rev-parse \
    --verify \
    "${REF:-HEAD}^{commit}"
)"


[[ "$COMMIT" == "$(
  git -C "$SOURCE_DIR" \
    rev-parse \
    HEAD
)" ]] \
  || die "--commit must be checked out in --source-dir; do not mutate the source checkout automatically."


TARGET="$RELEASES/$COMMIT"


info \
  "Commit: $COMMIT"


info \
  "Target release: $TARGET"


install \
  -d \
  -o root \
  -g root \
  -m 0755 \
  "$RELEASES"


progress 20 \
  "Running pre-deploy disk cleanup"


cleanup_backend_disk "Pre-deploy"


#
# ==========================================================
# BUILD RELEASE
# ==========================================================
#

if [[ ! -e "$TARGET" ]]; then

  progress 25 \
    "Copying source into immutable release"


  STAGE="$RELEASES/.${COMMIT}.new.$$"


  install \
    -d \
    -o creative-assets \
    -g creative-assets \
    -m 0750 \
    "$STAGE"


  #
  # rsync has its own real transfer percentage.
  #
  rsync \
    -a \
    --delete \
    --info=progress2 \
    --chown=creative-assets:creative-assets \
    --exclude=.git \
    --exclude=.env \
    --exclude=.env.local \
    --exclude=node_modules \
    --exclude=.venv \
    --exclude=__pycache__ \
    --exclude=.pytest_cache \
    --exclude='*.pyc' \
    "$SOURCE_DIR/" \
    "$STAGE/"


  progress 35 \
    "Creating Python virtual environment"


  runuser \
    -u creative-assets \
    -- \
    python3 \
    -m venv \
    "$STAGE/apps/api/.venv"


  progress 50 \
    "Installing Python dependencies"


  runuser \
    -u creative-assets \
    -- \
    "$STAGE/apps/api/.venv/bin/python" \
    -m pip install \
    --disable-pip-version-check \
    --no-input \
    --no-cache-dir \
    --requirement "$STAGE/apps/api/requirements.txt"


  progress 60 \
    "Finalizing immutable backend release"


  printf '%s\n' \
    "$COMMIT" \
    > "$STAGE/.cam-release"


  mv \
    -T \
    "$STAGE" \
    "$TARGET"


  #
  # Disable EXIT cleanup for the completed release.
  #
  STAGE=""


  info \
    "New immutable release created"

else

  progress 60 \
    "Using existing immutable backend release"


  info \
    "Release already exists: $TARGET"

fi


PYTHON="$TARGET/apps/api/.venv/bin/python"


[[ -x "$PYTHON" ]] \
  || die "Native Python virtual environment is missing."


#
# ==========================================================
# PRODUCTION CONFIG
# ==========================================================
#

progress 65 \
  "Validating production configuration"


"$PYTHON" \
  "$TARGET/deploy/tools/production_env.py" \
  check \
  --env-file "$ENV_FILE" \
  --expected-owner-uid 0 \
  --api-root "$TARGET/apps/api" \
  >/dev/null


#
# ==========================================================
# ALEMBIC
# ==========================================================
#

progress 72 \
  "Checking Alembic migration state"


HEADS="$(
  "$PYTHON" \
    -m alembic \
    -c "$TARGET/apps/api/alembic.ini" \
    heads \
  | wc -l \
  | tr -d '[:space:]'
)"


[[ "$HEADS" == "1" ]] \
  || die "Exactly one Alembic head is required."


#
# ==========================================================
# DATABASE MIGRATION
# ==========================================================
#

if ! $NO_MIGRATE; then

  progress 78 \
    "Running database migrations"


  "$TARGET/deploy/tools/production_env.py" \
    run-quiet \
    --env-file "$ENV_FILE" \
    --expected-owner-uid 0 \
    -- \
    "$PYTHON" \
    -m alembic \
    -c "$TARGET/apps/api/alembic.ini" \
    upgrade head

else

  info \
    "Database migrations skipped (--no-migrate)"

fi


#
# ==========================================================
# SYSTEMD
# ==========================================================
#

progress 84 \
  "Installing native systemd units"


for unit in \
  creative-asset-manager-api.service \
  creative-asset-manager-image-worker.service \
  creative-asset-manager-video-worker.service \
  creative-asset-manager-inventory-v41-snapshot.service \
  creative-asset-manager-inventory-v41-snapshot.timer \
  creative-asset-manager-inventory-v41-reconcile.service \
  creative-asset-manager-inventory-v41-reconcile.timer
do

  install \
    -o root \
    -g root \
    -m 0644 \
    "$TARGET/deploy/systemd/$unit" \
    "/etc/systemd/system/$unit"

done


systemctl daemon-reload


#
# Legacy worker must stay disabled.
#
info \
  "Stopping/disabling legacy all-role worker"


systemctl stop \
  creative-asset-manager-worker.service \
  || true


systemctl disable \
  creative-asset-manager-worker.service \
  || true


info \
  "Stopping/disabling legacy long-running Inventory scheduler"


systemctl stop \
  creative-asset-manager-inventory-scheduler.service \
  || true


systemctl disable \
  creative-asset-manager-inventory-scheduler.service \
  || true


#
# Ensure native services are enabled.
#
info \
  "Enabling production native services"


systemctl enable \
  creative-asset-manager-api.service \
  creative-asset-manager-image-worker.service \
  creative-asset-manager-video-worker.service


info \
  "Enabling Inventory V4.1 one-shot timers"


systemctl reset-failed \
  creative-asset-manager-inventory-v41-snapshot.service \
  creative-asset-manager-inventory-v41-reconcile.service \
  || true


systemctl enable \
  --now \
  creative-asset-manager-inventory-v41-snapshot.timer \
  creative-asset-manager-inventory-v41-reconcile.timer


#
# ==========================================================
# ACTIVATE
# ==========================================================
#

progress 90 \
  "Activating backend release"


activate_release \
  "$TARGET"


#
# ==========================================================
# RESTART
# ==========================================================
#

if ! $NO_RESTART; then

  progress 94 \
    "Restarting and validating backend services"


  restart_services

else

  info \
    "Service restart skipped (--no-restart)"

fi


#
# ==========================================================
# RELEASE CLEANUP
# ==========================================================
#

progress 98 \
  "Cleaning obsolete backend releases"


cleanup_backend_disk "Post-deploy"


#
# ==========================================================
# COMPLETE
# ==========================================================
#

progress 100 \
  "Deployment completed successfully"


printf '\n'

printf '%s\n' \
  '============================================================'

printf '%s\n' \
  ' BACKEND DEPLOYMENT COMPLETE'

printf '%s\n' \
  '============================================================'

printf ' Release : %s\n' \
  "$COMMIT"

printf ' Current : %s\n' \
  "$(readlink -f -- "$CURRENT")"

printf ' Previous: %s\n' \
  "$(readlink -f -- "$PREVIOUS" 2>/dev/null || echo none)"

printf ' Elapsed : %s\n' \
  "$(elapsed)"

printf ' Log     : %s\n' \
  "$LOG_FILE"

printf '%s\n' \
  '============================================================'
