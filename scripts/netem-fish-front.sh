#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${NETEM_CONTAINER:-tunnel-fish_front_whip-1}"
DEV="${NETEM_DEV:-eth0}"
PROFILE="${1:-}"
STATE_DIR="${NETEM_STATE_DIR:-.tmp/netem-fish-front}"
GUARD_PID_FILE="${NETEM_GUARD_PID_FILE:-/tmp/netem-fish-front-guard.pid}"
GUARD_PROFILE_FILE="${NETEM_GUARD_PROFILE_FILE:-/tmp/netem-fish-front-guard.profile}"

mkdir -p "$STATE_DIR"

usage() {
  cat <<'EOF'
Usage:
  scripts/netem-fish-front.sh <profile>

Profiles:
  clear
  show
  5g-good
  5g-mid
  5g-bad
  5g-jitter

Environment:
  NETEM_CONTAINER  default: tunnel-fish_front_whip-1
  NETEM_DEV        default: eth0

Examples:
  scripts/netem-fish-front.sh show
  scripts/netem-fish-front.sh 5g-good
  scripts/netem-fish-front.sh clear
EOF
}

is_guard_running() {
  docker exec "$CONTAINER" sh -lc "[ -f '$GUARD_PID_FILE' ] && kill -0 \$(cat '$GUARD_PID_FILE') 2>/dev/null"
}

stop_guard() {
  if is_guard_running; then
    docker exec "$CONTAINER" sh -lc "kill \$(cat '$GUARD_PID_FILE') 2>/dev/null || true"
  fi
  docker exec "$CONTAINER" sh -lc "rm -f '$GUARD_PID_FILE' '$GUARD_PROFILE_FILE'" >/dev/null 2>&1 || true
}

start_guard() {
  local guard_profile="$1"
  local tc_args=""
  case "$guard_profile" in
    5g-good)
      tc_args="delay 20ms 5ms distribution normal loss 0.1% rate 20mbit"
      ;;
    5g-mid)
      tc_args="delay 40ms 15ms distribution normal loss 0.5% rate 8mbit"
      ;;
    5g-bad)
      tc_args="delay 80ms 30ms distribution normal loss 1.0% rate 3mbit"
      ;;
    5g-jitter)
      tc_args="delay 40ms 40ms distribution normal loss 0.5% rate 8mbit"
      ;;
    *)
      echo "unknown guard profile: $guard_profile" >&2
      exit 2
      ;;
  esac
  stop_guard
  docker exec -d \
    -e NETEM_DEV="$DEV" \
    -e NETEM_PROFILE="$guard_profile" \
    -e NETEM_TC_ARGS="$tc_args" \
    -e NETEM_GUARD_PID_FILE="$GUARD_PID_FILE" \
    -e NETEM_GUARD_PROFILE_FILE="$GUARD_PROFILE_FILE" \
    "$CONTAINER" \
    /bin/sh -lc '
      echo "$NETEM_PROFILE" > "$NETEM_GUARD_PROFILE_FILE"
      echo $$ > "$NETEM_GUARD_PID_FILE"
      trap "rm -f \"$NETEM_GUARD_PID_FILE\" \"$NETEM_GUARD_PROFILE_FILE\"; exit 0" TERM INT EXIT
      while true; do
        if ! tc qdisc show dev "$NETEM_DEV" | grep -q netem; then
          tc qdisc replace dev "$NETEM_DEV" root netem $NETEM_TC_ARGS >/dev/null 2>&1 || true
        fi
        sleep 1
      done
    '
}

show_status() {
  docker exec "$CONTAINER" tc qdisc show dev "$DEV"
  if is_guard_running; then
    echo "guard running pid=$(docker exec "$CONTAINER" cat "$GUARD_PID_FILE") profile=$(docker exec "$CONTAINER" cat "$GUARD_PROFILE_FILE" 2>/dev/null || echo unknown)"
  else
    echo "guard stopped"
  fi
}

apply_profile() {
  local tc_args="$1"
  docker exec "$CONTAINER" sh -lc "tc qdisc replace dev '$DEV' root netem ${tc_args}; tc qdisc show dev '$DEV'"
  start_guard "$PROFILE"
}

if [[ -z "$PROFILE" ]]; then
  usage >&2
  exit 2
fi

case "$PROFILE" in
  show)
    show_status
    ;;
  clear)
    stop_guard
    docker exec "$CONTAINER" sh -lc "tc qdisc del dev '$DEV' root 2>/dev/null || true; tc qdisc show dev '$DEV'"
    ;;
  5g-good)
    apply_profile "delay 20ms 5ms distribution normal loss 0.1% rate 20mbit"
    ;;
  5g-mid)
    apply_profile "delay 40ms 15ms distribution normal loss 0.5% rate 8mbit"
    ;;
  5g-bad)
    apply_profile "delay 80ms 30ms distribution normal loss 1.0% rate 3mbit"
    ;;
  5g-jitter)
    apply_profile "delay 40ms 40ms distribution normal loss 0.5% rate 8mbit"
    ;;
  *)
    echo "unknown profile: $PROFILE" >&2
    usage >&2
    exit 2
    ;;
esac
