#!/usr/bin/env bash
# Install *only* pinned, isolated developer/research dependencies in workspace.
# Never executes "agent-reach install", "--system", cookie import, or browser login.
set -Eeuo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
TOOLS="$ROOT/integrations/agent-reach"
SRC="$TOOLS/upstream"
EXPECTED="64123aa9c0c20c4fb39b06212f51fac888077ab0"

if [[ ! -d "$SRC/.git" ]]; then
  git clone --no-checkout --depth 1 https://github.com/404QAQ/Agent-Reach.git "$SRC"
  # HEAD may move after this adapter is published. Fetch the pinned revision
  # rather than assuming the remote default branch is still at EXPECTED.
  git -C "$SRC" fetch --depth 1 origin "$EXPECTED"
  git -C "$SRC" checkout --detach "$EXPECTED"
fi
REVISION="$(git -C "$SRC" rev-parse HEAD)"
if [[ "$REVISION" != "$EXPECTED" ]]; then
  echo "Agent-Reach upstream commit drift; refusing install: $REVISION" >&2
  exit 2
fi
if [[ -n "$(git -C "$SRC" status --porcelain)" ]]; then
  echo "Pinned upstream source has local modifications; refusing to use it" >&2
  exit 2
fi
python3 -m venv "$TOOLS/.venv"
"$TOOLS/.venv/bin/pip" install --disable-pip-version-check --no-cache-dir -r "$TOOLS/requirements.lock"
mkdir -p -m 0700 "$TOOLS/runtime-home"
cd "$ROOT"
npm ci --prefix integrations/impeccable --ignore-scripts --no-audit --no-fund
node apps/client/scripts/ui-qa-impeccable.mjs --mode advisory
"$TOOLS/.venv/bin/python" "$TOOLS/research_intelligence.py" doctor
echo "Pinned tooling ready. No production jobs, login sessions, or system packages changed."
