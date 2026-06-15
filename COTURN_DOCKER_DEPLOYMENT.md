# coturn Deployment with Docker

This document describes how to deploy a `coturn` server with Docker for use with this repository.

The goal is to provide a practical relay server for WebRTC validation and integration, without embedding any real secret into the repository.

## Scope

This document covers:

- a minimal Docker-based `coturn` deployment
- required ports
- security group / firewall considerations
- recommended authentication mode
- how to connect it to the demo app

This document does not include:

- a real hostname
- a real TURN shared secret
- a real TLS certificate

## Recommended Authentication Mode

Use the standard `coturn` shared-secret mode:

```conf
use-auth-secret
static-auth-secret=YOUR_SHARED_SECRET
realm=turn.example.com
```

Do not use long-lived hardcoded TURN usernames and passwords in production.

Recommended pattern:

- store the shared secret only on the server or BFF
- issue short-lived TURN credentials
- pass temporary credentials to the frontend

## Minimal Port Plan

For a non-TLS setup used in validation:

- `3478/udp`
- `3478/tcp`
- relay UDP range such as `49152-49252/udp`

If you also want TLS-based TURN:

- `5349/tcp`

If your deployment must support highly restricted networks, you may later want `443/tcp` for TURN over TLS, but that usually conflicts with other services and should be planned carefully.

## Example Directory Layout

```text
coturn/
  docker-compose.yml
  turnserver.conf
  certs/
```

## Example Docker Compose

Create a dedicated deployment directory and add this `docker-compose.yml`:

```yaml
services:
  coturn:
    image: coturn/coturn:4.6.3
    container_name: coturn
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./turnserver.conf:/etc/coturn/turnserver.conf:ro
      - ./certs:/etc/coturn/certs:ro
      - ./logs:/var/log/coturn
```

### Why `network_mode: host`

`coturn` is easier to operate with host networking because TURN relay allocates many UDP ports dynamically inside the configured relay range. Host mode avoids extra Docker port-mapping complexity.

If you do not want host mode, you must explicitly publish:

- listening ports
- TLS port if used
- the full relay UDP range

That is possible, but usually more cumbersome.

## Example `turnserver.conf`

For relay validation without TLS:

```conf
listening-port=3478

fingerprint
use-auth-secret
static-auth-secret=REPLACE_WITH_LONG_RANDOM_SECRET
realm=turn.example.com
server-name=turn.example.com

listening-ip=0.0.0.0

min-port=49152
max-port=49252

no-tls
no-dtls

no-multicast-peers
no-cli
stale-nonce

simple-log
log-file=/var/log/coturn/turnserver.log
```

### Notes

- If the machine has a directly attached public IP, `external-ip` is usually not required.
- If the machine is behind NAT, you may need:

```conf
external-ip=PUBLIC_IP
```

or a mapped form depending on the deployment topology.

## Example TLS Extension

If you later add TLS:

```conf
tls-listening-port=5349
cert=/etc/coturn/certs/fullchain.pem
pkey=/etc/coturn/certs/privkey.pem
```

Then remove:

```conf
no-tls
```

If you also want DTLS, remove:

```conf
no-dtls
```

## Start the Server

From the coturn deployment directory:

```bash
docker compose up -d
```

Check status:

```bash
docker compose ps
docker compose logs --tail=100
```

## Firewall / Security Group Rules

At minimum, allow:

- `3478/udp`
- `3478/tcp`
- `49152-49252/udp`

If TLS is enabled, also allow:

- `5349/tcp`

If you use a cloud security group, make sure the relay UDP range is open. Opening only `3478` is not enough.

## Connecting the Demo App

In this repository, set:

```env
DEFAULT_TURN_URLS=turn:turn.example.com:3478?transport=udp,turn:turn.example.com:3478?transport=tcp
DEFAULT_TURN_USERNAME=
DEFAULT_TURN_CREDENTIAL=
```

If you are using temporary credentials from a BFF, leave the username and credential empty in `.env` and let the frontend fetch them dynamically.

For manual validation, you can temporarily fill in a short-lived username and credential.

## Validation Modes

### 1. Forced Relay Validation

Use:

- `DEFAULT_FORCE_RELAY=1`
- `DEFAULT_TURN_ONLY=1`

If the page connects and shows:

```text
relay / udp / relay->relay
```

then TURN relay is working.

### 2. Normal Fallback Validation

Use:

- `DEFAULT_FORCE_RELAY=0`
- `DEFAULT_TURN_ONLY=0`

This validates the real production behavior:

- direct path when possible
- TURN fallback when direct connectivity fails

## TURN Credential Generation

The usual credential format is:

- `username = expiry:user-id`
- `credential = base64(HMAC-SHA1(shared-secret, username))`

The application backend should generate these credentials and return them to authenticated clients.

## Operational Notes

- Start with low video bitrate on small VPS instances
- Keep relay ranges explicit and documented
- Monitor relay bandwidth usage
- Log direct-vs-relay outcomes in the application layer

For small validation servers, the example relay range `49152-49252` is enough. Increase it only when concurrency requires it.

## Common Mistakes

### Opening only 3478

TURN relay media usually uses the configured relay UDP range, not just the listening port.

### Putting real TURN secrets into the frontend

Never expose the TURN shared secret in browser code.

### Confusing TURN with signaling

`coturn` does not replace signaling. You still need signaling to exchange:

- offer
- answer
- ICE candidates

### Using a public reverse proxy in front of TURN

TURN is not normal HTTP traffic. Standard HTTP reverse proxies are usually not the right place to terminate or forward TURN traffic.

## Suggested Next Step

After the Docker-based TURN server is working:

1. validate forced relay
2. validate normal fallback
3. move TURN credential generation into the BFF
4. enable TLS if your network environment requires it
