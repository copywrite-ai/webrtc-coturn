"""Dependency-free ORTM v0 grayscale renderer and fixed-ROI decoder."""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Sequence

from .codec import (
    FINDER_LAYOUT_FOUR,
    FINDER_SIZE,
    GRID_SIZE,
    DecodeError,
    DecodedMarker,
    decode_grid,
    encoded_cells,
    encode_grid,
    finder_bit,
    in_active_finder,
)


@dataclass(frozen=True)
class RasterProfile:
    x: float = 24
    y: float = 24
    cell: float = 12
    padding: float = 12

    @property
    def box_size(self) -> float:
        return GRID_SIZE * self.cell + self.padding * 2


@dataclass(frozen=True)
class RasterDecode:
    marker: DecodedMarker
    threshold: float
    contrast: float
    black_level: float
    white_level: float
    profile: RasterProfile


DEFAULT_SCALES = (1.0, 0.95, 1.05, 0.9, 1.1)
DEFAULT_OFFSETS = (0, -4, 4, -8, 8, -12, 12)
SAMPLE_POSITIONS = (0.2, 0.5, 0.8)


def render_luma_frame(
    frame_seq: int,
    timestamp_ms: int,
    *,
    width: int = 640,
    height: int = 480,
    profile: RasterProfile = RasterProfile(),
    background: int = 127,
    black: int = 0,
    white: int = 255,
    finder_layout: str = FINDER_LAYOUT_FOUR,
) -> list[list[int]]:
    frame = [[background for _ in range(width)] for _ in range(height)]
    grid = encode_grid(
        frame_seq,
        timestamp_ms,
        finder_layout=finder_layout,
    )
    if profile.x < 0 or profile.y < 0 or profile.x + profile.box_size > width or profile.y + profile.box_size > height:
        raise ValueError("marker does not fit in frame")

    left = int(round(profile.x))
    top = int(round(profile.y))
    right = int(round(profile.x + profile.box_size))
    bottom = int(round(profile.y + profile.box_size))
    for y in range(top, bottom):
        for x in range(left, right):
            frame[y][x] = white

    origin_x = profile.x + profile.padding
    origin_y = profile.y + profile.padding
    for row, col in encoded_cells(finder_layout):
        value = black if grid[row][col] else white
        x0 = int(round(origin_x + col * profile.cell))
        x1 = int(round(origin_x + (col + 1) * profile.cell))
        y0 = int(round(origin_y + row * profile.cell))
        y1 = int(round(origin_y + (row + 1) * profile.cell))
        for y in range(y0, y1):
            for x in range(x0, x1):
                frame[y][x] = value
    return frame


def _sample_cell(frame: Sequence[Sequence[int]], profile: RasterProfile, row: int, col: int) -> float:
    height = len(frame)
    width = len(frame[0]) if height else 0
    origin_x = profile.x + profile.padding + col * profile.cell
    origin_y = profile.y + profile.padding + row * profile.cell
    samples: list[int] = []
    for py in SAMPLE_POSITIONS:
        for px in SAMPLE_POSITIONS:
            x = round(origin_x + profile.cell * px)
            y = round(origin_y + profile.cell * py)
            if not (0 <= x < width and 0 <= y < height):
                raise DecodeError("candidate-out-of-frame")
            samples.append(int(frame[y][x]))
    return sum(samples) / len(samples)


def candidate_profiles(
    nominal: RasterProfile = RasterProfile(),
    *,
    scales: Sequence[float] = DEFAULT_SCALES,
    offsets: Sequence[int] = DEFAULT_OFFSETS,
) -> list[RasterProfile]:
    return [
        RasterProfile(
            x=nominal.x + dx,
            y=nominal.y + dy,
            cell=nominal.cell * scale,
            padding=nominal.padding * scale,
        )
        for scale in scales
        for dx in offsets
        for dy in offsets
    ]


def decode_luma_frame(
    frame: Sequence[Sequence[int]],
    *,
    nominal: RasterProfile = RasterProfile(),
    scales: Sequence[float] = DEFAULT_SCALES,
    offsets: Sequence[int] = DEFAULT_OFFSETS,
    min_contrast: float = 32,
    finder_layout: str = FINDER_LAYOUT_FOUR,
) -> RasterDecode:
    if not frame or not frame[0] or any(len(row) != len(frame[0]) for row in frame):
        raise DecodeError("invalid-frame")
    height = len(frame)
    width = len(frame[0])
    best_error: DecodeError | None = None

    for profile in candidate_profiles(nominal, scales=scales, offsets=offsets):
        if profile.x < 0 or profile.y < 0 or profile.x + profile.box_size > width or profile.y + profile.box_size > height:
            continue
        try:
            luma = [[_sample_cell(frame, profile, row, col) for col in range(GRID_SIZE)] for row in range(GRID_SIZE)]
        except DecodeError as error:
            best_error = best_error or error
            continue

        finder_black: list[float] = []
        finder_white: list[float] = []
        for row in range(GRID_SIZE):
            for col in range(GRID_SIZE):
                if not in_active_finder(row, col, finder_layout):
                    continue
                target = finder_black if finder_bit(row, col) else finder_white
                target.append(luma[row][col])
        black_level = median(finder_black)
        white_level = median(finder_white)
        contrast = white_level - black_level
        threshold = (black_level + white_level) / 2
        if contrast < min_contrast:
            best_error = DecodeError("low-contrast", contrast=round(contrast))
            continue

        grid = [[int(luma[row][col] < threshold) for col in range(GRID_SIZE)] for row in range(GRID_SIZE)]
        try:
            marker = decode_grid(grid, finder_layout=finder_layout)
        except DecodeError as error:
            best_error = error
            continue
        return RasterDecode(
            marker=marker,
            threshold=threshold,
            contrast=contrast,
            black_level=black_level,
            white_level=white_level,
            profile=profile,
        )
    raise best_error or DecodeError("no-candidate")
