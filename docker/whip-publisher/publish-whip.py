#!/usr/bin/env python3
import os
import signal
import sys
from collections import deque
from datetime import datetime
from time import monotonic_ns
from zoneinfo import ZoneInfo

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Gst  # noqa: E402


def env_required(name):
    value = os.environ.get(name, "")
    if not value:
        raise SystemExit(f"{name} is required")
    return value


def env_value(name, default):
    return os.environ.get(name, default)


def gst_quote(value):
    escaped = value.replace("\\", "\\\\").replace('"', '\\"')
    return f'"{escaped}"'


def timestamp_text(timezone):
    return datetime.now(timezone).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


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

    width = env_value("WIDTH", "1280")
    height = env_value("HEIGHT", "720")
    fps = env_value("FPS", "30")
    bitrate_kbps = env_value("BITRATE_KBPS", "8000")
    key_int_max = env_value("KEY_INT_MAX", "60")
    pattern = env_value("PATTERN", "smpte")
    speed_preset = env_value("SPEED_PRESET", "ultrafast")
    x264_option_string = env_value(
        "X264_OPTION_STRING", "nal-hrd=cbr:force-cfr=1:filler=1"
    )
    whip_stun_server = env_value("WHIP_STUN_SERVER", "")
    whip_turn_server = env_value("WHIP_TURN_SERVER", "")
    whip_turn_server_2 = env_value("WHIP_TURN_SERVER_2", "")
    whip_force_turn = env_value("WHIP_FORCE_TURN", "0")
    whip_sink_extra_args = env_value("WHIP_SINK_EXTRA_ARGS", "")
    timestamp_overlay = env_value("TIMESTAMP_OVERLAY", "1")
    timestamp_font = env_value("TIMESTAMP_FONT", "Sans 22")
    timestamp_xpad = env_value("TIMESTAMP_XPAD", "12")
    timestamp_ypad = env_value("TIMESTAMP_YPAD", "10")
    timestamp_tz_name = env_value("TIMESTAMP_TZ", "Asia/Shanghai")
    timestamp_tz = ZoneInfo(timestamp_tz_name)
    metrics_enabled = env_value("PIPELINE_METRICS", "1")
    metrics_interval_frames = int(env_value("PIPELINE_METRICS_INTERVAL_FRAMES", "30"))

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
    print(f"  path mode    : {'device/stream' if whip_include_device == '1' else 'stream-only'}", flush=True)
    print(f"  resolution   : {width}x{height}", flush=True)
    print(f"  fps          : {fps}", flush=True)
    print(f"  bitrate      : {bitrate_kbps} kbps", flush=True)
    print(f"  pattern      : {pattern}", flush=True)
    print(f"  x264 preset  : {speed_preset}", flush=True)
    print(f"  x264 options : {x264_option_string}", flush=True)
    print(f"  gst debug    : {gst_debug_level}", flush=True)
    print(f"  timestamp    : {'milliseconds' if timestamp_overlay == '1' else '<off>'}", flush=True)
    print(f"  timestamp tz : {timestamp_tz_name}", flush=True)
    print(
        f"  pipe metrics : {'overlay->send' if metrics_enabled == '1' else '<off>'}",
        flush=True,
    )
    print(f"  stun server  : {whip_stun_server or '<none>'}", flush=True)
    print(f"  turn server  : {'<configured>' if whip_turn_server else '<none>'}", flush=True)
    print(f"  turn server 2: {'<configured>' if whip_turn_server_2 else '<none>'}", flush=True)
    print(f"  force turn   : {whip_force_turn}", flush=True)
    print("", flush=True)
    print(f"  sink args    : {sink_args_log}", flush=True)
    print("", flush=True)

    overlay = ""
    if timestamp_overlay == "1":
        overlay = (
            " ! textoverlay name=timestamp_overlay "
            "halignment=left valignment=top "
            f"xpad={timestamp_xpad} ypad={timestamp_ypad} "
            "shaded-background=true draw-shadow=true draw-outline=true "
            f"font-desc={gst_quote(timestamp_font)}"
        )

    pipeline_description = (
        f"videotestsrc is-live=true pattern={gst_quote(pattern)} "
        f"! video/x-raw,width={width},height={height},framerate={fps}/1,format=I420"
        f"{overlay} "
        f"! x264enc name=encoder bitrate={bitrate_kbps} speed-preset={gst_quote(speed_preset)} "
        f"tune=zerolatency key-int-max={key_int_max} bframes=0 "
        f"option-string={gst_quote(x264_option_string)} "
        "! video/x-h264,profile=baseline "
        "! h264parse name=h264parse0 config-interval=-1 "
        "! identity name=send_probe silent=true "
        f"! whipclientsink name=ws {' '.join(sink_args)}"
    )

    pipeline = Gst.parse_launch(pipeline_description)
    loop = GLib.MainLoop()

    timestamp = pipeline.get_by_name("timestamp_overlay")
    if timestamp is not None:
        timestamp.set_property("text", timestamp_text(timestamp_tz))

        def update_timestamp():
            timestamp.set_property("text", timestamp_text(timestamp_tz))
            return True

        GLib.timeout_add(10, update_timestamp)

    if metrics_enabled == "1":
        overlay_frame_times = deque()
        overlay_send_stats = {
            "count": 0,
            "total_ms": 0.0,
            "min_ms": None,
            "max_ms": None,
            "dropped": 0,
        }

        def overlay_probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK

            overlay_frame_times.append(monotonic_ns())
            if len(overlay_frame_times) > 180:
                overlay_frame_times.popleft()
                overlay_send_stats["dropped"] += 1
            return Gst.PadProbeReturn.OK

        def send_probe(_pad, info):
            buffer = info.get_buffer()
            if buffer is None:
                return Gst.PadProbeReturn.OK

            if not overlay_frame_times:
                return Gst.PadProbeReturn.OK

            started_at = overlay_frame_times.popleft()
            delay_ms = (monotonic_ns() - started_at) / 1_000_000.0
            overlay_send_stats["count"] += 1
            overlay_send_stats["total_ms"] += delay_ms
            overlay_send_stats["min_ms"] = (
                delay_ms
                if overlay_send_stats["min_ms"] is None
                else min(overlay_send_stats["min_ms"], delay_ms)
            )
            overlay_send_stats["max_ms"] = (
                delay_ms
                if overlay_send_stats["max_ms"] is None
                else max(overlay_send_stats["max_ms"], delay_ms)
            )

            if overlay_send_stats["count"] % max(metrics_interval_frames, 1) == 0:
                average_ms = (
                    overlay_send_stats["total_ms"] / overlay_send_stats["count"]
                )
                print(
                    "PIPELINE overlay_to_send_ms "
                    f"avg={average_ms:.1f} "
                    f"min={overlay_send_stats['min_ms']:.1f} "
                    f"max={overlay_send_stats['max_ms']:.1f} "
                    f"samples={overlay_send_stats['count']} "
                    f"dropped_pts={overlay_send_stats['dropped']}",
                    flush=True,
                )
            return Gst.PadProbeReturn.OK

        overlay_src_pad = timestamp.get_static_pad("src") if timestamp is not None else None
        send_probe_element = pipeline.get_by_name("send_probe")
        send_src_pad = (
            send_probe_element.get_static_pad("src")
            if send_probe_element is not None
            else None
        )

        if overlay_src_pad is not None and send_src_pad is not None:
            overlay_src_pad.add_probe(Gst.PadProbeType.BUFFER, overlay_probe)
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
