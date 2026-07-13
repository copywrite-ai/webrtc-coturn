#!/bin/sh
set -eu

SOCKET="${TS_SOCKET:-/tmp/tailscaled.sock}"
STATE_DIR="${TAILSCALE_STATE_DIR:-/var/lib/tailscale}"
STATE_FILE="${TAILSCALE_STATE_FILE:-${STATE_DIR}/tailscaled.state}"
HOSTNAME="${TAILSCALE_HOSTNAME:-derp-latency-exporter}"
EXTRA_ARGS="${TAILSCALE_UP_EXTRA_ARGS:-}"

mkdir -p "${STATE_DIR}" "$(dirname "${SOCKET}")"

tailscaled \
  --tun=userspace-networking \
  --socks5-server=localhost:1055 \
  --state="${STATE_FILE}" \
  --socket="${SOCKET}" &

TAILSCALED_PID="$!"

cleanup() {
  kill "${TAILSCALED_PID}" 2>/dev/null || true
}
trap cleanup INT TERM EXIT

for _ in $(seq 1 80); do
  if tailscale --socket="${SOCKET}" status >/dev/null 2>&1; then
    break
  fi
  sleep 0.25
done

if [ -n "${TAILSCALE_AUTHKEY:-}" ]; then
  # shellcheck disable=SC2086
  tailscale --socket="${SOCKET}" up \
    --authkey="${TAILSCALE_AUTHKEY}" \
    --hostname="${HOSTNAME}" \
    --accept-dns=false \
    ${EXTRA_ARGS}
else
  echo "TAILSCALE_AUTHKEY is not set; reusing existing state if already authenticated." >&2
fi

export TS_SOCKET="${SOCKET}"
exec node /app/derp-latency-exporter.mjs
