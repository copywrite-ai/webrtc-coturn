# ORTM 可迁移自动化实验套件

该目录把本仓库现有的 GStreamer publisher、MediaMTX、WHEP viewer、WebSocket RemoteControl、ORTM 监控和网络模拟组织成一个可迁移实验工程。换机器时只需要克隆同一仓库、创建本机 `.env`，不需要复制历史容器或手工修改脚本。

## 实验链路

```text
场景文件
  -> GStreamer testsrc + ORTM cairooverlay
  -> x264enc + whipclientsink
  -> MediaMTX WHIP/WHEP
  -> 远端浏览器 RemoteControl viewer
  -> client-log + monitor
  -> snapshots.jsonl + summary.json
```

WebSocket 只负责远端 viewer 的配置、重连和统计重置。编码参数由每轮场景文件传给 publisher 容器，网络条件由 publisher 容器内的 `tc/netem` 控制。

## 新机器要求

- macOS 或 Linux
- Docker Desktop、OrbStack 或 Docker Engine，支持 Docker Compose v2
- `curl`、`jq`、`git`、`openssl`
- 浏览器支持 WebRTC、WHEP 和 `requestVideoFrameCallback`
- 远端观看时，两台设备能访问同一个 HTTPS 页面与 WHEP 地址

默认使用 `testsrc/checkers-8`，因此迁移基准实验不依赖摄像头。摄像头采集属于机器相关能力，应在基准通过后单独配置。

## 从零启动

```bash
git clone https://github.com/copywrite-ai/webrtc-coturn.git
cd webrtc-coturn
git switch codex/whep-evolution

./ortm-lab/bin/ortm-lab init
```

编辑 `ortm-lab/.env`：

```env
DEVICE_ID=new-lab-host
LAB_PUBLIC_ORIGIN=https://new-lab-host.example.ts.net
```

同机浏览器测试可以保留：

```env
LAB_PUBLIC_ORIGIN=http://127.0.0.1:9001
```

第一次启动：

```bash
./ortm-lab/bin/ortm-lab bootstrap
```

`bootstrap` 会执行环境检查、构建首次耗时较长的 GStreamer Rust 基础镜像，并启动：

- Viewer/RemoteControl：`9001`
- MediaMTX：`8889`、`9997`、`9998`
- Monitor：`9010`
- Prometheus：`9090`
- Grafana：`3000`

如不需要 Prometheus/Grafana，在 `.env` 中设置：

```env
LAB_ENABLE_OBSERVABILITY=0
```

## 接入 Viewer

查看当前机器对应的页面地址：

```bash
./ortm-lab/bin/ortm-lab url
```

在观看设备打开输出的 URL。页面必须包含：

```text
whep-quad-direct.html?remoteControl=1
```

确认浏览器已注册：

```bash
./ortm-lab/bin/ortm-lab clients
```

只有一个 viewer 时会被自动选择。存在多个 viewer 时，在 `ortm-lab/.env` 中填写：

```env
VIEWER_CONTROL_PEER_ID=<目标 peer ID>
```

远端 Viewer 场景下，`LAB_PUBLIC_ORIGIN` 必须同时满足：

1. Viewer 能加载页面和 WebSocket。
2. Viewer 能访问 `${LAB_PUBLIC_ORIGIN}/fish_front/whep`。
3. Clock Sync 能访问同一 publisher origin 的 `/api/clock-sync`。

使用 Tailscale Serve 时，可在 publisher 主机执行：

```bash
tailscale serve 9001
```

## 运行单个场景

```bash
./ortm-lab/bin/ortm-lab run \
  ortm-lab/scenarios/01-two-top-baseline.env
```

流程会自动执行：

1. 确认核心服务和基础镜像。
2. 按场景重建 `fish_front` publisher。
3. 应用固定或动态 `tc/netem` 条件。
4. 自动发现 RemoteControl viewer。
5. 下发 WHEP 地址和 ORTM profile，清空本轮 viewer 统计。
6. 等待 publisher、reader、ICE、分辨率和 ORTM 样本全部就绪。
7. 预热并按固定周期采样。
8. 生成结构化结果并执行质量门。
9. 清除 netem，避免污染下一轮。

## 运行矩阵

Timing column 对照：

```bash
./ortm-lab/bin/ortm-lab matrix \
  ortm-lab/matrices/timing-column.list
```

网络条件矩阵：

```bash
./ortm-lab/bin/ortm-lab matrix \
  ortm-lab/matrices/network.list
```

矩阵文件是普通文本，每行指向一个场景文件，支持空行和 `#` 注释。每个场景在独立进程中执行，避免上一轮环境变量泄漏到下一轮。

## 结果目录

默认写入：

```text
ortm-lab/results/<时间>-<场景>/
```

每轮包括：

```text
scenario.env       本轮场景副本
manifest.json      Git/ORTM 版本、tracked 工作区状态、场景 hash、机器、端点和关键参数
publisher.log      Publisher 启动及运行日志
snapshots.jsonl    Monitor 原始时间序列
summary.json       聚合指标、最终计数和质量门结果
```

汇总历史结果：

```bash
./ortm-lab/bin/ortm-lab summary
```

`results/` 默认不进入 Git。需要长期保存时，应将完整结果目录复制到实验归档或对象存储；不要只保留最终截图。

## 场景参数

视频编码：

```env
WIDTH=1280
HEIGHT=720
FPS=60
BITRATE_KBPS=2500
KEY_INT_MAX=120
```

ORTM：

```env
ORTM_PROFILE=720p-two-top
ORTM_FINDER_LAYOUT=two-top
ORTM_CELL_ALPHA=0.70
ORTM_TIMING_COLUMN_ALPHA=0.625
```

网络条件二选一：

```env
NETWORK_PROFILE=5g-mid
NETWORK_SEQUENCE=
```

或：

```env
NETWORK_PROFILE=
NETWORK_SEQUENCE=random:120
RANDOM_STEP_SECONDS=5
```

质量门：

```env
MIN_DECODE_SUCCESS_RATE=99.9
MAX_CRC_FAILURES=0
MAX_STRUCTURE_FAILURES=0
MAX_LOW_CONTRAST_FAILURES=0
```

质量门失败时命令返回非零状态，适合接入 CI 或批量实验编排。设置 `FAIL_ON_QUALITY_GATE=0` 可以只记录、不阻断矩阵。

## 运维命令

```bash
./ortm-lab/bin/ortm-lab doctor
./ortm-lab/bin/ortm-lab status
./ortm-lab/bin/ortm-lab up
./ortm-lab/bin/ortm-lab down
```

停止命令会清除 publisher netem，并停止实验服务。`.env` 包含 RemoteControl token 和可能的私有域名，不应提交。

套件自测试：

```bash
./ortm-lab/test/smoke.sh
```

该测试不启动媒体链路，用合成 monitor 快照验证初始化、场景语法、聚合统计和质量门。完整媒体验证仍需运行至少一个实际场景。

## 可迁移边界

已经版本化：

- Publisher 编码、ORTM 和 timing-column 参数
- RemoteControl 协议
- Viewer profile
- MediaMTX、Monitor、Prometheus、Grafana 配置
- 场景、矩阵、质量门和结果格式

仍需每台机器单独配置：

- Tailscale Serve 或其他 HTTPS 入口
- 摄像头设备和采集 pipeline
- TURN/relay 凭证
- 两端系统时间和网络拓扑
- Docker 运行时对宿主机资源的限制
