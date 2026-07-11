#!/usr/bin/env bash
set -euo pipefail

DEVICE_ID="${DEVICE_ID:-}"
WIDTH="${WIDTH:-960}"
HEIGHT="${HEIGHT:-540}"
FPS="${FPS:-60}"
BITRATE_KBPS="${BITRATE_KBPS:-2500}"
KEY_INT_MAX="${KEY_INT_MAX:-120}"
WHIP_BASE_URL="${WHIP_BASE_URL:-http://mediamtx:8889}"
WHIP_INCLUDE_DEVICE="${WHIP_INCLUDE_DEVICE:-0}"
SAMPLE_SECONDS="${SAMPLE_SECONDS:-60}"
SNAPSHOT_INTERVAL_SECONDS="${SNAPSHOT_INTERVAL_SECONDS:-5}"
PROFILE="${PROFILE:-}"
PROFILE_SEQUENCE="${PROFILE_SEQUENCE:-}"
RESULT_DIR="${RESULT_DIR:-.tmp/5g-experiments}"

usage() {
  cat <<'EOF'
Usage:
  DEVICE_ID=<device> scripts/run-fish-front-5g-experiment.sh

Optional environment:
  WIDTH                     default: 960
  HEIGHT                    default: 540
  FPS                       default: 60
  BITRATE_KBPS              default: 2500
  KEY_INT_MAX               default: 120
  WHIP_BASE_URL             default: http://mediamtx:8889
  WHIP_INCLUDE_DEVICE       default: 0
  SAMPLE_SECONDS            default: 60
  SNAPSHOT_INTERVAL_SECONDS default: 5
  PROFILE                   single profile, e.g. 5g-mid
  PROFILE_SEQUENCE          sequence, e.g. 5g-good:20,5g-mid:20,5g-bad:20
  RESULT_DIR                default: .tmp/5g-experiments

Examples:
  DEVICE_ID=peng-mbp14 PROFILE=5g-mid scripts/run-fish-front-5g-experiment.sh
  DEVICE_ID=peng-mbp14 PROFILE_SEQUENCE='5g-good:20,5g-mid:20,5g-bad:20' SAMPLE_SECONDS=70 scripts/run-fish-front-5g-experiment.sh
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

require_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "missing required command: $1" >&2
    exit 1
  }
}

cleanup() {
  if [[ -n "${SEQUENCE_PID:-}" ]]; then
    kill "$SEQUENCE_PID" >/dev/null 2>&1 || true
    wait "$SEQUENCE_PID" >/dev/null 2>&1 || true
  fi
}

if [[ -z "$DEVICE_ID" ]]; then
  usage >&2
  echo "DEVICE_ID is required" >&2
  exit 2
fi

if [[ -n "$PROFILE" && -n "$PROFILE_SEQUENCE" ]]; then
  echo "PROFILE and PROFILE_SEQUENCE are mutually exclusive" >&2
  exit 2
fi

require_cmd docker
require_cmd jq

mkdir -p "$RESULT_DIR"

stamp="$(date '+%Y%m%d-%H%M%S')"
run_name="fish-front-${WIDTH}x${HEIGHT}-${FPS}fps-${BITRATE_KBPS}kbps-${stamp}"
snapshot_file="${RESULT_DIR}/${run_name}.jsonl"
summary_file="${RESULT_DIR}/${run_name}.summary.txt"

trap cleanup EXIT INT TERM

log "starting scenario run=$run_name"

DEVICE_ID="$DEVICE_ID" \
WHIP_BASE_URL="$WHIP_BASE_URL" \
WHIP_INCLUDE_DEVICE="$WHIP_INCLUDE_DEVICE" \
WIDTH="$WIDTH" \
HEIGHT="$HEIGHT" \
FPS="$FPS" \
BITRATE_KBPS="$BITRATE_KBPS" \
KEY_INT_MAX="$KEY_INT_MAX" \
docker compose -f docker-compose.whip-publisher.yml up -d --build --no-deps --force-recreate fish_front_whip

startup_logs="$(
  docker logs --since 20s tunnel-fish_front_whip-1 2>&1 \
    | rg -n "Starting WHIP publisher|resolution|fps|bitrate|target|sink args" || true
)"
if [[ -n "$startup_logs" ]]; then
  printf '%s\n' "$startup_logs" | tee "$summary_file"
else
  echo "startup logs not ready within initial 20s window" | tee "$summary_file"
fi

if [[ -n "$PROFILE_SEQUENCE" ]]; then
  log "starting profile sequence: $PROFILE_SEQUENCE"
  KEEP_LAST=0 scripts/netem-fish-front-sequence.sh "$PROFILE_SEQUENCE" &
  SEQUENCE_PID="$!"
elif [[ -n "$PROFILE" ]]; then
  log "applying single profile: $PROFILE"
  scripts/netem-fish-front.sh "$PROFILE"
else
  log "no profile requested, clearing netem"
  scripts/netem-fish-front.sh clear >/dev/null || true
fi

start_ts="$(date +%s)"
end_ts=$((start_ts + SAMPLE_SECONDS))

log "sampling snapshots for ${SAMPLE_SECONDS}s interval=${SNAPSHOT_INTERVAL_SECONDS}s"

while [[ "$(date +%s)" -lt "$end_ts" ]]; do
  snapshot="$(
    docker exec tunnel-monitor wget -qO- http://127.0.0.1:9010/api/snapshot \
      | jq -c '{
          ts: .updatedAt,
          fish_front: (
            .derived.streams.fish_front
            + {
                viewerSampleValid: ((.derived.streams.fish_front.readers // 0) > 0),
                viewerSampleWarning: (
                  if ((.derived.streams.fish_front.readers // 0) > 0)
                  then null
                  else "no active readers; viewer metrics may be stale"
                  end
                )
              }
          )
        }'
  )"
  printf '%s\n' "$snapshot" | tee -a "$snapshot_file"
  if [[ "$(printf '%s\n' "$snapshot" | jq -r '.fish_front.viewerSampleValid')" != "true" ]]; then
    log "warning: no active readers, viewer metrics may be stale"
  fi
  sleep "$SNAPSHOT_INTERVAL_SECONDS"
done

log "last snapshot summary"
jq -r '
  .fish_front as $s
  | [
      "ts=\(.ts)",
      "viewerValid=\($s.viewerSampleValid)",
      "ortm=\($s.viewer.ortm)ms",
      "ortmNet=\($s.viewer.ortmNet)ms",
      "upstream=\($s.viewer.upstream)ms",
      "upstreamNet=\($s.viewer.upstreamNet)ms",
      "browser=\($s.viewer.browser)ms",
      "jitter=\($s.viewer.rtcJitter)ms",
      "fps=\($s.viewer.rtcFps)",
      "bitrate=\($s.viewer.rtcBitrate)kbps",
      "lost=\($s.rtpPacketsLost)"
    ] | join(" ")
' "$snapshot_file" | tail -n 1 | tee -a "$summary_file"

log "snapshots saved to $snapshot_file"
log "summary saved to $summary_file"
