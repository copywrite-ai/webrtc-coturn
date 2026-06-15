# 使用 Docker 部署 coturn

本文档说明如何使用 Docker 部署一个可供本仓库使用的 `coturn` 服务。

目标是提供一个适合 WebRTC 验证和接入的 TURN 中继服务，同时不在仓库里包含任何真实密钥或真实域名。

## 文档范围

本文档覆盖：

- 基于 Docker 的最小 `coturn` 部署
- 必需端口
- 安全组 / 防火墙要求
- 推荐鉴权方式
- 如何接入本仓库里的 demo

本文档不包含：

- 真实域名
- 真实 TURN shared secret
- 真实 TLS 证书

## 推荐鉴权方式

建议使用 `coturn` 标准的 shared-secret 模式：

```conf
use-auth-secret
static-auth-secret=YOUR_SHARED_SECRET
realm=turn.example.com
```

不要在正式环境里给前端长期写死 TURN 用户名和密码。

推荐模式是：

- shared secret 只保存在服务端或 BFF
- 前端只拿临时 TURN 凭证
- 每次会话按需签发，过期自动失效

## 最小端口方案

如果先做非 TLS 验证，可开放：

- `3478/udp`
- `3478/tcp`
- 一段 relay UDP 端口范围，例如 `49152-49252/udp`

如果还要启用 TLS TURN，再开放：

- `5349/tcp`

如果你的目标网络非常受限，后续可能还需要 `443/tcp` 做 TURN over TLS，但这通常会和其他服务冲突，应提前规划。

## 推荐目录结构

```text
coturn/
  docker-compose.yml
  turnserver.conf
  certs/
```

## Docker Compose 示例

在一个独立目录下创建 `docker-compose.yml`：

```yaml
services:
  coturn:
    image: coturn/coturn:4.6.3
    container_name: coturn
    restart: unless-stopped
    network_mode: host
    volumes:
      - ./turnserver.conf:/etc/coturn/turnserver.conf:ro
      - ./certs:/etc/coturn/certs:ro
      - ./logs:/var/log/coturn
```

### 为什么建议 `network_mode: host`

`coturn` 在 relay 时通常需要使用一整段 UDP 端口范围。使用 host 网络最省事，因为它避免了 Docker 里逐段映射 TURN relay 端口。

如果你不想用 host 网络，也可以，但需要显式暴露：

- 监听端口
- TLS 端口
- 整个 relay UDP 范围

这通常更麻烦。

## `turnserver.conf` 示例

下面是一份适合 TURN relay 验证、暂不启用 TLS 的最小配置：

```conf
listening-port=3478

fingerprint
use-auth-secret
static-auth-secret=REPLACE_WITH_LONG_RANDOM_SECRET
realm=turn.example.com
server-name=turn.example.com

listening-ip=0.0.0.0

min-port=49152
max-port=49252

no-tls
no-dtls

no-multicast-peers
no-cli
stale-nonce

simple-log
log-file=/var/log/coturn/turnserver.log
```

### 补充说明

- 如果这台机器有直接绑定的公网 IP，一般不需要额外配置 `external-ip`
- 如果服务器位于 NAT 后面，可能需要：

```conf
external-ip=PUBLIC_IP
```

或者 NAT 映射形式，具体取决于你的部署拓扑

## TLS 配置扩展

如果后续启用 TLS，可增加：

```conf
tls-listening-port=5349
cert=/etc/coturn/certs/fullchain.pem
pkey=/etc/coturn/certs/privkey.pem
```

并删除：

```conf
no-tls
```

如果还想启用 DTLS，则删除：

```conf
no-dtls
```

## 启动服务

在 coturn 部署目录下执行：

```bash
docker compose up -d
```

查看状态：

```bash
docker compose ps
docker compose logs --tail=100
```

## 防火墙 / 安全组要求

最少需要放行：

- `3478/udp`
- `3478/tcp`
- `49152-49252/udp`

如果启用 TLS，还要放行：

- `5349/tcp`

如果你使用云厂商安全组，务必把 relay UDP 端口范围也开放。只开 `3478` 不够。

## 如何接入本项目

在本仓库中，可以通过环境变量配置 TURN：

```env
DEFAULT_TURN_URLS=turn:turn.example.com:3478?transport=udp,turn:turn.example.com:3478?transport=tcp
DEFAULT_TURN_USERNAME=
DEFAULT_TURN_CREDENTIAL=
```

如果你通过 BFF 动态签发临时 TURN 凭证，那么 `.env` 里可以把用户名和密码留空，由前端运行时获取。

如果只是人工验证，也可以临时填一组短期凭证。

## 验证模式

### 1. 强制 relay 验证

设置：

- `DEFAULT_FORCE_RELAY=1`
- `DEFAULT_TURN_ONLY=1`

如果页面连接成功，并显示：

```text
relay / udp / relay->relay
```

就说明 TURN relay 路径已经工作。

### 2. 正常 fallback 验证

设置：

- `DEFAULT_FORCE_RELAY=0`
- `DEFAULT_TURN_ONLY=0`

这验证的是正式模式下的真实行为：

- 能直连就直连
- 直连失败再回退到 TURN

## TURN 凭证生成

常见的临时凭证格式：

- `username = expiry:user-id`
- `credential = base64(HMAC-SHA1(shared-secret, username))`

建议由应用后端生成这些凭证，再返回给已认证客户端。

## 运维建议

- 在小带宽 VPS 上先从低码率开始
- relay 端口范围要明确记录
- 监控 relay 带宽消耗
- 在应用层记录直连 / relay 结果

对于小规模验证，`49152-49252` 这种 relay 端口范围通常已经够用；并发提高后再扩展。

## 常见错误

### 只开放 3478

TURN 媒体中继通常走 relay UDP 端口范围，不是只走监听端口。

### 把真实 TURN secret 放进前端

真实 TURN shared secret 绝不能出现在浏览器代码里。

### 把 TURN 当成信令服务

`coturn` 不能替代 signaling。你仍然需要某种方式交换：

- offer
- answer
- ICE candidates

### 在 TURN 前面放普通 HTTP 反代

TURN 不是普通 HTTP 流量。大多数 HTTP 反向代理不适合作为 TURN 的前置代理。

## 推荐后续步骤

当 Docker 部署的 TURN 验证通过之后，建议继续做：

1. 强制 relay 验证
2. 正常 fallback 验证
3. 把 TURN 凭证签发逻辑接进 BFF
4. 如果网络需要，再补 TLS
