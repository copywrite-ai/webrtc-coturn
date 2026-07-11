# 5G 网络劣化模拟测试计划

日期：2026-07-11

补充背景：

- 2026-07-09 已完成 direct / relay / ORTM 基础能力验证，见 [2026-07-09-WHEP-ORTM-SUMMARY.zh-CN.md](/Users/peng/Documents/tunnel/2026-07-09-WHEP-ORTM-SUMMARY.zh-CN.md)
- 本文档在原测试计划基础上，补充 2026-07-11 当天的实际弱网实验结果

## 目标

验证主产品推荐链路在不同网络条件下的低延迟表现：

```text
GStreamer Publisher
  -> WHIP
  -> MediaMTX / relay / TURN
  -> WHEP
  -> Browser WebRTC Viewer
```

重点观察：

- ORTM G2G latency。
- 前段 / 净前段 latency。
- WebRTC RTT / jitter / packet loss。
- bitrate / fps / resolution。
- 是否持续上涨。
- 是否 offline / reconnect。

## 工具

Linux 网络劣化使用：

```bash
scripts/netem-profile.sh <dev> <profile>
```

支持 profile：

```text
show       查看当前 qdisc
clear      清除 qdisc
5g-good    20ms +/- 5ms, 0.1% loss, 20mbit
5g-mid     40ms +/- 15ms, 0.5% loss, 8mbit
5g-bad     80ms +/- 30ms, 1.0% loss, 3mbit
5g-jitter  40ms +/- 40ms, 0.5% loss, 8mbit
```

示例：

```bash
sudo scripts/netem-profile.sh eth0 show
sudo scripts/netem-profile.sh eth0 5g-good
sudo scripts/netem-profile.sh eth0 clear
```

注意：脚本必须运行在目标网卡所在的 Linux network namespace 中，通常需要 root 或 `CAP_NET_ADMIN`。如果是在 Docker 容器内模拟 publisher 上行，需要容器具备 `NET_ADMIN`。

当前 macOS / OrbStack 本地测试中，`fish_front` 已提供专用封装：

```bash
scripts/netem-fish-front.sh show
scripts/netem-fish-front.sh 5g-good
scripts/netem-fish-front.sh 5g-mid
scripts/netem-fish-front.sh 5g-bad
scripts/netem-fish-front.sh 5g-jitter
scripts/netem-fish-front.sh clear
```

它默认作用于：

```text
container: tunnel-fish_front_whip-1
dev:       eth0
```

可通过环境变量覆盖：

```bash
NETEM_CONTAINER=tunnel-fish_front_whip-1 NETEM_DEV=eth0 scripts/netem-fish-front.sh show
```

## 测试原则

先只在 publisher 出口加劣化，模拟 5G 上行。不要同时在 publisher 和 viewer 两端加，否则延迟会叠加，解释会变复杂。

每轮固定：

```text
fish_front
720p
60 fps
5 Mbps
单路播放
观看 1-2 分钟
```

如果 `5g-bad` 下无法稳定，再降码率或分辨率：

```text
720p / 60fps / 3Mbps
480p / 60fps / 2Mbps
720p / 30fps / 3Mbps
```

## 流程

### 0. 清理网络劣化

```bash
sudo scripts/netem-profile.sh <dev> clear
```

### 1. 基线

```text
profile: none
目标：确认当前链路健康
预期：ORTM 稳定，不持续上涨，不 offline
```

### 2. 轻度 5G

```bash
sudo scripts/netem-profile.sh <dev> 5g-good
```

预期：

```text
ORTM 增加约 20-40ms
不持续上涨
不 offline
```

### 3. 中等 5G

```bash
sudo scripts/netem-profile.sh <dev> 5g-mid
```

预期：

```text
WebRTC jitter buffer 可能变大
重点观察是否持续上涨
```

### 4. 差 5G

```bash
sudo scripts/netem-profile.sh <dev> 5g-bad
```

预期：

```text
5Mbps 可能不稳
可能需要降到 2-3Mbps 或 480p
```

### 5. 高 jitter

```bash
sudo scripts/netem-profile.sh <dev> 5g-jitter
```

预期：

```text
如果 ORTM 缓慢上涨，重点查浏览器 jitter buffer 和 WebRTC 自适应行为
```

### 6. 恢复

```bash
sudo scripts/netem-profile.sh <dev> clear
```

## 每轮记录

记录表：

```text
轮次:
profile:
resolution:
fps:
bitrate:
path: direct / relay / turn
ORTM p50/p95/max:
净ORTM p50/p95/max:
前段 p50/p95/max:
净前段 p50/p95/max:
WebRTC RTT:
jitter:
packet loss:
MediaMTX lost/discarded:
是否上涨:
是否 offline:
结论:
```

## 判断标准

可接受：

```text
ORTM 稳定
p95 可解释
没有持续线性上涨
没有频繁 reconnect
控制操作体感可用
```

不可接受：

```text
ORTM 持续上涨
长时间不恢复
频繁 offline
丢包后浏览器 jitter buffer 不回落
MediaMTX 或 TURN 指标出现明显异常
```

## 下一步

完成模拟测试后，再进入真实链路：

```text
真实 5G 视频源
  -> relay / TURN
  -> 远端浏览器
```

真实链路需要额外记录：

- 5G 运营商和信号状态。
- 上行测速。
- 观看端网络类型。
- 是否强制 TURN relay。
- 控制指令 RTT。
- 设备执行反馈延迟。

## 实际结果

### 本轮环境

固定条件：

```text
stream: fish_front
page:   http://127.0.0.1:9001/whep-quad-direct.html
path:   Browser -> WHEP direct
profile: 5g-mid
netem: delay 40ms +/- 15ms, loss 0.5%, rate 8mbit
采样方式: 通过 tunnel-monitor 容器内 /api/snapshot 抓取稳定窗口快照
```

本轮中间做过两项工程修正：

1. `scripts/netem-fish-front.sh` 增加 guard 机制  
   解决 `fish_front` 容器重建后 `tc qdisc` 丢失，导致实验实际上退回无劣化的问题。
2. `docker/whip-publisher/Dockerfile.base` 补入 `gst-plugin-rtp` / `rtpgccbwe`  
   使发送端具备更完整的 WebRTC 拥塞控制基础。

### 有效样本

| 场景 | ORTM | 净ORTM | Upstream | 净Upstream | Browser | Jitter | rtcFps | rtcBitrate | MediaMTX rtpPacketsLost | 结论 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| `960x540 / 30fps / 2.5Mbps` | `146 ms` | `141 ms` | `134 ms` | `129 ms` | `4.6 ms` | `15.7 ms` | `30` | `2.34 Mbps` | `225` | 可用基线 |
| `960x540 / 30fps / 3.0Mbps` | `278 ms` | `273 ms` | `264 ms` | `259 ms` | `4.6 ms` | `82.0 ms` | `30` | `2.99 Mbps` | `127` | 明显变差 |
| `960x540 / 60fps / 3.0Mbps` | `137 ms` | `132 ms` | `121 ms` | `116 ms` | `4.6 ms` | `20.0 ms` | `55` | `2.71 Mbps` | `88` | 好于 30/2.5 |
| `960x540 / 60fps / 2.5Mbps` | `133 ms` | `128 ms` | `117 ms` | `112 ms` | `4.5 ms` | `7.5 ms` | `59` | `2.35 Mbps` | `78` | 本轮最优 |

### 本轮直接结论

1. 当前 `5g-mid` 条件下，最优点是：

```text
960x540 / 60fps / 2.5Mbps / key-int=120
```

2. `30fps / 3.0Mbps` 是明确坏点：

- ORTM 从 `146 ms` 上升到 `278 ms`
- upstream 从 `134 ms` 上升到 `264 ms`
- jitter 上升到 `82 ms`

这说明在当前 8mbit / 0.5% loss / 40ms+/-15ms 的上行模拟里，单纯提高码率会放大队列和抖动，不会自然降低端到端时延。

3. 在同样 `60fps` 下：

- `2.5Mbps` 比 `3.0Mbps` 更稳
- `rtcFps` 更接近目标值
- `jitter` 更低
- ORTM / upstream 都更好

这说明这里的关键不是“码率越高越低延迟”，而是编码输出、网络整形和浏览器接收侧缓冲之间存在一个平衡点。

4. 浏览器仍不是主瓶颈：

- Browser cost 始终约 `4.5 ~ 4.6 ms`
- 大头仍然在 upstream
- 发送端内部 `overlay_to_send` 约 `1.6 ~ 1.9 ms`

因此本轮的主要优化方向仍然应放在：

- publisher 输出节奏
- WebRTC sender pacing / congestion behavior
- 弱网下队列堆积与恢复

### 与 2026-07-09 结论的关系

2026-07-09 的 direct 本地优选点是：

```text
60fps / 5Mbps
```

它是在近乎本地健康链路下，把延迟压到最低的更激进档位。

而 2026-07-11 这轮 `5g-mid` 实测得到的推荐点变成：

```text
960x540 / 60fps / 2.5Mbps
```

这两者并不矛盾，说明已经出现了明确的双档位分化：

- 健康局域网 / 近端链路：可以推更高码率
- 弱网上行 / 5G 模拟：应优先保证高 fps，同时把总码率控制在较保守区间

### 当前建议默认档

如果接下来继续在 `5g-mid` 条件下做单路远程观看实验，建议默认使用：

```text
WIDTH=960
HEIGHT=540
FPS=60
BITRATE_KBPS=2500
KEY_INT_MAX=120
```

### 下一轮建议

下一轮最值得继续做的是围绕 `60fps` 做窄范围二分，而不是回到 `30fps`：

```text
960x540 / 60fps / 2.2Mbps
960x540 / 60fps / 2.5Mbps
960x540 / 60fps / 2.8Mbps
```

目标是确认：

- `2.5Mbps` 是否就是当前最优平衡点
- 是否存在更低码率但相同时延的点
- 弱网下 ORTM / upstream / jitter 的稳定性边界
