import { createServer, request as httpRequest } from 'node:http';
import { readFile } from 'node:fs/promises';
import { existsSync } from 'node:fs';
import { join } from 'node:path';

const PORT = Number(process.env.MONITOR_PORT || process.env.PORT || 9010);
const PUBLIC_DIR = join(process.cwd(), 'public');
const CLIENT_LOG_PATH = process.env.CLIENT_LOG_PATH || join(process.cwd(), 'logs/client-events.log');
const MEDIAMTX_API_ORIGIN = process.env.MEDIAMTX_API_ORIGIN || 'http://mediamtx:9997';
const MEDIAMTX_METRICS_ORIGIN = process.env.MEDIAMTX_METRICS_ORIGIN || 'http://mediamtx:9998';
const MEDIAMTX_USERNAME = process.env.MEDIAMTX_USERNAME || 'metrics';
const MEDIAMTX_PASSWORD = process.env.MEDIAMTX_PASSWORD || 'metrics123';
const DOCKER_SOCKET_PATH = process.env.DOCKER_SOCKET_PATH || '/var/run/docker.sock';
const PUBLISHER_CONTAINERS = splitCsv(process.env.PUBLISHER_CONTAINERS || [
  'tunnel-fish_front_whip-1',
  'tunnel-fish_back_whip-1',
  'tunnel-fish_left_whip-1',
  'tunnel-fish_right_whip-1',
].join(','));
const STREAMS = splitCsv(process.env.MONITOR_STREAMS || 'fish_front,fish_back,fish_left,fish_right');
const POLL_MS = Number(process.env.MONITOR_POLL_MS || 2000);
const HISTORY_LIMIT = Number(process.env.MONITOR_HISTORY_LIMIT || 900);
const CLIENT_LOG_TAIL_BYTES = Number(process.env.CLIENT_LOG_TAIL_BYTES || 2 * 1024 * 1024);

const state = {
  startedAt: new Date().toISOString(),
  updatedAt: null,
  errors: [],
  mediamtx: {
    apiOk: false,
    metricsOk: false,
    paths: {},
    metricNames: [],
    summary: {},
  },
  viewer: {
    latestBySlot: {},
    historyBySlot: {},
    activePeers: [],
  },
  publisher: {
    dockerSocketAvailable: false,
    latestByStream: {},
  },
  derived: {
    alerts: [],
    streams: {},
  },
};

function splitCsv(value) {
  return String(value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function pushError(source, error) {
  state.errors.push({
    ts: new Date().toISOString(),
    source,
    error: error?.message || String(error),
  });
  state.errors = state.errors.slice(-50);
}

function basicAuthHeader() {
  return `Basic ${Buffer.from(`${MEDIAMTX_USERNAME}:${MEDIAMTX_PASSWORD}`).toString('base64')}`;
}

function fetchText(url, headers = {}) {
  return new Promise((resolve, reject) => {
    const req = httpRequest(url, { headers }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        const body = Buffer.concat(chunks).toString('utf8');
        if ((res.statusCode || 500) >= 400) {
          reject(new Error(`${url} returned ${res.statusCode}: ${body.slice(0, 200)}`));
          return;
        }
        resolve(body);
      });
    });
    req.on('error', reject);
    req.setTimeout(3000, () => req.destroy(new Error(`timeout fetching ${url}`)));
    req.end();
  });
}

function dockerGet(path) {
  return new Promise((resolve, reject) => {
    const req = httpRequest({ socketPath: DOCKER_SOCKET_PATH, path, method: 'GET' }, (res) => {
      const chunks = [];
      res.on('data', (chunk) => chunks.push(chunk));
      res.on('end', () => {
        if ((res.statusCode || 500) >= 400) {
          reject(new Error(`docker ${path} returned ${res.statusCode}`));
          return;
        }
        resolve(Buffer.concat(chunks));
      });
    });
    req.on('error', reject);
    req.setTimeout(3000, () => req.destroy(new Error(`timeout reading docker ${path}`)));
    req.end();
  });
}

function parsePrometheus(text) {
  const metrics = {};
  for (const rawLine of String(text || '').split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) {
      continue;
    }
    const match = line.match(/^([a-zA-Z_:][a-zA-Z0-9_:]*)(?:\{([^}]*)\})?\s+(-?(?:\d+(?:\.\d*)?|\.\d+)(?:e[+-]?\d+)?|NaN|\+Inf|-Inf)$/);
    if (!match) {
      continue;
    }
    const [, name, labelText = '', valueText] = match;
    const labels = {};
    for (const labelMatch of labelText.matchAll(/([a-zA-Z_][a-zA-Z0-9_]*)="((?:\\"|[^"])*)"/g)) {
      labels[labelMatch[1]] = labelMatch[2].replace(/\\"/g, '"');
    }
    if (!metrics[name]) {
      metrics[name] = [];
    }
    metrics[name].push({ labels, value: Number(valueText) });
  }
  return metrics;
}

function metricSum(metrics, name, predicate = () => true) {
  return (metrics[name] || [])
    .filter((sample) => Number.isFinite(sample.value) && predicate(sample.labels))
    .reduce((sum, sample) => sum + sample.value, 0);
}

function firstMetric(metrics, names) {
  for (const name of names) {
    if (metrics[name]) {
      return metrics[name];
    }
  }
  return [];
}

function metricSumAny(metrics, names, predicate = () => true) {
  return firstMetric(metrics, names)
    .filter((sample) => Number.isFinite(sample.value) && predicate(sample.labels))
    .reduce((sum, sample) => sum + sample.value, 0);
}

function metricSeriesByPath(metrics, name) {
  const byPath = {};
  for (const sample of firstMetric(metrics, Array.isArray(name) ? name : [name])) {
    const path = sample.labels.path || sample.labels.name || sample.labels.id || 'unknown';
    byPath[path] = (byPath[path] || 0) + sample.value;
  }
  return byPath;
}

async function collectMediaMTX() {
  const headers = { Authorization: basicAuthHeader() };
  try {
    const body = await fetchText(`${MEDIAMTX_API_ORIGIN}/v3/paths/list`, headers);
    const data = JSON.parse(body);
    const paths = {};
    for (const item of data.items || []) {
      paths[item.name] = {
        ready: Boolean(item.ready),
        readyTime: item.readyTime || null,
        sourceType: item.source?.type || item.sourceType || null,
        tracks: item.tracks || [],
        bytesReceived: item.bytesReceived ?? null,
        readers: Array.isArray(item.readers) ? item.readers.length : Object.keys(item.readers || {}).length,
        rawReaders: item.readers || {},
      };
    }
    state.mediamtx.apiOk = true;
    state.mediamtx.paths = paths;
  } catch (error) {
    state.mediamtx.apiOk = false;
    pushError('mediamtx-api', error);
  }

  try {
    const body = await fetchText(`${MEDIAMTX_METRICS_ORIGIN}/metrics`, headers);
    const metrics = parsePrometheus(body);
    state.mediamtx.metricsOk = true;
    state.mediamtx.metricNames = Object.keys(metrics).sort();
    state.mediamtx.summary = {
      paths: metricSumAny(metrics, ['paths']),
      pathsReady: metricSumAny(metrics, ['paths'], (labels) => labels.state === 'ready'),
      bytesReceived: metricSeriesByPath(metrics, ['paths_bytes_received', 'paths_inbound_bytes']),
      bytesSent: metricSeriesByPath(metrics, ['paths_bytes_sent', 'paths_outbound_bytes']),
      readers: metricSeriesByPath(metrics, ['paths_readers']),
      rtpPacketsLost: metricSeriesByPath(metrics, [
        'webrtc_sessions_inbound_rtp_packets_lost',
        'rtsp_sessions_rtp_packets_lost',
      ]),
      inboundFramesInError: metricSeriesByPath(metrics, ['paths_inbound_frames_in_error']),
      outboundFramesDiscarded: metricSeriesByPath(metrics, [
        'webrtc_sessions_outbound_rtp_packets_discarded',
        'rtsp_sessions_outbound_rtp_packets_discarded',
      ]),
    };
  } catch (error) {
    state.mediamtx.metricsOk = false;
    pushError('mediamtx-metrics', error);
  }
}

function parseKeyValueMessage(message) {
  const data = {};
  for (const match of String(message || '').matchAll(/([a-zA-Z][a-zA-Z0-9_]*)=([^ ]+)/g)) {
    const key = match[1];
    const rawValue = match[2];
    const normalized = rawValue === '--' ? null : rawValue;
    const numeric = normalized == null ? null : Number(String(normalized).replace(/(?:ms|kbps)$/, ''));
    data[key] = Number.isFinite(numeric) ? numeric : normalized;
  }
  return data;
}

async function readTail(path, maxBytes) {
  const file = await readFile(path);
  return file.subarray(Math.max(0, file.length - maxBytes)).toString('utf8');
}

async function collectViewerLogs() {
  if (!existsSync(CLIENT_LOG_PATH)) {
    state.viewer.latestBySlot = {};
    state.viewer.historyBySlot = {};
    return;
  }
  try {
    const text = await readTail(CLIENT_LOG_PATH, CLIENT_LOG_TAIL_BYTES);
    const latestBySlot = {};
    const historyBySlot = {};
    const peerMap = new Map();
    for (const line of text.split(/\r?\n/)) {
      if (!line.trim()) {
        continue;
      }
      let entry;
      try {
        entry = JSON.parse(line);
      } catch {
        continue;
      }
      if (entry.peerId) {
        peerMap.set(entry.peerId, {
          peerId: entry.peerId,
          remote: entry.remote,
          page: entry.page,
          lastSeen: entry.ts,
        });
      }
      if (entry.group !== 'metrics') {
        continue;
      }
      const parsed = parseKeyValueMessage(entry.message);
      const slot = String(parsed.slot || '').toLowerCase();
      if (!slot) {
        continue;
      }
      const sample = {
        ts: entry.ts,
        remote: entry.remote,
        page: entry.page,
        peerId: entry.peerId,
        ...parsed,
      };
      latestBySlot[slot] = sample;
      if (!historyBySlot[slot]) {
        historyBySlot[slot] = [];
      }
      historyBySlot[slot].push(sample);
      historyBySlot[slot] = historyBySlot[slot].slice(-HISTORY_LIMIT);
    }
    state.viewer.latestBySlot = latestBySlot;
    state.viewer.historyBySlot = historyBySlot;
    state.viewer.activePeers = Array.from(peerMap.values()).slice(-20);
  } catch (error) {
    pushError('viewer-log', error);
  }
}

function stripDockerLogHeaders(buffer) {
  const chunks = [];
  let offset = 0;
  while (offset + 8 <= buffer.length) {
    const streamType = buffer[offset];
    const size = buffer.readUInt32BE(offset + 4);
    if ((streamType === 1 || streamType === 2) && size >= 0 && offset + 8 + size <= buffer.length) {
      chunks.push(buffer.subarray(offset + 8, offset + 8 + size));
      offset += 8 + size;
      continue;
    }
    return buffer.toString('utf8');
  }
  return Buffer.concat(chunks).toString('utf8');
}

function streamFromContainer(container) {
  const match = String(container || '').match(/fish_(front|back|left|right|front_remote)_whip/);
  if (!match) {
    return null;
  }
  if (match[1] === 'front_remote') {
    return 'fish_front';
  }
  return `fish_${match[1]}`;
}

function parsePublisherMetricLine(line, fallbackStream) {
  if (line.includes('FRAME encoded_size_bytes')) {
    const frame = parseKeyValueMessage(line);
    return {
      kind: 'frame',
      stream: frame.stream || fallbackStream,
      encodedFrameSizeAvgBytes: frame.avg,
      encodedFrameSizeMinBytes: frame.min,
      encodedFrameSizeMaxBytes: frame.max,
      encodedFrameSizeLastBytes: frame.last,
      encodedFrameSamples: frame.samples,
      keyframes: frame.keyframes,
      deltaFrames: frame.delta,
      keyframeLastBytes: frame.keyframe_last,
      keyframeAvgBytes: frame.keyframe_avg,
      keyframeMaxBytes: frame.keyframe_max,
      deltaFrameAvgBytes: frame.delta_avg,
      deltaFrameMaxBytes: frame.delta_max,
      encodedBitrateKbps: frame.bitrate_kbps,
      width: frame.width,
      height: frame.height,
      fps: frame.fps,
      targetBitrateKbps: frame.target_bitrate_kbps,
      keyInt: frame.key_int,
    };
  }
  const sender = line.match(/SENDER frame (?:stream=([^ ]+) )?seq=(\d+) timestamp_ms=(\d+)(?: render_ms=([0-9.]+))? overlay_to_send_ms=([0-9.]+)(?: encoded_to_send_ms=([0-9.-]+))? sender_pipeline_ms=([0-9.]+)/);
  if (sender) {
    return {
      kind: 'sender',
      stream: sender[1] || fallbackStream,
      frameSeq: Number(sender[2]),
      timestampMs: Number(sender[3]),
      renderMs: sender[4] == null ? null : Number(sender[4]),
      overlayToSendMs: Number(sender[5]),
      encodedToSendMs: sender[6] == null ? null : Number(sender[6]),
      senderPipelineMs: Number(sender[7]),
    };
  }
  const render = line.match(/ORTM render_ms (?:stream=([^ ]+) )?(?:(?:count=(\d+) last=([0-9.]+) avg=([0-9.]+) min=([0-9.]+) max=([0-9.]+))|(?:avg=([0-9.]+) min=([0-9.]+) max=([0-9.]+) samples=(\d+)))/);
  if (render) {
    return {
      kind: 'ortm-render',
      stream: render[1] || fallbackStream,
      count: Number(render[2] || render[10]),
      renderLastMs: render[3] == null ? null : Number(render[3]),
      renderAvgMs: Number(render[4] || render[7]),
      renderMinMs: Number(render[5] || render[8]),
      renderMaxMs: Number(render[6] || render[9]),
    };
  }
  const pipe = line.match(/PIPELINE overlay_to_send_ms (?:stream=([^ ]+) )?(?:(?:count=(\d+) last=([0-9.]+) avg=([0-9.]+) min=([0-9.]+) max=([0-9.]+))|(?:avg=([0-9.]+) min=([0-9.]+) max=([0-9.]+) samples=(\d+)(?: dropped_pts=(\d+))?))/);
  if (pipe) {
    return {
      kind: 'pipeline',
      stream: pipe[1] || fallbackStream,
      count: Number(pipe[2] || pipe[10]),
      overlayToSendLastMs: pipe[3] == null ? null : Number(pipe[3]),
      overlayToSendAvgMs: Number(pipe[4] || pipe[7]),
      overlayToSendMinMs: Number(pipe[5] || pipe[8]),
      overlayToSendMaxMs: Number(pipe[6] || pipe[9]),
      droppedPts: pipe[11] == null ? null : Number(pipe[11]),
    };
  }
  const pipeMetric = line.match(/PIPELINE (overlay_to_encoder_ms|encoder_ms|parse_ms|encoded_to_send_ms|overlay_to_send_ms) avg=([0-9.]+) min=([0-9.]+) max=([0-9.]+) samples=(\d+)/);
  if (pipeMetric) {
    const metricName = pipeMetric[1].replace(/_([a-z])/g, (_match, letter) => letter.toUpperCase());
    return {
      kind: 'pipeline',
      stream: fallbackStream,
      [`${metricName}Avg`]: Number(pipeMetric[2]),
      [`${metricName}Min`]: Number(pipeMetric[3]),
      [`${metricName}Max`]: Number(pipeMetric[4]),
      [`${metricName}Samples`]: Number(pipeMetric[5]),
    };
  }
  const dropped = line.match(/PIPELINE dropped_pts value=(\d+)/);
  if (dropped) {
    return {
      kind: 'pipeline',
      stream: fallbackStream,
      droppedPts: Number(dropped[1]),
    };
  }
  return null;
}

async function collectPublisherLogs() {
  if (!existsSync(DOCKER_SOCKET_PATH)) {
    state.publisher.dockerSocketAvailable = false;
    return;
  }
  state.publisher.dockerSocketAvailable = true;
  const latestByStream = { ...state.publisher.latestByStream };
  const since = Math.floor(Date.now() / 1000) - 120;
  for (const container of PUBLISHER_CONTAINERS) {
    try {
      const encoded = encodeURIComponent(container);
      const buffer = await dockerGet(`/containers/${encoded}/logs?stdout=1&stderr=1&tail=300&since=${since}`);
      const text = stripDockerLogHeaders(buffer);
      const fallbackStream = streamFromContainer(container);
      for (const line of text.split(/\r?\n/)) {
        const metric = parsePublisherMetricLine(line, fallbackStream);
        if (!metric?.stream) {
          continue;
        }
        latestByStream[metric.stream] = {
          ...(latestByStream[metric.stream] || {}),
          container,
          updatedAt: new Date().toISOString(),
          ...metric,
        };
      }
    } catch (error) {
      if (String(error?.message || '').includes(' returned 404')) {
        continue;
      }
      pushError(`publisher-log:${container}`, error);
    }
  }
  state.publisher.latestByStream = latestByStream;
}

function streamToSlot(stream) {
  return String(stream || '').replace(/^fish_/, '');
}

function computeDerived() {
  const alerts = [];
  const streams = {};
  for (const stream of STREAMS) {
    const slot = streamToSlot(stream);
    const path = state.mediamtx.paths[stream] || {};
    const viewer = state.viewer.latestBySlot[slot] || {};
    const publisher = state.publisher.latestByStream[stream] || {};
    const sent = Number(state.mediamtx.summary.bytesSent?.[stream] || 0);
    const received = Number(state.mediamtx.summary.bytesReceived?.[stream] || 0);
    const lost = Number(state.mediamtx.summary.rtpPacketsLost?.[stream] || 0);
    const discarded = Number(state.mediamtx.summary.outboundFramesDiscarded?.[stream] || 0);
    const inboundError = Number(state.mediamtx.summary.inboundFramesInError?.[stream] || 0);
    const item = {
      stream,
      slot,
      ready: Boolean(path.ready),
      readers: Number(path.readers || state.mediamtx.summary.readers?.[stream] || 0),
      bytesReceived: received,
      bytesSent: sent,
      rtpPacketsLost: lost,
      outboundFramesDiscarded: discarded,
      inboundFramesInError: inboundError,
      viewer,
      publisher,
    };
    if (!item.ready) {
      alerts.push({ level: 'warn', stream, message: 'MediaMTX path is not ready' });
    }
    if (viewer.status === 'playing' && Number(viewer.ortm) > 300) {
      alerts.push({ level: 'warn', stream, message: `ORTM high: ${viewer.ortm} ms` });
    }
    if (viewer.status === 'playing' && Number(viewer.upstream) > 200) {
      alerts.push({ level: 'warn', stream, message: `Upstream high: ${viewer.upstream} ms` });
    }
    if (lost > 0 || discarded > 0 || inboundError > 0) {
      alerts.push({ level: 'warn', stream, message: `Media errors lost=${lost} discarded=${discarded} inbound=${inboundError}` });
    }
    streams[stream] = item;
  }
  state.derived = { alerts, streams };
}

async function collectAll() {
  await Promise.all([
    collectMediaMTX(),
    collectViewerLogs(),
    collectPublisherLogs(),
  ]);
  computeDerived();
  state.updatedAt = new Date().toISOString();
}

function prometheusSnapshot() {
  const lines = [
    '# HELP tunnel_monitor_up Monitor collector health.',
    '# TYPE tunnel_monitor_up gauge',
    'tunnel_monitor_up 1',
  ];
  for (const [slot, viewer] of Object.entries(state.viewer.latestBySlot)) {
    const label = `slot="${prometheusLabelValue(slot)}",page="${prometheusLabelValue(viewer.page || '')}"`;
    if (isMetricNumber(viewer.ortm)) {
      lines.push(`tunnel_viewer_slot_ortm_ms{${label}} ${Number(viewer.ortm)}`);
    }
    if (isMetricNumber(viewer.ortmNet)) {
      lines.push(`tunnel_viewer_slot_ortm_net_ms{${label}} ${Number(viewer.ortmNet)}`);
    }
    if (isMetricNumber(viewer.fallback)) {
      lines.push(`tunnel_viewer_slot_fallback_ms{${label}} ${Number(viewer.fallback)}`);
    }
    if (isMetricNumber(viewer.upstream)) {
      lines.push(`tunnel_viewer_slot_upstream_ms{${label}} ${Number(viewer.upstream)}`);
    }
    if (isMetricNumber(viewer.upstreamNet)) {
      lines.push(`tunnel_viewer_slot_upstream_net_ms{${label}} ${Number(viewer.upstreamNet)}`);
    }
    if (isMetricNumber(viewer.browser)) {
      lines.push(`tunnel_viewer_slot_browser_cost_ms{${label}} ${Number(viewer.browser)}`);
    }
    if (isMetricNumber(viewer.decode)) {
      lines.push(`tunnel_viewer_slot_decode_cost_ms{${label}} ${Number(viewer.decode)}`);
    }
    if (isMetricNumber(viewer.displayTap)) {
      lines.push(`tunnel_viewer_slot_display_tap_ms{${label}} ${Number(viewer.displayTap)}`);
    }
    if (isMetricNumber(viewer.displaySubmit)) {
      lines.push(`tunnel_viewer_slot_display_submit_ms{${label}} ${Number(viewer.displaySubmit)}`);
    }
    if (isMetricNumber(viewer.rtcJitter)) {
      lines.push(`tunnel_viewer_slot_rtc_jitter_buffer_ms{${label}} ${Number(viewer.rtcJitter)}`);
    }
    if (isMetricNumber(viewer.rtcDecode)) {
      lines.push(`tunnel_viewer_slot_rtc_decode_ms{${label}} ${Number(viewer.rtcDecode)}`);
    }
    if (isMetricNumber(viewer.rtcFps)) {
      lines.push(`tunnel_viewer_slot_rtc_fps{${label}} ${Number(viewer.rtcFps)}`);
    }
    if (isMetricNumber(viewer.rtcDrop)) {
      lines.push(`tunnel_viewer_slot_rtc_frames_dropped{${label}} ${Number(viewer.rtcDrop)}`);
    }
    if (isMetricNumber(viewer.rtcBitrate)) {
      lines.push(`tunnel_viewer_slot_rtc_bitrate_kbps{${label}} ${Number(viewer.rtcBitrate)}`);
    }
    if (isMetricNumber(viewer.rtcPacketsLost)) {
      lines.push(`tunnel_viewer_slot_rtc_packets_lost{${label}} ${Number(viewer.rtcPacketsLost)}`);
    }
  }
  for (const [stream, item] of Object.entries(state.derived.streams)) {
    const label = `stream="${stream}"`;
    lines.push(`tunnel_stream_ready{${label}} ${item.ready ? 1 : 0}`);
    lines.push(`tunnel_stream_readers{${label}} ${item.readers || 0}`);
    lines.push(`tunnel_stream_bytes_received{${label}} ${item.bytesReceived || 0}`);
    lines.push(`tunnel_stream_bytes_sent{${label}} ${item.bytesSent || 0}`);
    lines.push(`tunnel_stream_rtp_packets_lost{${label}} ${item.rtpPacketsLost || 0}`);
    if (isMetricNumber(item.viewer.ortm)) {
      lines.push(`tunnel_viewer_ortm_ms{${label}} ${Number(item.viewer.ortm)}`);
    }
    if (isMetricNumber(item.viewer.upstream)) {
      lines.push(`tunnel_viewer_upstream_ms{${label}} ${Number(item.viewer.upstream)}`);
    }
    if (isMetricNumber(item.viewer.fallback)) {
      lines.push(`tunnel_viewer_fallback_ms{${label}} ${Number(item.viewer.fallback)}`);
    }
    if (isMetricNumber(item.viewer.browser)) {
      lines.push(`tunnel_viewer_browser_cost_ms{${label}} ${Number(item.viewer.browser)}`);
    }
    if (isMetricNumber(item.viewer.rtcJitter)) {
      lines.push(`tunnel_viewer_rtc_jitter_buffer_ms{${label}} ${Number(item.viewer.rtcJitter)}`);
    }
    if (isMetricNumber(item.viewer.rtcDecode)) {
      lines.push(`tunnel_viewer_rtc_decode_ms{${label}} ${Number(item.viewer.rtcDecode)}`);
    }
    if (isMetricNumber(item.viewer.rtcFps)) {
      lines.push(`tunnel_viewer_rtc_fps{${label}} ${Number(item.viewer.rtcFps)}`);
    }
    if (isMetricNumber(item.viewer.rtcDrop)) {
      lines.push(`tunnel_viewer_rtc_frames_dropped{${label}} ${Number(item.viewer.rtcDrop)}`);
    }
    if (isMetricNumber(item.viewer.rtcBitrate)) {
      lines.push(`tunnel_viewer_rtc_bitrate_kbps{${label}} ${Number(item.viewer.rtcBitrate)}`);
    }
    if (isMetricNumber(item.viewer.rtcPacketsLost)) {
      lines.push(`tunnel_viewer_rtc_packets_lost{${label}} ${Number(item.viewer.rtcPacketsLost)}`);
    }
    if (isMetricNumber(item.publisher.overlayToSendMs)) {
      lines.push(`tunnel_publisher_overlay_to_send_ms{${label}} ${Number(item.publisher.overlayToSendMs)}`);
    }
    if (isMetricNumber(item.publisher.overlayToSendMsAvg)) {
      lines.push(`tunnel_publisher_overlay_to_send_avg_ms{${label}} ${Number(item.publisher.overlayToSendMsAvg)}`);
    }
    if (isMetricNumber(item.publisher.overlayToSendMsMax)) {
      lines.push(`tunnel_publisher_overlay_to_send_max_ms{${label}} ${Number(item.publisher.overlayToSendMsMax)}`);
    }
    if (isMetricNumber(item.publisher.overlayToEncoderMsAvg)) {
      lines.push(`tunnel_publisher_overlay_to_encoder_avg_ms{${label}} ${Number(item.publisher.overlayToEncoderMsAvg)}`);
    }
    if (isMetricNumber(item.publisher.encoderMsAvg)) {
      lines.push(`tunnel_publisher_encoder_avg_ms{${label}} ${Number(item.publisher.encoderMsAvg)}`);
    }
    if (isMetricNumber(item.publisher.parseMsAvg)) {
      lines.push(`tunnel_publisher_parse_avg_ms{${label}} ${Number(item.publisher.parseMsAvg)}`);
    }
    if (isMetricNumber(item.publisher.encodedToSendMs)) {
      lines.push(`tunnel_publisher_encoded_to_send_ms{${label}} ${Number(item.publisher.encodedToSendMs)}`);
    }
    if (isMetricNumber(item.publisher.encodedToSendMsAvg)) {
      lines.push(`tunnel_publisher_encoded_to_send_avg_ms{${label}} ${Number(item.publisher.encodedToSendMsAvg)}`);
    }
    if (isMetricNumber(item.publisher.senderPipelineMs)) {
      lines.push(`tunnel_publisher_sender_pipeline_ms{${label}} ${Number(item.publisher.senderPipelineMs)}`);
    }
    if (isMetricNumber(item.publisher.droppedPts)) {
      lines.push(`tunnel_publisher_dropped_pts{${label}} ${Number(item.publisher.droppedPts)}`);
    }
    if (isMetricNumber(item.publisher.renderAvgMs)) {
      lines.push(`tunnel_publisher_ortm_render_avg_ms{${label}} ${Number(item.publisher.renderAvgMs)}`);
    }
    if (isMetricNumber(item.publisher.renderMs)) {
      lines.push(`tunnel_publisher_ortm_render_ms{${label}} ${Number(item.publisher.renderMs)}`);
    }
    if (isMetricNumber(item.publisher.encodedFrameSizeLastBytes)) {
      lines.push(`tunnel_publisher_encoded_frame_size_last_bytes{${label}} ${Number(item.publisher.encodedFrameSizeLastBytes)}`);
    }
    if (isMetricNumber(item.publisher.encodedFrameSizeAvgBytes)) {
      lines.push(`tunnel_publisher_encoded_frame_size_avg_bytes{${label}} ${Number(item.publisher.encodedFrameSizeAvgBytes)}`);
    }
    if (isMetricNumber(item.publisher.encodedFrameSizeMinBytes)) {
      lines.push(`tunnel_publisher_encoded_frame_size_min_bytes{${label}} ${Number(item.publisher.encodedFrameSizeMinBytes)}`);
    }
    if (isMetricNumber(item.publisher.encodedFrameSizeMaxBytes)) {
      lines.push(`tunnel_publisher_encoded_frame_size_max_bytes{${label}} ${Number(item.publisher.encodedFrameSizeMaxBytes)}`);
    }
    if (isMetricNumber(item.publisher.keyframes)) {
      lines.push(`tunnel_publisher_keyframes_total{${label}} ${Number(item.publisher.keyframes)}`);
    }
    if (isMetricNumber(item.publisher.deltaFrames)) {
      lines.push(`tunnel_publisher_delta_frames_total{${label}} ${Number(item.publisher.deltaFrames)}`);
    }
    if (isMetricNumber(item.publisher.keyframeLastBytes)) {
      lines.push(`tunnel_publisher_keyframe_size_last_bytes{${label}} ${Number(item.publisher.keyframeLastBytes)}`);
    }
    if (isMetricNumber(item.publisher.keyframeAvgBytes)) {
      lines.push(`tunnel_publisher_keyframe_size_avg_bytes{${label}} ${Number(item.publisher.keyframeAvgBytes)}`);
    }
    if (isMetricNumber(item.publisher.keyframeMaxBytes)) {
      lines.push(`tunnel_publisher_keyframe_size_max_bytes{${label}} ${Number(item.publisher.keyframeMaxBytes)}`);
    }
    if (isMetricNumber(item.publisher.deltaFrameAvgBytes)) {
      lines.push(`tunnel_publisher_delta_frame_size_avg_bytes{${label}} ${Number(item.publisher.deltaFrameAvgBytes)}`);
    }
    if (isMetricNumber(item.publisher.deltaFrameMaxBytes)) {
      lines.push(`tunnel_publisher_delta_frame_size_max_bytes{${label}} ${Number(item.publisher.deltaFrameMaxBytes)}`);
    }
    if (isMetricNumber(item.publisher.encodedBitrateKbps)) {
      lines.push(`tunnel_publisher_encoded_bitrate_kbps{${label}} ${Number(item.publisher.encodedBitrateKbps)}`);
    }
    if (isMetricNumber(item.publisher.width)) {
      lines.push(`tunnel_publisher_resolution_width{${label}} ${Number(item.publisher.width)}`);
    }
    if (isMetricNumber(item.publisher.height)) {
      lines.push(`tunnel_publisher_resolution_height{${label}} ${Number(item.publisher.height)}`);
    }
    if (isMetricNumber(item.publisher.fps)) {
      lines.push(`tunnel_publisher_target_fps{${label}} ${Number(item.publisher.fps)}`);
    }
    if (isMetricNumber(item.publisher.targetBitrateKbps)) {
      lines.push(`tunnel_publisher_target_bitrate_kbps{${label}} ${Number(item.publisher.targetBitrateKbps)}`);
    }
    if (isMetricNumber(item.publisher.keyInt)) {
      lines.push(`tunnel_publisher_key_int{${label}} ${Number(item.publisher.keyInt)}`);
    }
  }
  lines.push('');
  return lines.join('\n');
}

function isMetricNumber(value) {
  return value !== null && value !== undefined && value !== '' && Number.isFinite(Number(value));
}

function prometheusLabelValue(value) {
  return String(value).replace(/\\/g, '\\\\').replace(/"/g, '\\"').replace(/\n/g, '\\n');
}

function sendJson(res, data) {
  res.writeHead(200, {
    'Content-Type': 'application/json; charset=utf-8',
    'Cache-Control': 'no-store',
  });
  res.end(JSON.stringify(data, null, 2));
}

const server = createServer(async (req, res) => {
  try {
    const url = new URL(req.url || '/', `http://127.0.0.1:${PORT}`);
    if (url.pathname === '/healthz') {
      sendJson(res, { ok: true, updatedAt: state.updatedAt });
      return;
    }
    if (url.pathname === '/api/snapshot') {
      sendJson(res, state);
      return;
    }
    if (url.pathname === '/metrics') {
      res.writeHead(200, { 'Content-Type': 'text/plain; version=0.0.4; charset=utf-8' });
      res.end(prometheusSnapshot());
      return;
    }
    const path = url.pathname === '/' ? '/monitor.html' : url.pathname;
    const filePath = join(PUBLIC_DIR, path);
    const data = await readFile(filePath);
    const contentType = filePath.endsWith('.html') ? 'text/html; charset=utf-8' : 'text/plain; charset=utf-8';
    res.writeHead(200, { 'Content-Type': contentType });
    res.end(data);
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Not found');
  }
});

await collectAll();
setInterval(() => {
  collectAll().catch((error) => pushError('collector', error));
}, POLL_MS);

server.listen(PORT, () => {
  console.log(new Date().toISOString(), `monitor listening on :${PORT}`);
});
