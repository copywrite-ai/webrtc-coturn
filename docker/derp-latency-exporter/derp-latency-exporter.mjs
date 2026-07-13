import { createServer } from 'node:http';
import { execFile } from 'node:child_process';

const PORT = Number(process.env.DERP_EXPORTER_PORT || 9020);
const INTERVAL_MS = Number(process.env.DERP_NETCHECK_INTERVAL_MS || 30_000);
const TIMEOUT_MS = Number(process.env.DERP_NETCHECK_TIMEOUT_MS || 20_000);
const TAILSCALE_BIN = process.env.TAILSCALE_BIN || 'tailscale';
const TAILSCALE_SOCKET = process.env.TS_SOCKET || '';
const REGION_ALLOWLIST = new Set(
  String(process.env.DERP_REGION_ALLOWLIST || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean),
);

const state = {
  startedAt: Date.now(),
  lastRunAt: 0,
  lastSuccessAt: 0,
  lastDurationMs: 0,
  lastError: '',
  preferredDerp: null,
  udp: null,
  ipv4: null,
  ipv6: null,
  latencies: {
    any: {},
    v4: {},
    v6: {},
  },
};

function runNetcheck() {
  const started = Date.now();
  state.lastRunAt = started;

  execFile(
    TAILSCALE_BIN,
    buildTailscaleArgs('netcheck', '--format', 'json'),
    { env: process.env, timeout: TIMEOUT_MS, maxBuffer: 1024 * 1024 },
    (error, stdout, stderr) => {
      state.lastDurationMs = Date.now() - started;
      if (error) {
        state.lastError = String(error.message || error);
        return;
      }

      const raw = `${stdout || ''}\n${stderr || ''}`;
      const jsonStart = raw.indexOf('{');
      const jsonEnd = raw.lastIndexOf('}');
      if (jsonStart < 0 || jsonEnd <= jsonStart) {
        state.lastError = 'netcheck did not return json';
        return;
      }

      let data;
      try {
        data = JSON.parse(raw.slice(jsonStart, jsonEnd + 1));
      } catch (parseError) {
        state.lastError = `failed to parse netcheck json: ${parseError.message}`;
        return;
      }

      state.lastSuccessAt = Date.now();
      state.lastError = '';
      state.preferredDerp = numberOrNull(data.PreferredDERP);
      state.udp = boolOrNull(data.UDP);
      state.ipv4 = boolOrNull(data.IPv4);
      state.ipv6 = boolOrNull(data.IPv6);
      state.latencies = {
        any: normalizeLatencyMap(data.RegionLatency),
        v4: normalizeLatencyMap(data.RegionV4Latency),
        v6: normalizeLatencyMap(data.RegionV6Latency),
      };
    },
  );
}

function buildTailscaleArgs(...args) {
  return TAILSCALE_SOCKET ? [`--socket=${TAILSCALE_SOCKET}`, ...args] : args;
}

function normalizeLatencyMap(value) {
  const result = {};
  for (const [region, latencyNs] of Object.entries(value || {})) {
    if (REGION_ALLOWLIST.size > 0 && !REGION_ALLOWLIST.has(String(region))) {
      continue;
    }
    const numeric = Number(latencyNs);
    if (Number.isFinite(numeric) && numeric >= 0) {
      result[String(region)] = numeric / 1_000_000;
    }
  }
  return result;
}

function numberOrNull(value) {
  const numeric = Number(value);
  return Number.isFinite(numeric) ? numeric : null;
}

function boolOrNull(value) {
  return typeof value === 'boolean' ? value : null;
}

function labelValue(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
}

function emitMetrics() {
  const lines = [
    '# HELP derp_latency_exporter_up Whether the DERP latency exporter process is running.',
    '# TYPE derp_latency_exporter_up gauge',
    'derp_latency_exporter_up 1',
    '# HELP derp_netcheck_up Whether the latest tailscale netcheck succeeded.',
    '# TYPE derp_netcheck_up gauge',
    `derp_netcheck_up ${state.lastError ? 0 : 1}`,
    '# HELP derp_netcheck_last_run_timestamp_seconds Unix timestamp of the latest netcheck attempt.',
    '# TYPE derp_netcheck_last_run_timestamp_seconds gauge',
    `derp_netcheck_last_run_timestamp_seconds ${state.lastRunAt / 1000}`,
    '# HELP derp_netcheck_last_success_timestamp_seconds Unix timestamp of the latest successful netcheck.',
    '# TYPE derp_netcheck_last_success_timestamp_seconds gauge',
    `derp_netcheck_last_success_timestamp_seconds ${state.lastSuccessAt / 1000}`,
    '# HELP derp_netcheck_duration_seconds Duration of the latest netcheck attempt.',
    '# TYPE derp_netcheck_duration_seconds gauge',
    `derp_netcheck_duration_seconds ${state.lastDurationMs / 1000}`,
  ];

  if (state.preferredDerp != null) {
    lines.push('# HELP derp_preferred_region_id Preferred DERP region reported by tailscale netcheck.');
    lines.push('# TYPE derp_preferred_region_id gauge');
    lines.push(`derp_preferred_region_id ${state.preferredDerp}`);
  }
  if (REGION_ALLOWLIST.size > 0) {
    lines.push('# HELP derp_region_allowlist_info Configured DERP region allowlist.');
    lines.push('# TYPE derp_region_allowlist_info gauge');
    for (const region of REGION_ALLOWLIST) {
      lines.push(`derp_region_allowlist_info{region="${labelValue(region)}"} 1`);
    }
  }
  if (state.udp != null) {
    lines.push('# HELP derp_netcheck_udp Whether UDP connectivity is available.');
    lines.push('# TYPE derp_netcheck_udp gauge');
    lines.push(`derp_netcheck_udp ${state.udp ? 1 : 0}`);
  }
  if (state.ipv4 != null) {
    lines.push('# HELP derp_netcheck_ipv4 Whether IPv4 connectivity is available.');
    lines.push('# TYPE derp_netcheck_ipv4 gauge');
    lines.push(`derp_netcheck_ipv4 ${state.ipv4 ? 1 : 0}`);
  }
  if (state.ipv6 != null) {
    lines.push('# HELP derp_netcheck_ipv6 Whether IPv6 connectivity is available.');
    lines.push('# TYPE derp_netcheck_ipv6 gauge');
    lines.push(`derp_netcheck_ipv6 ${state.ipv6 ? 1 : 0}`);
  }

  lines.push('# HELP derp_region_latency_ms Tailscale netcheck DERP latency by region and IP version.');
  lines.push('# TYPE derp_region_latency_ms gauge');
  for (const [ipVersion, latencies] of Object.entries(state.latencies)) {
    for (const [region, latencyMs] of Object.entries(latencies)) {
      lines.push(`derp_region_latency_ms{region="${labelValue(region)}",ip_version="${labelValue(ipVersion)}"} ${latencyMs}`);
    }
  }

  if (state.lastError) {
    lines.push('# HELP derp_netcheck_error_info Last netcheck error as an info metric.');
    lines.push('# TYPE derp_netcheck_error_info gauge');
    lines.push(`derp_netcheck_error_info{message="${labelValue(state.lastError)}"} 1`);
  }

  lines.push('');
  return lines.join('\n');
}

const server = createServer((req, res) => {
  const url = new URL(req.url || '/', `http://127.0.0.1:${PORT}`);
  if (url.pathname === '/healthz') {
    res.writeHead(200, { 'Content-Type': 'application/json; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(JSON.stringify({
      ok: !state.lastError,
      lastRunAt: state.lastRunAt ? new Date(state.lastRunAt).toISOString() : null,
      lastSuccessAt: state.lastSuccessAt ? new Date(state.lastSuccessAt).toISOString() : null,
      lastError: state.lastError,
    }, null, 2));
    return;
  }
  if (url.pathname === '/metrics') {
    res.writeHead(200, { 'Content-Type': 'text/plain; version=0.0.4; charset=utf-8', 'Cache-Control': 'no-store' });
    res.end(emitMetrics());
    return;
  }
  res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
  res.end('Not found');
});

runNetcheck();
setInterval(runNetcheck, Math.max(5_000, INTERVAL_MS));

server.listen(PORT, '0.0.0.0', () => {
  console.log(new Date().toISOString(), `derp latency exporter listening on :${PORT}`);
});
