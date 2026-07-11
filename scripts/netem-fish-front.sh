#!/usr/bin/env bash
set -euo pipefail

CONTAINER="${NETEM_CONTAINER:-tunnel-fish_front_whip-1}"
DEV="${NETEM_DEV:-eth0}"
PROFILE="${1:-}"

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

if [[ -z "$PROFILE" ]]; then
  usage >&2
  exit 2
fi

case "$PROFILE" in
  show)
    docker exec "$CONTAINER" tc qdisc show dev "$DEV"
    ;;
  clear)
    docker exec "$CONTAINER" sh -lc "tc qdisc del dev '$DEV' root 2>/dev/null || true; tc qdisc show dev '$DEV'"
    ;;
  5g-good)
    docker exec "$CONTAINER" sh -lc "tc qdisc replace dev '$DEV' root netem delay 20ms 5ms distribution normal loss 0.1% rate 20mbit; tc qdisc show dev '$DEV'"
    ;;
  5g-mid)
    docker exec "$CONTAINER" sh -lc "tc qdisc replace dev '$DEV' root netem delay 40ms 15ms distribution normal loss 0.5% rate 8mbit; tc qdisc show dev '$DEV'"
    ;;
  5g-bad)
    docker exec "$CONTAINER" sh -lc "tc qdisc replace dev '$DEV' root netem delay 80ms 30ms distribution normal loss 1.0% rate 3mbit; tc qdisc show dev '$DEV'"
    ;;
  5g-jitter)
    docker exec "$CONTAINER" sh -lc "tc qdisc replace dev '$DEV' root netem delay 40ms 40ms distribution normal loss 0.5% rate 8mbit; tc qdisc show dev '$DEV'"
    ;;
  *)
    echo "unknown profile: $PROFILE" >&2
    usage >&2
    exit 2
    ;;
esac
