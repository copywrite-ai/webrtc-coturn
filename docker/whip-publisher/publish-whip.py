#!/usr/bin/env python3
import os
import signal
import sys
import time
from collections import deque
from datetime import datetime
from time import monotonic_ns
from zoneinfo import ZoneInfo

import gi

gi.require_foreign("cairo")
gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
import cairo
from gi.repository import GLib, Gst  # noqa: E402

from ortm.codec import (
    FINDER_LAYOUT_FOUR,
    GRID_SIZE,
    VERSION as ORTM_VERSION,
    encode_grid,
    encoded_cells,
    validate_finder_layout,
)


def env_required(name):
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def env_value(name, default):
    return os.environ.get(name, default)


def env_int(name, default):
    return int(env_value(name, str(default)))


def env_float(name, default):
    return float(env_value(name, str(default)))


def env_optional_value(name):
    value = os.environ.get(name)
    if value is None:
        return None
    value = value.strip()
    return value or None


def env_optional_int(name):
    value = env_optional_value(name)
    return int(value) if value is not None else None


def gst_quote(value):
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def wallclock_ms():
    return time.time_ns() // 1_000_000


def timestamp_text(timezone):
    return datetime.now(timezone).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


class StatsWindow:
    def __init__(self):
        self.count = 0
        self.total_ms = 0.0
        self.min_ms = None
        self.max_ms = None

    def add(self, value_ms):
        self.count += 1
        self.total_ms += value_ms
        self.min_ms = value_ms if self.min_ms is None else min(self.min_ms, value_ms)
        self.max_ms = value_ms if self.max_ms is None else max(self.max_ms, value_ms)

    def avg(self):
        return self.total_ms / self.count if self.count else 0.0


class EncodedFrameStats:
    def __init__(self, window_seconds):
        self.window_ns = int(max(window_seconds, 0.1) * 1_000_000_000)
        self.count = 0
        self.total_bytes = 0
        self.min_bytes = None
        self.max_bytes = None
        self.last_bytes = 0
        self.keyframe_count = 0
        self.keyframe_total_bytes = 0
        self.keyframe_last_bytes = 0
        self.keyframe_max_bytes = 0
        self.delta_count = 0
        self.delta_total_bytes = 0
        self.delta_max_bytes = 0
        self.byte_window = deque()
        self.byte_window_total = 0

    def add(self, now_ns, size_bytes, is_keyframe):
        self.count += 1
        self.total_bytes += size_bytes
        self.last_bytes = size_bytes
        self.min_bytes = (
            size_bytes if self.min_bytes is None else min(self.min_bytes, size_bytes)
        )
        self.max_bytes = (
            size_bytes if self.max_bytes is None else max(self.max_bytes, size_bytes)
        )
        if is_keyframe:
            self.keyframe_count += 1
            self.keyframe_total_bytes += size_bytes
            self.keyframe_last_bytes = size_bytes
            self.keyframe_max_bytes = max(self.keyframe_max_bytes, size_bytes)
        else:
            self.delta_count += 1
            self.delta_total_bytes += size_bytes
            self.delta_max_bytes = max(self.delta_max_bytes, size_bytes)

        self.byte_window.append((now_ns, size_bytes))
        self.byte_window_total += size_bytes
        cutoff = now_ns - self.window_ns
        while self.byte_window and self.byte_window[0][0] < cutoff:
            _timestamp, old_size = self.byte_window.popleft()
            self.byte_window_total -= old_size

    def avg_bytes(self):
        return self.total_bytes / self.count if self.count else 0.0

    def keyframe_avg_bytes(self):
        return (
            self.keyframe_total_bytes / self.keyframe_count
            if self.keyframe_count
            else 0.0
        )

    def delta_avg_bytes(self):
        return self.delta_total_bytes / self.delta_count if self.delta_count else 0.0

    def window_bitrate_kbps(self, now_ns):
        if len(self.byte_window) < 2:
            return 0.0
        elapsed_ns = max(now_ns - self.byte_window[0][0], 1)
        return (self.byte_window_total * 8.0) / (elapsed_ns / 1_000_000_000.0) / 1000.0

    def window_fps(self, now_ns):
        if len(self.byte_window) < 2:
            return 0.0
        elapsed_ns = max(now_ns - self.byte_window[0][0], 1)
        return ((len(self.byte_window) - 1) * 1_000_000_000.0) / elapsed_ns


class ORTMOverlayRenderer:
    def __init__(
        self,
        x,
        y,
        cell,
        padding,
        metrics_interval_frames,
        timezone,
        draw_timestamp_text,
        background_alpha,
        cell_alpha,
        border_alpha,
        finder_layout,
    ):
        self.x = x
        self.y = y
        self.cell = cell
        self.padding = padding
        self.frame_seq = 0
        self.metrics_interval_frames = max(metrics_interval_frames, 1)
        self.timezone = timezone
        self.draw_timestamp_text = draw_timestamp_text
        self.background_alpha = max(0.0, min(background_alpha, 1.0))
        self.cell_alpha = max(0.0, min(cell_alpha, 1.0))
        self.border_alpha = max(0.0, min(border_alpha, 1.0))
        validate_finder_layout(finder_layout)
        self.finder_layout = finder_layout
        self.encoded_cells = encoded_cells(finder_layout)
        self.render_stats = StatsWindow()
        self.frame_markers_by_pts = {}
        self.frame_marker_order = deque()
        self.marker_duplicate_pts = 0
        self.marker_expired_pts = 0

    @property
    def marker_size(self):
        return GRID_SIZE * self.cell + self.padding * 2

    def _remember_frame_marker(self, pts_ns, marker):
        if pts_ns is None:
            return
        if pts_ns in self.frame_markers_by_pts:
            self.marker_duplicate_pts += 1
        self.frame_markers_by_pts[pts_ns] = marker
        self.frame_marker_order.append(pts_ns)
        while len(self.frame_marker_order) > 300:
            old_pts = self.frame_marker_order.popleft()
            if self.frame_markers_by_pts.pop(old_pts, None) is not None:
                self.marker_expired_pts += 1

    def take_frame_marker(self, pts_ns):
        if pts_ns is None:
            return None
        return self.frame_markers_by_pts.pop(pts_ns, None)

    def take_nearest_frame_marker(self, pts_ns, tolerance_ns):
        if pts_ns is None:
            return None
        best_pts = None
        best_delta = None
        for candidate_pts in self.frame_markers_by_pts.keys():
            delta = abs(candidate_pts - pts_ns)
            if delta <= tolerance_ns and (best_delta is None or delta < best_delta):
                best_pts = candidate_pts
                best_delta = delta
        if best_pts is None:
            return None
        marker = self.frame_markers_by_pts.pop(best_pts)
        marker["pts_match_delta_ns"] = best_delta
        return marker

    def draw(self, _overlay, context, timestamp, _duration):
        started_at = monotonic_ns()
        current_frame_seq = self.frame_seq
        self.frame_seq = (self.frame_seq + 1) & 0xFFFF
        timestamp_ms_full = wallclock_ms()
        timestamp_ms_low32 = timestamp_ms_full & 0xFFFFFFFF
        ortm_bits = encode_grid(
            current_frame_seq,
            timestamp_ms_low32,
            ORTM_VERSION,
            finder_layout=self.finder_layout,
        )

        context.save()
        try:
            context.set_antialias(cairo.ANTIALIAS_NONE)
            context.set_line_width(2.0)

            if self.background_alpha > 0:
                context.set_source_rgba(1.0, 1.0, 1.0, self.background_alpha)
                context.rectangle(self.x, self.y, self.marker_size, self.marker_size)
                context.fill()

            grid_origin_x = self.x + self.padding
            grid_origin_y = self.y + self.padding

            context.set_source_rgba(1.0, 1.0, 1.0, self.cell_alpha)
            for row in range(GRID_SIZE):
                for col in range(GRID_SIZE):
                    if (
                        ortm_bits[row][col] != 0
                        or (row, col) not in self.encoded_cells
                    ):
                        continue
                    context.rectangle(
                        grid_origin_x + col * self.cell,
                        grid_origin_y + row * self.cell,
                        self.cell,
                        self.cell,
                    )
            context.fill()

            context.set_source_rgba(0.0, 0.0, 0.0, self.cell_alpha)
            for row in range(GRID_SIZE):
                for col in range(GRID_SIZE):
                    if ortm_bits[row][col] != 1:
                        continue
                    context.rectangle(
                        grid_origin_x + col * self.cell,
                        grid_origin_y + row * self.cell,
                        self.cell,
                        self.cell,
                    )
            context.fill()

            if self.border_alpha > 0:
                context.set_source_rgba(0.0, 0.0, 0.0, self.border_alpha)
                context.rectangle(self.x, self.y, self.marker_size, self.marker_size)
                context.stroke()

            if self.draw_timestamp_text:
                label = timestamp_text(self.timezone)
                label_y = self.y + self.marker_size + 28
                context.select_font_face(
                    "Sans",
                    cairo.FONT_SLANT_NORMAL,
                    cairo.FONT_WEIGHT_BOLD,
                )
                context.set_font_size(26)
                extents = context.text_extents(label)
                text_box_x = self.x
                text_box_y = label_y - extents.height - 10
                text_box_w = extents.width + 20
                text_box_h = extents.height + 20
                context.set_source_rgb(1.0, 1.0, 1.0)
                context.rectangle(text_box_x, text_box_y, text_box_w, text_box_h)
                context.fill()
                context.set_source_rgb(0.0, 0.0, 0.0)
                context.rectangle(text_box_x, text_box_y, text_box_w, text_box_h)
                context.stroke()
                context.move_to(self.x + 10, label_y)
                context.show_text(label)
        finally:
            context.restore()

        render_ms = (monotonic_ns() - started_at) / 1_000_000.0
        pts_ns = (
            int(timestamp)
            if timestamp is not None and int(timestamp) != Gst.CLOCK_TIME_NONE
            else None
        )
        self._remember_frame_marker(
            pts_ns,
            {
                "pts_ns": pts_ns,
                "frame_seq": current_frame_seq,
                "timestamp_ms_full": timestamp_ms_full,
                "render_ms": render_ms,
                "overlay_done_monotonic_ns": monotonic_ns(),
            },
        )
        self.render_stats.add(render_ms)
        if self.render_stats.count % self.metrics_interval_frames == 0:
            print(
                "ORTM render_ms "
                f"avg={self.render_stats.avg():.1f} "
                f"min={self.render_stats.min_ms:.1f} "
                f"max={self.render_stats.max_ms:.1f} "
                f"samples={self.render_stats.count}",
                flush=True,
            )


def build_pipeline_description(
    *,
    video_source,
    video_device,
    video_source_width,
    video_source_height,
    video_source_fps,
    source_pipeline,
    pattern,
    testsrc_horizontal_speed,
    width,
    height,
    fps,
    bitrate_kbps,
    speed_preset,
    key_int_max,
    x264_option_string,
    x264_threads,
    x264_sliced_threads,
    x264_vbv_buf_capacity_ms,
    sink_args,
):
    encoder_args = [
        f"bitrate={bitrate_kbps}",
        f"speed-preset={gst_quote(speed_preset)}",
        "tune=zerolatency",
        f"key-int-max={key_int_max}",
        "bframes=0",
        f"option-string={gst_quote(x264_option_string)}",
    ]
    if x264_threads is not None:
        encoder_args.append(f"threads={x264_threads}")
    if x264_sliced_threads is not None:
        encoder_args.append(f"sliced-threads={'true' if x264_sliced_threads else 'false'}")
    if x264_vbv_buf_capacity_ms is not None:
        encoder_args.append(f"vbv-buf-capacity={x264_vbv_buf_capacity_ms}")

    if source_pipeline:
        source_description = source_pipeline
    elif video_source == "testsrc":
        source_description = (
            f"videotestsrc is-live=true pattern={gst_quote(pattern)} "
            f"horizontal-speed={testsrc_horizontal_speed}"
        )
    elif video_source == "avf":
        source_caps = ""
        if video_source_width is not None and video_source_height is not None:
            source_caps = (
                f"! video/x-raw,width={video_source_width},height={video_source_height},"
                f"framerate={video_source_fps or fps}/1 "
            )
        source_description = (
            f"avfvideosrc device-index={int(video_device)} "
            f"{source_caps}"
            "! queue leaky=downstream max-size-buffers=2 max-size-time=0 max-size-bytes=0"
        )
    elif video_source == "v4l2":
        source_description = (
            f"v4l2src device={gst_quote(video_device)} do-timestamp=true "
            "! queue leaky=downstream max-size-buffers=2 max-size-time=0 max-size-bytes=0"
        )
    else:
        raise SystemExit(
            f"unsupported VIDEO_SOURCE={video_source!r}; use testsrc, avf, v4l2, or pipeline"
        )

    return (
        f"{source_description} "
        "! videoconvert "
        "! videoscale "
        "! videorate "
        f"! video/x-raw,width={width},height={height},framerate={fps}/1,format=BGRx "
        "! cairooverlay name=ortm_overlay "
        "! videoconvert "
        "! video/x-raw,format=I420 "
        "! identity name=pre_encoder_probe silent=true "
        f"! x264enc name=encoder {' '.join(encoder_args)} "
        "! identity name=post_encoder_probe silent=true "
        "! video/x-h264,profile=baseline "
        "! h264parse name=h264parse0 config-interval=-1 "
        "! identity name=post_parse_probe silent=true "
        "! identity name=send_probe silent=true "
        f"! whipclientsink name=ws {' '.join(sink_args)}"
    )


def main():
    gst_debug_level = env_value("GST_DEBUG_LEVEL", "2")
    os.environ["GST_DEBUG"] = os.environ.get(
        "GST_DEBUG", f"whip*:6,webrtc*:6,rswebrtc*:6,ice*:5,nice*:5,*:{gst_debug_level}"
    )

    Gst.init(None)

    stream_name = env_required("STREAM_NAME")
    whip_base_url = env_required("WHIP_BASE_URL")
    device_id = env_required("DEVICE_ID").strip("/")
    whip_include_device = env_value("WHIP_INCLUDE_DEVICE", "1")

    width = env_int("WIDTH", 1280)
    height = env_int("HEIGHT", 720)
    fps = env_int("FPS", 30)
    bitrate_kbps = env_int("BITRATE_KBPS", 8000)
    key_int_max = env_int("KEY_INT_MAX", 60)
    video_source = env_value("VIDEO_SOURCE", "testsrc").strip().lower()
    video_device = env_value("VIDEO_DEVICE", "/dev/video0")
    video_source_width = env_optional_int("VIDEO_SOURCE_WIDTH")
    video_source_height = env_optional_int("VIDEO_SOURCE_HEIGHT")
    video_source_fps = env_optional_int("VIDEO_SOURCE_FPS")
    if video_source == "avf" and video_source_width is None and video_source_height is None:
        output_ratio = width / height
        if abs(output_ratio - (16 / 9)) < abs(output_ratio - (4 / 3)):
            video_source_width = 1280
            video_source_height = 720
        else:
            video_source_width = 640
            video_source_height = 480
    source_pipeline = env_optional_value("SOURCE_PIPELINE")
    source_mode = "pipeline" if source_pipeline else video_source
    pattern = env_value("PATTERN", "checkers-8")
    testsrc_horizontal_speed = env_int("TESTSRC_HORIZONTAL_SPEED", 2)
    speed_preset = env_value("SPEED_PRESET", "ultrafast")
    x264_option_string = env_value(
        "X264_OPTION_STRING", "nal-hrd=cbr:force-cfr=1:filler=1"
    )
    x264_threads = env_optional_int("X264_THREADS")
    x264_sliced_threads = env_optional_int("X264_SLICED_THREADS")
    x264_vbv_buf_capacity_ms = env_optional_int("X264_VBV_BUF_CAPACITY_MS")
    whip_stun_server = env_value("WHIP_STUN_SERVER", "")
    whip_turn_server = env_value("WHIP_TURN_SERVER", "")
    whip_turn_server_2 = env_value("WHIP_TURN_SERVER_2", "")
    whip_force_turn = env_value("WHIP_FORCE_TURN", "0")
    whip_cc_min_bitrate_bps = env_optional_int("WHIP_CC_MIN_BITRATE_BPS")
    whip_cc_max_bitrate_bps = env_optional_int("WHIP_CC_MAX_BITRATE_BPS")
    whip_mitigation_modes = env_optional_value("WHIP_MITIGATION_MODES")
    whip_sink_extra_args = env_value("WHIP_SINK_EXTRA_ARGS", "")
    timestamp_overlay = env_value("TIMESTAMP_OVERLAY", "0")
    timestamp_tz_name = env_value("TIMESTAMP_TZ", "Asia/Shanghai")
    timestamp_tz = ZoneInfo(timestamp_tz_name)
    metrics_enabled = env_value("PIPELINE_METRICS", "1")
    metrics_interval_frames = env_int("PIPELINE_METRICS_INTERVAL_FRAMES", 30)
    frame_metrics_window_seconds = env_float("FRAME_METRICS_WINDOW_SECONDS", 2.0)
    sender_frame_log_interval = env_int("SENDER_FRAME_LOG_INTERVAL", 30)
    rate_watchdog_enabled = env_value("PIPELINE_RATE_WATCHDOG", "1") == "1"
    rate_watchdog_warmup_seconds = env_float(
        "PIPELINE_RATE_WATCHDOG_WARMUP_SECONDS", 5.0
    )
    rate_watchdog_max_fps_ratio = env_float(
        "PIPELINE_RATE_WATCHDOG_MAX_FPS_RATIO", 1.5
    )
    rate_watchdog_max_bitrate_ratio = env_float(
        "PIPELINE_RATE_WATCHDOG_MAX_BITRATE_RATIO", 2.0
    )
    rate_watchdog_consecutive_limit = env_int(
        "PIPELINE_RATE_WATCHDOG_CONSECUTIVE", 3
    )
    if rate_watchdog_warmup_seconds < 0:
        raise ValueError("PIPELINE_RATE_WATCHDOG_WARMUP_SECONDS must be >= 0")
    if rate_watchdog_max_fps_ratio <= 1:
        raise ValueError("PIPELINE_RATE_WATCHDOG_MAX_FPS_RATIO must be > 1")
    if rate_watchdog_max_bitrate_ratio <= 1:
        raise ValueError("PIPELINE_RATE_WATCHDOG_MAX_BITRATE_RATIO must be > 1")
    if rate_watchdog_consecutive_limit < 1:
        raise ValueError("PIPELINE_RATE_WATCHDOG_CONSECUTIVE must be >= 1")
    ortm_x = env_int("ORTM_X", 24)
    ortm_y = env_int("ORTM_Y", 24)
    ortm_cell = env_int("ORTM_CELL", 12)
    ortm_padding = env_int("ORTM_PADDING", 12)
    ortm_background_alpha = env_float("ORTM_BACKGROUND_ALPHA", 1.0)
    ortm_cell_alpha = env_float("ORTM_CELL_ALPHA", 1.0)
    ortm_border_alpha = env_float("ORTM_BORDER_ALPHA", 1.0)
    ortm_finder_layout = env_value("ORTM_FINDER_LAYOUT", FINDER_LAYOUT_FOUR)
    validate_finder_layout(ortm_finder_layout)

    base_url = whip_base_url.rstrip("/")
    if whip_include_device == "1":
        whip_url = f"{base_url}/{device_id}/{stream_name}/whip"
    else:
        whip_url = f"{base_url}/{stream_name}/whip"

    sink_args = [f"signaller::whip-endpoint={gst_quote(whip_url)}"]
    if whip_stun_server:
        sink_args.append(f"stun-server={gst_quote(whip_stun_server)}")
    if whip_turn_server:
        if whip_turn_server_2:
            sink_args.append(
                f'turn-servers=<"{whip_turn_server}", "{whip_turn_server_2}">'
            )
        else:
            sink_args.append(f'turn-servers=<"{whip_turn_server}">')
    if whip_force_turn == "1":
        sink_args.append("ice-transport-policy=relay")
    if whip_cc_min_bitrate_bps is not None:
        sink_args.append(f"min-bitrate={whip_cc_min_bitrate_bps}")
    if whip_cc_max_bitrate_bps is not None:
        sink_args.append(f"max-bitrate={whip_cc_max_bitrate_bps}")
    if whip_mitigation_modes is not None:
        sink_args.append(f"enable-mitigation-modes={gst_quote(whip_mitigation_modes)}")
    if whip_sink_extra_args:
        sink_args.append(whip_sink_extra_args)

    sink_args_log = " ".join(sink_args)
    if whip_turn_server:
        sink_args_log = sink_args_log.replace(whip_turn_server, "<turn-redacted>")
    if whip_turn_server_2:
        sink_args_log = sink_args_log.replace(whip_turn_server_2, "<turn2-redacted>")

    print("Starting WHIP publisher", flush=True)
    print(f"  device       : {device_id}", flush=True)
    print(f"  stream       : {stream_name}", flush=True)
    print(f"  target       : {whip_url}", flush=True)
    print(
        f"  path mode    : {'device/stream' if whip_include_device == '1' else 'stream-only'}",
        flush=True,
    )
    print(f"  resolution   : {width}x{height}", flush=True)
    print(f"  fps          : {fps}", flush=True)
    print(f"  bitrate      : {bitrate_kbps} kbps", flush=True)
    print(f"  video source : {source_mode}", flush=True)
    if source_mode == "testsrc":
        print(f"  pattern      : {pattern}", flush=True)
    if source_mode == "v4l2":
        print(f"  video device : {video_device}", flush=True)
    if source_mode == "avf":
        print(f"  avf index    : {video_device}", flush=True)
        if video_source_width is not None and video_source_height is not None:
            print(
                "  avf caps     : "
                f"{video_source_width}x{video_source_height}@{video_source_fps or fps}",
                flush=True,
            )
    if source_pipeline:
        print(f"  src pipeline : {source_pipeline}", flush=True)
    print(f"  x264 preset  : {speed_preset}", flush=True)
    print(f"  x264 options : {x264_option_string}", flush=True)
    print(
        f"  x264 threads : {x264_threads if x264_threads is not None else '<default>'}",
        flush=True,
    )
    print(
        "  x264 sliced  : "
        f"{x264_sliced_threads if x264_sliced_threads is not None else '<default>'}",
        flush=True,
    )
    print(
        "  x264 vbv ms  : "
        f"{x264_vbv_buf_capacity_ms if x264_vbv_buf_capacity_ms is not None else '<default>'}",
        flush=True,
    )
    print(f"  gst debug    : {gst_debug_level}", flush=True)
    print(f"  ortm version : {ORTM_VERSION}", flush=True)
    print(
        f"  ortm layout  : grid={GRID_SIZE}x{GRID_SIZE} cell={ortm_cell} padding={ortm_padding} x={ortm_x} y={ortm_y}",
        flush=True,
    )
    print(f"  ortm bg alpha: {max(0.0, min(ortm_background_alpha, 1.0)):.2f}", flush=True)
    print(f"  ortm cell alpha: {max(0.0, min(ortm_cell_alpha, 1.0)):.2f}", flush=True)
    print(f"  ortm border alpha: {max(0.0, min(ortm_border_alpha, 1.0)):.2f}", flush=True)
    print(f"  ortm finders : {ortm_finder_layout}", flush=True)
    print(
        f"  timestamp    : {'wallclock text below marker' if timestamp_overlay == '1' else '<off>'}",
        flush=True,
    )
    print(f"  timestamp tz : {timestamp_tz_name}", flush=True)
    print(
        f"  pipe metrics : {'ortm-render + overlay->send' if metrics_enabled == '1' else '<off>'}",
        flush=True,
    )
    print(
        "  rate watchdog: "
        + (
            f"on warmup={rate_watchdog_warmup_seconds:.1f}s "
            f"fps_ratio={rate_watchdog_max_fps_ratio:.2f} "
            f"bitrate_ratio={rate_watchdog_max_bitrate_ratio:.2f} "
            f"consecutive={rate_watchdog_consecutive_limit}"
            if rate_watchdog_enabled
            else "off"
        ),
        flush=True,
    )
    print(f"  stun server  : {whip_stun_server or '<none>'}", flush=True)
    print(f"  turn server  : {'<configured>' if whip_turn_server else '<none>'}", flush=True)
    print(f"  turn server 2: {'<configured>' if whip_turn_server_2 else '<none>'}", flush=True)
    print(f"  force turn   : {whip_force_turn}", flush=True)
    print(
        f"  cc min bps   : {whip_cc_min_bitrate_bps if whip_cc_min_bitrate_bps is not None else '<default>'}",
        flush=True,
    )
    print(
        f"  cc max bps   : {whip_cc_max_bitrate_bps if whip_cc_max_bitrate_bps is not None else '<default>'}",
        flush=True,
    )
    print(
        f"  mitigation   : {whip_mitigation_modes if whip_mitigation_modes is not None else '<default>'}",
        flush=True,
    )
    print("", flush=True)
    print(f"  sink args    : {sink_args_log}", flush=True)
    print("", flush=True)

    pipeline_description = build_pipeline_description(
        video_source=video_source,
        video_device=video_device,
        video_source_width=video_source_width,
        video_source_height=video_source_height,
        video_source_fps=video_source_fps,
        source_pipeline=source_pipeline,
        pattern=pattern,
        testsrc_horizontal_speed=testsrc_horizontal_speed,
        width=width,
        height=height,
        fps=fps,
        bitrate_kbps=bitrate_kbps,
        speed_preset=speed_preset,
        key_int_max=key_int_max,
        x264_option_string=x264_option_string,
        x264_threads=x264_threads,
        x264_sliced_threads=x264_sliced_threads,
        x264_vbv_buf_capacity_ms=x264_vbv_buf_capacity_ms,
        sink_args=sink_args,
    )

    pipeline = Gst.parse_launch(pipeline_description)
    loop = GLib.MainLoop()
    watchdog_triggered = False
    pipeline_error = None

    overlay = pipeline.get_by_name("ortm_overlay")
    if overlay is None:
        raise SystemExit("failed to get cairooverlay element")

    renderer = ORTMOverlayRenderer(
        x=ortm_x,
        y=ortm_y,
        cell=ortm_cell,
        padding=ortm_padding,
        metrics_interval_frames=metrics_interval_frames,
        timezone=timestamp_tz,
        draw_timestamp_text=timestamp_overlay == "1",
        background_alpha=ortm_background_alpha,
        cell_alpha=ortm_cell_alpha,
        border_alpha=ortm_border_alpha,
        finder_layout=ortm_finder_layout,
    )
    overlay.connect("draw", renderer.draw)

    if metrics_enabled == "1" or rate_watchdog_enabled:
        encoded_frame_stats = EncodedFrameStats(frame_metrics_window_seconds)
        watchdog_started_ns = monotonic_ns()
        watchdog_consecutive_breaches = 0
        frame_records_by_pts = {}
        frame_record_order = deque()
        frame_record_limit = 600
        nearest_marker_tolerance_ns = 2_000_000
        last_metric_print_count = 0
        encoded_pts_offset_ns = None
        encoded_pts_offset_confirm_samples = int(
            os.environ.get("PTS_OFFSET_CONFIRM_SAMPLES", "5")
        )
        encoded_pts_offset_candidates = {}
        encoded_pts_offset_seen = set()
        encoded_pts_offset_candidate_limit = 200

        def make_stats():
            return {
                "count": 0,
                "total_ms": 0.0,
                "min_ms": None,
                "max_ms": None,
            }

        metric_stats = {
            "overlay_to_encoder_ms": make_stats(),
            "encoder_ms": make_stats(),
            "parse_ms": make_stats(),
            "encoded_to_send_ms": make_stats(),
            "overlay_to_send_ms": make_stats(),
        }
        pts_stats = {
            "invalid": 0,
            "records_expired": 0,
            "overlay_marker_miss": 0,
            "overlay_marker_nearest": 0,
            "pts_sample_logs": 0,
            "encoded_pts_offset_discovered": 0,
            "encoded_pts_offset_miss": 0,
            "encoded_pts_offset_pending": 0,
            "encoded_pts_offset_confirmations": 0,
            "pre_encoder_miss": 0,
            "post_encoder_miss": 0,
            "post_parse_miss": 0,
            "send_miss": 0,
            "send_marker_miss": 0,
            "duplicate_stage": 0,
        }

        def buffer_pts_ns(buffer):
            pts = int(buffer.pts)
            if pts == Gst.CLOCK_TIME_NONE:
                pts_stats["invalid"] += 1
                return None
            return pts

        def expire_frame_records():
            while len(frame_record_order) > frame_record_limit:
                old_pts = frame_record_order.popleft()
                if frame_records_by_pts.pop(old_pts, None) is not None:
                    pts_stats["records_expired"] += 1

        def get_or_create_frame_record(pts_ns):
            if pts_ns is None:
                return None
            record = frame_records_by_pts.get(pts_ns)
            if record is None:
                record = {"pts_ns": pts_ns}
                frame_records_by_pts[pts_ns] = record
                frame_record_order.append(pts_ns)
                expire_frame_records()
            return record

        def get_frame_record(pts_ns, miss_name):
            if pts_ns is None:
                return None
            record = frame_records_by_pts.get(pts_ns)
            if record is None:
                pts_stats[miss_name] += 1
            return record

        def records_with_stage(stage_time_key):
            for candidate_pts in list(frame_record_order):
                record = frame_records_by_pts.get(candidate_pts)
                if record is None:
                    if frame_record_order and frame_record_order[0] == candidate_pts:
                        frame_record_order.popleft()
                    continue
                if stage_time_key in record:
                    yield candidate_pts, record

        def trim_offset_candidates():
            while len(encoded_pts_offset_candidates) > encoded_pts_offset_candidate_limit:
                weakest_offset = min(
                    encoded_pts_offset_candidates,
                    key=lambda offset: (
                        encoded_pts_offset_candidates[offset]["count"],
                        encoded_pts_offset_candidates[offset]["last_seen_ns"],
                    ),
                )
                encoded_pts_offset_candidates.pop(weakest_offset, None)

        def find_nearest_timed_record(stage_time_key, now_ns):
            best = None
            best_age_ns = None
            for candidate_pts, record in records_with_stage(stage_time_key):
                stage_time_ns = record.get(stage_time_key)
                if stage_time_ns is None or stage_time_ns > now_ns:
                    continue
                age_ns = now_ns - stage_time_ns
                if best_age_ns is None or age_ns < best_age_ns:
                    best = (candidate_pts, record)
                    best_age_ns = age_ns
            return best

        def maybe_confirm_encoded_pts_offset(encoded_pts_ns, stage_name, required_stage_time_key):
            nonlocal encoded_pts_offset_ns
            if encoded_pts_offset_ns is not None:
                return
            if stage_name != "post_encoder":
                pts_stats["encoded_pts_offset_pending"] += 1
                return

            seen_key = (stage_name, encoded_pts_ns)
            if seen_key in encoded_pts_offset_seen:
                return
            encoded_pts_offset_seen.add(seen_key)

            now_ns = monotonic_ns()
            candidate_record = find_nearest_timed_record(
                required_stage_time_key, now_ns
            )
            if candidate_record is not None:
                source_pts_ns, _record = candidate_record
                candidate_offset_ns = encoded_pts_ns - source_pts_ns
                candidate = encoded_pts_offset_candidates.setdefault(
                    candidate_offset_ns,
                    {"count": 0, "last_seen_ns": 0},
                )
                candidate["count"] += 1
                candidate["last_seen_ns"] = now_ns
                if candidate["count"] >= encoded_pts_offset_confirm_samples:
                    encoded_pts_offset_ns = candidate_offset_ns
                    pts_stats["encoded_pts_offset_discovered"] += 1
                    pts_stats["encoded_pts_offset_confirmations"] = candidate["count"]
                    print(
                        "PTS_OFFSET "
                        f"stage={stage_name} "
                        f"encoded_pts={encoded_pts_ns} "
                        f"offset={encoded_pts_offset_ns} "
                        f"confirmations={candidate['count']} "
                        f"required_confirmations={encoded_pts_offset_confirm_samples}",
                        flush=True,
                    )
            trim_offset_candidates()
            if encoded_pts_offset_ns is None:
                pts_stats["encoded_pts_offset_pending"] += 1

        def get_encoded_frame_record(encoded_pts_ns, miss_name, stage_name, required_stage_time_key):
            nonlocal encoded_pts_offset_ns
            if encoded_pts_ns is None:
                return None, None
            record = frame_records_by_pts.get(encoded_pts_ns)
            if record is not None:
                return record, encoded_pts_ns

            if encoded_pts_offset_ns is not None:
                source_pts_ns = encoded_pts_ns - encoded_pts_offset_ns
                record = frame_records_by_pts.get(source_pts_ns)
                if record is not None:
                    return record, source_pts_ns

            maybe_confirm_encoded_pts_offset(
                encoded_pts_ns, stage_name, required_stage_time_key
            )
            if encoded_pts_offset_ns is not None:
                source_pts_ns = encoded_pts_ns - encoded_pts_offset_ns
                record = frame_records_by_pts.get(source_pts_ns)
                if record is not None:
                    return record, source_pts_ns

            pts_stats[miss_name] += 1
            pts_stats["encoded_pts_offset_miss"] += 1
            return None, None

        def describe_active_pts():
            active_pts = list(frame_records_by_pts.keys())
            if not active_pts:
                return "active=0"
            return (
                f"active={len(active_pts)} "
                f"min={min(active_pts)} "
                f"max={max(active_pts)} "
                f"oldest={frame_record_order[0] if frame_record_order else -1} "
                f"newest={frame_record_order[-1] if frame_record_order else -1}"
            )

        def maybe_log_pts_sample(stage_name, pts_ns):
            if pts_stats["pts_sample_logs"] >= 12:
                return
            pts_stats["pts_sample_logs"] += 1
            print(
                "PTS_SAMPLE "
                f"stage={stage_name} "
                f"pts={pts_ns} "
                f"{describe_active_pts()}",
                flush=True,
            )

        def set_stage_time(record, stage_name, now_ns):
            if record is None:
                return
            key = f"{stage_name}_at"
            if key in record:
                pts_stats["duplicate_stage"] += 1
            record[key] = now_ns

        def record_stat(metric_name, delay_ms):
            stats = metric_stats[metric_name]
            stats["count"] += 1
            stats["total_ms"] += delay_ms
            stats["min_ms"] = (
                delay_ms
                if stats["min_ms"] is None
                else min(stats["min_ms"], delay_ms)
            )
            stats["max_ms"] = (
                delay_ms
                if stats["max_ms"] is None
                else max(stats["max_ms"], delay_ms)
            )

        def print_metric(metric_name, stats):
            if stats["count"] == 0:
                return
            average_ms = stats["total_ms"] / stats["count"]
            print(
                f"PIPELINE {metric_name} "
                f"avg={average_ms:.1f} "
                f"min={stats['min_ms']:.1f} "
                f"max={stats['max_ms']:.1f} "
                f"samples={stats['count']}",
                flush=True,
            )

        def print_frame_metrics(now_ns):
            actual_fps = encoded_frame_stats.window_fps(now_ns)
            print(
                "FRAME encoded_size_bytes "
                f"stream={stream_name} "
                f"avg={encoded_frame_stats.avg_bytes():.1f} "
                f"min={(encoded_frame_stats.min_bytes or 0)} "
                f"max={(encoded_frame_stats.max_bytes or 0)} "
                f"last={encoded_frame_stats.last_bytes} "
                f"samples={encoded_frame_stats.count} "
                f"keyframes={encoded_frame_stats.keyframe_count} "
                f"delta={encoded_frame_stats.delta_count} "
                f"keyframe_last={encoded_frame_stats.keyframe_last_bytes} "
                f"keyframe_avg={encoded_frame_stats.keyframe_avg_bytes():.1f} "
                f"keyframe_max={encoded_frame_stats.keyframe_max_bytes} "
                f"delta_avg={encoded_frame_stats.delta_avg_bytes():.1f} "
                f"delta_max={encoded_frame_stats.delta_max_bytes} "
                f"bitrate_kbps={encoded_frame_stats.window_bitrate_kbps(now_ns):.1f} "
                f"actual_fps={actual_fps:.1f} "
                f"width={width} "
                f"height={height} "
                f"fps={fps} "
                f"target_bitrate_kbps={bitrate_kbps} "
                f"key_int={key_int_max}",
                flush=True,
            )

        def check_rate_watchdog(now_ns):
            nonlocal watchdog_consecutive_breaches, watchdog_triggered
            if not rate_watchdog_enabled or watchdog_triggered:
                return
            uptime_seconds = (now_ns - watchdog_started_ns) / 1_000_000_000.0
            if uptime_seconds < rate_watchdog_warmup_seconds:
                return

            actual_fps = encoded_frame_stats.window_fps(now_ns)
            actual_bitrate_kbps = encoded_frame_stats.window_bitrate_kbps(now_ns)
            fps_ratio = actual_fps / max(fps, 1)
            bitrate_ratio = actual_bitrate_kbps / max(bitrate_kbps, 1)
            breached = (
                fps_ratio > rate_watchdog_max_fps_ratio
                or bitrate_ratio > rate_watchdog_max_bitrate_ratio
            )
            watchdog_consecutive_breaches = (
                watchdog_consecutive_breaches + 1 if breached else 0
            )
            if watchdog_consecutive_breaches < max(
                rate_watchdog_consecutive_limit, 1
            ):
                return

            watchdog_triggered = True
            print(
                "WATCHDOG rate_exceeded "
                f"stream={stream_name} "
                f"actual_fps={actual_fps:.1f} "
                f"target_fps={fps} "
                f"fps_ratio={fps_ratio:.2f} "
                f"actual_bitrate_kbps={actual_bitrate_kbps:.1f} "
                f"target_bitrate_kbps={bitrate_kbps} "
                f"bitrate_ratio={bitrate_ratio:.2f} "
                f"consecutive={watchdog_consecutive_breaches}",
                file=sys.stderr,
                flush=True,
            )
            GLib.idle_add(loop.quit)

        def print_pts_mapping():
            print(
                "PTS_MAPPING "
                f"active_records={len(frame_records_by_pts)} "
                f"invalid={pts_stats['invalid']} "
                f"records_expired={pts_stats['records_expired']} "
                f"marker_pending={len(renderer.frame_markers_by_pts)} "
                f"marker_expired={renderer.marker_expired_pts} "
                f"marker_duplicate={renderer.marker_duplicate_pts} "
                f"overlay_marker_miss={pts_stats['overlay_marker_miss']} "
                f"overlay_marker_nearest={pts_stats['overlay_marker_nearest']} "
                f"pts_sample_logs={pts_stats['pts_sample_logs']} "
                f"encoded_pts_offset={encoded_pts_offset_ns if encoded_pts_offset_ns is not None else '<unset>'} "
                f"encoded_pts_offset_discovered={pts_stats['encoded_pts_offset_discovered']} "
                f"encoded_pts_offset_miss={pts_stats['encoded_pts_offset_miss']} "
                f"encoded_pts_offset_pending={pts_stats['encoded_pts_offset_pending']} "
                f"encoded_pts_offset_candidates={len(encoded_pts_offset_candidates)} "
                f"encoded_pts_offset_confirmations={pts_stats['encoded_pts_offset_confirmations']} "
                f"encoded_pts_offset_required={encoded_pts_offset_confirm_samples} "
                f"pre_encoder_miss={pts_stats['pre_encoder_miss']} "
                f"post_encoder_miss={pts_stats['post_encoder_miss']} "
                f"post_parse_miss={pts_stats['post_parse_miss']} "
                f"send_miss={pts_stats['send_miss']} "
                f"send_marker_miss={pts_stats['send_marker_miss']} "
                f"duplicate_stage={pts_stats['duplicate_stage']}",
                flush=True,
            )

        def overlay_probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK
            pts_ns = buffer_pts_ns(buffer)
            now = monotonic_ns()
            record = get_or_create_frame_record(pts_ns)
            marker = renderer.take_frame_marker(pts_ns)
            if marker is None:
                marker = renderer.take_nearest_frame_marker(
                    pts_ns, nearest_marker_tolerance_ns
                )
                if marker is not None:
                    pts_stats["overlay_marker_nearest"] += 1
            if marker is None:
                pts_stats["overlay_marker_miss"] += 1
            if record is not None:
                record["marker"] = marker
                set_stage_time(record, "overlay", now)
            return Gst.PadProbeReturn.OK

        def pre_encoder_probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK
            now = monotonic_ns()
            pts_ns = buffer_pts_ns(buffer)
            record = get_frame_record(pts_ns, "pre_encoder_miss")
            if record is not None and "overlay_at" in record:
                record_stat(
                    "overlay_to_encoder_ms",
                    (now - record["overlay_at"]) / 1_000_000.0,
                )
            set_stage_time(record, "pre_encoder", now)
            return Gst.PadProbeReturn.OK

        def post_encoder_probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK
            now = monotonic_ns()
            pts_ns = buffer_pts_ns(buffer)
            record, _source_pts_ns = get_encoded_frame_record(
                pts_ns, "post_encoder_miss", "post_encoder", "pre_encoder_at"
            )
            if record is None:
                maybe_log_pts_sample("post_encoder", pts_ns)
            if record is not None and "pre_encoder_at" in record:
                record_stat(
                    "encoder_ms",
                    (now - record["pre_encoder_at"]) / 1_000_000.0,
                )
            set_stage_time(record, "post_encoder", now)
            return Gst.PadProbeReturn.OK

        def post_parse_probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK
            now = monotonic_ns()
            pts_ns = buffer_pts_ns(buffer)
            record, _source_pts_ns = get_encoded_frame_record(
                pts_ns, "post_parse_miss", "post_parse", "post_encoder_at"
            )
            if record is None:
                maybe_log_pts_sample("post_parse", pts_ns)
            if record is not None and "post_encoder_at" in record:
                record_stat(
                    "parse_ms",
                    (now - record["post_encoder_at"]) / 1_000_000.0,
                )
            set_stage_time(record, "post_parse", now)
            return Gst.PadProbeReturn.OK

        def send_probe(_pad, info):
            nonlocal last_metric_print_count
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK

            now = monotonic_ns()
            pts_ns = buffer_pts_ns(buffer)
            record, source_pts_ns = get_encoded_frame_record(
                pts_ns, "send_miss", "send", "post_parse_at"
            )
            if record is None:
                maybe_log_pts_sample("send", pts_ns)
            encoded_size_bytes = buffer.get_size()
            is_keyframe = not buffer.has_flags(Gst.BufferFlags.DELTA_UNIT)
            encoded_frame_stats.add(now, encoded_size_bytes, is_keyframe)
            encoded_to_send_ms = None
            if record is not None and "post_parse_at" in record:
                encoded_to_send_ms = (now - record["post_parse_at"]) / 1_000_000.0
                record_stat("encoded_to_send_ms", encoded_to_send_ms)

            frame_marker = record.get("marker") if record is not None else None
            overlay_to_send_ms = None
            if frame_marker is not None:
                overlay_done_at = frame_marker.get("overlay_done_monotonic_ns")
                if overlay_done_at is not None:
                    overlay_to_send_ms = (now - overlay_done_at) / 1_000_000.0
                else:
                    overlay_to_send_ms = None
            else:
                pts_stats["send_marker_miss"] += 1
            if overlay_to_send_ms is not None:
                record_stat("overlay_to_send_ms", overlay_to_send_ms)

            if (
                frame_marker is not None
                and overlay_to_send_ms is not None
                and metric_stats["overlay_to_send_ms"]["count"] % max(sender_frame_log_interval, 1) == 0
            ):
                sender_now_ms = wallclock_ms()
                sender_pipeline_ms = sender_now_ms - frame_marker["timestamp_ms_full"]
                print(
                    "SENDER frame "
                    f"stream={stream_name} "
                    f"seq={frame_marker['frame_seq']} "
                    f"timestamp_ms={frame_marker['timestamp_ms_full']} "
                    f"render_ms={frame_marker['render_ms']:.1f} "
                    f"overlay_to_send_ms={overlay_to_send_ms:.1f} "
                    f"encoded_to_send_ms={(encoded_to_send_ms if encoded_to_send_ms is not None else -1):.1f} "
                    f"sender_pipeline_ms={sender_pipeline_ms} "
                    f"sender_now_ms={sender_now_ms} "
                    f"encoded_size_bytes={encoded_size_bytes} "
                    f"keyframe={1 if is_keyframe else 0} "
                    f"width={width} "
                    f"height={height} "
                    f"fps={fps} "
                    f"target_bitrate_kbps={bitrate_kbps} "
                    f"key_int={key_int_max}",
                    flush=True,
                )

            if (
                encoded_frame_stats.count > last_metric_print_count
                and encoded_frame_stats.count % metrics_interval_frames == 0
            ):
                last_metric_print_count = encoded_frame_stats.count
                if metrics_enabled == "1":
                    for metric_name, stats in metric_stats.items():
                        print_metric(metric_name, stats)
                    print_frame_metrics(now)
                    print_pts_mapping()
                check_rate_watchdog(now)
            if record is not None:
                frame_records_by_pts.pop(source_pts_ns, None)
            return Gst.PadProbeReturn.OK

        overlay_src_pad = overlay.get_static_pad("src")
        pre_encoder_element = pipeline.get_by_name("pre_encoder_probe")
        post_encoder_element = pipeline.get_by_name("post_encoder_probe")
        post_parse_element = pipeline.get_by_name("post_parse_probe")
        send_probe_element = pipeline.get_by_name("send_probe")
        pre_encoder_src_pad = (
            pre_encoder_element.get_static_pad("src")
            if pre_encoder_element is not None
            else None
        )
        post_encoder_src_pad = (
            post_encoder_element.get_static_pad("src")
            if post_encoder_element is not None
            else None
        )
        post_parse_src_pad = (
            post_parse_element.get_static_pad("src")
            if post_parse_element is not None
            else None
        )
        send_src_pad = (
            send_probe_element.get_static_pad("src")
            if send_probe_element is not None
            else None
        )

        if (
            overlay_src_pad is not None
            and pre_encoder_src_pad is not None
            and post_encoder_src_pad is not None
            and post_parse_src_pad is not None
            and send_src_pad is not None
        ):
            overlay_src_pad.add_probe(Gst.PadProbeType.BUFFER, overlay_probe)
            pre_encoder_src_pad.add_probe(Gst.PadProbeType.BUFFER, pre_encoder_probe)
            post_encoder_src_pad.add_probe(Gst.PadProbeType.BUFFER, post_encoder_probe)
            post_parse_src_pad.add_probe(Gst.PadProbeType.BUFFER, post_parse_probe)
            send_src_pad.add_probe(Gst.PadProbeType.BUFFER, send_probe)
        else:
            print("PIPELINE metrics disabled: probe pads not found", flush=True)

    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_message(_bus, message):
        nonlocal pipeline_error
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            pipeline_error = err.message
            print(f"ERROR: {err.message}", file=sys.stderr, flush=True)
            if debug:
                print(f"DEBUG: {debug}", file=sys.stderr, flush=True)
            loop.quit()
        elif message.type == Gst.MessageType.EOS:
            loop.quit()

    bus.connect("message", on_message)

    def stop(_signum, _frame):
        pipeline.send_event(Gst.Event.new_eos())

    signal.signal(signal.SIGTERM, stop)
    signal.signal(signal.SIGINT, stop)

    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
    if watchdog_triggered:
        raise SystemExit(70)
    if pipeline_error is not None:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
