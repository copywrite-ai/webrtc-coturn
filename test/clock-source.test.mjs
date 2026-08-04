import assert from 'node:assert/strict';
import test from 'node:test';

import {
  deriveClockSyncEndpoint,
  resolveClockSyncEndpoint,
} from '../public/vendor/ortm/clock-source.js';

test('derives clock sync from the WHEP publisher origin', () => {
  assert.equal(
    deriveClockSyncEndpoint(
      'https://peng-ubuntu.li-adder.ts.net/fish_front/whep',
      'https://peng-mbp14.li-adder.ts.net:8889/whep-quad-direct.html',
    ),
    'https://peng-ubuntu.li-adder.ts.net/api/clock-sync',
  );
});

test('preserves an explicit WHEP origin port', () => {
  assert.equal(
    deriveClockSyncEndpoint(
      'http://127.0.0.1:9001/fish_front/whep',
      'https://viewer.example/',
    ),
    'http://127.0.0.1:9001/api/clock-sync',
  );
});

test('maps a media proxy device path to its Tailscale clock endpoint', () => {
  assert.equal(
    deriveClockSyncEndpoint(
      'https://media-proxy.nexusdot.cn/noproxy/peng-ubuntu/whep',
      'https://peng-mbp14.li-adder.ts.net/whep-single-direct.html',
    ),
    'https://peng-ubuntu.li-adder.ts.net:8889/api/clock-sync',
  );
});

test('does not map an invalid media proxy device name', () => {
  assert.equal(
    deriveClockSyncEndpoint(
      'https://media-proxy.nexusdot.cn/noproxy/not%20a%20host/whep',
      'https://viewer.example/',
    ),
    'https://media-proxy.nexusdot.cn/api/clock-sync',
  );
});

test('supports an explicit endpoint override and off switch', () => {
  assert.equal(
    resolveClockSyncEndpoint('https://publisher.example/fish/whep', {
      override: '/publisher-clock',
      baseUrl: 'https://viewer.example/app',
    }),
    'https://viewer.example/publisher-clock',
  );
  assert.equal(
    resolveClockSyncEndpoint('https://publisher.example/fish/whep', {
      override: 'off',
      baseUrl: 'https://viewer.example/app',
    }),
    null,
  );
});
