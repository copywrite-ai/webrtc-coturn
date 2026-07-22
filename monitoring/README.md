# Tunnel Monitoring

这套监控包含：

- `monitor`：汇总浏览器 ORTM、Publisher 帧级日志和 MediaMTX 状态，并暴露 Prometheus 指标。
- `Prometheus`：默认每 2 秒抓取一次 `monitor:9010/metrics`。
- `Grafana`：自动加载 Prometheus 数据源和 `Tunnel WebRTC Latency` Dashboard。

## 从零启动

前提：MediaMTX 已在宿主机运行，并开放 API `9997` 和 metrics `9998`。

```bash
cp monitoring/.env.example monitoring/.env
```

至少修改以下凭据：

```env
MEDIAMTX_PASSWORD=your-password
GRAFANA_ADMIN_PASSWORD=your-password
```

启动：

```bash
docker compose --env-file monitoring/.env -f monitoring/docker-compose.yml up -d --build
```

验证：

```bash
monitoring/verify.sh
```

访问地址：

- Monitor snapshot: `http://127.0.0.1:9010/api/snapshot`
- Prometheus metrics: `http://127.0.0.1:9010/metrics`
- Prometheus: `http://127.0.0.1:9090`
- Grafana: `http://127.0.0.1:3000`

如果端口已被占用，可在 `monitoring/.env` 修改三个 `*_HOST_PORT`。验证时同步传入地址：

```bash
MONITOR_URL=http://127.0.0.1:19010 \
PROMETHEUS_URL=http://127.0.0.1:19090 \
GRAFANA_URL=http://127.0.0.1:13000 \
monitoring/verify.sh
```

## 接入已有 Prometheus

只启动 `monitor`：

```bash
docker compose --env-file monitoring/.env -f monitoring/docker-compose.yml up -d --build monitor
```

然后在已有 Prometheus 增加：

```yaml
scrape_configs:
  - job_name: tunnel-exporter
    scrape_interval: 2s
    metrics_path: /metrics
    static_configs:
      - targets: ["video-host.example:9010"]
```

生产环境应限制 `9010` 的网络访问范围。Prometheus 需要能够访问视频主机，浏览器不需要直接访问该端口。

## 接入已有 Grafana

导入：

```text
grafana/dashboards/tunnel-latency.json
```

Dashboard 默认引用 UID 为 `Prometheus` 的数据源。已有 Grafana 可以：

1. 将目标 Prometheus 数据源 UID 设置为 `Prometheus`；或
2. 导入后使用 Grafana 的数据源替换功能，将 Dashboard 数据源映射到现有 Prometheus。

## 指标来源与降级

- 浏览器指标来自播放器写入的 `client-events.log`。
- Publisher 帧级指标来自 Docker 日志，因此 monitor 默认只读挂载 Docker socket。
- MediaMTX 状态来自 API 和 metrics 端点。
- 缺少 Docker socket 时，monitor 仍能输出浏览器和 MediaMTX 指标，但没有 Publisher 帧级指标。
- Publisher 位于另一台主机时，应在 Publisher 主机部署 monitor，或后续把 Publisher 日志改为直接暴露 metrics。

## 安全要求

- 不要提交 `monitoring/.env`。
- 不要在仓库中保留真实 MediaMTX、Grafana 或 TURN 凭据。
- Docker socket 具有很高权限；即使 bind mount 标记为 `ro`，也不代表 Docker API 变成只读，只能交给受信任的 monitor 容器。
- 对外部署时，应通过防火墙或反向代理限制 Monitor、Prometheus 和 Grafana 的访问。
