# 远程操作客户端与低延迟视频方案调研

日期：2026-07-11

## 背景

当前主产品是基于 Web 的远程操作页面，核心需求是在页面内低延迟观看实时视频，并基于画面进行远程操作。

当前本地验证链路是：

```text
本机 GStreamer Publisher
  -> WHIP / WebRTC
  -> 本机 MediaMTX / relay
  -> WHEP / WebRTC
  -> 浏览器或原生 GStreamer Player
```

需要注意：目前测试主要是本地回环/本机链路，还没有完成真实 5G 视频源、远端网络、非本地终端、TURN/relay 全链路验证。因此当前结果只能说明本地软件链路与播放器开销基线，不能直接等价为最终现场 G2G 延迟。

## 当前判断

主产品正式形态仍建议优先采用浏览器 WebRTC：

```text
设备端 / 边缘端 GStreamer
  -> 采集视频
  -> ORTM 时间码叠加
  -> 低延迟编码
  -> WHIP / WebRTC 推流
  -> MediaMTX / relay / TURN
  -> WHEP / WebRTC 拉流
  -> 主产品 Web 页面播放和远程操作
```

浏览器端不运行 GStreamer。GStreamer 的位置在发送端、边缘端或专用测试端。

```text
GStreamer Publisher + Browser WebRTC Viewer
```

这是当前最现实的主产品路线。

## 为什么不优先 fork Chromium

确实存在基于 Chromium 深度定制的浏览器项目，例如 Brave、ungoogled-chromium、CEF、Electron 等。但 fork Chromium 作为远程操作端起点成本很高：

- Chromium 代码规模巨大，持续跟进上游成本高。
- WebRTC、视频解码、渲染、GPU、平台适配都很复杂。
- 我们当前的问题还没有证明必须改浏览器内核才能解决。
- 主产品目标是 Web 页面低摩擦访问，fork 浏览器会增加交付和维护复杂度。

因此短期不建议走 Chromium fork。

更合理的分层是：

```text
主产品：
Browser WebRTC Viewer

基准测试：
Native GStreamer Player

专业客户端可选：
Qt / CEF / WPE + native GStreamer or native WebRTC
```

## Electron 的定位

Electron 本质是：

```text
Chromium + Node.js + 原生桌面壳
```

它内置 Chromium，因此页面渲染、WebRTC、video element、Canvas、WebCodecs 等能力仍主要来自 Chromium。

Electron 的价值是产品交付形态增强：

- 复用 Web 前端代码。
- 做独立桌面 App。
- 控制窗口、全屏、多屏、快捷键和本地权限。
- 集成本地日志、配置、Prometheus exporter。
- 通过 native addon 或 sidecar 调用本地能力。

但 Electron 不会天然显著降低 WebRTC 播放延迟，因为核心播放链路仍然是 Chromium：

```text
WebRTC receive
  -> Chromium jitter buffer / decoder
  -> video element
  -> compositor
  -> display
```

如果只是为了低延迟，不应优先选择 Electron。只有需要专用桌面客户端能力时再考虑。

## Hybrid Native Client 方向

如果未来需要“网页 UI + 原生低延迟视频能力”，可以考虑 Hybrid Native Client：

```text
Web UI
  -> 操作界面、业务逻辑、状态展示

Native media layer
  -> GStreamer 或 native WebRTC
  -> 低延迟播放
  -> ORTM 解码
  -> 指标上报

JS/native bridge
  -> start / stop / switch stream
  -> latency / fps / bitrate / connection state
```

候选路线如下。

### CEF + GStreamer / native WebRTC

```text
CEF Chromium WebView
  -> 承载 Web 产品 UI

C++ native layer
  -> GStreamer / libwebrtc
  -> 低延迟播放
  -> ORTM 解码
  -> 指标上报

JS bridge
  -> Web UI 控制 native player
```

优点：

- Chromium 嵌入能力成熟。
- 可以保持接近 Chrome 的 Web 兼容性。
- native 层可以自己控制媒体管线。

缺点：

- 工程复杂，基本是 C++ 客户端项目。
- 没有看到成熟的“CEF + GStreamer WHEP + ORTM”开源成品可直接复用。

参考：

- CEF：https://github.com/chromiumembedded/cef
- OBS browser plugin 作为原生视频应用嵌 CEF 的参考：https://github.com/obsproject/obs-browser

### Qt WebEngine + GStreamer

```text
Qt App
  -> Qt WebEngine 显示 Web UI
  -> GStreamer 播放视频
  -> Qt/C++ 负责窗口、视频 surface、指标和系统集成
```

优点：

- 适合工业控制台、Ubuntu/Windows 桌面客户端。
- 原生窗口、硬件集成、设备控制能力成熟。
- 比直接 CEF/C++ 更容易做完整操作台 PoC。

缺点：

- WebEngine 与 GStreamer 视频 surface 的集成仍需要工程实现。
- Windows/macOS/Linux 的视频 sink 和硬件解码路径需要分别验证。

参考：

- Qt WebEngine：https://doc.qt.io/qt-6/qtwebengine-index.html
- Qt Multimedia：https://doc.qt.io/qt-6/qtmultimedia-index.html

### WebKitGTK / WPE WebKit + GStreamer

这条路线是开源生态里最接近“浏览器 + GStreamer”的方向。

```text
WPE WebKit / WebKitGTK
  -> Embedded browser / webapp container
  -> Web UI
  -> Linux embedded / kiosk / operation station
  -> GStreamer media ecosystem
```

适合 Linux embedded、kiosk、操作台场景。

参考：

- WPE WebKit：https://wpewebkit.org/
- Cog WPE launcher：https://github.com/Igalia/cog
- WebKitGTK：https://webkitgtk.org/

### Electron + GStreamer sidecar

```text
Electron Chromium
  -> Web UI

Node / native addon / sidecar
  -> GStreamer 播放、解码或指标采集
```

优点：

- 最大化复用前端。
- 产品交付快。

缺点：

- 如果需要把 GStreamer 解码帧低延迟、低拷贝地嵌回 Electron 页面，复杂度会升高。
- 如果退化成 GStreamer 解码后传帧给 Canvas/WebGL，可能重新引入拷贝和渲染开销。

## Foxglove 的参考价值

Foxglove Studio 的产品形态大致是：

```text
React / TypeScript Web App
  -> 浏览器运行
  -> Electron 桌面端
  -> 面板化机器人数据可视化
  -> WebSocket / ROS / MCAP 数据接入
```

它更像机器人数据可视化工作台，不是低延迟视频播放客户端。

值得借鉴：

- Web-first 产品形态。
- 面板化布局。
- 时间线、指标、视频、控制区组合。
- Electron 作为专业客户端交付形态。
- 插件化 Panel。

不应直接借鉴为：

```text
GStreamer 低延迟播放器
```

公开的 `foxglove/studio` 仓库已经归档：

- https://github.com/foxglove/studio

## PocketJS 的判断

PocketJS 更像是：

```text
QuickJS guest
  -> JS/JSX 写 UI 或游戏逻辑

Rust core
  -> native layout / rendering / simulation

wgpu / PSP / WASM host
  -> 渲染到不同平台
```

它不是 Chromium 浏览器，也不是 WebRTC/GStreamer 客户端。

从 `RUNTIMES.md` 看，它的 runtime 设计强调：

- Core + Surface + Guest。
- QuickJS guest。
- capability 由 surface 显式暴露。
- 默认没有 ambient filesystem / network / process access。

因此它默认不等于有 WebSocket、WebRTC、GStreamer 或联机能力。若要联机，需要自己在 Rust core 里实现网络能力并通过 surface 暴露给 JS。

参考：

- https://github.com/pocket-stack/pocketjs/blob/main/RUNTIMES.md

## 云游戏串流的参考价值

云游戏串流和我们的远程操作很接近。

典型云游戏链路：

```text
远端游戏 / 应用 / 桌面
  -> 采集画面
  -> GPU/CPU 编码
  -> WebRTC / RTP
  -> 浏览器播放
  -> 键盘鼠标/手柄输入回传
```

浏览器端通常是：

```text
HTML 页面
  -> video element / RTCPeerConnection
  -> Canvas/WebGL overlay
  -> Keyboard / Mouse / Gamepad API
  -> WebSocket 或 WebRTC DataChannel 回传输入
```

与我们的差异：

```text
云游戏：
用户操作的是远端虚拟游戏画面

远程操作：
用户操作的是真实设备，画面来自真实摄像头
```

因此我们还需要额外关注：

- 摄像头采集延迟。
- 编码器延迟。
- 5G 上行抖动。
- TURN/relay 延迟。
- 设备执行延迟。
- 控制指令 RTT。
- 真实 G2G latency。
- 安全和权限。

### CloudRetro

CloudRetro 是开源云游戏项目，目标是在浏览器中玩复古游戏。

架构大致是：

```text
Browser
  -> WebRTC 接收视频/音频
  -> DataChannel 回传输入

Coordinator
  -> Web 前端
  -> 负载均衡
  -> WebRTC 信令
  -> 选择 worker

Worker
  -> Libretro 模拟器
  -> 捕获游戏画面和音频
  -> 编码 H.264 / Opus
  -> WebRTC 发给浏览器
```

CloudRetro 中 GStreamer 的位置不是浏览器端，而是 worker/native 侧媒体依赖。浏览器仍然只是 WebRTC 播放和输入回传。

对我们的启发：

```text
WebRTC video/audio
  + DataChannel 输入
  + Coordinator/Worker 分层
  + 按延迟选择 worker
  + 浏览器作为正式客户端
```

但没有看到 CloudRetro 公开严谨的端到端性能测试报告，例如 p50/p95 G2G、编码耗时、浏览器解码耗时、输入到画面变化耗时等。

参考：

- CloudRetro：https://github.com/giongto35/cloud-game
- webrtcHacks 文章：https://webrtchacks.com/open-source-cloud-gaming-with-webrtc/

### Selkies / Neko

Selkies 和 Neko 更偏远程桌面/云应用/云游戏形态。

值得借鉴：

- 低延迟浏览器串流。
- WebRTC 作为视频通道。
- 浏览器端输入回传。
- 多用户/会话/权限。
- 网络状态与连接管理。

参考：

- Selkies：https://github.com/selkies-project/selkies
- Neko：https://github.com/m1k1o/neko

## 对当前产品的建议

短期继续推进：

```text
GStreamer Publisher
  -> WHIP
  -> MediaMTX / relay / TURN
  -> WHEP
  -> Browser WebRTC Viewer
  -> ORTM + WebRTC stats + control channel metrics
```

同时保留：

```text
Native GStreamer Player
  -> 用作链路下限和排障基准
```

中期可以做一个 Hybrid Client PoC：

```text
Qt / CEF / WPE App
  -> Web UI 加载远程操作页面
  -> native GStreamer 拉 WHEP 或 native WebRTC
  -> ORTM 解码和 Prometheus 指标
  -> JS bridge 控制 start/stop/switch stream
```

不建议短期做：

```text
fork Chromium
```

## 后续验证计划

建议按三层推进：

```text
A. 本地基线
本机推流 -> 本机播放
目标：确认软件链路下限

B. 局域网 / Tailscale
一台机器推流 -> 另一台机器播放
目标：验证跨机器 WebRTC/WHEP 行为

C. 真实 5G
5G 视频源 -> relay / TURN -> 远端浏览器
目标：验证真实远程操作时延和稳定性
```

每一层都保留同一套指标：

- ORTM G2G latency。
- WebRTC RTT / jitter / packet loss。
- bitrate / fps / resolution。
- MediaMTX 指标。
- TURN relay 流量和连接指标。
- 控制指令 RTT。
- 设备执行反馈延迟。

最终需要判断的不是单一“视频延迟”，而是完整闭环：

```text
用户操作
  -> 指令发出
  -> 设备接收并执行
  -> 摄像头看到变化
  -> 视频传回
  -> 页面显示变化
```

这才是远程操作的真实体验指标。
