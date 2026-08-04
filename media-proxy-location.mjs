function normalizePrefix(value) {
  const normalized = `/${String(value || '')}`
    .replace(/\/+/g, '/')
    .replace(/\/$/, '');
  return normalized === '/' ? '' : normalized;
}

export function mapUpstreamPathToPublic(
  publicPrefix,
  upstreamPrefix,
  upstreamPath,
) {
  const publicRoot = normalizePrefix(publicPrefix);
  const upstreamRoot = normalizePrefix(upstreamPrefix);
  const path = String(upstreamPath || '/').startsWith('/')
    ? String(upstreamPath || '/')
    : `/${upstreamPath}`;

  if (!upstreamRoot) {
    return `${publicRoot}${path}` || '/';
  }
  if (path === upstreamRoot) {
    return publicRoot || '/';
  }
  if (path.startsWith(`${upstreamRoot}/`)) {
    return `${publicRoot}${path.slice(upstreamRoot.length)}` || '/';
  }

  return `${publicRoot}${path}` || '/';
}

export function relativeLocationForClient(
  requestPath,
  targetPath,
  search = '',
) {
  const current = String(requestPath || '/');
  const target = String(targetPath || '/');
  const baseDir = current.endsWith('/')
    ? current
    : current.slice(0, current.lastIndexOf('/') + 1) || '/';
  let relative = pathPosix.relative(baseDir, target) || '.';
  if (target.endsWith('/') && relative !== '.' && !relative.endsWith('/')) {
    relative += '/';
  }
  return `${relative}${search}`;
}
import { posix as pathPosix } from 'node:path';
