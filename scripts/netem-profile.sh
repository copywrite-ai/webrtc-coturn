#!/usr/bin/env bash
set -euo pipefail

usage() {
  cat <<'EOF'
Usage:
  scripts/netem-profile.sh <dev> <profile>

Profiles:
  clear     Remove root qdisc from <dev>
  show      Show qdisc on <dev>
  5g-good   20ms +/- 5ms, 0.1% loss, 20mbit
  5g-mid    40ms +/- 15ms, 0.5% loss, 8mbit
  5g-bad    80ms +/- 30ms, 1.0% loss, 3mbit
  5g-jitter 40ms +/- 40ms, 0.5% loss, 8mbit

Examples:
  scripts/netem-profile.sh eth0 show
  sudo scripts/netem-profile.sh eth0 5g-good
  sudo scripts/netem-profile.sh eth0 clear

Notes:
  - This script is for Linux tc/netem. It must run on the network namespace
    that owns <dev>, usually as root or with CAP_NET_ADMIN.
  - Apply on the publisher egress to simulate 5G uplink.
  - Do not apply on both publisher and viewer unless you explicitly want the
    latency to stack.
EOF
}

if [[ $# -ne 2 ]]; then
  usage >&2
  exit 2
fi

DEV="$1"
PROFILE="$2"

if ! command -v tc >/dev/null 2>&1; then
  echo "tc not found. Install iproute2 first." >&2
  exit 127
fi

case "$PROFILE" in
  show)
    tc qdisc show dev "$DEV"
    ;;
  clear)
    tc qdisc del dev "$DEV" root 2>/dev/null || true
    tc qdisc show dev "$DEV"
    ;;
  5g-good)
    tc qdisc replace dev "$DEV" root netem delay 20ms 5ms distribution normal loss 0.1% rate 20mbit
    tc qdisc show dev "$DEV"
    ;;
  5g-mid)
    tc qdisc replace dev "$DEV" root netem delay 40ms 15ms distribution normal loss 0.5% rate 8mbit
    tc qdisc show dev "$DEV"
    ;;
  5g-bad)
    tc qdisc replace dev "$DEV" root netem delay 80ms 30ms distribution normal loss 1.0% rate 3mbit
    tc qdisc show dev "$DEV"
    ;;
  5g-jitter)
    tc qdisc replace dev "$DEV" root netem delay 40ms 40ms distribution normal loss 0.5% rate 8mbit
    tc qdisc show dev "$DEV"
    ;;
  *)
    echo "unknown profile: $PROFILE" >&2
    usage >&2
    exit 2
    ;;
esac
