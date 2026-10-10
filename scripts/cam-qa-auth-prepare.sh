#!/usr/bin/env bash
# Prepare an isolated, minimal Playwright CLI for the cam-qa Chrome profile.
# Does not launch a browser or read cookies. Root is needed only for install.
set -Eeuo pipefail
umask 077
[[ $EUID == 0 ]] || { echo "Run with sudo/root" >&2; exit 1; }
id cam-qa >/dev/null 2>&1 || { echo "cam-qa account missing" >&2; exit 1; }
source_root="$(cd -- "$(dirname -- "$0")/.." && pwd)"
target="/home/cam-qa/cam-browser-qa/apps/client"
source_dir="$source_root/apps/client/scripts"
for filename in production-ui-auth-capture.mjs production-ui-auth-capture-chrome.mjs; do
  [[ -s "$source_dir/$filename" ]] || { echo "Missing source script: $filename" >&2; exit 1; }
done
command -v node >/dev/null && command -v npm >/dev/null || { echo "Node and npm are required" >&2; exit 1; }

# install -d with a nested path leaves newly created intermediate directories
# owned by root. Explicitly repair only our private QA subtree so cam-qa can
# traverse it and create node_modules (and npm's lockfile) as a non-root user.
for qa_dir in \
  /home/cam-qa/cam-browser-qa \
  /home/cam-qa/cam-browser-qa/apps \
  "$target" \
  "$target/scripts"; do
  [[ ! -L "$qa_dir" ]] || { echo "Refusing symlinked QA directory: $qa_dir" >&2; exit 1; }
  install -d -m 0700 -o cam-qa -g cam-qa "$qa_dir"
done
# Fail early with a clear permission diagnostic instead of an npm EACCES.
runuser -u cam-qa -- test -w "$target" ||
  { echo "cam-qa cannot write to the QA app directory: $target" >&2; exit 1; }
for filename in production-ui-auth-capture.mjs production-ui-auth-capture-chrome.mjs; do
  install -m 0600 -o cam-qa -g cam-qa "$source_dir/$filename" "$target/scripts/$filename"
done
# Minimal Playwright CDP client: no browser downloads, no Google login automation.
if [[ ! -s "$target/package.json" ]]; then
  printf '%s\n' '{"name":"cam-private-qa-auth","version":"1.0.0","private":true,"type":"module","dependencies":{"playwright":"1.56.1"}}' > "$target/package.json"
  chown cam-qa:cam-qa "$target/package.json"
fi
if [[ ! -d "$target/node_modules/playwright" ]]; then
  runuser -u cam-qa -- env HOME=/home/cam-qa npm install \
    --prefix "$target" --no-audit --no-fund --ignore-scripts --omit=dev
fi
runuser -u cam-qa -- env HOME=/home/cam-qa node --check "$target/scripts/production-ui-auth-capture-chrome.mjs"
printf 'QA capture tooling is ready in the cam-qa private home.\n'
printf 'As cam-qa, close normal Chrome after Google login and run:\n'
printf '  export DISPLAY=:90 XAUTHORITY="$HOME/.Xauthority"\n'
printf '  node "$HOME/cam-browser-qa/apps/client/scripts/production-ui-auth-capture-chrome.mjs" \\\n'
printf '    --url https://creative-assets.ddns.net \\\n'
printf '    --profile-dir "$HOME/.config/cam-qa-chrome-profile" \\\n'
printf '    --chrome google-chrome-stable \\\n'
printf '    --output "$HOME/cam-production-ui-session.json"\n'
printf 'Do not paste the session JSON or cookies into chat/logs.\n'
