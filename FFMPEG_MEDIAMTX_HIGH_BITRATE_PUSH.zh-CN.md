# FFmpeg 到 MediaMTX 的高码率稳定推流方案

## 1. 目标

目标不是“浏览器里把上限设成 10000 kbps”，而是让推流端本身以更可控的方式持续输出接近 `10 Mbps` 的视频流，并继续复用当前项目里的 `MediaMTX -> WHEP` 播放链路。

推荐思路分两档：

1. 首选：`FFmpeg -> WHIP -> MediaMTX`
2. 更稳的工程回退：`FFmpeg -> RTSP -> MediaMTX`

之所以保留第二档，是因为 `FFmpeg` 官方把 `whip` muxer 标成 experimental，而 `MediaMTX` 官方对 `FFmpeg` 发布方式的推荐仍然是 `RTSP client`。

## 2. 什么时候该换成 FFmpeg

当你遇到下面这些情况时，就不该继续拿浏览器发流做“稳定 10M”测试：

- 需要尽量接近固定码率，而不是自适应码率。
- 需要固定 GOP、固定 fps、固定编码参数。
- 需要长期压测，而不是人工点页面测试。
- 需要排除浏览器硬件编码器和 WebRTC 拥塞控制的干扰。

## 3. 方案 A：FFmpeg 直接推 WHIP

### 3.1 前提

先确认本机 `ffmpeg` 支持 `whip`：

```bash
ffmpeg -hide_banner -muxers | rg whip
```

如果没有输出，说明这台机器上的 `ffmpeg` 构建不带 `whip` muxer，直接跳到第 4 节，用 `RTSP ingest`。

### 3.2 最小可用命令

下面命令适合先做稳定性验证：

```bash
ffmpeg -re \
  -f avfoundation -framerate 30 -video_size 1920x1080 -i "0:none" \
  -an \
  -c:v libx264 \
  -pix_fmt yuv420p \
  -profile:v baseline \
  -preset veryfast \
  -tune zerolatency \
  -g 60 \
  -keyint_min 60 \
  -sc_threshold 0 \
  -bf 0 \
  -b:v 10M \
  -maxrate 10M \
  -bufsize 20M \
  -f whip \
  http://127.0.0.1:8889/demo-stream/whip
```

说明：

- `-b:v 10M -maxrate 10M -bufsize 20M`：把目标拉到接近 10 Mbps。
- `-g 60 -keyint_min 60 -sc_threshold 0`：固定关键帧节奏，减少码率漂移。
- `-bf 0`：关闭 B 帧，兼容官方对 WHIP 的建议。
- `-tune zerolatency`：减小编码器内部缓存。
- `-an`：如果只是看视频带宽，先把音频去掉。

### 3.3 如果是文件压测

文件压测比真实摄像头更容易复现：

```bash
ffmpeg -re -stream_loop -1 -i sample-1080p-high-motion.mp4 \
  -an \
  -c:v libx264 \
  -pix_fmt yuv420p \
  -profile:v baseline \
  -preset veryfast \
  -tune zerolatency \
  -g 60 \
  -keyint_min 60 \
  -sc_threshold 0 \
  -bf 0 \
  -b:v 10M \
  -maxrate 10M \
  -bufsize 20M \
  -f whip \
  http://127.0.0.1:8889/demo-stream/whip
```

### 3.4 macOS 设备号

先列出设备：

```bash
ffmpeg -f avfoundation -list_devices true -i ""
```

再把 `-i "0:none"` 里的 `0` 改成实际摄像头编号。

### 3.5 注意事项

- 如果你用的是 `FFmpeg 8.0`，`MediaMTX` 文档特别提醒：可能需要视频轨和音频轨同时存在。
- 如果纯视频推不进去，就把 `-an` 去掉，补一条音频输入。

示例：

```bash
ffmpeg -re \
  -f avfoundation -framerate 30 -video_size 1920x1080 -i "0:0" \
  -c:v libx264 \
  -pix_fmt yuv420p \
  -profile:v baseline \
  -preset veryfast \
  -tune zerolatency \
  -g 60 \
  -keyint_min 60 \
  -sc_threshold 0 \
  -bf 0 \
  -b:v 10M \
  -maxrate 10M \
  -bufsize 20M \
  -c:a libopus -ar 48000 -ac 2 -b:a 128k \
  -f whip \
  http://127.0.0.1:8889/demo-stream/whip
```

## 4. 方案 B：FFmpeg 先推 RTSP，再由 MediaMTX 出 WHEP

如果你的目标是“稳定、可长期跑、尽量少踩 experimental 特性”，这条通常更稳。

`MediaMTX` 官方在 `FFmpeg` 发布页里写得很明确：推荐方式是 acting as a RTSP client。

### 4.1 命令

```bash
ffmpeg -re \
  -f avfoundation -framerate 30 -video_size 1920x1080 -i "0:none" \
  -an \
  -c:v libx264 \
  -pix_fmt yuv420p \
  -preset veryfast \
  -tune zerolatency \
  -g 60 \
  -keyint_min 60 \
  -sc_threshold 0 \
  -bf 0 \
  -b:v 10M \
  -maxrate 10M \
  -bufsize 20M \
  -f rtsp \
  rtsp://127.0.0.1:8554/demo-stream
```

然后继续用现有播放地址：

```text
http://127.0.0.1:8889/demo-stream/whep
```

或者你现在项目里经过公网反代的对应 `WHEP` 地址。

### 4.2 这条链路的意义

- `FFmpeg` 负责稳定编码。
- `MediaMTX` 负责协议扇出。
- 浏览器只负责播放，不再负责发流。

这通常比“浏览器直接 WHIP 发布并试图稳定 10M”更接近生产可控状态。

## 5. 编码参数建议

如果目标是尽量稳定接近 `10 Mbps`，建议优先从这里调：

- `1920x1080`
- `30 fps`
- `libx264`
- `preset=veryfast` 或 `faster`
- `g=60`
- `bf=0`
- `b:v=10M`
- `maxrate=10M`
- `bufsize=20M`

说明：

- `preset` 越慢，压缩效率越高，同样画质下越不容易真正打满 10M。
- 如果你想更容易把码率顶起来，可以先别用太慢的 preset。
- 静态画面本身就不需要 10M；想看见更高实际发送码率，要用高运动、高细节内容。

## 6. 验证步骤

### 6.1 确认 MediaMTX 已收到发布

先看容器日志：

```bash
docker logs -f mediamtx
```

应能看到对应 path 的 publish / reader 事件。

### 6.2 确认 WHEP 能拉到流

在你当前项目页面里填：

- `WHEP`: `http://127.0.0.1:8889/demo-stream/whep`

或者你的公网地址：

- `https://<public-host>/<stream>/whep`

### 6.3 看发送端真实码率

用 `ffmpeg` 推流时，不要只看浏览器页面的发送码率。浏览器页在这条方案里只是播放端。

更应该同时看：

- `ffmpeg` 控制台输出里的实时 `bitrate`
- `mediamtx` 日志
- 播放端 `getStats()` 里的接收码率、分辨率、丢包

## 7. 实际工程判断

如果你要的是：

- 标准化 WebRTC ingest
- 后续可以继续贴近浏览器/WebRTC 入口

先试第 3 节 `FFmpeg -> WHIP`。

如果你要的是：

- 更稳定的压测
- 更容易长期逼近固定 10M
- 更少依赖 experimental 特性

直接走第 4 节 `FFmpeg -> RTSP -> MediaMTX -> WHEP`。

## 8. 与当前项目的关系

当前仓库里已经有：

- `mediamtx` 服务
- `WHIP` 发布页
- `WHEP` 播放页

所以你不用再改整体架构，只是把“发布端”从浏览器切成 `FFmpeg`。

## 9. 参考

- MediaMTX publish page: https://mediamtx.org/docs/features/publish
- MediaMTX publish with FFmpeg: https://mediamtx.org/docs/publish/ffmpeg
- FFmpeg formats / whip muxer: https://ffmpeg.org/ffmpeg-formats.html
