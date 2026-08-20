#!/usr/bin/env bash
set -Eeuo pipefail
umask 027

[[ "${EUID}" -eq 0 ]] || { echo "Run as root: sudo $0"; exit 1; }

SOURCE_DIR="${CAM_SOURCE_DIR:-/srv/creative-asset-manager-source}"
ENV_FILE="/etc/creative-asset-manager/production.env"

[[ -d "${SOURCE_DIR}/.git" ]] || { echo "Missing source checkout: ${SOURCE_DIR}"; exit 2; }
[[ -z "$(git -C "${SOURCE_DIR}" status --porcelain)" ]] || {
  echo "ERROR: source checkout is dirty. Refusing auto stash/reset."; exit 3;
}

echo "[1/7] Update main"
git -C "${SOURCE_DIR}" fetch origin main
git -C "${SOURCE_DIR}" checkout main
git -C "${SOURCE_DIR}" merge --ff-only origin/main
HEAD_SHA="$(git -C "${SOURCE_DIR}" rev-parse HEAD)"
RELEASE_ID="${HEAD_SHA:0:12}"

echo "[2/7] Update BUILD_COMMIT"
python3 - "${ENV_FILE}" "${HEAD_SHA}" <<'PY'
from pathlib import Path
import sys
path = Path(sys.argv[1])
sha = sys.argv[2]
lines = path.read_text().splitlines()
found = False
out = []
for line in lines:
    if line.startswith("BUILD_COMMIT="):
        out.append("BUILD_COMMIT=" + sha)
        found = True
    else:
        out.append(line)
if not found:
    out.append("BUILD_COMMIT=" + sha)
path.write_text("\n".join(out) + "\n")
PY
chown root:creative-assets "${ENV_FILE}"
chmod 0640 "${ENV_FILE}"

cd "${SOURCE_DIR}"

echo "[3/7] Install immutable release ${RELEASE_ID}"
if [[ ! -d "/opt/creative-asset-manager/releases/${RELEASE_ID}" ]]; then
  deploy/bin/cam-deploy install-release "${SOURCE_DIR}" "${RELEASE_ID}"
else
  echo "Release already installed; reusing ${RELEASE_ID}"
fi

echo "[4/7] Validate"
deploy/bin/cam-deploy check-config "${RELEASE_ID}"
deploy/bin/cam-deploy verify-alembic-head "${RELEASE_ID}"

echo "[5/7] Forward DB operations"
deploy/bin/cam-deploy migrate "${RELEASE_ID}"
deploy/bin/cam-deploy seed "${RELEASE_ID}"

echo "[6/7] Switch + restart"
deploy/bin/cam-deploy switch-release "${RELEASE_ID}"
deploy/bin/cam-deploy restart-api
deploy/bin/cam-deploy restart-worker

echo "[7/7] Verify"
deploy/bin/cam-deploy verify-api
deploy/bin/cam-deploy verify-worker
deploy/bin/cam-deploy diagnostics

echo "UPDATE_DEPLOY=PASS"
echo "MAIN_HEAD=${HEAD_SHA}"
echo "RELEASE_ID=${RELEASE_ID}"
