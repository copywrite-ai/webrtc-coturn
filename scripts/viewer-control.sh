#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
if [[ -f "${ROOT_DIR}/.env" ]]; then
  set -a
  # shellcheck disable=SC1091
  source "${ROOT_DIR}/.env"
  set +a
fi

API_ORIGIN="${VIEWER_CONTROL_API_ORIGIN:-http://127.0.0.1:${PORT:-9001}}"
TOKEN="${VIEWER_CONTROL_TOKEN:-}"
PEER_ID="${VIEWER_CONTROL_PEER_ID:-}"

if [[ -z "${TOKEN}" ]]; then
  echo "VIEWER_CONTROL_TOKEN is required" >&2
  exit 2
fi

request() {
  curl -fsS \
    -H "Authorization: Bearer ${TOKEN}" \
    -H 'Content-Type: application/json' \
    "$@"
}

command_payload() {
  local type="$1"
  local whep_url="${2:-}"
  local ortm_profile="${3:-default}"
  jq -cn \
    --arg type "${type}" \
    --arg whepUrl "${whep_url}" \
    --arg ortmProfile "${ortm_profile}" \
    --arg peerId "${PEER_ID}" \
    '{
      command: ({type: $type, slot: 0}
        + (if $type == "configure-and-play" then {whepUrl: $whepUrl, ortmProfile: $ortmProfile} else {} end)),
      peerId: $peerId
    }'
}

case "${1:-}" in
  clients|status)
    request "${API_ORIGIN}/api/viewer-control/clients"
    ;;
  play)
    whep_url="${2:-https://peng-mbp14.li-adder.ts.net/fish_front/whep}"
    profile="${3:-720p-minimal}"
    request \
      -X POST \
      --data "$(command_payload configure-and-play "${whep_url}" "${profile}")" \
      "${API_ORIGIN}/api/viewer-control/command"
    ;;
  reconnect|stop)
    request \
      -X POST \
      --data "$(command_payload "$1")" \
      "${API_ORIGIN}/api/viewer-control/command"
    ;;
  *)
    cat >&2 <<'EOF'
Usage:
  scripts/viewer-control.sh clients
  scripts/viewer-control.sh play [whep-url] [ortm-profile]
  scripts/viewer-control.sh reconnect
  scripts/viewer-control.sh stop

Set VIEWER_CONTROL_PEER_ID to target one registered browser. Without it,
commands are delivered to every opt-in viewer.
EOF
    exit 2
    ;;
esac
