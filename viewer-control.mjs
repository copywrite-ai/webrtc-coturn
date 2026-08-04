import { randomUUID, timingSafeEqual } from 'node:crypto';

const COMMAND_TYPES = new Set([
  'configure-and-play',
  'get-status',
  'play',
  'reconnect',
  'stop',
]);

const ORTM_PROFILES = new Set(['default', '720p-minimal', '720p-three-finder']);

function asTrimmedString(value, maxLength = 2048) {
  return typeof value === 'string' ? value.trim().slice(0, maxLength) : '';
}

export function authorizeViewerControl(req, configuredToken) {
  if (!configuredToken) return false;
  const header = asTrimmedString(req.headers.authorization, 512);
  if (!header.startsWith('Bearer ')) return false;
  const supplied = Buffer.from(header.slice(7));
  const expected = Buffer.from(configuredToken);
  return supplied.length === expected.length && timingSafeEqual(supplied, expected);
}

export function validateViewerCommand(payload) {
  if (!payload || typeof payload !== 'object' || Array.isArray(payload)) {
    throw new Error('command must be an object');
  }

  const type = asTrimmedString(payload.type, 64);
  if (!COMMAND_TYPES.has(type)) {
    throw new Error(`unsupported command: ${type || '<empty>'}`);
  }

  const command = {
    type,
    requestId: asTrimmedString(payload.requestId, 128) || randomUUID(),
    slot: Number.isInteger(payload.slot) && payload.slot >= 0 && payload.slot <= 3
      ? payload.slot
      : 0,
  };

  if (type === 'configure-and-play') {
    const whepUrl = asTrimmedString(payload.whepUrl);
    let parsedUrl;
    try {
      parsedUrl = new URL(whepUrl);
    } catch {
      throw new Error('whepUrl must be an absolute HTTP(S) URL');
    }
    if (!['http:', 'https:'].includes(parsedUrl.protocol)) {
      throw new Error('whepUrl must use HTTP(S)');
    }
    const ortmProfile = asTrimmedString(payload.ortmProfile, 64) || 'default';
    if (!ORTM_PROFILES.has(ortmProfile)) {
      throw new Error(`unsupported ORTM profile: ${ortmProfile}`);
    }
    command.whepUrl = parsedUrl.toString();
    command.ortmProfile = ortmProfile;
  }

  return command;
}

export class ViewerControlHub {
  constructor() {
    this.clients = new Map();
  }

  register(ws, metadata = {}) {
    const peerId = asTrimmedString(metadata.peerId, 128) || randomUUID();
    this.clients.set(peerId, {
      ws,
      peerId,
      page: asTrimmedString(metadata.page),
      connectedAt: new Date().toISOString(),
      lastResult: null,
    });
    return peerId;
  }

  remove(ws) {
    for (const [peerId, client] of this.clients) {
      if (client.ws === ws) this.clients.delete(peerId);
    }
  }

  recordResult(ws, result) {
    for (const client of this.clients.values()) {
      if (client.ws === ws) {
        client.lastResult = {
          ...result,
          receivedAt: new Date().toISOString(),
        };
        return;
      }
    }
  }

  list() {
    return Array.from(this.clients.values()).map(({ ws: _ws, ...client }) => client);
  }

  dispatch(command, peerId = '') {
    const targets = peerId
      ? [this.clients.get(asTrimmedString(peerId, 128))].filter(Boolean)
      : Array.from(this.clients.values());
    let delivered = 0;
    for (const client of targets) {
      if (client.ws.readyState !== client.ws.OPEN) continue;
      client.ws.send(JSON.stringify({ type: 'viewer-control-command', command }));
      delivered += 1;
    }
    return delivered;
  }
}
