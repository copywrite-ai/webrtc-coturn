#!/usr/bin/env bash
set -euo pipefail

ROOT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
PYTHON_BIN="${PYTHON_BIN:-/opt/homebrew/bin/python3}"

if ! command -v gst-launch-1.0 >/dev/null 2>&1; then
  echo "gst-launch-1.0 not found. Install GStreamer with Homebrew first." >&2
  exit 1
fi

if ! command -v "${PYTHON_BIN}" >/dev/null 2>&1; then
  echo "Python not found: ${PYTHON_BIN}" >&2
  exit 1
fi

export STREAM_NAME="${STREAM_NAME:-fish_front}"
export DEVICE_ID="${DEVICE_ID:-$(hostname -s)}"
export WHIP_BASE_URL="${WHIP_BASE_URL:-http://127.0.0.1:8889}"
export WHIP_INCLUDE_DEVICE="${WHIP_INCLUDE_DEVICE:-0}"

export VIDEO_SOURCE="${VIDEO_SOURCE:-avf}"
export VIDEO_DEVICE="${VIDEO_DEVICE:-0}"
export VIDEO_SOURCE_WIDTH="${VIDEO_SOURCE_WIDTH:-}"
export VIDEO_SOURCE_HEIGHT="${VIDEO_SOURCE_HEIGHT:-}"
export VIDEO_SOURCE_FPS="${VIDEO_SOURCE_FPS:-}"
export WIDTH="${WIDTH:-960}"
export HEIGHT="${HEIGHT:-540}"
export FPS="${FPS:-60}"
export BITRATE_KBPS="${BITRATE_KBPS:-2500}"
export KEY_INT_MAX="${KEY_INT_MAX:-60}"
export SPEED_PRESET="${SPEED_PRESET:-ultrafast}"
export TIMESTAMP_TZ="${TIMESTAMP_TZ:-Asia/Shanghai}"
export ORTM_BACKGROUND_ALPHA="${ORTM_BACKGROUND_ALPHA:-1.0}"
export ORTM_CELL_ALPHA="${ORTM_CELL_ALPHA:-1.0}"
export PIPELINE_METRICS="${PIPELINE_METRICS:-1}"
export PIPELINE_METRICS_INTERVAL_FRAMES="${PIPELINE_METRICS_INTERVAL_FRAMES:-30}"
export X264_OPTION_STRING="${X264_OPTION_STRING:-nal-hrd=cbr:force-cfr=1:filler=1}"

echo "Starting local macOS camera WHIP publisher"
echo "  stream       : ${STREAM_NAME}"
echo "  whip base    : ${WHIP_BASE_URL}"
echo "  source       : avfvideosrc device-index=${VIDEO_DEVICE}"
if [[ -n "${VIDEO_SOURCE_WIDTH}" && -n "${VIDEO_SOURCE_HEIGHT}" ]]; then
  echo "  source caps  : ${VIDEO_SOURCE_WIDTH}x${VIDEO_SOURCE_HEIGHT}@${VIDEO_SOURCE_FPS:-${FPS}}"
else
  echo "  source caps  : auto"
fi
echo "  resolution   : ${WIDTH}x${HEIGHT}@${FPS}"
echo "  bitrate      : ${BITRATE_KBPS} kbps"
echo "  ortm bg alpha: ${ORTM_BACKGROUND_ALPHA}"
echo "  ortm cell alpha: ${ORTM_CELL_ALPHA}"

exec "${PYTHON_BIN}" "${ROOT_DIR}/docker/whip-publisher/publish-whip.py"
