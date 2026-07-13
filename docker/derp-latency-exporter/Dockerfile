FROM node:20-alpine

RUN apk add --no-cache tailscale

WORKDIR /app
COPY derp-latency-exporter.mjs ./derp-latency-exporter.mjs
COPY entrypoint.sh ./entrypoint.sh
RUN chmod +x ./entrypoint.sh

ENV DERP_EXPORTER_PORT=9020
ENV DERP_NETCHECK_INTERVAL_MS=30000
ENV DERP_NETCHECK_TIMEOUT_MS=20000
ENV TAILSCALE_BIN=tailscale
ENV TS_SOCKET=/tmp/tailscaled.sock

EXPOSE 9020
ENTRYPOINT ["/app/entrypoint.sh"]
