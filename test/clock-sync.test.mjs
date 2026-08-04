import assert from 'node:assert/strict';
import test from 'node:test';

import {
  ClockSynchronizer,
  calculateClockSample,
  selectClockEstimate,
} from '../public/vendor/ortm/clock-sync.js';

test('calculates NTP offset and network RTT', () => {
  const sample = calculateClockSample({
    clientSendUnixMs: 1000,
    serverReceiveUnixMs: 1055,
    serverSendUnixMs: 1057,
    clientReceiveUnixMs: 1022,
    clientElapsedMs: 22,
  });
  assert.equal(sample.offsetMs, 45);
  assert.equal(sample.rttMs, 20);
  assert.equal(sample.serverProcessingMs, 2);
});

test('selects the minimum RTT sample', () => {
  const estimate = selectClockEstimate([
    { offsetMs: -191, rttMs: 12 },
    { offsetMs: -200, rttMs: 3 },
    { offsetMs: -198, rttMs: 5 },
  ], 1234);
  assert.equal(estimate.offsetMs, -200);
  assert.equal(estimate.rttMs, 3);
  assert.equal(estimate.sampleCount, 3);
  assert.equal(estimate.measuredAtUnixMs, 1234);
  assert.ok(estimate.uncertaintyMs >= 1.5);
});

test('rejects an empty estimate', () => {
  assert.throws(() => selectClockEstimate([]), /no valid clock samples/);
});

test('accepts Carla clock-sync snake_case timestamps', async () => {
  const synchronizer = new ClockSynchronizer('https://publisher.example/api/clock-sync', {
    sampleCount: 1,
    sampleGapMs: 0,
    fetchImpl: async () => ({
      ok: true,
      status: 200,
      json: async () => ({
        ok: true,
        server_receive_ms: 1_785_295_465_625,
        server_transmit_ms: 1_785_295_465_626,
      }),
    }),
  });

  const sample = await synchronizer.sample();
  assert.equal(sample.serverReceiveUnixMs, 1_785_295_465_625);
  assert.equal(sample.serverSendUnixMs, 1_785_295_465_626);
  assert.ok(Number.isFinite(sample.offsetMs));
});
