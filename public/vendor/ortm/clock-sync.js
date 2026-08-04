const DEFAULT_SAMPLE_COUNT = 12;
const DEFAULT_SAMPLE_GAP_MS = 25;
const DEFAULT_TIMEOUT_MS = 2000;

function finiteNumber(value, name) {
  const number = Number(value);
  if (!Number.isFinite(number)) throw new TypeError(`${name} must be finite`);
  return number;
}

function sleep(ms) {
  return new Promise((resolve) => setTimeout(resolve, ms));
}

export function calculateClockSample({
  clientSendUnixMs,
  clientReceiveUnixMs,
  clientElapsedMs,
  serverReceiveUnixMs,
  serverSendUnixMs,
}) {
  const t1 = finiteNumber(clientSendUnixMs, 'clientSendUnixMs');
  const t2 = finiteNumber(serverReceiveUnixMs, 'serverReceiveUnixMs');
  const t3 = finiteNumber(serverSendUnixMs, 'serverSendUnixMs');
  const t4 = finiteNumber(clientReceiveUnixMs, 'clientReceiveUnixMs');
  const serverProcessingMs = Math.max(0, t3 - t2);
  const wallElapsedMs = Math.max(0, t4 - t1);
  const measuredElapsedMs = Number.isFinite(Number(clientElapsedMs))
    ? Math.max(0, Number(clientElapsedMs))
    : wallElapsedMs;
  const rttMs = Math.max(0, measuredElapsedMs - serverProcessingMs);
  const offsetMs = ((t2 - t1) + (t3 - t4)) / 2;
  return {
    offsetMs,
    rttMs,
    serverProcessingMs,
    clientSendUnixMs: t1,
    clientReceiveUnixMs: t4,
    serverReceiveUnixMs: t2,
    serverSendUnixMs: t3,
  };
}

export function selectClockEstimate(samples, nowUnixMs = Date.now()) {
  const valid = (samples || [])
    .filter((sample) => Number.isFinite(sample?.offsetMs) && Number.isFinite(sample?.rttMs))
    .sort((left, right) => left.rttMs - right.rttMs);
  if (!valid.length) throw new Error('no valid clock samples');

  const best = valid[0];
  const lowDelay = valid.slice(0, Math.min(5, valid.length));
  const lowOffsets = lowDelay.map((sample) => sample.offsetMs);
  const offsetSpreadMs = (Math.max(...lowOffsets) - Math.min(...lowOffsets)) / 2;
  return {
    ok: true,
    offsetMs: best.offsetMs,
    rttMs: best.rttMs,
    uncertaintyMs: best.rttMs / 2 + offsetSpreadMs,
    offsetSpreadMs,
    sampleCount: valid.length,
    selectedSample: best,
    measuredAtUnixMs: Number(nowUnixMs),
  };
}

export class ClockSynchronizer {
  constructor(endpoint, options = {}) {
    this.endpoint = endpoint;
    this.sampleCount = options.sampleCount ?? DEFAULT_SAMPLE_COUNT;
    this.sampleGapMs = options.sampleGapMs ?? DEFAULT_SAMPLE_GAP_MS;
    this.timeoutMs = options.timeoutMs ?? DEFAULT_TIMEOUT_MS;
    this.fetchImpl = options.fetchImpl ?? globalThis.fetch?.bind(globalThis);
    this.estimate = null;
    if (!this.fetchImpl) throw new Error('fetch is unavailable');
  }

  async sample(index = 0) {
    const url = new URL(this.endpoint, globalThis.location?.href || 'http://localhost/');
    url.searchParams.set('requestId', `${Date.now()}-${index}`);
    url.searchParams.set('_', String(Date.now()));
    const controller = new AbortController();
    const timeout = setTimeout(() => controller.abort(), this.timeoutMs);
    const clientSendUnixMs = Date.now();
    const clientSendPerfMs = performance.now();
    try {
      const response = await this.fetchImpl(url, {
        method: 'GET',
        cache: 'no-store',
        credentials: 'omit',
        signal: controller.signal,
      });
      const payload = await response.json();
      const clientReceivePerfMs = performance.now();
      const clientReceiveUnixMs = Date.now();
      if (!response.ok || payload?.ok !== true) {
        throw new Error(`clock endpoint returned ${response.status}`);
      }
      const serverReceiveUnixMs =
        payload.serverReceiveUnixMs ??
        payload.server_receive_ms ??
        payload.server_time_ms ??
        payload.unix_ms;
      const serverSendUnixMs =
        payload.serverSendUnixMs ??
        payload.server_transmit_ms ??
        payload.server_time_ms ??
        payload.unix_ms;
      return calculateClockSample({
        clientSendUnixMs,
        clientReceiveUnixMs,
        clientElapsedMs: clientReceivePerfMs - clientSendPerfMs,
        serverReceiveUnixMs,
        serverSendUnixMs,
      });
    } finally {
      clearTimeout(timeout);
    }
  }

  async synchronize() {
    const samples = [];
    const errors = [];
    for (let index = 0; index < this.sampleCount; index += 1) {
      try {
        samples.push(await this.sample(index));
      } catch (error) {
        errors.push(error);
      }
      if (index + 1 < this.sampleCount && this.sampleGapMs > 0) {
        await sleep(this.sampleGapMs);
      }
    }
    if (!samples.length) {
      throw errors.at(-1) || new Error('clock synchronization failed');
    }
    this.estimate = selectClockEstimate(samples);
    return this.estimate;
  }

  correctedUnixMs(clientUnixMs = Date.now()) {
    return Number(clientUnixMs) + (this.estimate?.offsetMs || 0);
  }
}
