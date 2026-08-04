export function deriveClockSyncEndpoint(whepUrl, baseUrl = globalThis.location?.href) {
  const mediaUrl = new URL(whepUrl, baseUrl);
  if (mediaUrl.protocol !== 'http:' && mediaUrl.protocol !== 'https:') {
    throw new TypeError(`unsupported WHEP protocol: ${mediaUrl.protocol}`);
  }

  const proxyDeviceMatch = mediaUrl.pathname.match(/^\/noproxy\/([^/]+)(?:\/|$)/);
  if (proxyDeviceMatch) {
    const deviceName = decodeURIComponent(proxyDeviceMatch[1]);
    if (/^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/i.test(deviceName)) {
      return `https://${deviceName.toLowerCase()}.li-adder.ts.net:8889/api/clock-sync`;
    }
  }

  return new URL('/api/clock-sync', mediaUrl.origin).toString();
}

export function resolveClockSyncEndpoint(
  whepUrl,
  { override = null, baseUrl = globalThis.location?.href } = {},
) {
  if (override != null) {
    const normalized = String(override).trim();
    if (normalized.toLowerCase() === 'off') return null;
    if (normalized) return new URL(normalized, baseUrl).toString();
  }
  return deriveClockSyncEndpoint(whepUrl, baseUrl);
}
