#!/usr/bin/env sh
set -eu

MONITOR_URL=${MONITOR_URL:-http://127.0.0.1:9010}
PROMETHEUS_URL=${PROMETHEUS_URL:-http://127.0.0.1:9090}
GRAFANA_URL=${GRAFANA_URL:-http://127.0.0.1:3000}
MAX_ATTEMPTS=${MAX_ATTEMPTS:-30}

wait_for() {
  name=$1
  url=$2
  attempt=1
  while [ "$attempt" -le "$MAX_ATTEMPTS" ]; do
    if curl -fsS "$url" >/dev/null 2>&1; then
      printf '%s: ok\n' "$name"
      return 0
    fi
    attempt=$((attempt + 1))
    sleep 2
  done
  printf '%s: failed (%s)\n' "$name" "$url" >&2
  return 1
}

wait_for monitor "$MONITOR_URL/healthz"
wait_for prometheus "$PROMETHEUS_URL/-/healthy"
wait_for grafana "$GRAFANA_URL/api/health"

metrics=$(curl -fsS "$MONITOR_URL/metrics")
printf '%s\n' "$metrics" | grep -q '^tunnel_monitor_up 1$'

query_url="$PROMETHEUS_URL/api/v1/query?query=tunnel_monitor_up"
query=$(curl -fsS "$query_url")
printf '%s\n' "$query" | grep -q '"status":"success"'
printf '%s\n' "$query" | grep -q '"result":\[{'

printf '%s\n' 'monitoring smoke test: passed'
