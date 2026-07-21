import {
  GRID_SIZE,
  OrtmDecodeError,
  decodeGrid,
  finderBit,
  frameAgeMs,
  inFinder,
  structureErrors,
} from './ortm.js';

export const DEFAULT_RASTER_PROFILE = Object.freeze({
  x: 24,
  y: 24,
  cell: 12,
  padding: 12,
  scales: [1.0, 0.95, 1.05, 0.9, 1.1],
  offsets: [0, -4, 4, -8, 8, -12, 12],
  samplePositions: [0.2, 0.5, 0.8],
  minContrast: 32,
  maxFinderErrors: 8,
  maxTimingErrors: 10,
  maxAgeMs: 5000,
});

function median(values) {
  if (!values.length) return Number.NaN;
  const sorted = [...values].sort((left, right) => left - right);
  const middle = Math.floor(sorted.length / 2);
  return sorted.length % 2 ? sorted[middle] : (sorted[middle - 1] + sorted[middle]) / 2;
}

export function buildCandidates(profile = DEFAULT_RASTER_PROFILE) {
  const scales = profile.scales ?? DEFAULT_RASTER_PROFILE.scales;
  const offsets = profile.offsets ?? DEFAULT_RASTER_PROFILE.offsets;
  return scales.flatMap((scale) =>
    offsets.flatMap((dx) =>
      offsets.map((dy) => {
        const cell = profile.cell * scale;
        const padding = profile.padding * scale;
        return {
          scale,
          dx,
          dy,
          x: profile.x + dx,
          y: profile.y + dy,
          cell,
          padding,
          boxSize: GRID_SIZE * cell + padding * 2,
        };
      }),
    ),
  );
}

export function buildReadRoi(profile = DEFAULT_RASTER_PROFILE) {
  const candidates = buildCandidates(profile);
  const x = Math.max(0, Math.floor(Math.min(...candidates.map((candidate) => candidate.x))));
  const y = Math.max(0, Math.floor(Math.min(...candidates.map((candidate) => candidate.y))));
  const right = Math.ceil(Math.max(...candidates.map((candidate) => candidate.x + candidate.boxSize)));
  const bottom = Math.ceil(Math.max(...candidates.map((candidate) => candidate.y + candidate.boxSize)));
  return { x, y, width: Math.max(1, right - x), height: Math.max(1, bottom - y) };
}

function sampleCell(imageData, candidate, row, col, profile, roiX, roiY) {
  const positions = profile.samplePositions ?? DEFAULT_RASTER_PROFILE.samplePositions;
  const originX = candidate.x + candidate.padding + col * candidate.cell;
  const originY = candidate.y + candidate.padding + row * candidate.cell;
  let total = 0;
  let count = 0;
  for (const py of positions) {
    for (const px of positions) {
      const x = Math.round(originX + candidate.cell * px) - roiX;
      const y = Math.round(originY + candidate.cell * py) - roiY;
      if (x < 0 || x >= imageData.width || y < 0 || y >= imageData.height) continue;
      const offset = (y * imageData.width + x) * 4;
      const red = imageData.data[offset];
      const green = imageData.data[offset + 1];
      const blue = imageData.data[offset + 2];
      total += 0.299 * red + 0.587 * green + 0.114 * blue;
      count += 1;
    }
  }
  return count ? total / count : Number.NaN;
}

export function decodeRgbaImage(
  imageData,
  frameWidth,
  frameHeight,
  nowMs,
  { profile = DEFAULT_RASTER_PROFILE, roiX = 0, roiY = 0 } = {},
) {
  let bestFailure = null;
  for (const candidate of buildCandidates(profile)) {
    if (
      candidate.x < 0 ||
      candidate.y < 0 ||
      candidate.x + candidate.boxSize > frameWidth ||
      candidate.y + candidate.boxSize > frameHeight
    ) continue;

    const luma = Array.from({ length: GRID_SIZE }, () => Array(GRID_SIZE).fill(255));
    let valid = true;
    for (let row = 0; row < GRID_SIZE && valid; row += 1) {
      for (let col = 0; col < GRID_SIZE; col += 1) {
        const value = sampleCell(imageData, candidate, row, col, profile, roiX, roiY);
        if (!Number.isFinite(value)) {
          valid = false;
          break;
        }
        luma[row][col] = value;
      }
    }
    if (!valid) continue;

    const finderBlack = [];
    const finderWhite = [];
    for (let row = 0; row < GRID_SIZE; row += 1) {
      for (let col = 0; col < GRID_SIZE; col += 1) {
        if (!inFinder(row, col)) continue;
        (finderBit(row, col) ? finderBlack : finderWhite).push(luma[row][col]);
      }
    }
    const blackLevel = median(finderBlack);
    const whiteLevel = median(finderWhite);
    const threshold = (blackLevel + whiteLevel) / 2;
    const contrast = whiteLevel - blackLevel;
    const grid = luma.map((row) => row.map((value) => Number(value < threshold)));
    const errors = structureErrors(grid);
    const score = errors.finderErrors * 4 + errors.timingErrors * 2 - Math.max(contrast, 0) / 32;
    const details = {
      ok: false,
      score,
      finder_errors: errors.finderErrors,
      timing_errors: errors.timingErrors,
      threshold,
      contrast,
      black_level: blackLevel,
      white_level: whiteLevel,
      candidate,
    };
    let reason = null;
    if (
      errors.finderErrors > (profile.maxFinderErrors ?? DEFAULT_RASTER_PROFILE.maxFinderErrors) ||
      errors.timingErrors > (profile.maxTimingErrors ?? DEFAULT_RASTER_PROFILE.maxTimingErrors)
    ) reason = 'structure-mismatch';
    else if (contrast < (profile.minContrast ?? DEFAULT_RASTER_PROFILE.minContrast)) reason = 'low-contrast';

    try {
      if (reason) throw new OrtmDecodeError(reason);
      const marker = decodeGrid(grid, {
        maxFinderErrors: profile.maxFinderErrors ?? DEFAULT_RASTER_PROFILE.maxFinderErrors,
        maxTimingErrors: profile.maxTimingErrors ?? DEFAULT_RASTER_PROFILE.maxTimingErrors,
      });
      const latencyMs = frameAgeMs(nowMs, marker.timestampMs, {
        maxAgeMs: profile.maxAgeMs ?? DEFAULT_RASTER_PROFILE.maxAgeMs,
      });
      return {
        version: marker.version,
        frame_seq: marker.frameSeq,
        timestamp_ms: marker.timestampMs,
        latency_ms: latencyMs,
        ...details,
        ok: true,
      };
    } catch (error) {
      const failure = { ...details, reason: reason ?? error.reason ?? 'decode-error' };
      if (!bestFailure || failure.score < bestFailure.score) bestFailure = failure;
    }
  }
  return bestFailure ?? {
    ok: false,
    reason: 'no-candidate',
    score: Number.POSITIVE_INFINITY,
    finder_errors: null,
    timing_errors: null,
    contrast: null,
  };
}
