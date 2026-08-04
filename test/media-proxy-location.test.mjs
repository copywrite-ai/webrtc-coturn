import assert from 'node:assert/strict';
import test from 'node:test';

import {
  mapUpstreamPathToPublic,
  relativeLocationForClient,
} from '../media-proxy-location.mjs';

test('fish root redirect preserves one public stream prefix', () => {
  assert.equal(
    mapUpstreamPathToPublic('/fish_front', '/fish_front', '/fish_front/'),
    '/fish_front/',
  );
});

test('fish WHEP session location preserves the public endpoint', () => {
  assert.equal(
    mapUpstreamPathToPublic(
      '/fish_front',
      '/fish_front',
      '/fish_front/whep/session-id',
    ),
    '/fish_front/whep/session-id',
  );
});

test('prefixed MediaMTX paths map back to an unprefixed public stream', () => {
  assert.equal(
    mapUpstreamPathToPublic(
      '/fish_front',
      '/mtx/fish_front',
      '/mtx/fish_front/',
    ),
    '/fish_front/',
  );
});

test('dynamic device paths replace the upstream mtx prefix', () => {
  assert.equal(
    mapUpstreamPathToPublic(
      '/mtx/device-a',
      '/mtx',
      '/mtx/fish_front/whep/session-id',
    ),
    '/mtx/device-a/fish_front/whep/session-id',
  );
});

test('directory redirects preserve the trailing slash', () => {
  assert.equal(
    relativeLocationForClient('/fish_front', '/fish_front/'),
    'fish_front/',
  );
});
