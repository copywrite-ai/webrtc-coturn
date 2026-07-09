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

if ! gst-inspect-1.0 textoverlay >/dev/null 2>&1; then
  echo "GStreamer textoverlay plugin not found" >&2
  exit 1
fi

exec /usr/local/bin/publish-whip.py
