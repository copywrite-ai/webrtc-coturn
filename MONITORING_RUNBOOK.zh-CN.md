# 本地监控运行手册

这套监控的生产化入口是 Prometheus + Grafana。`monitor` 服务只承担 exporter/adaptor 角色，用于把本工程特有的日志指标转成 Prometheus metrics：

- MediaMTX API: path ready、reader、track 状态。
- MediaMTX metrics: bytes、readers、RTP lost、discarded、inbound error。
- Viewer 日志: ORTM、Fallback、Upstream、Browser cost、ICE、播放状态。
- Publisher 日志: ORTM render、overlay_to_send、sender_pipeline。

## 启动标准监控栈

```bash
docker compose up -d --build monitor prometheus grafana
```

访问：

```text
Grafana:    http://127.0.0.1:3000/
Prometheus: http://127.0.0.1:9090/
Exporter:   http://127.0.0.1:9010/
```

Grafana 默认账号密码：

```text
admin / admin
```

可以通过环境变量覆盖：

```bash
GRAFANA_ADMIN_USER=admin GRAFANA_ADMIN_PASSWORD='change-me' docker compose up -d grafana
```

机器可读接口：

```text
http://127.0.0.1:9010/api/snapshot
http://127.0.0.1:9010/metrics
http://127.0.0.1:9010/healthz
```

Prometheus 当前抓取两个目标：

- `mediamtx:9998/metrics`
- `monitor:9010/metrics`

Grafana 会自动加载：

- datasource: `Prometheus`
- dashboard: `Tunnel WebRTC Latency`

## 指标含义

- `ORTM`: viewer 从视频帧 marker 解出来的 G2G latency。
- `Fallback`: 浏览器 WebRTC stats 估计的接收侧播放/抖动缓冲开销。
- `Upstream`: `ORTM - Fallback`，用于粗略观察发送端、网络、TURN/relay、服务端侧大头。
- `Browser`: viewer 页面 draw/read/decode 的观测成本。
- `Overlay`: publisher 里 overlay 后到 send probe 的近似耗时。
- `Render`: ORTM cairooverlay draw 回调耗时。

## Docker 日志采集

publisher 指标来自容器日志。`monitor` 默认挂载 Docker socket，只读访问：

```text
/var/run/docker.sock:/var/run/docker.sock:ro
```

如果不想挂载 Docker socket，dashboard 仍然可以看到 MediaMTX 和 viewer 指标，只是 Publisher 区域会显示 `no socket`。

## Prometheus 指标

Exporter 额外提供这些本工程指标：

- `tunnel_stream_ready`
- `tunnel_stream_readers`
- `tunnel_stream_bytes_received`
- `tunnel_stream_bytes_sent`
- `tunnel_stream_rtp_packets_lost`
- `tunnel_viewer_ortm_ms`
- `tunnel_viewer_upstream_ms`
- `tunnel_viewer_fallback_ms`
- `tunnel_viewer_browser_cost_ms`
- `tunnel_publisher_overlay_to_send_ms`
- `tunnel_publisher_ortm_render_avg_ms`

## 常用排查

1. `ORTM` 高，但 `Fallback` 低：大头通常在发送端、TURN/relay、网络、MediaMTX 入口/出口之前。
2. `Fallback` 高：重点看浏览器 jitter buffer、解码和下行网络。
3. MediaMTX `lost/discarded/inbound error` 为 0，但 `ORTM` 高：MediaMTX 没有明显堆积证据，优先查 TURN/relay 和网络路径。
4. `Browser` 高：viewer 页面观测成本偏高，降低 ORTM 解码频率或改 ROI-only。

## Direct / Relay MediaMTX 配置切换

默认配置使用：

```text
mediamtx/mediamtx.yml
```

这份配置包含 `webrtcICEServers2`，适合 relay/WHEP 测试。

如果要做纯 direct 本地测试，不希望 MediaMTX 服务端连接 coturn，使用：

```bash
MEDIAMTX_CONFIG=./mediamtx/mediamtx.direct.yml docker compose up -d mediamtx
```

然后访问：

```text
http://127.0.0.1:9001/whep-quad-direct.html
```

这份 direct 配置里：

```yaml
webrtcICEServers2: []
```

因此 direct 播放时浏览器侧和 MediaMTX 服务端都不会主动使用 coturn。
## DERP 延迟 Exporter

用于轻量记录本机到 Tailscale DERP region 的 netcheck 延迟。Exporter 不在 Prometheus scrape 时实时探测，而是按固定间隔缓存一次 `tailscale netcheck --format json` 结果，降低观测扰动。

### 指标

- `derp_region_latency_ms{region,ip_version}`：本机到 DERP region 的 RTT，单位 ms。
- `derp_preferred_region_id`：当前首选 DERP region。
- `derp_region_allowlist_info{region}`：当前 exporter 配置观测的 DERP region。
- `derp_netcheck_up`：最近一次 netcheck 是否成功。
- `derp_netcheck_duration_seconds`：最近一次 netcheck 耗时。
- `derp_netcheck_udp` / `derp_netcheck_ipv4` / `derp_netcheck_ipv6`：本机网络能力探测结果。

### 启动

默认只观测自定义 Aliyun DERP region `900`。Docker 版 exporter 会在容器内启动独立的 `tailscaled`，使用 userspace networking 加入 tailnet 后再执行 netcheck。

```bash
TAILSCALE_AUTHKEY=tskey-auth-xxx \
docker compose --profile derp up -d --build derp-latency-exporter
```

默认 hostname 是 `derp-latency-exporter`，可以覆盖：

```bash
TAILSCALE_AUTHKEY=tskey-auth-xxx \
TAILSCALE_HOSTNAME=derp-latency-exporter-peng-mbp14 \
docker compose --profile derp up -d --build derp-latency-exporter
```

如果自定义 DERP region ID 不是 `900`：

```bash
DERP_REGION_ALLOWLIST=900,901 \
docker compose --profile derp up -d --build derp-latency-exporter
```

本地验证：

```bash
curl -fsS http://127.0.0.1:9020/metrics | rg 'derp_region_latency_ms|derp_region_allowlist_info|derp_preferred_region_id|derp_netcheck_up'
```

### Prometheus

`prometheus/prometheus.yml` 已增加：

```yaml
- job_name: derp-latency-exporter
  metrics_path: /metrics
  scheme: http
  static_configs:
    - targets:
        - derp-latency-exporter:9020
```

启动 exporter 后，重载或重启 Prometheus：

```bash
docker restart tunnel-prometheus
```

### 注意

容器会作为一个独立 Tailscale node 出现在 tailnet 中。首次启动需要 `TAILSCALE_AUTHKEY`；后续状态保存在 Docker volume `derp-tailscale-state`，一般不需要重复传 auth key。
