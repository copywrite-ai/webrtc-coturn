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

Use scripts/push-10m-rtsp.sh instead, or install an ffmpeg build with:
  - a "whip" muxer in `ffmpeg -muxers`

This repository currently recommends the RTSP ingest path for stability.
EOF
  exit 1
fi

CAMERA_INDEX="${CAMERA_INDEX:-0}"
AUDIO_INDEX="${AUDIO_INDEX:-0}"
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
AUDIO_CODEC="${AUDIO_CODEC:-libopus}"
AUDIO_BITRATE="${AUDIO_BITRATE:-128k}"
WHIP_URL="${WHIP_URL:-http://127.0.0.1:8889/online/whip}"
PUBLIC_WHEP_URL="${PUBLIC_WHEP_URL:-${DEFAULT_WHEP_URL:-}}"

echo "Starting FFmpeg WHIP push"
echo "  source        : avfoundation ${CAMERA_INDEX}:${AUDIO_INDEX}"
echo "  resolution    : ${WIDTH}x${HEIGHT}"
echo "  fps           : ${FPS}"
echo "  video codec   : ${VIDEO_CODEC}"
echo "  bitrate       : ${VIDEO_BITRATE} (maxrate=${MAXRATE}, bufsize=${BUFSIZE})"
echo "  audio codec   : ${AUDIO_CODEC} (${AUDIO_BITRATE})"
echo "  target        : ${WHIP_URL}"
echo
if [ -n "${PUBLIC_WHEP_URL}" ]; then
  echo "Matching web playback URL: ${PUBLIC_WHEP_URL}"
  echo
fi

exec ffmpeg -re \
  -f avfoundation -framerate "${FPS}" -video_size "${WIDTH}x${HEIGHT}" -i "${CAMERA_INDEX}:${AUDIO_INDEX}" \
  -c:v "${VIDEO_CODEC}" \
  -pix_fmt "${PIX_FMT}" \
  -profile:v "${PROFILE}" \
  -preset "${PRESET}" \
  -tune zerolatency \
  -g "${GOP}" \
  -keyint_min "${GOP}" \
  -sc_threshold 0 \
  -bf 0 \
  -b:v "${VIDEO_BITRATE}" \
  -maxrate "${MAXRATE}" \
  -bufsize "${BUFSIZE}" \
  -c:a "${AUDIO_CODEC}" -ar 48000 -ac 2 -b:a "${AUDIO_BITRATE}" \
  -f whip \
  "${WHIP_URL}"
