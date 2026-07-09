# 本地链路时延测试矩阵

## 目标

在同一台机器上，对比以下几类播放链路的端到端时延：

1. 非 WebRTC 本地播放链路
2. MediaMTX 的 WebRTC server path
3. 浏览器接收播放链路
4. GStreamer `whepsrc` 接收播放链路

目标不是一次性给出绝对结论，而是把时延大头定位到哪一层。

---

## 固定条件

每组测试尽量保持一致：

- 同一发送端：当前 ORTM 版 `fish_front_whip`
- 同一分辨率：`1280x720`
- 同一帧率：`30 fps`
- 同一码率：例如 `3000 kbps`
- 同一台机器
- 同一时间段
- 同一个 ORTM 码制
- 每组观察至少 `30~60s`

如果条件变了，不要直接横向比较。

---

## 当前可用指标

### 发送端

`fish_front_whip` 容器日志中有：

- `ORTM render_ms`
- `PIPELINE overlay_to_send_ms`
- `SENDER frame ...`

示例：

```text
SENDER frame seq=119 timestamp_ms=1783578989387 render_ms=0.3 overlay_to_send_ms=3.7 sender_pipeline_ms=4 sender_now_ms=1783578989391
```

重点看：

- `render_ms`
- `overlay_to_send_ms`
- `sender_pipeline_ms`

### 浏览器播放页

[public/whep-quad-direct.html](/Users/peng/Documents/tunnel/public/whep-quad-direct.html) 当前会显示：

- `ORTM`
- `Fallback`
- `前段`

含义：

- `ORTM`：端到端 G2G
- `Fallback`：浏览器接收侧缓冲估算
- `前段`：`ORTM - Fallback`

---

## 测试矩阵

### 1. GStreamer -> RTSP -> 本地 ffplay

目的：

- 拿到一条非 WebRTC、非浏览器的本地基线

链路：

```text
GStreamer sender
-> RTSP
-> ffplay
```

建议命令：

```bash
ffplay -fflags nobuffer -flags low_delay -framedrop -strict experimental \
  -rtsp_transport tcp \
  rtsp://127.0.0.1:8554/fish_front
```

观测方法：

- 直接肉眼对比 ORTM / 时间戳与墙钟
- 或截图后人工比对

结果解释：

- 如果这组已经很低，说明发送端本机处理不是大头
- 如果这组都很高，先不要怀疑 WebRTC，先怀疑发送端或播放器

---

### 2. GStreamer -> RTSP -> 本地 GStreamer player

目的：

- 排除 `ffplay` 播放器实现差异

链路：

```text
GStreamer sender
-> RTSP
-> GStreamer player
```

建议命令：

```bash
gst-launch-1.0 -e \
  rtspsrc location=rtsp://127.0.0.1:8554/fish_front latency=0 protocols=tcp ! \
  rtph264depay ! h264parse ! avdec_h264 ! videoconvert ! autovideosink sync=false
```

结果解释：

- 和 ffplay 接近：RTSP 本地链路本身没大问题
- 比 ffplay 高很多：播放器实现差异明显

---

### 3. GStreamer -> WHIP -> MediaMTX -> WHEP -> 本地浏览器

目的：

- 这是当前主链路

链路：

```text
GStreamer sender
-> WHIP
-> MediaMTX
-> WHEP
-> Browser
```

播放页面：

```text
http://127.0.0.1:9001/whep-quad-direct.html
```

默认 front 已自动填入：

```text
http://127.0.0.1:9001/fish_front/whep
```

重点记录：

- `ORTM`
- `Fallback`
- `前段`
- 发送端 `sender_pipeline_ms`

结果解释：

- `ORTM` 代表真实端到端
- `Fallback` 只代表浏览器接收缓冲
- `前段` 代表非浏览器接收缓冲那部分的大头

---

### 4. GStreamer -> WHIP -> MediaMTX -> WHEP -> 本地 GStreamer whepsrc

目的：

- 去掉浏览器，只保留 WebRTC server path + GStreamer receiver

链路：

```text
GStreamer sender
-> WHIP
-> MediaMTX
-> WHEP
-> GStreamer whepsrc
```

参考文档：

[GSTREAMER_WHEPSRC_PLAYER_SETUP.zh-CN.md](/Users/peng/Documents/tunnel/GSTREAMER_WHEPSRC_PLAYER_SETUP.zh-CN.md)

当前可用地址：

```text
http://127.0.0.1:9001/fish_front/whep
```

如果只想抓帧做对比，可用：

```bash
python3 play-whep-snapshot.py \
  http://127.0.0.1:9001/fish_front/whep \
  /tmp/fish_front_whep_snapshot.png
```

如果 `play-whep-snapshot.py` 所在环境没有 `gi` 或 `whepsrc`，用已经验证过的 GStreamer Docker 镜像跑。

结果解释：

- 如果这组明显低于浏览器组，浏览器是大头之一
- 如果这组和浏览器组接近，浏览器不是主因

---

### 5. 可选：GStreamer -> 远端 WHEP -> 本地浏览器

目的：

- 对比本地 MediaMTX 与远端真实服务

链路：

```text
GStreamer sender
-> remote WHIP / server
-> remote WHEP
-> local browser
```

结果解释：

- 本地显著低于远端：网络和远端服务是主要增量
- 本地与远端接近：本地链路自身已经占了绝大部分

---

## 建议记录表

每组测试记录以下字段：

| 项目 | 链路 | 分辨率 | fps | 码率 | ORTM avg | ORTM min/max | Fallback avg | 前段 avg | sender_pipeline_ms | 备注 |
|---|---|---:|---:|---:|---:|---|---:|---:|---:|---|
| 1 | RTSP -> ffplay | 1280x720 | 30 | 3000k | - | - | - | - | - | |
| 2 | RTSP -> GStreamer | 1280x720 | 30 | 3000k | - | - | - | - | - | |
| 3 | WHEP -> Browser | 1280x720 | 30 | 3000k | - | - | - | - | - | |
| 4 | WHEP -> whepsrc | 1280x720 | 30 | 3000k | - | - | - | - | - | |
| 5 | Remote WHEP -> Browser | 1280x720 | 30 | 3000k | - | - | - | - | - | 可选 |

---

## 结果解读模板

### 情况 A

- RTSP 本地播放器很低
- WHEP 本地 GStreamer 中等
- WHEP 本地浏览器更高

说明：

- RTSP 本地链路本身没问题
- WebRTC server path 引入了一笔固定成本
- 浏览器又在其上增加了一笔成本

### 情况 B

- RTSP 本地播放器低
- WHEP 本地 GStreamer已经很高
- WHEP 本地浏览器只比它略高

说明：

- 大头主要在 `WHIP -> MediaMTX -> WHEP` 这条 WebRTC 服务链路
- 浏览器只是次要增量

### 情况 C

- RTSP 本地播放器都不低

说明：

- 先排查发送端和本地播放器链路
- 暂时不要把主要责任归到 WebRTC

---

## 当前已知结论

根据当前已有观测：

- `sender_pipeline_ms` 约 `3~5 ms`
- 浏览器 `Fallback` 约 `5~10 ms`
- 浏览器 `ORTM` 约 `大几十 ms ~ 100 ms`

这说明：

- 发送端本机处理不是主要瓶颈
- 浏览器接收缓冲也不是主要瓶颈
- 大头在“完整 WebRTC 收发 + 服务端转发 + 浏览器解码前后”这一段

下一步最值得做的是第 1~4 组本地对照。
