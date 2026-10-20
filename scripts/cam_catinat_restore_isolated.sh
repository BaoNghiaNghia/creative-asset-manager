#!/usr/bin/env bash
# Restore a known SHA256+MD5 verified Catinat backup to an isolated PG16 cluster.
# This script NEVER connects to PostgreSQL production port 5432 or production.env.
# Must be explicitly invoked by an authorized human root SSH operator.
set -euo pipefail
umask 077
# Never inherit an operator shell's production PG target or password.
unset PGHOST PGHOSTADDR PGPORT PGUSER PGDATABASE PGPASSWORD PGSERVICE PGSERVICEFILE PGSSLMODE || true

fail() { printf 'RESTORE_REHEARSAL=BLOCKED: %s\n' "$*" >&2; exit 2; }
usage() {
  printf 'Usage: bash scripts/cam_catinat_restore_isolated.sh --dump /root/.../cam-db-*.dump --sha256 HEX64 --md5 HEX32 [--resume-existing-cluster]\n'
}
[[ "${1:-}" != "--help" ]] || { usage; exit 0; }
[[ "$EUID" -eq 0 ]] || fail "Run as the authorized root SSH operator."
[[ ("$#" -eq 6 || ("$#" -eq 7 && "$7" == "--resume-existing-cluster")) && "$1" == "--dump" && "$3" == "--sha256" && "$5" == "--md5" ]] || { usage; exit 2; }
MODE="new"
if [[ "$#" -eq 7 ]]; then MODE="resume"; fi
DUMP="$2"
SHA="$4"
MD5="$6"
WORK="/root/catinat-db-rehearsal-20261020"
CLUSTER="catinat_rehearsal"
PORT=55432
DB="cam_restore_rehearsal_oct2026"
ROLE="cam_rehearsal_user"
PASSFILE="$WORK/rehearsal.pgpass"
EVIDENCE="$WORK/restore-proof.json"
SCRIPT_DIR="$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd -P)"
RESTORER="$SCRIPT_DIR/cam_db_restore_rehearsal.py"

# Fail before touching PostgreSQL for uncertain or unsafe inputs.
[[ -d "$WORK" && ! -L "$WORK" ]] || fail "Expected private backup directory missing."
[[ "$(stat -c '%u' "$WORK")" == "0" && "$(stat -c '%a' "$WORK")" == "700" ]] || fail "Backup directory must be owned by root, mode 0700."
[[ -f "$DUMP" && ! -L "$DUMP" ]] || fail "Regular nonsymlink backup file required."
[[ "$(dirname -- "$(realpath -e -- "$DUMP")")" == "$WORK" ]] || fail "Backup must reside directly inside the expected private directory."
[[ "$(stat -c '%a' "$DUMP")" == "600" && "$(stat -c '%u' "$DUMP")" == "0" ]] || fail "Dump must be root-owned mode 0600."
[[ "$DUMP" =~ /cam-db-[0-9]{8}-[0-9]{6}[+-][0-9]{4}\.dump$ ]] || fail "Unexpected backup filename."
[[ "$SHA" =~ ^[0-9a-f]{64}$ && "$MD5" =~ ^[0-9a-f]{32}$ ]] || fail "Missing/invalid independent backup checksums."
[[ "$(sha256sum -- "$DUMP" | cut -d' ' -f1)" == "$SHA" ]] || fail "SHA256 mismatch."
[[ "$(md5sum -- "$DUMP" | cut -d' ' -f1)" == "$MD5" ]] || fail "MD5 mismatch."
[[ -f "$RESTORER" ]] || fail "Guarded restore verifier not found."
[[ ! -e "$EVIDENCE" && ! -e "$PASSFILE" ]] || fail "A prior rehearsal exists. Never overwrite existing credentials or evidence."

FREE_BYTES="$(df -PB1 "$WORK" | awk 'NR==2 { print $4 }')"
[[ "$FREE_BYTES" =~ ^[0-9]+$ && "$FREE_BYTES" -ge 8589934592 ]] || fail "At least 8 GiB of free disk needed before creating isolated cluster."
[[ "$(stat -c '%s' "$DUMP")" -gt 0 ]] || fail "Empty backup."
if [[ "$MODE" == "new" ]]; then
  [[ ! -e /etc/postgresql/16/"$CLUSTER" ]] || fail "Named isolated cluster already exists; use --resume-existing-cluster only after verifying an interrupted initial creation."
  ! pg_lsclusters --no-header | awk '$1=="16" && $2=="catinat_rehearsal" {found=1} END{exit !found}' || fail "Isolated cluster already registered."
else
  [[ -d /etc/postgresql/16/"$CLUSTER" ]] || fail "Existing rehearsal config not found."
  pg_lsclusters --no-header | awk '$1=="16" && $2=="catinat_rehearsal" && $3=="55432" && $4=="down" && $5=="postgres" && $6=="/var/lib/postgresql/16/catinat_rehearsal" {n++} END{exit(n==1?0:1)}' || fail "Resume requires exactly the failed, stopped PG16 rehearsal cluster on port 55432."
  [[ -d /var/lib/postgresql/16/"$CLUSTER" && ! -L /var/lib/postgresql/16/"$CLUSTER" ]] || fail "Rehearsal cluster data directory is not regular."
fi
! ss -lnt | grep -Eq '(:|\])55432[[:space:]]' || fail "Isolated port already in use."
pg_lsclusters --no-header | awk '$1=="16" && $2=="main" && $3=="5432" && $4=="online" {found=1} END{exit !found}' || fail "Production cluster identity unexpected; stop and investigate."
for exe in pg_createcluster pg_conftool pg_ctlcluster runuser psql pg_restore openssl; do
  command -v "$exe" >/dev/null || fail "Required PostgreSQL admin tool unavailable: $exe"
done

printf 'RESTORE_PREFLIGHT=PASS; target=%s at 127.0.0.1:%s, mode=%s (production remains at 5432)\n' "$DB" "$PORT" "$MODE"
# PG16 rehearsal is an entirely independent cluster, with manual startup only.
if [[ "$MODE" == "new" ]]; then
  pg_createcluster -p "$PORT" --start-conf manual 16 "$CLUSTER" -- --auth-host=scram-sha-256 --auth-local=peer
fi
# pg_conftool does NOT safely encode PostgreSQL listen_addresses:
# unquoted IP yields syntax error; quoted input becomes triple-quoted text.
# Strictly edit only /etc/postgresql/16/catinat_rehearsal config in a
# dedicated, audited helper; local trust is also hardened to peer.
pg_conftool 16 "$CLUSTER" set shared_buffers 128MB
python3 "$SCRIPT_DIR/cam_catinat_fix_rehearsal_conf.py"
pg_ctlcluster 16 "$CLUSTER" start
pg_lsclusters --no-header | awk '$1=="16" && $2=="catinat_rehearsal" && $3=="55432" && $4=="online" && $5=="postgres" {found=1} END{exit !found}' || fail "Isolated cluster did not start with expected identity."
ss -lnt | grep -Eq '127\.0\.0\.1:55432[[:space:]]' || fail "Isolated cluster not listening on loopback."
# Resume is valid only before the earlier run could create any user role or
# restore target. Refuse to touch a partially initialized database.
for sql in "SELECT COUNT(*) FROM pg_roles WHERE rolname='$ROLE'" "SELECT COUNT(*) FROM pg_database WHERE datname='$DB'"; do
  result="$(runuser -u postgres -- psql -X -w -h /var/run/postgresql -p "$PORT" -U postgres -d postgres -A -t -v ON_ERROR_STOP=1 -c "$sql")"
  [[ "$result" == "0" ]] || fail "Target already initialized; refuse to overwrite existing rehearsal state."
done

# The isolated DB is owned by a *non-superuser*. Secret never enters argv.
PASS="$(openssl rand -hex 24)"
printf "CREATE ROLE %s WITH LOGIN NOSUPERUSER NOCREATEDB NOCREATEROLE NOREPLICATION PASSWORD '%s';\n" "$ROLE" "$PASS" |
  runuser -u postgres -- psql -X -q -v ON_ERROR_STOP=1 -h /var/run/postgresql -p "$PORT" -U postgres -d postgres -f - >/dev/null
runuser -u postgres -- createdb -h /var/run/postgresql -p "$PORT" -U postgres -O "$ROLE" "$DB"
printf '127.0.0.1:%s:%s:%s:%s\n' "$PORT" "$DB" "$ROLE" "$PASS" > "$PASSFILE"
chmod 600 "$PASSFILE"
unset PASS

# The existing verifier checks zero objects + non-superuser + exact localhost
# identity, checks both hashes independently, restores in one transaction and
# emits a private evidence file with a positive user table count.
PGPASSFILE="$PASSFILE" python3 "$RESTORER" \
  --dump "$DUMP" --sha256 "$SHA" --md5 "$MD5" \
  --database "$DB" --db-user "$ROLE" --db-port "$PORT" \
  --evidence "$EVIDENCE" --ack-isolated-target

DBREV="$(PGPASSFILE="$PASSFILE" psql -X -w -h 127.0.0.1 -p "$PORT" -U "$ROLE" -d "$DB" -A -t -v ON_ERROR_STOP=1 -c 'select version_num from alembic_version' | tr -d '\r\n')"
[[ "$DBREV" == "0168_branding_dna_outline_versions" ]] || fail "Restored schema Alembic revision does not match expected 0168."
printf 'RESTORED_ALEMBIC=%s\nRESTORE_SOURCE_REVISION=PASS\n' "$DBREV"
printf 'NEXT_GATE=Run Alembic 0168->0172 only on isolated DB port %s using dedicated role; do not touch production.\n' "$PORT"
