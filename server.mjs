import { createServer, request as httpRequest } from 'node:http';
import { createHmac } from 'node:crypto';
import { existsSync, readFileSync } from 'node:fs';
import { readFile } from 'node:fs/promises';
import { extname, join } from 'node:path';
import { WebSocketServer } from 'ws';

loadDotEnv();

const PORT = Number(process.env.PORT || 9001);
const PUBLIC_DIR = join(process.cwd(), 'public');
const MEDIAMTX_ORIGIN = process.env.MEDIAMTX_ORIGIN || 'http://127.0.0.1:8889';

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
    defaultWhipUrl: process.env.DEFAULT_WHIP_URL || 'http://localhost:9001/mtx/demo-stream/whip',
    defaultWhepUrl: process.env.DEFAULT_WHEP_URL || 'http://localhost:9001/mtx/demo-stream/whep',
    defaultForceRelay: process.env.DEFAULT_FORCE_RELAY !== '0',
    defaultTurnOnly: process.env.DEFAULT_TURN_ONLY !== '0',
    defaultAutoStart: process.env.DEFAULT_AUTO_START !== '0',
  };
}

function proxyToMediaMTX(req, res, requestUrl) {
  const upstreamPath = requestUrl.pathname.replace(/^\/mtx/, '') || '/';
  const upstreamUrl = new URL(upstreamPath + requestUrl.search, MEDIAMTX_ORIGIN);
  const headers = { ...req.headers };
  headers.host = upstreamUrl.host;

  const upstream = httpRequest(upstreamUrl, {
    method: req.method,
    headers,
  }, (upstreamRes) => {
    const responseHeaders = { ...upstreamRes.headers };
    const location = upstreamRes.headers.location;
    if (location) {
      const absolute = new URL(location, upstreamUrl);
      responseHeaders.location = `/mtx${absolute.pathname}${absolute.search}`;
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

const server = createServer(async (req, res) => {
  try {
    const requestUrl = new URL(req.url || '/', 'http://localhost');
    if (requestUrl.pathname === '/config.js') {
      const appConfig = buildAppConfig();
      res.writeHead(200, {
        'Content-Type': 'text/javascript; charset=utf-8',
        'Cache-Control': 'no-store',
      });
      res.end(`window.APP_CONFIG = ${JSON.stringify(appConfig, null, 2)};\n`);
      return;
    }
    if (requestUrl.pathname === '/mtx' || requestUrl.pathname.startsWith('/mtx/')) {
      proxyToMediaMTX(req, res, requestUrl);
      return;
    }
    const url = requestUrl.pathname === '/' ? '/index.html' : requestUrl.pathname;
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
