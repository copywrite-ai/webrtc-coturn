# Issue: WHIP Publisher ICE/Media Not Flowing

## Goal

Start 4 local test streams at `720p / 8M` and publish them directly to the cloud MediaMTX through WHIP:

```text
https://media-proxy.nexusdot.cn/noproxy/peng-mbp14/fish_front/whip
https://media-proxy.nexusdot.cn/noproxy/peng-mbp14/fish_back/whip
https://media-proxy.nexusdot.cn/noproxy/peng-mbp14/fish_left/whip
https://media-proxy.nexusdot.cn/noproxy/peng-mbp14/fish_right/whip
```

Expected WHEP playback URL example:

```text
https://media-proxy.nexusdot.cn/noproxy/peng-mbp14/fish_front/whep
```

## Local Implementation

Added a Docker-based WHIP publisher:

```text
docker-compose.whip-publisher.yml
docker/whip-publisher/Dockerfile
docker/whip-publisher/publish-whip.sh
```

The image is based on `ubuntu:22.04` and uses:

```text
simple-whip-client
GStreamer x264enc
rtph264pay
```

Start command:

```bash
DEVICE_ID=peng-mbp14 docker compose -f docker-compose.whip-publisher.yml up -d
```

Stop command:

```bash
DEVICE_ID=peng-mbp14 docker compose -f docker-compose.whip-publisher.yml down
```

## Current Symptom

All four containers start successfully, and all four WHIP sessions receive an SDP answer from the cloud:

```text
Received SDP answer
```

However, media does not actually flow:

```text
docker stats:
CPU 0%
NetIO only tens of KB
```

As a result, cloud MediaMTX does not have a valid online stream. Visiting:

```text
https://media-proxy.nexusdot.cn/noproxy/peng-mbp14/fish_front/
```

returns:

```text
stream not found
```

This matches the local observation: WHIP signaling works, but the media path does not come up.

## Important Logs

The cloud returns Resource URLs like:

```text
https://media-proxy.nexusdot.cn/fish_front/whip/...
```

instead of preserving the original public prefix:

```text
https://media-proxy.nexusdot.cn/noproxy/peng-mbp14/fish_front/whip/...
```

With trickle ICE enabled, subsequent PATCH requests target the wrong Resource URL and fail with:

```text
[trickle] 7 Connection terminated unexpectedly
```

The publisher was changed to default to `--no-trickle`, putting candidates into the initial SDP and avoiding the PATCH path issue. Media still does not flow.

## Current Hypothesis

The HTTP WHIP signaling layer is working, but ICE/media connectivity is not.

The server SDP advertises candidates like:

```text
47.102.106.162:8189 udp
47.102.106.162:8189 tcp passive
```

The local Docker publisher advertises Docker-internal candidates like:

```text
192.168.214.x
```

The cloud cannot connect back to these Docker-internal addresses. The publisher needs a publicly reachable candidate, or media must be relayed.

## Likely Next Steps

Add TURN support to the Docker publisher and force relay for WHIP publishing:

```text
--turn-server turn://AUTH_SECRET:<password>@47.102.106.162:3478?transport=udp
--force-turn
```

Also verify on the cloud side:

```text
UDP 8189 is open from the publisher side to the cloud MediaMTX
MediaMTX webrtcAdditionalHosts / ICE candidate config is correct
nginx/media-proxy preserves or rewrites WHIP Location headers correctly
```

## Current Code State

`publish-whip.sh` currently supports:

```text
DEVICE_ID=peng-mbp14
WHIP_NO_TRICKLE=1
WHIP_LOG_LEVEL=6
```

It does not yet support TURN parameters.

## Current Docker Compose

File: `docker-compose.whip-publisher.yml`

```yaml
services:
  fish_front_whip:
    build:
      context: ./docker/whip-publisher
    image: tunnel-whip-publisher:local
    environment:
      STREAM_NAME: fish_front
      DEVICE_ID: "${DEVICE_ID:?DEVICE_ID is required}"
      WHIP_BASE_URL: "${WHIP_BASE_URL:-https://media-proxy.nexusdot.cn/noproxy}"
      WIDTH: "${WIDTH:-1280}"
      HEIGHT: "${HEIGHT:-720}"
      FPS: "${FPS:-30}"
      BITRATE_KBPS: "${BITRATE_KBPS:-8000}"
      KEY_INT_MAX: "${KEY_INT_MAX:-60}"
      PATTERN: "${PATTERN:-smpte}"
      SPEED_PRESET: "${SPEED_PRESET:-ultrafast}"
      WHIP_LOG_LEVEL: "${WHIP_LOG_LEVEL:-6}"
      WHIP_NO_TRICKLE: "${WHIP_NO_TRICKLE:-1}"
      X264_OPTION_STRING: "${X264_OPTION_STRING:-nal-hrd=cbr:force-cfr=1:filler=1}"
    restart: unless-stopped

  fish_back_whip:
    image: tunnel-whip-publisher:local
    depends_on:
      - fish_front_whip
    environment:
      STREAM_NAME: fish_back
      DEVICE_ID: "${DEVICE_ID:?DEVICE_ID is required}"
      WHIP_BASE_URL: "${WHIP_BASE_URL:-https://media-proxy.nexusdot.cn/noproxy}"
      WIDTH: "${WIDTH:-1280}"
      HEIGHT: "${HEIGHT:-720}"
      FPS: "${FPS:-30}"
      BITRATE_KBPS: "${BITRATE_KBPS:-8000}"
      KEY_INT_MAX: "${KEY_INT_MAX:-60}"
      PATTERN: "${PATTERN:-smpte}"
      SPEED_PRESET: "${SPEED_PRESET:-ultrafast}"
      WHIP_LOG_LEVEL: "${WHIP_LOG_LEVEL:-6}"
      WHIP_NO_TRICKLE: "${WHIP_NO_TRICKLE:-1}"
      X264_OPTION_STRING: "${X264_OPTION_STRING:-nal-hrd=cbr:force-cfr=1:filler=1}"
    restart: unless-stopped

  fish_left_whip:
    image: tunnel-whip-publisher:local
    depends_on:
      - fish_front_whip
    environment:
      STREAM_NAME: fish_left
      DEVICE_ID: "${DEVICE_ID:?DEVICE_ID is required}"
      WHIP_BASE_URL: "${WHIP_BASE_URL:-https://media-proxy.nexusdot.cn/noproxy}"
      WIDTH: "${WIDTH:-1280}"
      HEIGHT: "${HEIGHT:-720}"
      FPS: "${FPS:-30}"
      BITRATE_KBPS: "${BITRATE_KBPS:-8000}"
      KEY_INT_MAX: "${KEY_INT_MAX:-60}"
      PATTERN: "${PATTERN:-smpte}"
      SPEED_PRESET: "${SPEED_PRESET:-ultrafast}"
      WHIP_LOG_LEVEL: "${WHIP_LOG_LEVEL:-6}"
      WHIP_NO_TRICKLE: "${WHIP_NO_TRICKLE:-1}"
      X264_OPTION_STRING: "${X264_OPTION_STRING:-nal-hrd=cbr:force-cfr=1:filler=1}"
    restart: unless-stopped

  fish_right_whip:
    image: tunnel-whip-publisher:local
    depends_on:
      - fish_front_whip
    environment:
      STREAM_NAME: fish_right
      DEVICE_ID: "${DEVICE_ID:?DEVICE_ID is required}"
      WHIP_BASE_URL: "${WHIP_BASE_URL:-https://media-proxy.nexusdot.cn/noproxy}"
      WIDTH: "${WIDTH:-1280}"
      HEIGHT: "${HEIGHT:-720}"
      FPS: "${FPS:-30}"
      BITRATE_KBPS: "${BITRATE_KBPS:-8000}"
      KEY_INT_MAX: "${KEY_INT_MAX:-60}"
      PATTERN: "${PATTERN:-smpte}"
      SPEED_PRESET: "${SPEED_PRESET:-ultrafast}"
      WHIP_LOG_LEVEL: "${WHIP_LOG_LEVEL:-6}"
      WHIP_NO_TRICKLE: "${WHIP_NO_TRICKLE:-1}"
      X264_OPTION_STRING: "${X264_OPTION_STRING:-nal-hrd=cbr:force-cfr=1:filler=1}"
    restart: unless-stopped
```
