import { createServer } from 'node:http';
import { readFile } from 'node:fs/promises';
import { extname, join } from 'node:path';
import { WebSocketServer } from 'ws';

const PORT = Number(process.env.PORT || 9001);
const PUBLIC_DIR = join(process.cwd(), 'public');
const DEFAULT_TURN_URLS = (process.env.DEFAULT_TURN_URLS || '')
  .split(',')
  .map((item) => item.trim())
  .filter(Boolean);
const APP_CONFIG = {
  defaultRoom: process.env.DEFAULT_ROOM || 'demo-room',
  defaultTurnUrls: DEFAULT_TURN_URLS,
  defaultTurnUsername: process.env.DEFAULT_TURN_USERNAME || '',
  defaultTurnCredential: process.env.DEFAULT_TURN_CREDENTIAL || '',
  defaultForceRelay: process.env.DEFAULT_FORCE_RELAY !== '0',
  defaultTurnOnly: process.env.DEFAULT_TURN_ONLY !== '0',
  defaultAutoStart: process.env.DEFAULT_AUTO_START !== '0',
};

const MIME = {
  '.html': 'text/html; charset=utf-8',
  '.js': 'text/javascript; charset=utf-8',
  '.css': 'text/css; charset=utf-8',
};

const server = createServer(async (req, res) => {
  try {
    const requestUrl = new URL(req.url || '/', 'http://localhost');
    if (requestUrl.pathname === '/config.js') {
      res.writeHead(200, {
        'Content-Type': 'text/javascript; charset=utf-8',
        'Cache-Control': 'no-store',
      });
      res.end(`window.APP_CONFIG = ${JSON.stringify(APP_CONFIG, null, 2)};\n`);
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
      ws.send(JSON.stringify({ type: 'joined', roomId, peerId }));
      broadcastPeers(roomId);
      return;
    }

    if (!roomId || !peerId) return;

    if (msg.type === 'signal' && msg.to) {
      const target = rooms.get(roomId)?.get(String(msg.to));
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
    if (room.size === 0) {
      rooms.delete(roomId);
      return;
    }
    broadcastPeers(roomId);
  });
});

server.listen(PORT, '0.0.0.0', () => {
  console.log(`WebRTC demo server listening on http://0.0.0.0:${PORT}`);
});
