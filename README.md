# WebRTC TURN Relay Lab

Minimal WebRTC publisher/viewer demo for validating TURN relay fallback with:

- a local WebRTC page running on your laptop
- a public `coturn` server
- mobile access over a tailnet or any other private path to the web page

The project is intentionally small:

- `server.mjs`: static file server + WebSocket signaling
- `public/index.html`: single-page publisher/viewer test UI
- `Dockerfile` and `docker-compose.yml`: local container runtime
- `INTEGRATION_DESIGN.md`: how to integrate this approach into an existing frontend and BFF
- `COTURN_DOCKER_DEPLOYMENT.md`: how to deploy a Docker-based coturn server for this project

Docs:

- [English README](./README.md)
- [中文 README](./README.zh-CN.md)
- [English integration design](./INTEGRATION_DESIGN.md)
- [中文集成设计](./INTEGRATION_DESIGN.zh-CN.md)
- [coturn Docker deployment](./COTURN_DOCKER_DEPLOYMENT.md)
- [coturn Docker 部署文档](./COTURN_DOCKER_DEPLOYMENT.zh-CN.md)
- [coturn Docker 执行手册（中文）](./COTURN_DOCKER_RUNBOOK.zh-CN.md)

License: MIT

## What This Project Verifies

This demo is useful when you want to prove:

1. direct WebRTC works on good networks
2. media falls back to TURN relay when direct connectivity fails
3. your `coturn` deployment is actually carrying media

The UI shows the selected candidate pair. When it reports values such as:

```text
relay / udp / relay->relay
```

the media path is going through TURN relay.

## Quick Start

### Option 1: Run with Node.js

```bash
npm install
npm start
```

Open:

```text
http://localhost:9001
```

### Option 2: Run with Docker

```bash
docker compose up --build -d
```

Open:

```text
http://localhost:9001
```

Stop it with:

```bash
docker compose down
```

## Configuration

All defaults are injected through environment variables. Copy `.env.example` to `.env` and edit as needed:

```bash
cp .env.example .env
```

Supported variables:

- `PORT`: local HTTP port
- `DEFAULT_ROOM`: room name shown in the UI
- `DEFAULT_TURN_URLS`: comma-separated TURN URLs
- `DEFAULT_TURN_USERNAME`: optional prefilled TURN username
- `DEFAULT_TURN_CREDENTIAL`: optional prefilled TURN credential
- `DEFAULT_FORCE_RELAY`: `1` or `0`
- `DEFAULT_TURN_ONLY`: `1` or `0`
- `DEFAULT_AUTO_START`: `1` or `0`

Example:

```env
PORT=9001
DEFAULT_ROOM=demo-room
DEFAULT_TURN_URLS=turn:turn.example.com:3478?transport=udp,turn:turn.example.com:3478?transport=tcp
DEFAULT_TURN_USERNAME=
DEFAULT_TURN_CREDENTIAL=
DEFAULT_FORCE_RELAY=1
DEFAULT_TURN_ONLY=1
DEFAULT_AUTO_START=1
```

When using Docker Compose, Compose will automatically read `.env` if it exists.

## Typical Test Flow

### Forced TURN relay validation

1. Open the page on the laptop.
2. Select `publisher`.
3. Keep `force relay` enabled.
4. Keep `turn only` enabled.
5. Join the room.
6. Open the same page on the phone with `role=viewer`.
7. Join the same room on the phone.
8. Start the connection from the publisher.

If the page connects and the candidate pair shows `relay`, TURN is working.

### Real fallback validation

1. Disable `force relay`.
2. Disable `turn only`.
3. Reconnect both peers.

This validates the normal strategy:

- direct on good networks
- TURN fallback on restricted networks

## Shareable Viewer URL

The page can generate a viewer URL with query parameters such as:

```text
https://device.example.ts.net/?room=demo-room&role=viewer&relay=1&turnOnly=1
```

The viewer role is locked by the URL so the mobile side cannot accidentally turn into a publisher.

## Tailscale Serve

If you want HTTPS on a tailnet without adding another reverse proxy, use Tailscale Serve and keep this app on plain local HTTP:

```bash
tailscale serve 9001
```

Then access:

```text
https://your-device.your-tailnet.ts.net
```

Do not use `https://...:9001` directly for this mode. The HTTPS endpoint is provided by Tailscale on its own frontend port and reverse-proxies to local port `9001`.

## coturn Notes

This repository does **not** ship a real TURN secret, hostname, or credential.

For production-style validation:

- use temporary TURN credentials
- keep the TURN shared secret server-side only
- prefer low bitrate during relay testing on low-bandwidth VPS instances

Example low-bitrate settings already used by the demo:

- `280 kbps` video
- `12 fps`
- `360p` target height

## Security Notes

- Do not commit real TURN usernames or passwords.
- Do not commit your TURN shared secret.
- Do not commit personal tailnet names, device names, or internal hostnames.
- Treat `.env` as local-only configuration.

## Publishing Checklist

Before publishing, verify that:

- `.env` is not committed
- no real TURN credential appears in source or screenshots
- no personal domain, tailnet, or VPS IP remains in the repository
- unrelated local artifacts are removed from the repo history

## Release Notes

This repository is intended as a minimal validation lab, not a production signaling service.

If you publish it, consider adding:

- screenshots or a short demo GIF
- a small architecture diagram
- a note describing the exact network topology you validated
