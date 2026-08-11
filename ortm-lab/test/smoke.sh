#!/usr/bin/env bash
set -euo pipefail

TEST_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
LAB_DIR="$(cd "${TEST_DIR}/.." && pwd)"
CLI="${LAB_DIR}/bin/ortm-lab"
TMP_DIR="$(mktemp -d "${TMPDIR:-/tmp}/ortm-lab-test.XXXXXX")"
trap 'rm -rf "$TMP_DIR"' EXIT

bash -n "$CLI"
for file in "${LAB_DIR}/.env.example" "${LAB_DIR}"/scenarios/*.env; do
  bash -n "$file"
done
for matrix in "${LAB_DIR}"/matrices/*.list; do
  matrix_dir="$(cd "$(dirname "$matrix")" && pwd)"
  while IFS= read -r line || [[ -n "$line" ]]; do
    line="${line%%#*}"
    line="$(printf '%s' "$line" | tr -d '[:space:]')"
    [[ -n "$line" ]] || continue
    test -f "${matrix_dir}/${line}"
  done < "$matrix"
done

ORTM_LAB_ENV_FILE="${TMP_DIR}/lab.env" "$CLI" init >/dev/null
test -s "${TMP_DIR}/lab.env"
grep -q '^VIEWER_CONTROL_TOKEN=' "${TMP_DIR}/lab.env"
if grep -q '^VIEWER_CONTROL_TOKEN=change-me$' "${TMP_DIR}/lab.env"; then
  echo "init did not replace VIEWER_CONTROL_TOKEN" >&2
  exit 1
fi

cat > "${TMP_DIR}/snapshots.jsonl" <<'EOF'
{"ts":"2026-08-11T00:00:00Z","data":{"viewerSampleValid":true,"viewer":{"status":"playing","ice":"connected","resolution":"1280x720","ortm":40,"ortmNet":35,"upstream":30,"browser":5,"rtcFps":60,"rtcBitrate":2500,"rtcJitter":4,"ortmDecodeSuccessRate":100,"ortmDecodeAttempts":100,"ortmDecodeFailures":0,"ortmCrcFailures":0,"ortmStructureFailures":0,"ortmLowContrastFailures":0,"rtcFreezeCount":0,"rtcDrop":0,"rtcPacketsLost":0}}}
{"ts":"2026-08-11T00:00:05Z","data":{"viewerSampleValid":true,"viewer":{"status":"playing","ice":"connected","resolution":"1280x720","ortm":50,"ortmNet":44,"upstream":38,"browser":6,"rtcFps":59,"rtcBitrate":2450,"rtcJitter":5,"ortmDecodeSuccessRate":100,"ortmDecodeAttempts":125,"ortmDecodeFailures":0,"ortmCrcFailures":0,"ortmStructureFailures":0,"ortmLowContrastFailures":0,"rtcFreezeCount":0,"rtcDrop":0,"rtcPacketsLost":0}}}
EOF

# Source the CLI without executing its command dispatcher.
# shellcheck disable=SC1090
source "$CLI"
SCENARIO_NAME=smoke
WIDTH=1280
HEIGHT=720
MIN_DECODE_SUCCESS_RATE=99.9
MAX_CRC_FAILURES=0
MAX_STRUCTURE_FAILURES=0
MAX_LOW_CONTRAST_FAILURES=0
render_summary "${TMP_DIR}/snapshots.jsonl" "${TMP_DIR}/summary.json"

jq -e '
  .quality.passed == true
  and .validSamples == 2
  and .metrics.ortmMs.avg == 45
  and .metrics.ortmMs.p95 == 50
  and .final.decodeAttempts == 125
' "${TMP_DIR}/summary.json" >/dev/null

jq '.[]? // .' "${TMP_DIR}/summary.json" >/dev/null
echo "ortm-lab smoke test passed"
