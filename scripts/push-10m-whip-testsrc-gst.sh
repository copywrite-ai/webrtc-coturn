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
  echo "gst-launch-1.0 not found"
  exit 1
fi

WIDTH="${WIDTH:-1920}"
HEIGHT="${HEIGHT:-1080}"
FPS="${FPS:-30}"
BITRATE_KBPS="${BITRATE_KBPS:-10000}"
KEY_INT_MAX="${KEY_INT_MAX:-60}"
WHIP_URL="${WHIP_URL:-http://127.0.0.1:9001/online/whip}"
PUBLIC_WHEP_URL="${PUBLIC_WHEP_URL:-${DEFAULT_WHEP_URL:-http://127.0.0.1:9001/online/whep}}"
PATTERN="${PATTERN:-ball}"

echo "Starting GStreamer WHIP 10M test-pattern push"
echo "  source        : videotestsrc pattern=${PATTERN}"
echo "  resolution    : ${WIDTH}x${HEIGHT}"
echo "  fps           : ${FPS}"
echo "  bitrate       : ${BITRATE_KBPS} kbps"
echo "  target        : ${WHIP_URL}"
echo
echo "Matching web playback URL: ${PUBLIC_WHEP_URL}"
echo

exec gst-launch-1.0 -e \
  videotestsrc is-live=true pattern="${PATTERN}" ! \
  video/x-raw,width="${WIDTH}",height="${HEIGHT}",framerate="${FPS}/1" ! \
  x264enc bitrate="${BITRATE_KBPS}" speed-preset=veryfast tune=zerolatency key-int-max="${KEY_INT_MAX}" bframes=0 ! \
  h264parse config-interval=-1 ! \
  whipsink whip-endpoint="${WHIP_URL}"
