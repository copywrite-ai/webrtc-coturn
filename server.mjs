import { createServer, request as httpRequest } from 'node:http';
import { createHmac } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { appendFile, readFile } from 'node:fs/promises';
import { request as httpsRequest } from 'node:https';
import { extname, join, posix as pathPosix } from 'node:path';
import { WebSocketServer } from 'ws';

loadDotEnv();

const PORT = Number(process.env.PORT || 9001);
const PUBLIC_DIR = join(process.cwd(), 'public');
const MEDIAMTX_ORIGIN = process.env.MEDIAMTX_ORIGIN || 'http://127.0.0.1:8889';
const MEDIAMTX_PATH_PREFIX = process.env.MEDIAMTX_PATH_PREFIX || '';
const DEVICE_DOMAIN_SUFFIX = process.env.DEVICE_DOMAIN_SUFFIX || '.beago-fish.ts.net';
const CLIENT_LOG_PATH = process.env.CLIENT_LOG_PATH || join(process.cwd(), 'client-events.log');

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
};

function log(...parts) {
  console.log(new Date().toISOString(), ...parts);
}

function loadDotEnv() {
  const envPath = join(process.cwd(), '.env');
  if (!existsSync(envPath)) {
    return;
  }

  const content = readFileSync(envPath, 'utf8');
  for (const rawLine of content.split(/\r?\n/)) {
    const line = rawLine.trim();
    if (!line || line.startsWith('#')) {
      continue;
    }

    const separator = line.indexOf('=');
    if (separator <= 0) {
      continue;
    }

    const key = line.slice(0, separator).trim();
    if (!key || Object.prototype.hasOwnProperty.call(process.env, key)) {
      continue;
    }

    let value = line.slice(separator + 1).trim();
    if (
      (value.startsWith('"') && value.endsWith('"')) ||
      (value.startsWith('\'') && value.endsWith('\''))
    ) {
      value = value.slice(1, -1);
    }
    process.env[key] = value;
  }
}

function splitCsv(value) {
  return (value || '')
    .split(',')
    .map((item) => item.trim())
    .filter(Boolean);
}

function parsePositiveInt(value, fallback) {
  const parsed = Number.parseInt(String(value || ''), 10);
  return Number.isFinite(parsed) && parsed > 0 ? parsed : fallback;
}

function buildTemporaryTurnAuth(prefix = '') {
  const secret = process.env[`${prefix}SHARED_SECRET`] || process.env.TURN_SHARED_SECRET || '';
  if (!secret) {
    return null;
  }

  const ttlSeconds = parsePositiveInt(
    process.env[`${prefix}TTL_SECONDS`] || process.env.TURN_TTL_SECONDS,
    24 * 60 * 60,
  );
  const usernameSuffix = process.env[`${prefix}USERNAME_SUFFIX`] || process.env.TURN_USERNAME_SUFFIX || 'client';
  const expiresAt = Math.floor(Date.now() / 1000) + ttlSeconds;
  const username = `${expiresAt}:${usernameSuffix}`;
  const credential = createHmac('sha1', secret).update(username).digest('base64');
  return { username, credential };
}

function resolveTurnAuth(prefix = '') {
  const temporary = buildTemporaryTurnAuth(prefix);
  if (temporary) {
    return temporary;
  }

  return {
    username: process.env[`${prefix}USERNAME`] || '',
    credential: process.env[`${prefix}CREDENTIAL`] || '',
  };
}

function buildTurnPreset(name, fallback) {
  const prefix = `TURN_PRESET_${name.toUpperCase()}_`;
  const urls = splitCsv(process.env[`${prefix}URLS`]);
  const auth = resolveTurnAuth(prefix);
  return {
    label: process.env[`${prefix}LABEL`] || fallback.label,
    urls: urls.length ? urls : fallback.urls,
    username: auth.username,
    credential: auth.credential,
  };
}

function buildTurnPresets() {
  return {
    ip: buildTurnPreset('ip', {
      label: 'turn-ip.example.com',
      urls: [
        'turn:turn-ip.example.com:3478?transport=udp',
        'turn:turn-ip.example.com:3478?transport=tcp',
      ],
    }),
    domain: buildTurnPreset('domain', {
      label: 'turn.example.com',
      urls: [
        'turn:turn.example.com:3480?transport=udp',
        'turn:turn.example.com:3480?transport=tcp',
      ],
    }),
  };
}

function buildAppConfig() {
  const defaultTurnUrls = splitCsv(process.env.DEFAULT_TURN_URLS);
  const defaultTurnAuth = resolveTurnAuth('DEFAULT_TURN_');
  return {
    defaultRoom: process.env.DEFAULT_ROOM || 'demo-room',
    defaultTurnUrls,
    defaultTurnUsername: defaultTurnAuth.username,
    defaultTurnCredential: defaultTurnAuth.credential,
    defaultServerBandwidthMbps: process.env.DEFAULT_SERVER_BANDWIDTH_MBPS || '20',
    turnPresets: buildTurnPresets(),
    defaultWhipUrl: process.env.DEFAULT_WHIP_URL || '/online/whip',
    defaultWhepUrl: process.env.DEFAULT_WHEP_URL || '/online/whep',
    defaultForceRelay: process.env.DEFAULT_FORCE_RELAY !== '0',
    defaultTurnOnly: process.env.DEFAULT_TURN_ONLY !== '0',
    defaultAutoStart: process.env.DEFAULT_AUTO_START !== '0',
  };
}

function normalizePathPrefix(value) {
  return value ? `/${value}`.replace(/\/+/g, '/').replace(/\/$/, '') : '';
}

function buildDynamicDeviceOrigin(device) {
  const normalizedDevice = String(device || '').trim().toLowerCase();
  if (!/^[a-z0-9-]+$/.test(normalizedDevice)) {
    return null;
  }
  const suffix = String(DEVICE_DOMAIN_SUFFIX || '').trim();
  if (!suffix.startsWith('.')) {
    return null;
  }
  return `https://${normalizedDevice}${suffix}`;
}

function resolveMediaUpstream(requestUrl) {
  const path = String(requestUrl.pathname || '');
  const onlineMatch = path.match(/^\/online(?:\/(.*))?$/i);
  if (onlineMatch) {
    const [, rest = ''] = onlineMatch;
    const prefix = normalizePathPrefix(MEDIAMTX_PATH_PREFIX);
    const normalizedRest = String(rest || '').replace(/^\/+/, '');
    const targetPath = normalizedRest
      ? `${prefix}/online/${normalizedRest}${requestUrl.search}`
      : `${prefix}/online${requestUrl.search}`;
    return {
      publicPrefix: '/online',
      upstreamUrl: new URL(targetPath, MEDIAMTX_ORIGIN),
    };
  }

  const offlineAliasMatch = path.match(/^\/offline(?:\/(.*))?$/i);
  if (offlineAliasMatch) {
    const [, rest = ''] = offlineAliasMatch;
    const prefix = normalizePathPrefix(MEDIAMTX_PATH_PREFIX);
    const normalizedRest = String(rest || '').replace(/^\/+/, '');
    const targetPath = normalizedRest
      ? `${prefix}/online/${normalizedRest}${requestUrl.search}`
      : `${prefix}/online${requestUrl.search}`;
    return {
      publicPrefix: '/online',
      upstreamUrl: new URL(targetPath, MEDIAMTX_ORIGIN),
    };
  }

  const fishMatch = path.match(/^\/(fish_(?:front|back|left|right))(?:\/(.*))?$/i);
  if (fishMatch) {
    const [, stream, rest = ''] = fishMatch;
    const prefix = normalizePathPrefix(MEDIAMTX_PATH_PREFIX);
    const normalizedRest = String(rest || '').replace(/^\/+/, '');
    const targetPath = normalizedRest
      ? `${prefix}/${stream}/${normalizedRest}${requestUrl.search}`
      : `${prefix}/${stream}${requestUrl.search}`;
    return {
      publicPrefix: `/${stream}`,
      upstreamUrl: new URL(targetPath, MEDIAMTX_ORIGIN),
    };
  }

  const match = path.match(/^\/mtx\/([a-z0-9-]+)\/([^/]+)\/(whep|whip)(\/.*)?$/i);
  if (match) {
    const [, device, stream, action, tail = ''] = match;
    const origin = buildDynamicDeviceOrigin(device);
    if (origin) {
      return {
        publicPrefix: `/mtx/${device}`,
        upstreamUrl: new URL(`/mtx/${stream}/${action}${tail}${requestUrl.search}`, origin),
      };
    }
  }

  const relativePath = path.replace(/^\/mtx/, '') || '/';
  const prefix = normalizePathPrefix(MEDIAMTX_PATH_PREFIX);
  return {
    publicPrefix: '/mtx',
    upstreamUrl: new URL(`${prefix}${relativePath}${requestUrl.search}`, MEDIAMTX_ORIGIN),
  };
}

function rewriteLocationForClient(requestPath, targetPath, search = '') {
  const currentPath = String(requestPath || '/');
  const target = String(targetPath || '/');
  const baseDir = currentPath.endsWith('/')
    ? currentPath
    : currentPath.slice(0, currentPath.lastIndexOf('/') + 1) || '/';
  const relativePath = pathPosix.relative(baseDir, target) || '.';
  return `${relativePath}${search}`;
}

function proxyToMediaMTX(req, res, requestUrl) {
  const { upstreamUrl, publicPrefix } = resolveMediaUpstream(requestUrl);
  const headers = { ...req.headers };
  headers.host = upstreamUrl.host;
  const requestImpl = upstreamUrl.protocol === 'https:' ? httpsRequest : httpRequest;

  const upstream = requestImpl(upstreamUrl, {
    method: req.method,
    headers,
  }, (upstreamRes) => {
    const responseHeaders = { ...upstreamRes.headers };
    const location = upstreamRes.headers.location;
    if (location) {
      const absolute = new URL(location, upstreamUrl);
      const rewrittenPath = absolute.pathname.startsWith('/mtx/')
        ? `${publicPrefix}${absolute.pathname.slice('/mtx'.length)}`
        : `${publicPrefix}${absolute.pathname}`;
      responseHeaders.location = rewriteLocationForClient(
        requestUrl.pathname,
        rewrittenPath,
        absolute.search,
      );
    }
    res.writeHead(upstreamRes.statusCode || 502, responseHeaders);
    upstreamRes.pipe(res);
  });

  upstream.on('error', (error) => {
    res.writeHead(502, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end(`MediaMTX upstream error: ${error.message}`);
  });

  req.pipe(upstream);
}

function readRequestBody(req) {
  return new Promise((resolve, reject) => {
    const chunks = [];
    req.on('data', (chunk) => chunks.push(chunk));
    req.on('end', () => resolve(Buffer.concat(chunks).toString('utf8')));
    req.on('error', reject);
  });
}

async function handleClientLog(req, res) {
  const raw = await readRequestBody(req);
  let payload;
  try {
    payload = JSON.parse(raw || '{}');
  } catch {
    res.writeHead(400, { 'Content-Type': 'application/json; charset=utf-8' });
    res.end(JSON.stringify({ ok: false, error: 'invalid json' }));
    return;
  }

  const line = JSON.stringify({
    ts: new Date().toISOString(),
    remote: req.socket.remoteAddress || '-',
    page: String(payload.page || ''),
    peerId: String(payload.peerId || ''),
    group: String(payload.group || ''),
    level: String(payload.level || ''),
    message: String(payload.message || ''),
  });
  await appendFile(CLIENT_LOG_PATH, `${line}\n`, 'utf8');
  res.writeHead(204);
  res.end();
}

const server = createServer(async (req, res) => {
  try {
    const requestUrl = new URL(req.url || '/', 'http://localhost');
    if (requestUrl.pathname === '/offline') {
      res.writeHead(302, {
        Location: rewriteLocationForClient(requestUrl.pathname, '/online/', requestUrl.search),
        'Cache-Control': 'no-store',
      });
      res.end();
      return;
    }
    if (req.method === 'POST' && requestUrl.pathname === '/client-log') {
      await handleClientLog(req, res);
      return;
    }
    if (requestUrl.pathname === '/config.js') {
      const appConfig = buildAppConfig();
      res.writeHead(200, {
        'Content-Type': 'text/javascript; charset=utf-8',
        'Cache-Control': 'no-store',
      });
      res.end(`window.APP_CONFIG = ${JSON.stringify(appConfig, null, 2)};\n`);
      return;
    }
    if (
      requestUrl.pathname === '/mtx' ||
      requestUrl.pathname.startsWith('/mtx/') ||
      requestUrl.pathname === '/offline' ||
      requestUrl.pathname === '/online' ||
      requestUrl.pathname.startsWith('/online/') ||
      /^\/fish_(?:front|back|left|right)(?:\/|$)/i.test(requestUrl.pathname) ||
      requestUrl.pathname.startsWith('/offline/')
    ) {
      proxyToMediaMTX(req, res, requestUrl);
      return;
    }
    let url = requestUrl.pathname;
    if (url === '/') {
      url = requestUrl.searchParams.get('v') === '4' ? '/v4.html' : '/index.html';
    }
    const filePath = join(PUBLIC_DIR, url);
    const data = await readFile(filePath);
    const mime = MIME[extname(filePath)] || 'application/octet-stream';
    res.writeHead(200, { 'Content-Type': mime });
    res.end(data);
  } catch {
    res.writeHead(404, { 'Content-Type': 'text/plain; charset=utf-8' });
    res.end('Not found');
  }
});

const wss = new WebSocketServer({ server, path: '/signal' });
const rooms = new Map();

function broadcastPeers(roomId) {
  const peers = Array.from(rooms.get(roomId)?.keys() || []);
  for (const ws of rooms.get(roomId)?.values() || []) {
    if (ws.readyState === ws.OPEN) {
      ws.send(JSON.stringify({ type: 'peers', peers }));
    }
  }
}

wss.on('connection', (ws) => {
  let roomId = null;
  let peerId = null;
  const remoteAddr = ws._socket?.remoteAddress || '-';
  log('ws connected', `remote=${remoteAddr}`);

  ws.on('message', (raw) => {
    let msg;
    try {
      msg = JSON.parse(raw.toString());
    } catch {
      return;
    }

    if (msg.type === 'join') {
      roomId = String(msg.roomId || 'demo');
      peerId = String(msg.peerId || Math.random().toString(36).slice(2));
      if (!rooms.has(roomId)) rooms.set(roomId, new Map());
      rooms.get(roomId).set(peerId, ws);
      log('peer joined', `room=${roomId}`, `peer=${peerId}`, `remote=${remoteAddr}`, `size=${rooms.get(roomId).size}`);
      ws.send(JSON.stringify({ type: 'joined', roomId, peerId }));
      broadcastPeers(roomId);
      return;
    }

    if (!roomId || !peerId) return;

    if (msg.type === 'signal' && msg.to) {
      const target = rooms.get(roomId)?.get(String(msg.to));
      const signalKind = msg.data?.offer
        ? 'offer'
        : msg.data?.answer
          ? 'answer'
          : msg.data?.candidate
            ? 'candidate'
            : 'unknown';
      log('signal relay', `room=${roomId}`, `from=${peerId}`, `to=${String(msg.to)}`, `kind=${signalKind}`);
      if (target && target.readyState === target.OPEN) {
        target.send(JSON.stringify({
          type: 'signal',
          from: peerId,
          data: msg.data,
        }));
      }
    }
  });

  ws.on('close', () => {
    if (!roomId || !peerId) return;
    const room = rooms.get(roomId);
    if (!room) return;
    room.delete(peerId);
    log('peer left', `room=${roomId}`, `peer=${peerId}`, `remaining=${room.size}`);
    if (room.size === 0) {
      rooms.delete(roomId);
      log('room removed', `room=${roomId}`);
      return;
    }
    broadcastPeers(roomId);
  });
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`WebRTC demo server listening on http://0.0.0.0:${PORT}`);
});
