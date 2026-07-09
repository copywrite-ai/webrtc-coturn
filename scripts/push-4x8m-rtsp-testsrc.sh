#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PUSH_SCRIPT="${ROOT_DIR}/scripts/push-cbr-rtsp-testsrc.sh"

STREAMS=(
  fish_front
  fish_back
  fish_left
  fish_right
)

VIDEO_BITRATE="${VIDEO_BITRATE:-8M}"
MAXRATE="${MAXRATE:-${VIDEO_BITRATE}}"
BUFSIZE="${BUFSIZE:-16M}"
LOG_DIR="${LOG_DIR:-${ROOT_DIR}/.tmp/fish-4x8m-logs}"

mkdir -p "${LOG_DIR}"

pids=()

stop_all() {
  local pid
  for pid in "${pids[@]:-}"; do
    if kill -0 "${pid}" 2>/dev/null; then
      kill "${pid}" 2>/dev/null || true
    fi
  done
}

trap 'stop_all' INT TERM EXIT

echo "Starting 4x CBR RTSP test-pattern push"
echo "  bitrate per stream : ${VIDEO_BITRATE} (maxrate=${MAXRATE}, bufsize=${BUFSIZE})"
echo "  aggregate target   : 4 x ${VIDEO_BITRATE}"
echo "  logs               : ${LOG_DIR}"
echo

for stream in "${STREAMS[@]}"; do
  log_file="${LOG_DIR}/${stream}.log"
  echo "Starting ${stream} -> rtsp://127.0.0.1:8554/${stream}"
  STREAM_NAME="${stream}" \
    VIDEO_BITRATE="${VIDEO_BITRATE}" \
    MAXRATE="${MAXRATE}" \
    BUFSIZE="${BUFSIZE}" \
    PUBLIC_WHEP_URL="http://127.0.0.1:9001/${stream}/whep" \
    "${PUSH_SCRIPT}" >"${log_file}" 2>&1 &
  pids+=("$!")
done

echo
echo "WHEP URLs:"
for stream in "${STREAMS[@]}"; do
  echo "  http://127.0.0.1:9001/${stream}/whep"
done
echo
echo "Press Ctrl-C to stop all streams."

wait
