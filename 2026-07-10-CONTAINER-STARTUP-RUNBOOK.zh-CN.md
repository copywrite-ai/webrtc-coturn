# 2026-07-10 容器启动 Runbook

这份文档只记录一件事：如何把当前这套 `mediamtx + webrtc-relay-demo + 4 路 GStreamer publisher` 稳定启动起来，并避免今天上午已经踩过的坑。

## 1. 结论先行

正常启动时，必须区分两类 compose 文件：

- 基础服务：`docker-compose.yml`
- 四路 publisher：`docker-compose.whip-publisher.yml`

如果只执行默认的：

```bash
docker compose up -d
```

只会拉起：

- `mediamtx`
- `webrtc-relay-demo`

**不会**自动拉起四路 `fish_*_whip` publisher。

这正是今天上午反复出现“服务都在，但 `stream is offline`”的主要原因之一。

## 2. 标准启动顺序

推荐固定按下面顺序执行。

### 2.1 启动基础服务

```bash
docker compose up -d
```

目标：

- `mediamtx`
- `webrtc-relay-demo`

验证：

```bash
docker compose ps
```

应至少看到：

- `mediamtx`
- `webrtc-relay-demo`

## 2.2 启动四路本地 publisher

当前本地可工作的启动命令是：

```bash
DEVICE_ID=peng-mbp14 WHIP_BASE_URL=http://mediamtx:8889 docker compose -f docker-compose.whip-publisher.yml up -d fish_front_whip fish_back_whip fish_left_whip fish_right_whip
```

这里最关键的是：

- `DEVICE_ID=peng-mbp14`
- `WHIP_BASE_URL=http://mediamtx:8889`

这表示四路 publisher 走**本地** `mediamtx` 的 WHIP。

## 2.3 验证四路 publisher 已启动

```bash
docker ps -a --format '{{.Names}}\t{{.Status}}' | rg 'tunnel-fish_(front|back|left|right)_whip'
```

正常时应该看到四路都是 `Up`。

如果看到：

- `Restarting`

说明 publisher 没真正推上去，需要立即查日志。

## 2.4 验证 mediamtx 已收到流

```bash
docker logs --since 2m mediamtx 2>&1 | tail -n 120
```

正常时会看到类似：

- `path fish_front stream is available`
- `path fish_back stream is available`
- `path fish_left stream is available`
- `path fish_right stream is available`

## 2.5 页面验证

本地页面入口：

- direct: `http://127.0.0.1:9001/whep-quad-direct.html`
- relay: `http://127.0.0.1:9001/whep-quad-relay.html`

`Front` 默认地址应为：

```text
http://127.0.0.1:9001/fish_front/whep
```

## 3. 最小可用命令集

### 3.1 启动基础服务

```bash
docker compose up -d
```

### 3.2 停止基础服务

```bash
docker compose down
```

### 3.3 启动四路本地 publisher

```bash
DEVICE_ID=peng-mbp14 WHIP_BASE_URL=http://mediamtx:8889 docker compose -f docker-compose.whip-publisher.yml up -d fish_front_whip fish_back_whip fish_left_whip fish_right_whip
```

### 3.4 停止四路本地 publisher

```bash
docker compose -f docker-compose.whip-publisher.yml stop fish_front_whip fish_back_whip fish_left_whip fish_right_whip
```

### 3.5 重建四路本地 publisher

```bash
DEVICE_ID=peng-mbp14 WHIP_BASE_URL=http://mediamtx:8889 docker compose -f docker-compose.whip-publisher.yml up -d --force-recreate fish_front_whip fish_back_whip fish_left_whip fish_right_whip
```

### 3.6 重启 mediamtx

```bash
docker compose restart mediamtx
```

## 4. 今天确认过的几个关键事实

### 4.1 `9001/fish_front/whep` 是正确的本地入口

这条地址不是“静态页面”，而是 `webrtc-relay-demo` 代理到 `mediamtx` 的 WHEP 路径：

```text
http://127.0.0.1:9001/fish_front/whep
```

### 4.2 `8889` 不是给你直接开页面用的入口

`mediamtx` 本身在当前配置下负责 WHEP / WebRTC 服务，但实际日常测试是通过 `9001` 上的页面和代理路径访问，不是直接把 `8889` 当成前端页面入口。

### 4.3 publisher 如果还指向远端 `noproxy`，很容易 404

今天已经确认过，下面这种目标会导致 publisher 重启循环：

```text
https://media-proxy.nexusdot.cn/noproxy/fish_front/whip
```

典型日志：

- `Unexpected response: 404 - Not found`

所以本地验证阶段，publisher 应该明确指向：

```text
http://mediamtx:8889
```

## 5. 常见故障与处理

### 5.1 页面显示 `stream is offline`

优先按这个顺序查：

1. publisher 是否真的在跑
2. `mediamtx` path 是否 online
3. viewer 会话是否只是没连上

命令：

```bash
docker ps -a --format '{{.Names}}\t{{.Status}}' | rg 'tunnel-fish_(front|back|left|right)_whip'
docker logs --since 2m mediamtx 2>&1 | tail -n 120
```

### 5.2 publisher 一直 `Restarting`

最常见原因：

- `WHIP_BASE_URL` 指错
- 远端 WHIP endpoint 返回 `404`

命令：

```bash
docker logs --since 5m tunnel-fish_front_whip-1 2>&1 | tail -n 120
```

如果看到：

- `Unexpected response: 404 - Not found`

就不要继续猜，直接把 `WHIP_BASE_URL` 切回本地 `http://mediamtx:8889`。

### 5.3 relay 延迟上升到几百毫秒甚至 2 秒

今天实际看到的特征是：

- sender 仍然只有几毫秒
- browser 只有几毫秒
- `upstream` 持续上涨

这类问题优先看：

```bash
docker logs --since 10m mediamtx 2>&1 | tail -n 160
```

重点关键词：

- `Fail to refresh permissions`
- `CreatePermission`
- `broken pipe`
- `deadline exceeded`

### 5.4 ORTM 一直是 `--`

先不要直接判断链路坏了。

优先确认：

1. 当前画面里是否真的有 ORTM
2. 当前分辨率是否变化
3. 当前拉到的是不是另一条没有 ORTM 的流

## 6. 推荐的日常启动方式

如果只是要快速起本地链路，推荐固定执行这两条：

```bash
docker compose up -d
DEVICE_ID=peng-mbp14 WHIP_BASE_URL=http://mediamtx:8889 docker compose -f docker-compose.whip-publisher.yml up -d fish_front_whip fish_back_whip fish_left_whip fish_right_whip
```

然后验证：

```bash
docker compose ps
docker logs --since 2m mediamtx 2>&1 | tail -n 120
```

## 7. 不推荐的做法

### 7.1 只执行默认 `docker compose up -d` 后就开始测流

因为这只会起基础服务，不会起四路 publisher。

### 7.2 不显式指定 `WHIP_BASE_URL`

因为 `docker-compose.whip-publisher.yml` 当前默认值仍然是远端：

```text
https://media-proxy.nexusdot.cn/noproxy
```

本地验证阶段，这个默认值不安全。

### 7.3 同时混测本地流和远端流

今天已经证明，这样很容易把问题混在一起，尤其是：

- local direct
- local relay
- remote noproxy

应先固定单路、单路径、单目标。

## 8. 一句话版

今天这套环境要正常起，最稳的做法是：

1. `docker compose up -d`
2. `DEVICE_ID=peng-mbp14 WHIP_BASE_URL=http://mediamtx:8889 docker compose -f docker-compose.whip-publisher.yml up -d fish_front_whip fish_back_whip fish_left_whip fish_right_whip`
3. 看 `mediamtx` 日志确认 `fish_front` 等 path online
4. 再开 `http://127.0.0.1:9001/whep-quad-direct.html` 或 `relay.html`

