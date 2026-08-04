import assert from 'node:assert/strict';
import test from 'node:test';
import { validateViewerCommand } from '../viewer-control.mjs';

test('accepts a constrained configure-and-play command', () => {
  const command = validateViewerCommand({
    type: 'configure-and-play',
    whepUrl: 'https://video.example/fish_front/whep',
    ortmProfile: '720p-minimal',
    slot: 0,
    requestId: 'run-1',
  });
  assert.deepEqual(command, {
    type: 'configure-and-play',
    whepUrl: 'https://video.example/fish_front/whep',
    ortmProfile: '720p-minimal',
    slot: 0,
    requestId: 'run-1',
  });
});

test('accepts the experimental three-finder profile', () => {
  const command = validateViewerCommand({
    type: 'configure-and-play',
    whepUrl: 'https://example.test/fish_front/whep',
    ortmProfile: '720p-three-finder',
  });
  assert.equal(command.ortmProfile, '720p-three-finder');
});

test('accepts the experimental two-top profile', () => {
  const command = validateViewerCommand({
    type: 'configure-and-play',
    whepUrl: 'https://example.test/fish_front/whep',
    ortmProfile: '720p-two-top',
  });
  assert.equal(command.ortmProfile, '720p-two-top');
});

test('rejects arbitrary commands and non-http WHEP URLs', () => {
  assert.throws(() => validateViewerCommand({ type: 'eval', code: 'alert(1)' }), /unsupported command/);
  assert.throws(
    () => validateViewerCommand({
      type: 'configure-and-play',
      whepUrl: 'javascript:alert(1)',
      ortmProfile: 'default',
    }),
    /HTTP\(S\)/,
  );
});

test('rejects unknown ORTM profiles', () => {
  assert.throws(
    () => validateViewerCommand({
      type: 'configure-and-play',
      whepUrl: 'https://video.example/fish_front/whep',
      ortmProfile: 'untrusted-profile',
    }),
    /unsupported ORTM profile/,
  );
});
