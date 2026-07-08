#!/usr/bin/env bash
set -euo pipefail

if ! command -v gst-launch-1.0 >/dev/null 2>&1; then
  echo "gst-launch-1.0 not found" >&2
  exit 1
fi

if ! gst-inspect-1.0 x264enc >/dev/null 2>&1; then
  echo "GStreamer x264enc plugin not found" >&2
  exit 1
fi

if ! gst-inspect-1.0 whipclientsink >/dev/null 2>&1; then
  echo "GStreamer whipclientsink plugin not found" >&2
  exit 1
fi

STREAM_NAME="${STREAM_NAME:?STREAM_NAME is required}"
WHIP_BASE_URL="${WHIP_BASE_URL:?WHIP_BASE_URL is required}"
DEVICE_ID="${DEVICE_ID:?DEVICE_ID is required}"

WIDTH="${WIDTH:-1280}"
HEIGHT="${HEIGHT:-720}"
FPS="${FPS:-30}"
BITRATE_KBPS="${BITRATE_KBPS:-8000}"
KEY_INT_MAX="${KEY_INT_MAX:-60}"
PATTERN="${PATTERN:-smpte}"
SPEED_PRESET="${SPEED_PRESET:-ultrafast}"
X264_OPTION_STRING="${X264_OPTION_STRING:-nal-hrd=cbr:force-cfr=1:filler=1}"
GST_DEBUG_LEVEL="${GST_DEBUG_LEVEL:-2}"
WHIP_STUN_SERVER="${WHIP_STUN_SERVER:-}"
WHIP_TURN_SERVER="${WHIP_TURN_SERVER:-}"
WHIP_TURN_SERVER_2="${WHIP_TURN_SERVER_2:-}"
WHIP_FORCE_TURN="${WHIP_FORCE_TURN:-0}"
WHIP_SINK_EXTRA_ARGS="${WHIP_SINK_EXTRA_ARGS:-}"

base_url="${WHIP_BASE_URL%/}"
device_id="${DEVICE_ID#/}"
device_id="${device_id%/}"
whip_url="${base_url}/${device_id}/${STREAM_NAME}/whip"

echo "Starting WHIP publisher"
echo "  device       : ${device_id}"
echo "  stream       : ${STREAM_NAME}"
echo "  target       : ${whip_url}"
echo "  resolution   : ${WIDTH}x${HEIGHT}"
echo "  fps          : ${FPS}"
echo "  bitrate      : ${BITRATE_KBPS} kbps"
echo "  pattern      : ${PATTERN}"
echo "  x264 preset  : ${SPEED_PRESET}"
echo "  x264 options : ${X264_OPTION_STRING}"
echo "  gst debug    : ${GST_DEBUG_LEVEL}"
echo "  stun server  : ${WHIP_STUN_SERVER:-<none>}"
if [ -n "${WHIP_TURN_SERVER}" ]; then
  echo "  turn server  : <configured>"
else
  echo "  turn server  : <none>"
fi
if [ -n "${WHIP_TURN_SERVER_2}" ]; then
  echo "  turn server 2: <configured>"
else
  echo "  turn server 2: <none>"
fi
echo "  force turn   : ${WHIP_FORCE_TURN}"
echo

sink_args=(
  "signaller::whip-endpoint=${whip_url}"
)

if [ -n "${WHIP_STUN_SERVER}" ]; then
  sink_args+=("stun-server=${WHIP_STUN_SERVER}")
fi

if [ -n "${WHIP_TURN_SERVER}" ]; then
  if [ -n "${WHIP_TURN_SERVER_2}" ]; then
    sink_args+=("turn-servers=<\"${WHIP_TURN_SERVER}\", \"${WHIP_TURN_SERVER_2}\">")
  else
    sink_args+=("turn-servers=<\"${WHIP_TURN_SERVER}\">")
  fi
fi

if [ "${WHIP_FORCE_TURN}" = "1" ]; then
  sink_args+=("ice-transport-policy=relay")
fi

if [ -n "${WHIP_SINK_EXTRA_ARGS}" ]; then
  # shellcheck disable=SC2206
  extra_args=(${WHIP_SINK_EXTRA_ARGS})
  sink_args+=("${extra_args[@]}")
fi

sink_args_log="${sink_args[*]}"
if [ -n "${WHIP_TURN_SERVER}" ]; then
  sink_args_log="${sink_args_log//${WHIP_TURN_SERVER}/<turn-redacted>}"
fi
if [ -n "${WHIP_TURN_SERVER_2}" ]; then
  sink_args_log="${sink_args_log//${WHIP_TURN_SERVER_2}/<turn2-redacted>}"
fi
echo "  sink args    : ${sink_args_log}"
echo

export GST_DEBUG="${GST_DEBUG:-whip*:6,webrtc*:6,rswebrtc*:6,ice*:5,nice*:5,*:${GST_DEBUG_LEVEL}}"

exec gst-launch-1.0 -e -v \
  videotestsrc is-live=true "pattern=${PATTERN}" \
  ! "video/x-raw,width=${WIDTH},height=${HEIGHT},framerate=${FPS}/1,format=I420" \
  ! x264enc "bitrate=${BITRATE_KBPS}" "speed-preset=${SPEED_PRESET}" tune=zerolatency "key-int-max=${KEY_INT_MAX}" bframes=0 "option-string=${X264_OPTION_STRING}" \
  ! video/x-h264,profile=baseline \
  ! h264parse config-interval=-1 \
  ! whipclientsink name=ws "${sink_args[@]}"
