export const GRID_SIZE = 32;
export const FINDER_SIZE = 4;
export const TIMING_INDEX = 4;
export const VERSION = 0;
export const PAYLOAD_BITS = 68;

export class OrtmDecodeError extends Error {
  constructor(reason, details = {}) {
    super(reason);
    this.name = 'OrtmDecodeError';
    this.reason = reason;
    this.details = details;
  }
}

export function crc16CcittFalse(bytes) {
  let crc = 0xffff;
  for (const rawByte of bytes) {
    crc ^= (rawByte & 0xff) << 8;
    for (let index = 0; index < 8; index += 1) {
      crc = crc & 0x8000 ? ((crc << 1) ^ 0x1021) & 0xffff : (crc << 1) & 0xffff;
    }
  }
  return crc;
}

function bitsFromInt(value, width) {
  const unsigned = Number(value) >>> 0;
  return Array.from({ length: width }, (_, index) => (unsigned >>> (width - index - 1)) & 1);
}

function bitsToInt(bits) {
  let value = 0;
  for (const bit of bits) value = ((value << 1) | (Number(bit) & 1)) >>> 0;
  return value;
}

export function inFinder(row, col) {
  return (
    (row < FINDER_SIZE && col < FINDER_SIZE) ||
    (row < FINDER_SIZE && col >= GRID_SIZE - FINDER_SIZE) ||
    (row >= GRID_SIZE - FINDER_SIZE && col < FINDER_SIZE) ||
    (row >= GRID_SIZE - FINDER_SIZE && col >= GRID_SIZE - FINDER_SIZE)
  );
}

export function isReserved(row, col) {
  return inFinder(row, col) || row === TIMING_INDEX || col === TIMING_INDEX;
}

export function finderBit(row, col) {
  const localRow = row < FINDER_SIZE ? row : row - (GRID_SIZE - FINDER_SIZE);
  const localCol = col < FINDER_SIZE ? col : col - (GRID_SIZE - FINDER_SIZE);
  return Number(
    localRow === 0 || localRow === FINDER_SIZE - 1 || localCol === 0 || localCol === FINDER_SIZE - 1,
  );
}

export const PAYLOAD_CELLS = Object.freeze(
  Array.from({ length: GRID_SIZE }, (_, row) =>
    Array.from({ length: GRID_SIZE }, (_, col) => [row, col]),
  )
    .flat()
    .filter(([row, col]) => !isReserved(row, col))
    .slice(0, PAYLOAD_BITS),
);

export const ENCODED_CELLS = Object.freeze(
  Array.from({ length: GRID_SIZE }, (_, row) =>
    Array.from({ length: GRID_SIZE }, (_, col) => [row, col]),
  )
    .flat()
    .filter(([row, col]) => isReserved(row, col) || PAYLOAD_CELLS.some(([payloadRow, payloadCol]) => payloadRow === row && payloadCol === col)),
);

export function crcInput(version, frameSeq, timestampMs) {
  return Uint8Array.from([
    version & 0x0f,
    (frameSeq >>> 8) & 0xff,
    frameSeq & 0xff,
    (timestampMs >>> 24) & 0xff,
    (timestampMs >>> 16) & 0xff,
    (timestampMs >>> 8) & 0xff,
    timestampMs & 0xff,
  ]);
}

export function encodeGrid(frameSeq, timestampMs, version = VERSION) {
  if (!Number.isInteger(version) || version < 0 || version > 0x0f) throw new RangeError('version must fit in 4 bits');
  if (!Number.isInteger(frameSeq) || frameSeq < 0 || frameSeq > 0xffff) throw new RangeError('frameSeq must fit in 16 bits');
  if (!Number.isInteger(timestampMs) || timestampMs < 0 || timestampMs > 0xffffffff) throw new RangeError('timestampMs must fit in 32 bits');
  const checksum = crc16CcittFalse(crcInput(version, frameSeq, timestampMs));
  const payload = [
    ...bitsFromInt(version, 4),
    ...bitsFromInt(frameSeq, 16),
    ...bitsFromInt(timestampMs, 32),
    ...bitsFromInt(checksum, 16),
  ];
  const grid = Array.from({ length: GRID_SIZE }, () => Array(GRID_SIZE).fill(0));
  for (let row = 0; row < GRID_SIZE; row += 1) {
    for (let col = 0; col < GRID_SIZE; col += 1) {
      if (inFinder(row, col)) grid[row][col] = finderBit(row, col);
      else if (row === TIMING_INDEX) grid[row][col] = col % 2;
      else if (col === TIMING_INDEX) grid[row][col] = row % 2;
    }
  }
  PAYLOAD_CELLS.forEach(([row, col], index) => {
    grid[row][col] = payload[index];
  });
  return grid;
}

function validateGrid(grid) {
  if (!Array.isArray(grid) || grid.length !== GRID_SIZE || grid.some((row) => !Array.isArray(row) || row.length !== GRID_SIZE)) {
    throw new OrtmDecodeError('invalid-grid-size');
  }
  if (grid.some((row) => row.some((bit) => bit !== 0 && bit !== 1))) {
    throw new OrtmDecodeError('invalid-grid-bit');
  }
}

export function structureErrors(grid) {
  validateGrid(grid);
  let finderErrors = 0;
  let timingErrors = 0;
  for (let row = 0; row < GRID_SIZE; row += 1) {
    for (let col = 0; col < GRID_SIZE; col += 1) {
      if (inFinder(row, col)) finderErrors += Number(grid[row][col] !== finderBit(row, col));
      else if (row === TIMING_INDEX) timingErrors += Number(grid[row][col] !== col % 2);
      else if (col === TIMING_INDEX) timingErrors += Number(grid[row][col] !== row % 2);
    }
  }
  return { finderErrors, timingErrors };
}

export function decodeGrid(
  grid,
  { maxFinderErrors = 8, maxTimingErrors = 10, supportedVersion = VERSION } = {},
) {
  const { finderErrors, timingErrors } = structureErrors(grid);
  if (finderErrors > maxFinderErrors || timingErrors > maxTimingErrors) {
    throw new OrtmDecodeError('structure-mismatch', { finderErrors, timingErrors });
  }
  const bits = PAYLOAD_CELLS.map(([row, col]) => grid[row][col]);
  const version = bitsToInt(bits.slice(0, 4));
  const frameSeq = bitsToInt(bits.slice(4, 20));
  const timestampMs = bitsToInt(bits.slice(20, 52));
  const crc16 = bitsToInt(bits.slice(52, 68));
  if (version !== supportedVersion) throw new OrtmDecodeError('unsupported-version', { version });
  const expectedCrc16 = crc16CcittFalse(crcInput(version, frameSeq, timestampMs));
  if (crc16 !== expectedCrc16) throw new OrtmDecodeError('crc-mismatch', { crc16, expectedCrc16 });
  return { version, frameSeq, timestampMs, crc16, finderErrors, timingErrors };
}

export function frameAgeMs(nowMs, timestampMs, { maxAgeMs = 5000 } = {}) {
  const ageMs = ((Number(nowMs) >>> 0) - (Number(timestampMs) >>> 0)) >>> 0;
  if (maxAgeMs !== null && ageMs >= maxAgeMs) {
    throw new OrtmDecodeError('latency-out-of-range', { ageMs });
  }
  return ageMs;
}

export function gridRows(grid) {
  validateGrid(grid);
  return grid.map((row) => row.join(''));
}

export function rowsToGrid(rows) {
  if (!Array.isArray(rows) || rows.length !== GRID_SIZE || rows.some((row) => typeof row !== 'string' || row.length !== GRID_SIZE)) {
    throw new OrtmDecodeError('invalid-grid-size');
  }
  const grid = rows.map((row) => [...row].map((bit) => Number(bit)));
  validateGrid(grid);
  return grid;
}
