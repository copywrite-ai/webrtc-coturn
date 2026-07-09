# GStreamer `whepsrc` 播放器搭建方案

## 目标

在另一台 Ubuntu 机器上，使用 GStreamer 的 `whepsrc` 直接拉取 WHEP 流并本地播放，用来和浏览器 WHEP 播放的延迟做对比。

本文优先给出一条最稳妥的方案：

- 不在播放机上重新编译 `gst-plugin-webrtchttp`
- 直接使用已经验证过的 Docker 镜像
- 用一个很小的 Python GStreamer 播放器脚本处理 `whepsrc` 的动态 pad

## 推荐方案

### 方案概述

播放机上的链路如下：

```text
WHEP endpoint
-> whepsrc
-> RTP depay
-> H264 decode
-> 本地显示
```

推荐使用 Docker 跑播放器，而不是在播放机上原生编译插件。原因很简单：

- `whepsrc` 来自 `gst-plugin-webrtchttp`
- 原生编译这一套依赖重，和当前推流镜像一样会花不少时间
- Docker 方案可复用当前已经验证过的镜像环境

## 环境要求

播放机需要满足：

- Ubuntu 22.04 或 24.04
- 已安装 Docker
- 能访问目标 WHEP 地址
- 如果需要本地显示，宿主机有图形环境，且允许容器访问 X11

## 播放地址

当前可用的本地 WHEP 地址示例：

```text
http://127.0.0.1:9001/fish_front/whep
http://127.0.0.1:9001/fish_back/whep
http://127.0.0.1:9001/fish_left/whep
http://127.0.0.1:9001/fish_right/whep
```

远端对比地址示例：

```text
https://media-proxy.nexusdot.cn/noproxy/peng-mbp14/fish_front/whep
```

在另一台机器上使用时，把 `127.0.0.1` 换成可访问那台发布机的实际地址或 `ts` 域名。

## 播放器脚本

在播放机上创建 [play-whep.py](/Users/peng/Documents/tunnel/play-whep.py)：

```python
#!/usr/bin/env python3
import signal
import sys

import gi

gi.require_version("Gst", "1.0")
gi.require_version("GLib", "2.0")
from gi.repository import GLib, Gst


def main():
    if len(sys.argv) < 2:
        print("usage: play-whep.py <whep-endpoint>", file=sys.stderr)
        sys.exit(1)

    whep_endpoint = sys.argv[1]

    Gst.init(None)

    pipeline = Gst.Pipeline.new("whep-player")

    src = Gst.ElementFactory.make("whepsrc", "src")
    depay = Gst.ElementFactory.make("rtph264depay", "depay")
    parse = Gst.ElementFactory.make("h264parse", "parse")
    decode = Gst.ElementFactory.make("avdec_h264", "decode")
    convert = Gst.ElementFactory.make("videoconvert", "convert")
    sink = Gst.ElementFactory.make("autovideosink", "sink")

    for element in [src, depay, parse, decode, convert, sink]:
        if element is None:
            print("failed to create gstreamer element", file=sys.stderr)
            sys.exit(2)

    src.set_property("whep-endpoint", whep_endpoint)

    pipeline.add(src)
    pipeline.add(depay)
    pipeline.add(parse)
    pipeline.add(decode)
    pipeline.add(convert)
    pipeline.add(sink)

    if not depay.link(parse):
        print("failed to link depay -> parse", file=sys.stderr)
        sys.exit(3)
    if not parse.link(decode):
        print("failed to link parse -> decode", file=sys.stderr)
        sys.exit(3)
    if not decode.link(convert):
        print("failed to link decode -> convert", file=sys.stderr)
        sys.exit(3)
    if not convert.link(sink):
        print("failed to link convert -> sink", file=sys.stderr)
        sys.exit(3)

    def on_pad_added(_src, pad):
        caps = pad.get_current_caps() or pad.query_caps(None)
        structure = caps.get_structure(0) if caps and caps.get_size() > 0 else None
        media = structure.get_string("media") if structure else None
        encoding_name = structure.get_string("encoding-name") if structure else None

        if media != "video" or encoding_name != "H264":
            return

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

    def stop(_signum, _frame):
        pipeline.send_event(Gst.Event.new_eos())

    signal.signal(signal.SIGINT, stop)
    signal.signal(signal.SIGTERM, stop)

    pipeline.set_state(Gst.State.PLAYING)
    try:
        loop.run()
    finally:
        pipeline.set_state(Gst.State.NULL)


if __name__ == "__main__":
    main()
```

## Docker 运行方式

假设你已经有一个包含 `whepsrc` 的镜像。

先确认镜像里有 `whepsrc`：

```bash
docker run --rm --entrypoint gst-inspect-1.0 <your-image> whepsrc
```

如果有图形界面，在播放机上：

```bash
xhost +local:
docker run --rm -it \
  -e DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$PWD/play-whep.py:/play-whep.py:ro" \
  --entrypoint python3 \
  <your-image> \
  /play-whep.py "http://<host>:9001/fish_front/whep"
```

示例：

```bash
docker run --rm -it \
  -e DISPLAY=$DISPLAY \
  -v /tmp/.X11-unix:/tmp/.X11-unix \
  -v "$PWD/play-whep.py:/play-whep.py:ro" \
  --entrypoint python3 \
  tunnel-whip-publisher:local \
  /play-whep.py "http://192.168.1.20:9001/fish_front/whep"
```

## 无图形环境方案

如果播放机没有桌面环境，不要用 `autovideosink`。把脚本中的 sink 改成：

- `fakesink`
  用来只验证收流和解码链路
- `fpsdisplaysink video-sink=fakesink`
  用来粗看帧率
- `filesink`
  用来落盘

例如把：

```python
sink = Gst.ElementFactory.make("autovideosink", "sink")
```

改成：

```python
sink = Gst.ElementFactory.make("fakesink", "sink")
```

## 如果必须原生安装

不推荐，但如果你坚持原生安装，至少需要：

```bash
sudo apt update
sudo apt install -y \
  python3 python3-gi python3-gst-1.0 \
  gstreamer1.0-tools \
  gstreamer1.0-plugins-base \
  gstreamer1.0-plugins-good \
  gstreamer1.0-plugins-bad \
  gstreamer1.0-plugins-ugly \
  gstreamer1.0-libav \
  gstreamer1.0-x
```

但仅这些还不够，因为 Ubuntu 仓库默认通常没有你当前使用的 `gst-plugin-webrtchttp` Rust 插件。你还需要自己编 `gst-plugin-webrtchttp`，这正是本文不推荐原生方案的原因。

## 验证方法

### 1. 先确认 WHEP 地址存在

直接 `GET` 一个 WHEP 地址时，正常表现应是：

```text
405 method not allowed
```

这说明 endpoint 存在，只是 WHEP 不接受普通 GET 播放。

### 2. 再启动播放器

如果播放器成功连上，应当看到：

- 本地画面出现
- 或至少没有 SDP / ICE / HTTP 错误

### 3. 做延迟对比

如果画面里已经有时间戳，可以直接拿：

- 画面内时间
- 播放机本地墙钟

做肉眼对比。

如果这个 GStreamer player 的延迟明显低于浏览器 WHEP，说明大头在浏览器端缓冲和渲染，而不是服务端或发布端。

## 结论

对另一台 Ubuntu 机器，最实际的方案是：

1. 使用已经包含 `whepsrc` 的 Docker 镜像
2. 运行一个很小的 Python GStreamer 播放器
3. 直接连到：
   `http://<host>:9001/fish_front/whep`

这条路径最省时间，也最适合用来和浏览器 WHEP 延迟做对比。
