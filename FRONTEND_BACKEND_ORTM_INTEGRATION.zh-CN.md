# ORTM 视频链路前后端接入指南

本文说明如何把当前已经验证的低延迟视频与 ORTM 监控能力接入正式 Web 产品。

适用链路：

```text
摄像头或测试源
→ GStreamer Publisher
→ WHIP
→ MediaMTX
→ WHEP
→ Web 前端播放器
→ ORTM 解码与浏览器 WebRTC 指标
→ BFF /client-log
→ monitor /metrics
→ Prometheus / Grafana
```

当前优先接入 `fish_front`，其他流可以沿用相同接口扩展。

## 1. 职责边界

### 发送端

GStreamer Publisher 负责：

- 读取摄像头或测试源。
- 在编码前绘制 ORTM。
- 使用 H.264 编码。
- 通过 WHIP 发布到 MediaMTX。
- 输出实际 FPS、编码码率和帧级 pipeline 指标。

发送端不属于浏览器前端，也不应由观看页面访问摄像头。

### Web 前端

Web 前端负责：

- 通过 WHEP 建立只接收视频的 `RTCPeerConnection`。
- 播放远端视频，不采集观看端摄像头。
- 使用 `requestVideoFrameCallback` 在视频帧到达显示路径时解码 ORTM。
- 计算近似 G2G、帧停滞、浏览器开销和 WebRTC 接收指标。
- 周期性把指标发送给同源 BFF。

### BFF

BFF 负责：

- 对用户和设备进行认证、鉴权。
- 将同源 WHEP URL 反向代理到 MediaMTX。
- 正确转发 WHEP 的 SDP、状态码和会话 `Location`。
- 接收播放器指标并写入日志或指标系统。
- 向前端提供非敏感运行配置。

BFF 不转发 WebRTC 媒体包。媒体在浏览器和 MediaMTX WebRTC transport 之间传输。

## 2. 推荐公开地址

正式页面和 WHEP 最好使用同一个 HTTPS Origin：

```text
https://video.example.com/whep-quad-direct.html
https://video.example.com/fish_front/whep
```

WHEP 地址末尾不要增加 `/`：

```text
正确：https://video.example.com/fish_front/whep
错误：https://video.example.com/fish_front/whep/
```

同源代理可以避免：

- HTTPS 页面请求 HTTP WHEP 产生 mixed content。
- 浏览器 CORS 配置分散。
- 前端直接感知 MediaMTX 内部地址。
- WHEP session URL 暴露内部拓扑。

## 3. 前端最小 WHEP 接入

当前页面实现位于：

```text
public/whep-quad-direct.html
```

最小播放流程如下：

```js
function waitForIceGatheringComplete(pc, timeoutMs = 3000) {
  if (pc.iceGatheringState === 'complete') return Promise.resolve();
  return new Promise((resolve) => {
    const timeout = setTimeout(done, timeoutMs);
    function done() {
      clearTimeout(timeout);
      pc.removeEventListener('icegatheringstatechange', onChange);
      resolve();
    }
    function onChange() {
      if (pc.iceGatheringState === 'complete') done();
    }
    pc.addEventListener('icegatheringstatechange', onChange);
  });
}

export async function startWhep(video, whepUrl) {
  const pc = new RTCPeerConnection({ iceServers: [] });
  pc.addTransceiver('video', { direction: 'recvonly' });

  pc.ontrack = (event) => {
    video.srcObject = event.streams[0];
    video.play().catch(() => {});
  };

  const offer = await pc.createOffer();
  await pc.setLocalDescription(offer);
  await waitForIceGatheringComplete(pc);

  const response = await fetch(whepUrl, {
    method: 'POST',
    headers: { 'Content-Type': 'application/sdp' },
    body: pc.localDescription.sdp,
  });
  if (!response.ok) {
    throw new Error(`WHEP failed: ${response.status} ${await response.text()}`);
  }

  const sessionLocation = response.headers.get('Location');
  const answer = await response.text();
  await pc.setRemoteDescription({ type: 'answer', sdp: answer });

  return {
    pc,
    sessionUrl: sessionLocation
      ? new URL(sessionLocation, whepUrl).toString()
      : null,
  };
}
```

停止播放时应同时关闭 PeerConnection 和服务端 WHEP session：

```js
export async function stopWhep({ pc, sessionUrl }) {
  if (sessionUrl) {
    await fetch(sessionUrl, { method: 'DELETE' }).catch(() => {});
  }
  pc?.close();
}
```

生产组件至少应展示：

- 播放状态和 ICE 状态。
- 当前分辨率和接收 FPS。
- 近似 G2G 与 ORTM 解码状态。
- freeze、jitter、丢包和接收码率。
- 明确的重连和停止操作。

不要使用自动累积重试。失败后应先清理旧 session，再进行有上限且带退避的重连。

### Direct 与 relay

当前应先以 direct 作为产品基线：

```js
new RTCPeerConnection({ iceServers: [] });
```

需要强制验证 TURN relay 时，由 BFF 签发短期 TURN 凭据，前端改为：

```js
new RTCPeerConnection({
  iceServers: [
    {
      urls: [
        'turn:turn.example.com:3478?transport=udp',
        'turn:turn.example.com:3478?transport=tcp',
      ],
      username: temporaryUsername,
      credential: temporaryCredential,
    },
  ],
  iceTransportPolicy: 'relay',
});
```

direct 和 relay 可以使用同一个 `fish_front` WHEP 业务地址。不要把 coturn shared secret 或长期 TURN 密码放进前端配置。

## 4. ORTM 前端接入

浏览器参考实现位于：

```text
public/vendor/ortm/ortm.js
public/vendor/ortm/raster.js
```

推荐流程：

1. 使用 `requestVideoFrameCallback` 对齐实际视频帧回调。
2. 每 200 ms 左右读取一次固定 ROI，不需要每帧执行 `drawImage/getImageData`。
3. 调用 `decodeRgbaImage()`。
4. 只有 CRC、finder、timing 和时间范围均通过时，才写入正常 G2G 曲线。
5. `latency-out-of-range` 单独计数，并记录 `raw_age_ms`，不要作为正常延迟样本。

近似 G2G 定义：

```text
浏览器当前墙钟 - 发送端写入视频帧的墙钟 timestamp_ms
```

发送端和观看端必须由可靠的时间同步机制校时。时间不同步时，ORTM 码制仍可能正确解码，但 G2G 数值没有意义。

## 5. 前端指标上报

当前播放器把结构化的 key/value 指标写到：

```http
POST /client-log
Content-Type: application/json
```

请求示例：

```json
{
  "peerId": "viewer-session-id",
  "page": "https://video.example.com/player",
  "group": "metrics",
  "level": "info",
  "message": "slot=Front ice=connected ortm=82ms rtcFps=60 status=playing"
}
```

正式 BFF 应补充：

- 用户与设备身份。
- 请求体大小限制。
- 上报频率限制。
- 字段白名单和数值范围校验。
- 日志保留和敏感信息清理策略。

不要信任浏览器上报值来做计费、权限或安全决策。

## 6. BFF WHEP 代理要求

当前 Node 参考实现位于 `server.mjs`。对以下路径做代理：

```text
/fish_front/whep
/fish_front/whep/{session-id}
```

代理必须支持并原样处理：

| 项目 | 要求 |
| --- | --- |
| 方法 | `POST`、`DELETE`；启用 trickle ICE 时还要支持 `PATCH` |
| 请求体 | `application/sdp` 和相应的 ICE fragment |
| 响应状态 | 保留 MediaMTX 返回的 `201`、`204`、`4xx`、`5xx` |
| `Location` | 重写为浏览器可访问的同源 session URL |
| 流式响应 | 不要把 SDP 当 JSON 解析 |
| 超时 | 信令请求设置合理超时，但不要影响建立后的 UDP 媒体 |

推荐映射：

```text
公开：https://video.example.com/fish_front/whep
内部：http://mediamtx:8889/fish_front/whep
```

生产环境还应在代理层校验用户是否有权观看 `fish_front`，不要仅依靠路径不可猜测。

## 7. 配置接口

当前服务通过以下接口向页面提供配置：

```http
GET /config.js
```

返回 `window.APP_CONFIG`。正式产品更推荐使用认证后的 JSON 接口，例如：

```http
GET /api/video/session/fish_front
```

```json
{
  "stream": "fish_front",
  "whepUrl": "/fish_front/whep",
  "mode": "direct",
  "ortm": {
    "enabled": true,
    "version": 0
  }
}
```

TURN shared secret、MediaMTX 管理密码等敏感信息绝不能返回浏览器。

## 8. 指标系统接入

monitor 暴露：

```text
GET http://monitor:9010/healthz
GET http://monitor:9010/api/snapshot
GET http://monitor:9010/metrics
```

已有 Prometheus 只需要抓取 `/metrics`。已有 Grafana 导入：

```text
grafana/dashboards/tunnel-latency.json
```

完整部署和外部监控平台接入方式见：

```text
monitoring/README.md
```

## 9. 推荐生产拓扑

```text
Internet / Tailscale
        │
        ▼
HTTPS Ingress
        │
        ├── Web assets
        ├── /fish_front/whep ──→ MediaMTX :8889
        ├── /client-log ───────→ BFF metrics ingestion
        └── /api/video/* ──────→ BFF auth/config

GStreamer Publisher ──WHIP──→ MediaMTX
monitor ──scrape/log read──→ MediaMTX + Publisher + client logs
Prometheus ──scrape──→ monitor
Grafana ──query──→ Prometheus
```

## 10. 接入验收

前端验收：

- 页面不访问观看端摄像头。
- WHEP 失败能显示 HTTP 状态与有限长度的错误正文。
- 停止和重连会删除旧 session。
- ORTM 解码失败不会污染正常延迟统计。
- 页面后台、网络切换和视频冻结都有明确指标。

后端验收：

- WHEP `Location` 重写正确。
- HTTPS 页面不存在 mixed content。
- 未授权用户不能读取流或提交无限量日志。
- `/metrics` 不直接暴露到公网。
- Prometheus 能看到 Publisher、Viewer、MediaMTX 和 ORTM 指标。

链路验收：

- 先通过 `fish_front + direct` 完成稳定性基线。
- 再验证 Tailscale/DERP 和 TURN relay。
- 使用本机回环、5G 发送端和异地观看端分别记录 median、p95、p99 与 freeze。
