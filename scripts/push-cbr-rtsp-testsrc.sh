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

FFMPEG_BIN="${FFMPEG_BIN:-ffmpeg}"
STREAM_NAME="${STREAM_NAME:-online}"
RTSP_URL="${RTSP_URL:-rtsp://127.0.0.1:8554/${STREAM_NAME}}"
WIDTH="${WIDTH:-1920}"
HEIGHT="${HEIGHT:-1080}"
FPS="${FPS:-30}"
GOP="${GOP:-60}"
VIDEO_BITRATE="${VIDEO_BITRATE:-10M}"
MAXRATE="${MAXRATE:-${VIDEO_BITRATE}}"
BUFSIZE="${BUFSIZE:-20M}"
PRESET="${PRESET:-veryfast}"
PROFILE="${PROFILE:-baseline}"
NOISE_STRENGTH="${NOISE_STRENGTH:-20}"
PUBLIC_WHEP_URL="${PUBLIC_WHEP_URL:-${DEFAULT_WHEP_URL:-http://127.0.0.1:9001/online/whep}}"

echo "Starting CBR RTSP test-pattern push"
echo "  source        : testsrc2 + temporal noise"
echo "  resolution    : ${WIDTH}x${HEIGHT}"
echo "  fps           : ${FPS}"
echo "  bitrate       : ${VIDEO_BITRATE} (maxrate=${MAXRATE}, bufsize=${BUFSIZE})"
echo "  stream name   : ${STREAM_NAME}"
echo "  target        : ${RTSP_URL}"
echo
echo "Matching WHEP URL: ${PUBLIC_WHEP_URL}"
echo

exec "${FFMPEG_BIN}" -re \
  -f lavfi -i "testsrc2=size=${WIDTH}x${HEIGHT}:rate=${FPS}" \
  -vf "noise=alls=${NOISE_STRENGTH}:allf=t+u" \
  -an \
  -c:v libx264 \
  -pix_fmt yuv420p \
  -profile:v "${PROFILE}" \
  -preset "${PRESET}" \
  -tune zerolatency \
  -g "${GOP}" \
  -keyint_min "${GOP}" \
  -sc_threshold 0 \
  -bf 0 \
  -r "${FPS}" \
  -fps_mode cfr \
  -b:v "${VIDEO_BITRATE}" \
  -minrate "${VIDEO_BITRATE}" \
  -maxrate "${MAXRATE}" \
  -bufsize "${BUFSIZE}" \
  -x264-params "nal-hrd=cbr:force-cfr=1:filler=1" \
  -rtsp_transport tcp \
  -f rtsp \
  "${RTSP_URL}"
