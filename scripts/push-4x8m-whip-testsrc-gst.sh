#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="${ROOT_DIR}/.env"

if [ -f "${ENV_FILE}" ]; then
  set -a
  # shellcheck disable=SC1090
  . "${ENV_FILE}"
  set +a
fi

if ! command -v gst-launch-1.0 >/dev/null 2>&1; then
  cat <<'EOF'
gst-launch-1.0 not found.

Install GStreamer with the WHIP plugin, then retry.
On macOS/Homebrew this is typically:
  brew install gstreamer gst-plugins-base gst-plugins-good gst-plugins-bad
EOF
  exit 1
fi

if ! gst-inspect-1.0 whipsink >/dev/null 2>&1; then
  cat <<'EOF'
GStreamer whipsink plugin not found.

Install gst-plugins-bad or a GStreamer build that contains whipsink.
EOF
  exit 1
fi

if [ -z "${WHIP_BASE_URL:-}" ]; then
  cat <<'EOF'
WHIP_BASE_URL is required.

Example:
  WHIP_BASE_URL=https://media.example.com/proxy ./scripts/push-4x8m-whip-testsrc-gst.sh

This publishes to:
  https://media.example.com/proxy/fish_front/whip
  https://media.example.com/proxy/fish_back/whip
  https://media.example.com/proxy/fish_left/whip
  https://media.example.com/proxy/fish_right/whip
EOF
  exit 1
fi

STREAMS=(
  fish_front
  fish_back
  fish_left
  fish_right
)

WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
FPS="${FPS:-30}"
BITRATE_KBPS="${BITRATE_KBPS:-8000}"
KEY_INT_MAX="${KEY_INT_MAX:-60}"
PATTERN="${PATTERN:-smpte}"
SPEED_PRESET="${SPEED_PRESET:-ultrafast}"
X264_OPTION_STRING="${X264_OPTION_STRING:-nal-hrd=cbr:force-cfr=1:filler=1}"
LOG_DIR="${LOG_DIR:-${ROOT_DIR}/.tmp/fish-4x8m-whip-logs}"

base_url="${WHIP_BASE_URL%/}"
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

echo "Starting 4x WHIP test-pattern push"
echo "  resolution        : ${WIDTH}x${HEIGHT}"
echo "  fps               : ${FPS}"
echo "  bitrate per stream: ${BITRATE_KBPS} kbps"
echo "  aggregate target  : 4 x ${BITRATE_KBPS} kbps"
echo "  pattern           : ${PATTERN}"
echo "  x264 preset       : ${SPEED_PRESET}"
echo "  x264 options      : ${X264_OPTION_STRING}"
echo "  logs              : ${LOG_DIR}"
echo

for stream in "${STREAMS[@]}"; do
  whip_url="${base_url}/${stream}/whip"
  log_file="${LOG_DIR}/${stream}.log"
  echo "Starting ${stream} -> ${whip_url}"
  gst-launch-1.0 -e \
    videotestsrc is-live=true pattern="${PATTERN}" ! \
    "video/x-raw,width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1" ! \
    x264enc bitrate="${BITRATE_KBPS}" speed-preset="${SPEED_PRESET}" tune=zerolatency \
      key-int-max="${KEY_INT_MAX}" bframes=0 option-string="${X264_OPTION_STRING}" ! \
    h264parse config-interval=-1 ! \
    whipsink whip-endpoint="${whip_url}" >"${log_file}" 2>&1 &
  pids+=("$!")
done

echo
echo "Published WHIP URLs:"
for stream in "${STREAMS[@]}"; do
  echo "  ${base_url}/${stream}/whip"
done
echo
echo "Press Ctrl-C to stop all streams."

wait
