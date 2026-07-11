#!/usr/bin/env python3
import os
import signal
import struct
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


GRID_SIZE = 32
FINDER_SIZE = 4
TIMING_INDEX = 4
ORTM_VERSION = 0


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


def crc16_ccitt_false(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc


def bits_from_int(value, width):
    return [(value >> shift) & 1 for shift in range(width - 1, -1, -1)]


def in_finder(row, col):
    return (
        (row < FINDER_SIZE and col < FINDER_SIZE)
        or (row < FINDER_SIZE and col >= GRID_SIZE - FINDER_SIZE)
        or (row >= GRID_SIZE - FINDER_SIZE and col < FINDER_SIZE)
        or (row >= GRID_SIZE - FINDER_SIZE and col >= GRID_SIZE - FINDER_SIZE)
    )


def is_reserved_cell(row, col):
    return in_finder(row, col) or row == TIMING_INDEX or col == TIMING_INDEX


def draw_finder(bits, top, left):
    for row in range(FINDER_SIZE):
        for col in range(FINDER_SIZE):
            bits[top + row][left + col] = (
                1
                if row in (0, FINDER_SIZE - 1) or col in (0, FINDER_SIZE - 1)
                else 0
            )


def build_ortm_bits(version, frame_seq, timestamp_ms_low32):
    crc_input = struct.pack(">BHI", version & 0x0F, frame_seq & 0xFFFF, timestamp_ms_low32)
    crc16 = crc16_ccitt_false(crc_input)

    payload_bits = []
    payload_bits.extend(bits_from_int(version & 0x0F, 4))
    payload_bits.extend(bits_from_int(frame_seq & 0xFFFF, 16))
    payload_bits.extend(bits_from_int(timestamp_ms_low32, 32))
    payload_bits.extend(bits_from_int(crc16, 16))

    bits = [[0 for _ in range(GRID_SIZE)] for _ in range(GRID_SIZE)]

    draw_finder(bits, 0, 0)
    draw_finder(bits, 0, GRID_SIZE - FINDER_SIZE)
    draw_finder(bits, GRID_SIZE - FINDER_SIZE, 0)
    draw_finder(bits, GRID_SIZE - FINDER_SIZE, GRID_SIZE - FINDER_SIZE)

    for col in range(GRID_SIZE):
        if not in_finder(TIMING_INDEX, col):
            bits[TIMING_INDEX][col] = col % 2
    for row in range(GRID_SIZE):
        if not in_finder(row, TIMING_INDEX):
            bits[row][TIMING_INDEX] = row % 2

    payload_index = 0
    for row in range(GRID_SIZE):
        for col in range(GRID_SIZE):
            if is_reserved_cell(row, col):
                continue
            if payload_index < len(payload_bits):
                bits[row][col] = payload_bits[payload_index]
                payload_index += 1
            else:
                bits[row][col] = 0

    if payload_index != len(payload_bits):
        raise RuntimeError("ORTM payload does not fit into the 32x32 grid")

    return bits


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
    ):
        self.x = x
        self.y = y
        self.cell = cell
        self.padding = padding
        self.frame_seq = 0
        self.metrics_interval_frames = max(metrics_interval_frames, 1)
        self.timezone = timezone
        self.draw_timestamp_text = draw_timestamp_text
        self.render_stats = StatsWindow()
        self.frame_markers = deque()

    @property
    def marker_size(self):
        return GRID_SIZE * self.cell + self.padding * 2

    def draw(self, _overlay, context, _timestamp, _duration):
        started_at = monotonic_ns()
        current_frame_seq = self.frame_seq
        self.frame_seq = (self.frame_seq + 1) & 0xFFFF
        timestamp_ms_full = wallclock_ms()
        timestamp_ms_low32 = timestamp_ms_full & 0xFFFFFFFF
        ortm_bits = build_ortm_bits(ORTM_VERSION, current_frame_seq, timestamp_ms_low32)

        context.save()
        try:
            context.set_antialias(cairo.ANTIALIAS_NONE)
            context.set_line_width(2.0)

            context.set_source_rgb(1.0, 1.0, 1.0)
            context.rectangle(self.x, self.y, self.marker_size, self.marker_size)
            context.fill()

            context.set_source_rgb(0.0, 0.0, 0.0)
            context.rectangle(self.x, self.y, self.marker_size, self.marker_size)
            context.stroke()

            grid_origin_x = self.x + self.padding
            grid_origin_y = self.y + self.padding
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
        self.frame_markers.append(
            {
                "frame_seq": current_frame_seq,
                "timestamp_ms_full": timestamp_ms_full,
                "render_ms": render_ms,
                "overlay_done_monotonic_ns": monotonic_ns(),
            }
        )
        if len(self.frame_markers) > 180:
            self.frame_markers.popleft()
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
    pattern,
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

    return (
        f"videotestsrc is-live=true pattern={gst_quote(pattern)} "
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
    pattern = env_value("PATTERN", "smpte")
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
    ortm_x = env_int("ORTM_X", 24)
    ortm_y = env_int("ORTM_Y", 24)
    ortm_cell = env_int("ORTM_CELL", 12)
    ortm_padding = env_int("ORTM_PADDING", 12)

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
    print(f"  pattern      : {pattern}", flush=True)
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
    print(
        f"  timestamp    : {'wallclock text below marker' if timestamp_overlay == '1' else '<off>'}",
        flush=True,
    )
    print(f"  timestamp tz : {timestamp_tz_name}", flush=True)
    print(
        f"  pipe metrics : {'ortm-render + overlay->send' if metrics_enabled == '1' else '<off>'}",
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
        pattern=pattern,
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
    )
    overlay.connect("draw", renderer.draw)

    if metrics_enabled == "1":
        overlay_frame_times = deque()
        pre_encoder_times = deque()
        post_encoder_times = deque()
        post_parse_times = deque()
        encoded_frame_stats = EncodedFrameStats(frame_metrics_window_seconds)

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
        pipeline_dropped = 0

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
                f"width={width} "
                f"height={height} "
                f"fps={fps} "
                f"target_bitrate_kbps={bitrate_kbps} "
                f"key_int={key_int_max}",
                flush=True,
            )

        def append_time(queue):
            nonlocal pipeline_dropped
            queue.append(monotonic_ns())
            if len(queue) > 180:
                queue.popleft()
                pipeline_dropped += 1

        def overlay_probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK
            append_time(overlay_frame_times)
            return Gst.PadProbeReturn.OK

        def pre_encoder_probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK
            now = monotonic_ns()
            if overlay_frame_times:
                started_at = overlay_frame_times.popleft()
                record_stat("overlay_to_encoder_ms", (now - started_at) / 1_000_000.0)
            append_time(pre_encoder_times)
            return Gst.PadProbeReturn.OK

        def post_encoder_probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK
            now = monotonic_ns()
            if pre_encoder_times:
                started_at = pre_encoder_times.popleft()
                record_stat("encoder_ms", (now - started_at) / 1_000_000.0)
            append_time(post_encoder_times)
            return Gst.PadProbeReturn.OK

        def post_parse_probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK
            now = monotonic_ns()
            if post_encoder_times:
                started_at = post_encoder_times.popleft()
                record_stat("parse_ms", (now - started_at) / 1_000_000.0)
            append_time(post_parse_times)
            return Gst.PadProbeReturn.OK

        def send_probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK

            now = monotonic_ns()
            encoded_size_bytes = buffer.get_size()
            is_keyframe = not buffer.has_flags(Gst.BufferFlags.DELTA_UNIT)
            encoded_frame_stats.add(now, encoded_size_bytes, is_keyframe)
            encoded_to_send_ms = None
            if post_parse_times:
                started_at = post_parse_times.popleft()
                encoded_to_send_ms = (now - started_at) / 1_000_000.0
                record_stat("encoded_to_send_ms", encoded_to_send_ms)

            frame_marker = (
                renderer.frame_markers.popleft() if renderer.frame_markers else None
            )
            overlay_to_send_ms = None
            if frame_marker is not None:
                overlay_done_at = frame_marker.get("overlay_done_monotonic_ns")
                if overlay_done_at is not None:
                    overlay_to_send_ms = (now - overlay_done_at) / 1_000_000.0
                else:
                    overlay_to_send_ms = None
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

            if metric_stats["overlay_to_send_ms"]["count"] % metrics_interval_frames == 0:
                for metric_name, stats in metric_stats.items():
                    print_metric(metric_name, stats)
                print_frame_metrics(now)
                print(
                    "PIPELINE dropped_pts "
                    f"value={pipeline_dropped}",
                    flush=True,
                )
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
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
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


if __name__ == "__main__":
    main()
