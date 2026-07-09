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

if ! ffmpeg -hide_banner -muxers 2>/dev/null | grep -q ' whip$'; then
  cat <<'EOF'
Current ffmpeg build does not include the WHIP muxer.

Use a build where `ffmpeg -muxers` contains `whip`,
or use scripts/push-10m-whip-testsrc-gst.sh if GStreamer is available.
EOF
  exit 1
fi

FPS="${FPS:-30}"
WIDTH="${WIDTH:-1920}"
HEIGHT="${HEIGHT:-1080}"
VIDEO_CODEC="${VIDEO_CODEC:-libx264}"
PRESET="${PRESET:-veryfast}"
PROFILE="${PROFILE:-baseline}"
GOP="${GOP:-60}"
VIDEO_BITRATE="${VIDEO_BITRATE:-10M}"
MAXRATE="${MAXRATE:-10M}"
BUFSIZE="${BUFSIZE:-20M}"
PIX_FMT="${PIX_FMT:-yuv420p}"
WHIP_URL="${WHIP_URL:-http://127.0.0.1:9001/online/whip}"
PUBLIC_WHEP_URL="${PUBLIC_WHEP_URL:-${DEFAULT_WHEP_URL:-http://127.0.0.1:9001/online/whep}}"
TEST_PATTERN="${TEST_PATTERN:-testsrc2}"
NOISE_FILTER="${NOISE_FILTER:-noise=alls=20:allf=t+u}"

echo "Starting FFmpeg WHIP 10M test-pattern push"
echo "  source        : ${TEST_PATTERN} + ${NOISE_FILTER}"
echo "  resolution    : ${WIDTH}x${HEIGHT}"
echo "  fps           : ${FPS}"
echo "  video codec   : ${VIDEO_CODEC}"
echo "  bitrate       : ${VIDEO_BITRATE} (maxrate=${MAXRATE}, bufsize=${BUFSIZE})"
echo "  target        : ${WHIP_URL}"
echo
echo "Matching web playback URL: ${PUBLIC_WHEP_URL}"
echo

exec ffmpeg -re \
  -f lavfi -i "${TEST_PATTERN}=size=${WIDTH}x${HEIGHT}:rate=${FPS}" \
  -vf "${NOISE_FILTER}" \
  -an \
  -c:v "${VIDEO_CODEC}" \
  -pix_fmt "${PIX_FMT}" \
  -profile:v "${PROFILE}" \
  -preset "${PRESET}" \
  -tune zerolatency \
  -g "${GOP}" \
  -keyint_min "${GOP}" \
  -sc_threshold 0 \
  -bf 0 \
  -r "${FPS}" \
  -b:v "${VIDEO_BITRATE}" \
  -maxrate "${MAXRATE}" \
  -bufsize "${BUFSIZE}" \
  -f whip \
  "${WHIP_URL}"
