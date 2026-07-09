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
            "usage: play-whep-snapshot.py <whep-endpoint> <output-image>",
            file=sys.stderr,
        )
        return 1

    whep_endpoint = sys.argv[1]
    output_image = sys.argv[2]

    Gst.init(None)

    pipeline = Gst.Pipeline.new("whep-snapshot")
    src = Gst.ElementFactory.make("whepsrc", "src")
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

    src.set_property("whep-endpoint", whep_endpoint)
    src.set_property("audio-caps", Gst.Caps.new_empty())
    src.set_property(
        "video-caps",
        Gst.Caps.from_string(
            "application/x-rtp,media=video,encoding-name=H264,clock-rate=90000,payload=103,packetization-mode=1,profile-level-id=42e01f,level-asymmetry-allowed=1"
        ),
    )
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

    aux_pad_index = 0

    def on_pad_added(_src, pad):
        nonlocal aux_pad_index
        caps = pad.get_current_caps() or pad.query_caps(None)
        structure = caps.get_structure(0) if caps and caps.get_size() > 0 else None
        media = structure.get_string("media") if structure else None
        encoding_name = structure.get_string("encoding-name") if structure else None
        caps_text = caps.to_string() if caps is not None else "<none>"
        print(
            f"PAD_ADDED name={pad.get_name()} media={media} encoding={encoding_name} caps={caps_text}",
            flush=True,
        )
        sink_pad = depay.get_static_pad("sink")
        should_link_primary = (
            not sink_pad.is_linked()
            and (media == "video" or media is None)
            and (encoding_name in (None, "H264"))
        )
        if should_link_primary:
            result = pad.link(sink_pad)
            if result != Gst.PadLinkReturn.OK:
                print(f"failed to link src pad: {result}", file=sys.stderr)
            else:
                print("PRIMARY_PAD_LINK_RESULT=ok", flush=True)
            return

        if media != "video" or encoding_name != "H264":
            aux_pad_index += 1
            queue = Gst.ElementFactory.make("queue", f"aux_queue_{aux_pad_index}")
            fake = Gst.ElementFactory.make("fakesink", f"aux_sink_{aux_pad_index}")
            if queue is None or fake is None:
                print("failed to create aux sink chain", file=sys.stderr)
                return
            fake.set_property("sync", False)
            pipeline.add(queue)
            pipeline.add(fake)
            queue.sync_state_with_parent()
            fake.sync_state_with_parent()
            if not queue.link(fake):
                print("failed to link aux queue -> fakesink", file=sys.stderr)
                return
            aux_sink_pad = queue.get_static_pad("sink")
            result = pad.link(aux_sink_pad)
            print(f"AUX_PAD_LINK_RESULT={result.value_nick}", flush=True)
            return
        print("PRIMARY_PAD_ALREADY_LINKED", flush=True)

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
