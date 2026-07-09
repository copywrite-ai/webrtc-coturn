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

derive_stream_name() {
  local url="${1:-}"
  url="${url%%\?*}"
  if [[ "${url}" =~ /online/whip$ ]]; then
    printf '%s\n' "online"
    return 0
  fi
  if [[ "${url}" =~ /offline/whip$ ]]; then
    printf '%s\n' "online"
    return 0
  fi
  if [[ "${url}" =~ /mtx/[^/]+/([^/]+)/(whip|whep)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  if [[ "${url}" =~ /([^/]+)/(whip|whep)$ ]]; then
    printf '%s\n' "${BASH_REMATCH[1]}"
    return 0
  fi
  return 1
}

DEFAULT_STREAM_NAME="$(
  derive_stream_name "${DEFAULT_WHIP_URL:-}" 2>/dev/null \
    || derive_stream_name "${DEFAULT_WHEP_URL:-}" 2>/dev/null \
    || printf '%s\n' "online"
)"

CAMERA_INDEX="${CAMERA_INDEX:-0}"
AUDIO_INDEX="${AUDIO_INDEX:-none}"
FPS="${FPS:-30}"
WIDTH="${WIDTH:-1920}"
HEIGHT="${HEIGHT:-1080}"
THREAD_QUEUE_SIZE="${THREAD_QUEUE_SIZE:-512}"
STREAM_NAME="${STREAM_NAME:-${DEFAULT_STREAM_NAME}}"
VIDEO_CODEC="${VIDEO_CODEC:-libx264}"
PRESET="${PRESET:-veryfast}"
PROFILE="${PROFILE:-baseline}"
GOP="${GOP:-60}"
VIDEO_BITRATE="${VIDEO_BITRATE:-10M}"
MAXRATE="${MAXRATE:-10M}"
BUFSIZE="${BUFSIZE:-20M}"
PIX_FMT="${PIX_FMT:-yuv420p}"
RTSP_URL="${RTSP_URL:-rtsp://127.0.0.1:8554/${STREAM_NAME}}"
PUBLIC_WHIP_URL="${PUBLIC_WHIP_URL:-${DEFAULT_WHIP_URL:-}}"
PUBLIC_WHEP_URL="${PUBLIC_WHEP_URL:-${DEFAULT_WHEP_URL:-}}"

echo "Starting FFmpeg RTSP push"
echo "  source        : avfoundation ${CAMERA_INDEX}:${AUDIO_INDEX}"
echo "  resolution    : ${WIDTH}x${HEIGHT}"
echo "  fps           : ${FPS}"
echo "  video codec   : ${VIDEO_CODEC}"
echo "  bitrate       : ${VIDEO_BITRATE} (maxrate=${MAXRATE}, bufsize=${BUFSIZE})"
echo "  stream name   : ${STREAM_NAME}"
echo "  target        : ${RTSP_URL}"
echo
if [ -n "${PUBLIC_WHIP_URL}" ]; then
  echo "Matching web publish URL : ${PUBLIC_WHIP_URL}"
fi
if [ -n "${PUBLIC_WHEP_URL}" ]; then
  echo "Matching web playback URL: ${PUBLIC_WHEP_URL}"
else
  echo "WHEP playback URL is typically: http://127.0.0.1:8889/${STREAM_NAME}/whep"
fi
echo

exec ffmpeg -re \
  -use_wallclock_as_timestamps 1 \
  -fflags +genpts \
  -f avfoundation -thread_queue_size "${THREAD_QUEUE_SIZE}" -framerate "${FPS}" -video_size "${WIDTH}x${HEIGHT}" -i "${CAMERA_INDEX}:${AUDIO_INDEX}" \
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
  -fps_mode cfr \
  -b:v "${VIDEO_BITRATE}" \
  -maxrate "${MAXRATE}" \
  -bufsize "${BUFSIZE}" \
  -rtsp_transport tcp \
  -f rtsp \
  "${RTSP_URL}"
