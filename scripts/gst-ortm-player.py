#!/usr/bin/env python3
import argparse
import ctypes
import ctypes.util
import json
import os
import signal
import sys
import threading
import time
import uuid
from datetime import datetime, timezone

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Gst  # noqa: E402


ORTM = {
    "x": 24,
    "y": 24,
    "cell": 12,
    "padding": 12,
    "grid_size": 32,
    "finder_size": 4,
    "timing_index": 4,
    "scales": [1.0, 0.95, 1.05, 0.9, 1.1],
    "offsets": [0, -4, 4, -8, 8, -12, 12],
}
ORTM_PAYLOAD_BITS = 4 + 16 + 32 + 16
ORTM_MAX_REASONABLE_LATENCY_MS = 5000


def crc16_ccitt_false(data):
    crc = 0xFFFF
    for byte in data:
        crc ^= byte << 8
        for _ in range(8):
            if crc & 0x8000:
                crc = ((crc << 1) ^ 0x1021) & 0xFFFF
            else:
                crc = (crc << 1) & 0xFFFF
    return crc & 0xFFFF


def bits_to_number(bits, start, length):
    value = 0
    for i in range(length):
        value = (value << 1) | (bits[start + i] & 1)
    return value


def in_finder(row, col):
    f = ORTM["finder_size"]
    n = ORTM["grid_size"]
    return (
        (row < f and col < f)
        or (row < f and col >= n - f)
        or (row >= n - f and col < f)
        or (row >= n - f and col >= n - f)
    )


def is_reserved(row, col):
    return in_finder(row, col) or row == ORTM["timing_index"] or col == ORTM["timing_index"]


def expected_finder_bit(local_row, local_col):
    f = ORTM["finder_size"]
    return 1 if local_row == 0 or local_row == f - 1 or local_col == 0 or local_col == f - 1 else 0


def build_candidates():
    candidates = []
    for scale in ORTM["scales"]:
        for dx in ORTM["offsets"]:
            for dy in ORTM["offsets"]:
                cell = ORTM["cell"] * scale
                padding = ORTM["padding"] * scale
                marker_size = ORTM["grid_size"] * cell
                candidates.append(
                    {
                        "x": ORTM["x"] + dx,
                        "y": ORTM["y"] + dy,
                        "cell": cell,
                        "padding": padding,
                        "box_size": marker_size + padding * 2,
                    }
                )
    return candidates


ORTM_CANDIDATES = build_candidates()


def sample_cell_luma(data, width, height, stride, candidate, row, col):
    origin_x = candidate["x"] + candidate["padding"] + col * candidate["cell"]
    origin_y = candidate["y"] + candidate["padding"] + row * candidate["cell"]
    total = 0.0
    count = 0
    for py in (0.3, 0.5, 0.7):
        for px in (0.3, 0.5, 0.7):
            x = int(round(origin_x + candidate["cell"] * px))
            y = int(round(origin_y + candidate["cell"] * py))
            if x < 0 or y < 0 or x >= width or y >= height:
                return None
            offset = y * stride + x * 4
            r = data[offset]
            g = data[offset + 1]
            b = data[offset + 2]
            total += 0.299 * r + 0.587 * g + 0.114 * b
            count += 1
    return total / count if count else None


def decode_ortm_rgba(data, width, height, now_ms):
    stride = width * 4
    best_failure = None

    for candidate in ORTM_CANDIDATES:
        if (
            candidate["x"] < 0
            or candidate["y"] < 0
            or candidate["x"] + candidate["box_size"] > width
            or candidate["y"] + candidate["box_size"] > height
        ):
            continue

        luma_grid = [[255.0 for _ in range(ORTM["grid_size"])] for _ in range(ORTM["grid_size"])]
        min_luma = float("inf")
        max_luma = float("-inf")
        invalid = False

        for row in range(ORTM["grid_size"]):
            if invalid:
                break
            for col in range(ORTM["grid_size"]):
                luma = sample_cell_luma(data, width, height, stride, candidate, row, col)
                if luma is None:
                    invalid = True
                    break
                luma_grid[row][col] = luma
                min_luma = min(min_luma, luma)
                max_luma = max(max_luma, luma)
        if invalid:
            continue

        threshold = (min_luma + max_luma) / 2
        contrast = max_luma - min_luma
        bits = [[1 if luma_grid[row][col] < threshold else 0 for col in range(ORTM["grid_size"])] for row in range(ORTM["grid_size"])]
        finder_errors = 0
        timing_errors = 0

        for row in range(ORTM["grid_size"]):
            for col in range(ORTM["grid_size"]):
                if in_finder(row, col):
                    local_row = row if row < ORTM["finder_size"] else row - (ORTM["grid_size"] - ORTM["finder_size"])
                    local_col = col if col < ORTM["finder_size"] else col - (ORTM["grid_size"] - ORTM["finder_size"])
                    if bits[row][col] != expected_finder_bit(local_row, local_col):
                        finder_errors += 1
                elif row == ORTM["timing_index"]:
                    if bits[row][col] != col % 2:
                        timing_errors += 1
                elif col == ORTM["timing_index"]:
                    if bits[row][col] != row % 2:
                        timing_errors += 1

        score = finder_errors * 4 + timing_errors * 2 - contrast / 32
        failure_base = {
            "ok": False,
            "score": score,
            "finder_errors": finder_errors,
            "timing_errors": timing_errors,
            "threshold": threshold,
            "contrast": contrast,
        }

        if finder_errors > 8 or timing_errors > 10 or contrast < 32:
            failure = {
                **failure_base,
                "reason": "structure-mismatch" if finder_errors > 8 or timing_errors > 10 else "low-contrast",
            }
            if best_failure is None or failure["score"] < best_failure["score"]:
                best_failure = failure
            continue

        payload_bits = []
        for row in range(ORTM["grid_size"]):
            for col in range(ORTM["grid_size"]):
                if not is_reserved(row, col):
                    payload_bits.append(bits[row][col])

        if len(payload_bits) < ORTM_PAYLOAD_BITS:
            failure = {**failure_base, "reason": "payload-short"}
            if best_failure is None or failure["score"] < best_failure["score"]:
                best_failure = failure
            continue

        version = bits_to_number(payload_bits, 0, 4)
        frame_seq = bits_to_number(payload_bits, 4, 16)
        timestamp_ms = bits_to_number(payload_bits, 20, 32)
        crc16 = bits_to_number(payload_bits, 52, 16)
        expected_crc16 = crc16_ccitt_false(
            bytes(
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
        )
        if crc16 != expected_crc16:
            failure = {**failure_base, "reason": "crc-mismatch"}
            if best_failure is None or failure["score"] < best_failure["score"]:
                best_failure = failure
            continue

        latency_ms = ((now_ms & 0xFFFFFFFF) - timestamp_ms) & 0xFFFFFFFF
        if latency_ms >= ORTM_MAX_REASONABLE_LATENCY_MS:
            failure = {**failure_base, "reason": "latency-out-of-range"}
            if best_failure is None or failure["score"] < best_failure["score"]:
                best_failure = failure
            continue

        return {
            "ok": True,
            "version": version,
            "frame_seq": frame_seq,
            "timestamp_ms": timestamp_ms,
            "latency_ms": latency_ms,
            "finder_errors": finder_errors,
            "timing_errors": timing_errors,
            "threshold": threshold,
            "contrast": contrast,
        }

    return best_failure or {
        "ok": False,
        "reason": "no-candidate",
        "score": float("inf"),
        "finder_errors": None,
        "timing_errors": None,
        "threshold": None,
        "contrast": None,
    }


def make_pipeline(args):
    source = args.source
    if source == "auto":
        source = "whepclientsrc" if Gst.ElementFactory.find("whepclientsrc") else "whepsrc"

    decoder = args.decoder
    if decoder == "auto":
        for candidate in ("vtdec", "avdec_h264", "openh264dec"):
            if Gst.ElementFactory.find(candidate):
                decoder = candidate
                break
        else:
            raise RuntimeError("no H264 decoder found; expected vtdec, avdec_h264, or openh264dec")

    if args.display:
        raw_tail = (
            "tee name=t "
            "t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
            "! videoconvert ! video/x-raw,format=RGBA "
            "! appsink name=ortm_sink emit-signals=true sync=false max-buffers=1 drop=true "
            "t. ! queue leaky=downstream max-size-buffers=1 max-size-bytes=0 max-size-time=0 "
            "! videoconvert "
            "! identity name=display_tap signal-handoffs=true "
            f"! {args.video_sink} sync=false async=false"
        )
    else:
        raw_tail = "videoconvert ! video/x-raw,format=RGBA ! appsink name=ortm_sink emit-signals=true sync=false max-buffers=1 drop=true"

    if source == "whepclientsrc":
        pipeline_text = (
            f"whepclientsrc signaller::whep-endpoint={args.whep_url} "
            f"stun-server= video-codecs=H264 name=src "
            "src.req_video_0 ! application/x-rtp,media=video,encoding-name=H264,clock-rate=90000 "
            f"! rtph264depay ! h264parse ! {decoder} "
            f"! {raw_tail}"
        )
    elif source == "whepsrc":
        caps = (
            "application/x-rtp,media=video,encoding-name=H264,payload=96,clock-rate=90000,"
            "packetization-mode=(string)1,profile-level-id=(string)42e01f,level-asymmetry-allowed=(string)1"
        )
        pipeline_text = (
            f"whepsrc whep-endpoint={args.whep_url} use-link-headers=false "
            f"audio-caps=EMPTY video-caps='{caps}' name=src "
            "src. ! application/x-rtp,media=video,encoding-name=H264,clock-rate=90000 "
            f"! rtph264depay ! h264parse ! {decoder} "
            f"! {raw_tail}"
        )
    else:
        raise RuntimeError(f"unsupported source: {source}")

    return Gst.parse_launch(pipeline_text), source, decoder, pipeline_text


def now_iso():
    return datetime.now(timezone.utc).isoformat(timespec="milliseconds").replace("+00:00", "Z")


def append_client_log(path, peer_id, args, message):
    os.makedirs(os.path.dirname(path), exist_ok=True)
    entry = {
        "ts": now_iso(),
        "page": "gst-ortm-player",
        "remote": args.whep_url,
        "peerId": peer_id,
        "group": "metrics",
        "level": "info",
        "message": message,
    }
    with open(path, "a", encoding="utf-8") as handle:
        handle.write(json.dumps(entry, ensure_ascii=False, separators=(",", ":")) + "\n")


def parse_args():
    parser = argparse.ArgumentParser(description="Native GStreamer WHEP ORTM v0 latency player")
    parser.add_argument("--whep-url", default="http://127.0.0.1:8889/fish_front/whep")
    parser.add_argument("--slot", default="gstreamer_front")
    parser.add_argument("--source", choices=("auto", "whepclientsrc", "whepsrc"), default="auto")
    parser.add_argument("--decoder", default="auto", help="auto, vtdec, avdec_h264, openh264dec, ...")
    parser.add_argument("--decode-interval-ms", type=int, default=200)
    parser.add_argument("--log-file", default="logs/client-events.log")
    parser.add_argument("--no-log-file", action="store_true")
    parser.add_argument("--display", action="store_true", help="also show video and measure handoff into the video sink")
    parser.add_argument("--video-sink", default="osxvideosink", help="video sink to use with --display")
    parser.add_argument("--print-pipeline", action="store_true")
    return parser.parse_args()


def run_player(args):
    Gst.init(None)
    pipeline, source, decoder, pipeline_text = make_pipeline(args)
    if args.print_pipeline:
        print(pipeline_text, flush=True)

    sink = pipeline.get_by_name("ortm_sink")
    if sink is None:
        raise RuntimeError("appsink not found")
    display_tap = pipeline.get_by_name("display_tap")

    peer_id = f"gst-{uuid.uuid4()}"
    loop = GLib.MainLoop()
    last_decode_at = 0.0
    frame_times = {}
    latest_display_submit_ms = None
    latest_display_tap_latency_ms = None
    latest_display_tap_perf_ms = None
    latest_display_tap_wall_ms = None
    stats = {
        "frames": 0,
        "decoded": 0,
        "failed": 0,
        "last_seq": None,
    }

    def frame_key(buffer):
        pts = int(buffer.pts)
        if pts >= 0 and pts != Gst.CLOCK_TIME_NONE:
            return pts
        dts = int(buffer.dts)
        if dts >= 0 and dts != Gst.CLOCK_TIME_NONE:
            return dts
        return None

    def update_display_tap_latency(item):
        nonlocal latest_display_tap_latency_ms
        if "display_tap_wall_ms" not in item or "timestamp_ms" not in item:
            return
        latency_ms = ((int(item["display_tap_wall_ms"]) & 0xFFFFFFFF) - int(item["timestamp_ms"])) & 0xFFFFFFFF
        if latency_ms < ORTM_MAX_REASONABLE_LATENCY_MS:
            latest_display_tap_latency_ms = latency_ms

    def record_frame_time(key, field, value_ms, wall_ms=None):
        nonlocal latest_display_submit_ms
        if key is None:
            return
        item = frame_times.setdefault(key, {})
        item[field] = value_ms
        if wall_ms is not None:
            item[f"{field}_wall_ms"] = wall_ms
        if "appsink_ms" in item and "display_tap_ms" in item:
            latest_display_submit_ms = item["display_tap_ms"] - item["appsink_ms"]
            if args.display:
                print(f"DISPLAY submit_ms={latest_display_submit_ms:.2f} pts={key}", flush=True)
        update_display_tap_latency(item)
        if len(frame_times) > 240:
            for old_key in sorted(frame_times)[:120]:
                frame_times.pop(old_key, None)

    def record_ortm_timestamp(key, timestamp_ms, appsink_perf_ms=None):
        nonlocal latest_display_tap_latency_ms
        if key is None:
            item = {}
        else:
            item = frame_times.setdefault(key, {})
            item["timestamp_ms"] = timestamp_ms
            update_display_tap_latency(item)
        if (
            latest_display_tap_perf_ms is not None
            and latest_display_tap_wall_ms is not None
            and appsink_perf_ms is not None
            and abs(latest_display_tap_perf_ms - appsink_perf_ms) <= 50
        ):
            latency_ms = ((int(latest_display_tap_wall_ms) & 0xFFFFFFFF) - int(timestamp_ms)) & 0xFFFFFFFF
            if latency_ms < ORTM_MAX_REASONABLE_LATENCY_MS:
                latest_display_tap_latency_ms = latency_ms

    def emit_metric(result, width, height, cost_ms):
        status = "playing" if result["ok"] else f"decode-{result.get('reason', 'failed')}"
        ortm = f"{int(round(result['latency_ms']))}ms" if result["ok"] else "--"
        ortm_net = ortm
        display_submit = "--" if latest_display_submit_ms is None else f"{latest_display_submit_ms:.1f}ms"
        display_tap = "--" if latest_display_tap_latency_ms is None else f"{latest_display_tap_latency_ms:.0f}ms"
        message = " ".join(
            [
                f"slot={args.slot}",
                "ice=connected",
                f"resolution={width}x{height}",
                f"ortm={ortm}",
                f"ortmNet={ortm_net}",
                "fallback=--",
                "upstream=--",
                "upstreamNet=--",
                "decodeMode=gstreamer-appsink",
                f"browser={cost_ms:.1f}ms",
                "draw=0.0ms",
                "read=0.0ms",
                f"decode={cost_ms:.1f}ms",
                f"displayTap={display_tap}",
                f"displaySubmit={display_submit}",
                "rtcJitter=--",
                "rtcDecode=--",
                "rtcFps=--",
                "rtcDrop=--",
                "rtcBitrate=--",
                "rtcPacketsLost=--",
                f"status={status}",
            ]
        )
        if not args.no_log_file:
            append_client_log(args.log_file, peer_id, args, message)
        print(message, flush=True)

    def on_new_sample(appsink):
        nonlocal last_decode_at
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR

        stats["frames"] += 1
        monotonic_now = time.monotonic()
        if (monotonic_now - last_decode_at) * 1000 < args.decode_interval_ms:
            return Gst.FlowReturn.OK
        last_decode_at = monotonic_now

        caps = sample.get_caps()
        structure = caps.get_structure(0)
        width = structure.get_value("width")
        height = structure.get_value("height")
        buffer = sample.get_buffer()
        key = frame_key(buffer)
        appsink_perf_ms = time.perf_counter() * 1000
        record_frame_time(key, "appsink_ms", appsink_perf_ms)
        ok, map_info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            return Gst.FlowReturn.ERROR
        start = time.perf_counter()
        try:
            now_ms = time.time_ns() // 1_000_000
            result = decode_ortm_rgba(map_info.data, width, height, now_ms)
        finally:
            buffer.unmap(map_info)
        cost_ms = (time.perf_counter() - start) * 1000

        if result["ok"]:
            record_ortm_timestamp(key, result["timestamp_ms"], appsink_perf_ms)
            stats["decoded"] += 1
            stats["last_seq"] = result["frame_seq"]
            print(
                "ORTM ok "
                f"seq={result['frame_seq']} latency={int(round(result['latency_ms']))}ms "
                f"finder={result['finder_errors']} timing={result['timing_errors']} "
                f"contrast={result['contrast']:.1f} cost={cost_ms:.2f}ms",
                flush=True,
            )
        else:
            stats["failed"] += 1
            print(
                "ORTM fail "
                f"reason={result.get('reason')} finder={result.get('finder_errors')} "
                f"timing={result.get('timing_errors')} contrast={result.get('contrast')} cost={cost_ms:.2f}ms",
                flush=True,
            )
        emit_metric(result, width, height, cost_ms)
        return Gst.FlowReturn.OK

    sink.connect("new-sample", on_new_sample)

    def on_display_handoff(_identity, buffer, *_args):
        nonlocal latest_display_tap_perf_ms, latest_display_tap_wall_ms
        latest_display_tap_perf_ms = time.perf_counter() * 1000
        latest_display_tap_wall_ms = time.time_ns() // 1_000_000
        record_frame_time(
            frame_key(buffer),
            "display_tap_ms",
            latest_display_tap_perf_ms,
            wall_ms=latest_display_tap_wall_ms,
        )

    if display_tap is not None:
        display_tap.connect("handoff", on_display_handoff)

    bus = pipeline.get_bus()
    bus.add_signal_watch()

    def on_message(_bus, message):
        if message.type == Gst.MessageType.ERROR:
            err, debug = message.parse_error()
            print(f"ERROR: {err.message}", file=sys.stderr)
            if debug:
                print(f"DEBUG: {debug}", file=sys.stderr)
            loop.quit()
        elif message.type == Gst.MessageType.EOS:
            loop.quit()

    bus.connect("message", on_message)

    def stop(_signum, _frame):
        loop.quit()

    if threading.current_thread() is threading.main_thread():
        signal.signal(signal.SIGINT, stop)
        signal.signal(signal.SIGTERM, stop)

    print(f"gst-ortm-player source={source} decoder={decoder} slot={args.slot} url={args.whep_url}", flush=True)
    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
        print(
            f"gst-ortm-player stopped frames={stats['frames']} decoded={stats['decoded']} failed={stats['failed']} last_seq={stats['last_seq']}",
            flush=True,
        )
    return 0


_MACOS_CALLBACK = None
_MACOS_ARGS = None


def run_with_macos_main(args):
    global _MACOS_CALLBACK, _MACOS_ARGS
    lib_path = ctypes.util.find_library("gstreamer-1.0") or "/opt/homebrew/lib/libgstreamer-1.0.dylib"
    lib = ctypes.CDLL(lib_path)
    callback_type = ctypes.CFUNCTYPE(ctypes.c_int, ctypes.c_void_p)
    _MACOS_ARGS = args

    def callback(_user_data):
        try:
            return int(run_player(_MACOS_ARGS))
        except BaseException as error:
            print(f"ERROR: {error}", file=sys.stderr, flush=True)
            return 1

    _MACOS_CALLBACK = callback_type(callback)
    lib.gst_macos_main_simple.argtypes = [callback_type, ctypes.c_void_p]
    lib.gst_macos_main_simple.restype = ctypes.c_int
    return int(lib.gst_macos_main_simple(_MACOS_CALLBACK, None))


def main():
    args = parse_args()
    if args.display and sys.platform == "darwin" and not os.environ.get("GST_ORTM_NO_MACOS_MAIN"):
        return run_with_macos_main(args)
    return run_player(args)


if __name__ == "__main__":
    raise SystemExit(main())
