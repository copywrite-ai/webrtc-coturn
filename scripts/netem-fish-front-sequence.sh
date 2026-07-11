#!/usr/bin/env bash
set -euo pipefail

KEEP_LAST="${KEEP_LAST:-0}"
SEQUENCE="${1:-}"
RANDOM_STEP_SECONDS="${RANDOM_STEP_SECONDS:-5}"

usage() {
  cat <<'EOF'
Usage:
  scripts/netem-fish-front-sequence.sh "<profile:seconds,...>"

Examples:
  scripts/netem-fish-front-sequence.sh "5g-good:20,5g-mid:20,5g-bad:20,5g-mid:20,5g-good:20"
  RANDOM_STEP_SECONDS=4 scripts/netem-fish-front-sequence.sh "random:120"
  KEEP_LAST=1 scripts/netem-fish-front-sequence.sh "clear:10,5g-mid:60"

Notes:
  - Profiles: clear, 5g-good, 5g-mid, 5g-bad, 5g-jitter
  - Pseudo profile: random
  - By default the script clears netem on exit.
EOF
}

log() {
  printf '[%s] %s\n' "$(date '+%Y-%m-%d %H:%M:%S')" "$*"
}

cleanup() {
  if [[ "$KEEP_LAST" != "1" ]]; then
    log "clearing netem"
    scripts/netem-fish-front.sh clear >/dev/null || true
  fi
}

random_profile() {
  local profiles=(5g-good 5g-mid 5g-bad 5g-jitter)
  local idx=$(( RANDOM % ${#profiles[@]} ))
  printf '%s\n' "${profiles[$idx]}"
}

apply_random_window() {
  local total_seconds="$1"
  local remaining="$1"

  if ! [[ "$total_seconds" =~ ^[0-9]+$ ]]; then
    echo "invalid random duration: $total_seconds" >&2
    exit 2
  fi
  if ! [[ "$RANDOM_STEP_SECONDS" =~ ^[0-9]+$ ]] || [[ "$RANDOM_STEP_SECONDS" -le 0 ]]; then
    echo "RANDOM_STEP_SECONDS must be a positive integer" >&2
    exit 2
  fi

  log "apply random window duration=${total_seconds}s step=${RANDOM_STEP_SECONDS}s"
  while [[ "$remaining" -gt 0 ]]; do
    local profile
    local slice
    profile="$(random_profile)"
    if [[ "$remaining" -lt "$RANDOM_STEP_SECONDS" ]]; then
      slice="$remaining"
    else
      slice="$RANDOM_STEP_SECONDS"
    fi
    log "random pick profile=$profile duration=${slice}s remaining=${remaining}s"
    scripts/netem-fish-front.sh "$profile"
    sleep "$slice"
    remaining=$((remaining - slice))
  done
}

apply_step() {
  local profile="$1"
  local seconds="$2"

  case "$profile" in
    clear|5g-good|5g-mid|5g-bad|5g-jitter|random)
      ;;
    *)
      echo "unknown profile in sequence: $profile" >&2
      exit 2
      ;;
  esac

  if ! [[ "$seconds" =~ ^[0-9]+$ ]]; then
    echo "invalid seconds for $profile: $seconds" >&2
    exit 2
  fi

  if [[ "$profile" == "random" ]]; then
    apply_random_window "$seconds"
    return
  fi

  log "apply profile=$profile duration=${seconds}s"
  scripts/netem-fish-front.sh "$profile"

  if [[ "$seconds" -gt 0 ]]; then
    sleep "$seconds"
  fi
}

if [[ -z "$SEQUENCE" ]]; then
  usage >&2
  exit 2
fi

trap cleanup EXIT INT TERM

IFS=',' read -r -a steps <<<"$SEQUENCE"
for step in "${steps[@]}"; do
  step="${step// /}"
  profile="${step%%:*}"
  seconds="${step#*:}"
  if [[ "$profile" == "$step" || "$seconds" == "$step" ]]; then
    echo "invalid step: $step" >&2
    usage >&2
    exit 2
  fi
  apply_step "$profile" "$seconds"
done

if [[ "$KEEP_LAST" == "1" ]]; then
  log "sequence complete, keeping last profile"
else
  log "sequence complete"
fi
