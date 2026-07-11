# 5G 网络劣化模拟测试计划

日期：2026-07-11

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
