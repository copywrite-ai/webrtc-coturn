#!/usr/bin/env python3
import signal
import sys
from datetime import datetime
from zoneinfo import ZoneInfo

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Gst  # noqa: E402


TZ = ZoneInfo("Asia/Shanghai")


def now_text():
    return datetime.now(TZ).strftime("%Y-%m-%d %H:%M:%S.%f")[:-3]


def main():
    if len(sys.argv) != 3:
        print(
            "usage: play-rtsp-snapshot.py <rtsp-url> <output-image>",
            file=sys.stderr,
        )
        return 1

    rtsp_url = sys.argv[1]
    output_image = sys.argv[2]

    Gst.init(None)

    pipeline = Gst.Pipeline.new("rtsp-snapshot")
    src = Gst.ElementFactory.make("rtspsrc", "src")
    depay = Gst.ElementFactory.make("rtph264depay", "depay")
    parse = Gst.ElementFactory.make("h264parse", "parse")
    decode = (
        Gst.ElementFactory.make("avdec_h264", "decode")
        or Gst.ElementFactory.make("openh264dec", "decode")
    )
    convert = Gst.ElementFactory.make("videoconvert", "convert")
    pngenc = Gst.ElementFactory.make("pngenc", "pngenc")
    sink = Gst.ElementFactory.make("appsink", "sink")

    for element in [src, depay, parse, decode, convert, pngenc, sink]:
        if element is None:
            print("failed to create gstreamer element", file=sys.stderr)
            return 2

    src.set_property("location", rtsp_url)
    src.set_property("protocols", "tcp")
    src.set_property("latency", 0)
    src.set_property("drop-on-latency", True)
    sink.set_property("emit-signals", True)
    sink.set_property("sync", False)
    sink.set_property("max-buffers", 1)
    sink.set_property("drop", True)

    for element in [src, depay, parse, decode, convert, pngenc, sink]:
        pipeline.add(element)

    if not depay.link(parse):
        print("failed to link depay -> parse", file=sys.stderr)
        return 3
    if not parse.link(decode):
        print("failed to link parse -> decode", file=sys.stderr)
        return 3
    if not decode.link(convert):
        print("failed to link decode -> convert", file=sys.stderr)
        return 3
    if not convert.link(pngenc):
        print("failed to link convert -> pngenc", file=sys.stderr)
        return 3
    if not pngenc.link(sink):
        print("failed to link pngenc -> sink", file=sys.stderr)
        return 3

    def on_pad_added(_src, pad):
        sink_pad = depay.get_static_pad("sink")
        if sink_pad.is_linked():
            return
        result = pad.link(sink_pad)
        if result != Gst.PadLinkReturn.OK:
            print(f"failed to link src pad: {result}", file=sys.stderr)

    src.connect("pad-added", on_pad_added)

    loop = GLib.MainLoop()
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

    def on_new_sample(appsink):
        sample = appsink.emit("pull-sample")
        if sample is None:
            return Gst.FlowReturn.ERROR
        buffer = sample.get_buffer()
        ok, map_info = buffer.map(Gst.MapFlags.READ)
        if not ok:
            print("failed to map sample buffer", file=sys.stderr)
            loop.quit()
            return Gst.FlowReturn.ERROR
        try:
            with open(output_image, "wb") as handle:
                handle.write(map_info.data)
            print(f"SNAPSHOT_CAPTURED_AT={now_text()}", flush=True)
            print(f"SNAPSHOT_FILE={output_image}", flush=True)
        finally:
            buffer.unmap(map_info)
        loop.quit()
        return Gst.FlowReturn.OK

    sink.connect("new-sample", on_new_sample)

    def stop(_signum, _frame):
        loop.quit()

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
