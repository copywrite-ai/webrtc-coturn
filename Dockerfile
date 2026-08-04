FROM node:20-alpine

WORKDIR /app

COPY package.json package-lock.json ./
RUN npm ci --omit=dev

COPY server.mjs ./server.mjs
COPY media-proxy-location.mjs ./media-proxy-location.mjs
COPY viewer-control.mjs ./viewer-control.mjs
COPY monitor.mjs ./monitor.mjs
COPY public ./public

ENV PORT=9001

EXPOSE 9001 9010

CMD ["node", "server.mjs"]
