"""Logical ORTM v0 codec with no image or GStreamer dependencies."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, Sequence

GRID_SIZE = 32
FINDER_SIZE = 4
TIMING_INDEX = 4
VERSION = 0
PAYLOAD_BITS = 4 + 16 + 32 + 16
UINT32_MASK = 0xFFFFFFFF
FINDER_LAYOUT_FOUR = "four"
FINDER_LAYOUT_THREE = "three"
FINDER_LAYOUT_TWO_TOP = "two-top"
FINDER_LAYOUTS = frozenset(
    (FINDER_LAYOUT_FOUR, FINDER_LAYOUT_THREE, FINDER_LAYOUT_TWO_TOP)
)


@dataclass(frozen=True)
class DecodedMarker:
    version: int
    frame_seq: int
    timestamp_ms: int
    crc16: int
    finder_errors: int
    timing_errors: int


class DecodeError(ValueError):
    """Raised when a logical grid is not a valid ORTM v0 marker."""

    def __init__(self, reason: str, **details: int) -> None:
        super().__init__(reason)
        self.reason = reason
        self.details = details


def crc16_ccitt_false(data: bytes | bytearray | Iterable[int]) -> int:
    crc = 0xFFFF
    for byte in data:
        crc ^= (byte & 0xFF) << 8
        for _ in range(8):
            crc = ((crc << 1) ^ 0x1021) & 0xFFFF if crc & 0x8000 else (crc << 1) & 0xFFFF
    return crc


def bits_from_int(value: int, width: int) -> list[int]:
    return [(value >> shift) & 1 for shift in range(width - 1, -1, -1)]


def bits_to_int(bits: Sequence[int]) -> int:
    value = 0
    for bit in bits:
        value = (value << 1) | (int(bit) & 1)
    return value


def in_finder(row: int, col: int) -> bool:
    return (
        (row < FINDER_SIZE and col < FINDER_SIZE)
        or (row < FINDER_SIZE and col >= GRID_SIZE - FINDER_SIZE)
        or (row >= GRID_SIZE - FINDER_SIZE and col < FINDER_SIZE)
        or (row >= GRID_SIZE - FINDER_SIZE and col >= GRID_SIZE - FINDER_SIZE)
    )


def in_bottom_right_finder(row: int, col: int) -> bool:
    return row >= GRID_SIZE - FINDER_SIZE and col >= GRID_SIZE - FINDER_SIZE


def in_bottom_finder(row: int, col: int) -> bool:
    return row >= GRID_SIZE - FINDER_SIZE and (
        col < FINDER_SIZE or col >= GRID_SIZE - FINDER_SIZE
    )


def validate_finder_layout(finder_layout: str) -> None:
    if finder_layout not in FINDER_LAYOUTS:
        raise ValueError(f"unsupported finder layout {finder_layout!r}")


def in_active_finder(
    row: int,
    col: int,
    finder_layout: str = FINDER_LAYOUT_FOUR,
) -> bool:
    validate_finder_layout(finder_layout)
    if not in_finder(row, col):
        return False
    if finder_layout == FINDER_LAYOUT_THREE:
        return not in_bottom_right_finder(row, col)
    if finder_layout == FINDER_LAYOUT_TWO_TOP:
        return not in_bottom_finder(row, col)
    return True


def is_reserved(row: int, col: int) -> bool:
    return in_finder(row, col) or row == TIMING_INDEX or col == TIMING_INDEX


def finder_bit(row: int, col: int) -> int:
    local_row = row if row < FINDER_SIZE else row - (GRID_SIZE - FINDER_SIZE)
    local_col = col if col < FINDER_SIZE else col - (GRID_SIZE - FINDER_SIZE)
    return int(
        local_row in (0, FINDER_SIZE - 1)
        or local_col in (0, FINDER_SIZE - 1)
    )


def payload_cells() -> tuple[tuple[int, int], ...]:
    cells = [
        (row, col)
        for row in range(GRID_SIZE)
        for col in range(GRID_SIZE)
        if not is_reserved(row, col)
    ]
    return tuple(cells[:PAYLOAD_BITS])


PAYLOAD_CELLS = payload_cells()
ENCODED_CELLS = frozenset(
    [(row, col) for row in range(GRID_SIZE) for col in range(GRID_SIZE) if is_reserved(row, col)]
    + list(PAYLOAD_CELLS)
)


def encoded_cells(
    finder_layout: str = FINDER_LAYOUT_FOUR,
) -> frozenset[tuple[int, int]]:
    validate_finder_layout(finder_layout)
    if finder_layout == FINDER_LAYOUT_FOUR:
        return ENCODED_CELLS
    omitted = (
        in_bottom_finder
        if finder_layout == FINDER_LAYOUT_TWO_TOP
        else in_bottom_right_finder
    )
    return frozenset(
        (row, col)
        for row, col in ENCODED_CELLS
        if not omitted(row, col)
    )


def crc_input(version: int, frame_seq: int, timestamp_ms: int) -> bytes:
    return bytes(
        [
            version & 0x0F,
            (frame_seq >> 8) & 0xFF,
            frame_seq & 0xFF,
            (timestamp_ms >> 24) & 0xFF,
            (timestamp_ms >> 16) & 0xFF,
            (timestamp_ms >> 8) & 0xFF,
            timestamp_ms & 0xFF,
        ]
    )


def payload(version: int, frame_seq: int, timestamp_ms: int) -> tuple[list[int], int]:
    if not 0 <= version <= 0x0F:
        raise ValueError("version must fit in 4 bits")
    if not 0 <= frame_seq <= 0xFFFF:
        raise ValueError("frame_seq must fit in 16 bits")
    if not 0 <= timestamp_ms <= UINT32_MASK:
        raise ValueError("timestamp_ms must fit in 32 bits")
    checksum = crc16_ccitt_false(crc_input(version, frame_seq, timestamp_ms))
    bits = (
        bits_from_int(version, 4)
        + bits_from_int(frame_seq, 16)
        + bits_from_int(timestamp_ms, 32)
        + bits_from_int(checksum, 16)
    )
    return bits, checksum


def encode_grid(
    frame_seq: int,
    timestamp_ms: int,
    version: int = VERSION,
    *,
    finder_layout: str = FINDER_LAYOUT_FOUR,
) -> list[list[int]]:
    validate_finder_layout(finder_layout)
    payload_bits, _ = payload(version, frame_seq, timestamp_ms)
    grid = [[0] * GRID_SIZE for _ in range(GRID_SIZE)]

    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            if in_active_finder(row, col, finder_layout):
                grid[row][col] = finder_bit(row, col)
            elif row == TIMING_INDEX:
                grid[row][col] = col % 2
            elif col == TIMING_INDEX:
                grid[row][col] = row % 2

    for (row, col), bit in zip(PAYLOAD_CELLS, payload_bits, strict=True):
        grid[row][col] = bit
    return grid


def validate_grid_shape(grid: Sequence[Sequence[int]]) -> None:
    if len(grid) != GRID_SIZE or any(len(row) != GRID_SIZE for row in grid):
        raise DecodeError("invalid-grid-size")
    if any(int(bit) not in (0, 1) for row in grid for bit in row):
        raise DecodeError("invalid-grid-bit")


def structure_errors(
    grid: Sequence[Sequence[int]],
    *,
    finder_layout: str = FINDER_LAYOUT_FOUR,
) -> tuple[int, int]:
    validate_finder_layout(finder_layout)
    finder_errors = 0
    timing_errors = 0
    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            bit = int(grid[row][col])
            if in_active_finder(row, col, finder_layout):
                finder_errors += int(bit != finder_bit(row, col))
            elif row == TIMING_INDEX:
                timing_errors += int(bit != col % 2)
            elif col == TIMING_INDEX:
                timing_errors += int(bit != row % 2)
    return finder_errors, timing_errors


def decode_grid(
    grid: Sequence[Sequence[int]],
    *,
    max_finder_errors: int = 8,
    max_timing_errors: int = 10,
    supported_version: int = VERSION,
    finder_layout: str = FINDER_LAYOUT_FOUR,
) -> DecodedMarker:
    validate_grid_shape(grid)
    finder_errors, timing_errors = structure_errors(
        grid,
        finder_layout=finder_layout,
    )
    if finder_errors > max_finder_errors or timing_errors > max_timing_errors:
        raise DecodeError(
            "structure-mismatch",
            finder_errors=finder_errors,
            timing_errors=timing_errors,
        )

    bits = [int(grid[row][col]) for row, col in PAYLOAD_CELLS]
    version = bits_to_int(bits[0:4])
    frame_seq = bits_to_int(bits[4:20])
    timestamp_ms = bits_to_int(bits[20:52])
    checksum = bits_to_int(bits[52:68])
    if version != supported_version:
        raise DecodeError("unsupported-version", version=version)
    expected = crc16_ccitt_false(crc_input(version, frame_seq, timestamp_ms))
    if checksum != expected:
        raise DecodeError("crc-mismatch", crc16=checksum, expected_crc16=expected)
    return DecodedMarker(
        version=version,
        frame_seq=frame_seq,
        timestamp_ms=timestamp_ms,
        crc16=checksum,
        finder_errors=finder_errors,
        timing_errors=timing_errors,
    )


def frame_age_ms(now_ms: int, timestamp_ms: int, *, max_age_ms: int | None = 5000) -> int:
    age = ((now_ms & UINT32_MASK) - (timestamp_ms & UINT32_MASK)) & UINT32_MASK
    if max_age_ms is not None and age >= max_age_ms:
        raise DecodeError("latency-out-of-range", age_ms=age)
    return age


def grid_rows(grid: Sequence[Sequence[int]]) -> list[str]:
    validate_grid_shape(grid)
    return ["".join(str(int(bit)) for bit in row) for row in grid]


def rows_to_grid(rows: Sequence[str]) -> list[list[int]]:
    if len(rows) != GRID_SIZE or any(len(row) != GRID_SIZE for row in rows):
        raise DecodeError("invalid-grid-size")
    return [[int(bit) for bit in row] for row in rows]
