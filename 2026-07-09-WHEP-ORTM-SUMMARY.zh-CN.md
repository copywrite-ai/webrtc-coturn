# 2026-07-09 WHEP / GStreamer / ORTM 进展总结

## 今日结论

今天完成了三块核心工作：

1. 打通了基于 GStreamer 的本地/远端 WHIP 推流与 WHEP 拉流链路
2. 实现了 ORTM v0 发送端叠加与浏览器播放端固定 ROI 解码
3. 建立了 direct / relay 两套可重复观测的延迟实验页面与日志链路

当前系统已经具备：

- 本地生成视频并通过 `whipclientsink` 推流
- 单路或四路通过 WHEP 在浏览器拉流播放
- 发送端在编码前叠加 ORTM v0 标记
- 播放端从视频帧中解码 `timestamp_ms`
- 实时计算 `ORTM / 净ORTM / 前段 / 净前段 / 浏览器` 指标
- 将客户端指标持续写入本地 `logs/client-events.log`

## 已落地能力

### 1. GStreamer 推流能力

已经具备四路推流能力：

- `fish_front`
- `fish_back`
- `fish_left`
- `fish_right`

支持：

- 本地 WHIP 推流
- 远端 WHIP 推流
- 统一码率 / 分辨率 / 帧率参数化
- ORTM 叠加参数化

当前基础镜像也已经补充了国内镜像源优化，首次构建后可复用缓存。

### 2. 浏览器播放器能力

当前有两套页面：

- `public/whep-quad-direct.html`
- `public/whep-quad-relay.html`

其中：

- `direct` 页面强制直连，不注入 TURN
- `relay` 页面读取 `/config.js` 中的 TURN 配置，并强制 `iceTransportPolicy = relay`

两页都支持：

- 四路独立 WHEP 地址
- 单路 / 多路独立启动
- 每路独立日志
- 每路独立延迟指标

### 3. ORTM v0 发送端与解码端

发送端：

- 已实现 ORTM v0 叠加器 PoC
- 规则为固定左上角 ROI
- 在编码前写入视频帧
- 时间戳采用中国时区墙钟毫秒值低 32 位

播放端：

- 已实现固定 ROI 解码器
- 支持 finder / timing / payload / CRC16 校验
- 支持轻微缩放与偏移候选集尝试
- 输出 `timestamp_ms` 与 `frame_seq`

### 4. 客户端日志采集

已经打通浏览器页面向本地服务端回传日志：

- 页面通过 `/client-log` 回传
- 服务端落盘到 `logs/client-events.log`

因此即使页面是从 Tailscale 或 `noproxy` 路径访问，只要页面内容来自本机服务，当前机器仍然能看到播放端指标日志。

## 延迟测量口径

当前页面展示的主要指标含义：

- `ORTM`: 发送端打标时间到播放端解码时间的总延迟
- `净ORTM`: `ORTM - 浏览器解码开销`
- `Fallback`: 浏览器 WebRTC 统计路径得到的播放侧参考值
- `前段`: `ORTM - Fallback`
- `净前段`: `前段 - 浏览器解码开销`
- `浏览器`: `drawImage + getImageData + ORTM decode` 平均耗时

## 关键实验结果

### 1. 本地 direct 链路

在 direct 场景下，通过 ORTM 对墙钟测量，浏览器端整体延迟已经可做到很低，好的时候可以到个位数到数十毫秒量级。

这说明：

- 浏览器播放链路本身不是主要瓶颈
- ORTM 固定 ROI 解码方案是可用的

### 2. 30 fps / 60 fps / 不同码率对比

已经完成一轮较完整的参数对比，核心结论如下：

| 配置 | ORTM | 净ORTM | 前段 | 净前段 | 结论 |
| --- | --- | --- | --- | --- | --- |
| 30 fps / 3 Mbps | ~68 ms | ~61 ms | ~61 ms | ~54 ms | 基线 |
| 60 fps / 3 Mbps | ~81 ms | ~77 ms | ~73 ms | ~69 ms | 明显变差 |
| 60 fps / 4 Mbps | ~63 ms | ~59 ms | ~55 ms | ~51 ms | 基本恢复 |
| 60 fps / 5 Mbps | ~43 ms | ~39 ms | ~35 ms | ~31 ms | 当前最优 |

工程判断：

- `3 Mbps` 对 `60 fps` 不够
- `4 Mbps` 可以基本追回
- `5 Mbps` 明显更稳，当前是更合理的高帧率档位

### 3. relay 场景

relay 页面已经能稳定连接并拿到完整指标。

当前观察到：

- 四路 relay 下，延迟整体显著高于 direct
- 主要大头在 `前段`，而不是浏览器解码
- 降到 `480p` 后，浏览器开销会下降，但 relay 前段高延迟并不会自然消失

进一步观察表明：

- 四路并发会放大问题
- 但即便单路 relay，前段仍然处于较高水平

这说明 relay 路径本身就引入了明显延迟底座。

## 当前工程判断

### 1. 浏览器不是主瓶颈

在 direct 与 relay 两组实验中，浏览器侧 `drawImage/getImageData/decode` 开销已经被压到较低水平，通常只是总链路中的小头。

### 2. relay 问题主要在前段

目前主要的时延增量来自：

- 编码后发送节奏
- WebRTC sender pacing / congestion control
- relay 路径中的队列与 jitter buffer
- 服务端转发路径

而不是 ORTM 解码算法本身。

### 3. ORTM 方案已经足够进入下一阶段

当前 ORTM v0 方案已经可以用于：

- direct / relay 对比
- 多路并发对比
- 分辨率 / 帧率 / 码率对比
- 服务端 / 播放端进一步拆分定位

下一阶段重点不再是“能不能测”，而是“沿着测量结果继续压链路时延”。

## 当前仓库中的关键文件

- `public/whep-quad-direct.html`
- `public/whep-quad-relay.html`
- `server.mjs`
- `docker-compose.whip-publisher.yml`
- `docker/whip-publisher/`
- `GSTREAMER_WHEPSRC_PLAYER_SETUP.zh-CN.md`
- `LOCAL_LATENCY_TEST_MATRIX.zh-CN.md`

## 下一步建议

### 页面侧

- 优化四宫格布局，保证首屏可同时看四路
- 给每路增加 ORTM 实时趋势图
- 将日志与配置折叠，减少对视频高度的占用

### 链路侧

- 固定单路 direct / relay 参数做一组更长时间的对照
- 继续拆解 relay 路径前段时延
- 对服务端转发、发送端 pacing、接收端 jitter buffer 逐项排查

### 推流侧

- 以 `60 fps / 5 Mbps` 作为当前高帧率基准档
- 在不超过带宽预算的前提下继续寻找更稳的分辨率与码率组合

## 当前状态

截至 2026-07-09 晚间，系统已经从“基础链路搭建”进入“可量化优化”阶段。

也就是说，当前最重要的不是再补一套新的观测工具，而是利用已经具备的 ORTM + 日志能力，持续逼近 relay 与多路场景下的真实瓶颈。
