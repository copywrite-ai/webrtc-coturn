# coturn Docker 部署执行手册

本文档面向 SRE / 运维执行人员，目标是提供一份可操作的 `coturn` Docker 部署手册。

适用场景：

- 需要为 WebRTC 提供 TURN relay 能力
- 计划使用 Docker 部署 `coturn`
- 希望把媒体中继与业务前端/BFF 分离

本文档尽量采用“执行手册”写法，而不是概念说明写法。

## 文档定位与派生关系

本文件是 `coturn` 的基础部署手册，重点覆盖：

- `coturn` Docker 部署
- 监听端口与 relay 端口规划
- 鉴权模式
- 基础验证
- 基础排障

如果后续要在它的基础上扩展更完整的架构场景，例如：

- `nginx + coturn + WHEP`
- `mediamtx` 端侧回源
- `Tailscale` 回源
- 动态设备路径，例如 `/mtx/{device}/{stream}/whep`

建议新增“扩展 runbook”，而不是直接把所有场景细节都堆进本文件。

例如当前仓库中的：

- [COTURN_NGINX_WHEP_RUNBOOK.zh-CN.md](/Users/peng/Documents/tunnel/COTURN_NGINX_WHEP_RUNBOOK.zh-CN.md)

就属于建立在本文件之上的架构扩展文档。

后续迭代建议遵循：

1. 基础能力改动，优先更新本文件
2. 场景化架构改动，更新扩展 runbook
3. 新增内容尽量采用“增量章节”方式，而不是复制整份文档重写

## 1. 目标

在一台具备公网访问能力的 Linux 主机上，使用 Docker 部署一个最小可用的 `coturn` 服务，供 WebRTC 客户端在直连失败时进行 TURN relay。

默认部署目标：

- 非 TLS TURN
- 临时凭证模式（`use-auth-secret`）
- 小规模验证环境

默认开放端口：

- `3478/tcp`
- `3478/udp`
- `49152-49252/udp`

## 2. 前提条件

执行前确认：

1. 服务器可以访问公网
2. 已安装 Docker 和 Docker Compose
3. 服务器有公网 IP，或者已经明确 NAT 映射关系
4. 安全组 / 防火墙可按要求开放端口
5. 已确定 TURN 域名，例如：
   - `turn.example.com`
6. 已准备随机生成的 TURN shared secret

如果后续还要启用 TLS，请另外准备：

- 对应域名证书
- 证书文件路径

## 3. 输入参数

执行前请先明确以下参数。

| 参数 | 示例 | 说明 |
|---|---|---|
| TURN 域名 | `turn.example.com` | `realm` 和客户端 TURN URL 使用 |
| TURN shared secret | 长随机串 | 只保存在服务端/BFF |
| 监听端口 | `3478` | TURN 非 TLS 监听端口 |
| TLS 端口 | `5349` | 如果启用 TLS |
| relay 端口范围 | `49152-49252` | 小规模验证可先用 100 个 UDP 端口 |
| 日志目录 | `/opt/coturn/logs` | 容器日志挂载目录 |
| 部署目录 | `/opt/coturn` | Docker Compose 所在目录 |

## 4. 端口规划

### 默认推荐

- `3478/tcp`
- `3478/udp`
- `49152-49252/udp`

### 如果启用 TLS

再增加：

- `5349/tcp`

### 特殊情况

如果 `3478`、`5349`、`443` 与现有服务冲突，可以调整监听端口，但要同步更新：

- `turnserver.conf`
- 安全组规则
- 前端/BFF 下发的 TURN URL

## 5. 安全组与防火墙

至少放行：

- `3478/tcp`
- `3478/udp`
- `49152-49252/udp`

启用 TLS 时，再放行：

- `5349/tcp`

如果使用云厂商安全组，务必确认 relay UDP 端口范围已开放。  
只开放 `3478` 不足以承载 TURN relay 媒体流。

## 6. 目录结构

推荐目录结构：

```text
/opt/coturn/
  docker-compose.yml
  turnserver.conf
  logs/
  certs/
```

创建目录：

```bash
mkdir -p /opt/coturn/logs
mkdir -p /opt/coturn/certs
cd /opt/coturn
```

## 7. 编写 Docker Compose

创建 `/opt/coturn/docker-compose.yml`：

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

### 说明

- 这里使用 `network_mode: host`
- 目的是避免 TURN relay 的 UDP 端口映射复杂化
- 如果不能接受 host 网络模式，需要显式映射监听端口和 relay 端口范围

## 8. 编写 `turnserver.conf`

创建 `/opt/coturn/turnserver.conf`：

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

### 必改项

上线前至少替换：

- `static-auth-secret`
- `realm`
- `server-name`

### NAT 场景

如果服务器本身没有直接绑定公网 IP，而是位于 NAT 后，请补充：

```conf
external-ip=PUBLIC_IP
```

如有复杂 NAT 映射，按实际网络拓扑补映射形式。

## 9. 启动服务

在 `/opt/coturn` 下执行：

```bash
docker compose up -d
```

## 10. 启动后检查

### 查看容器状态

```bash
docker compose ps
```

预期：

- `coturn` 容器状态为 `Up`

### 查看最近日志

```bash
docker compose logs --tail=100
```

预期：

- 无持续报错
- 无配置解析错误
- 无端口占用错误

### 查看端口监听

```bash
ss -lntup | egrep ':(3478|5349|49152|49252)\s'
```

预期：

- `3478/tcp` 监听
- `3478/udp` 监听
- relay 端口范围在运行时会被动态使用

## 11. 客户端接入参数

前端/BFF 下发的 TURN URL 参考：

```text
turn:turn.example.com:3478?transport=udp
turn:turn.example.com:3478?transport=tcp
```

如果启用 TLS：

```text
turns:turn.example.com:5349?transport=tcp
```

## 12. TURN 凭证模式

推荐使用临时凭证模式：

- `username = expiry:user-id`
- `credential = base64(HMAC-SHA1(shared-secret, username))`

shared secret 必须只保存在：

- BFF
- 或安全的后端服务

不要：

- 把 shared secret 写到前端
- 把长期固定 TURN 用户名密码直接给浏览器

## 13. 验证步骤

### 方式一：强制 relay 验证

客户端设置：

- `iceTransportPolicy = relay`
- 只配置 TURN，不带公共 STUN

如果页面连接成功，并显示：

```text
relay / udp / relay->relay
```

说明 TURN relay 工作正常。

### 方式二：正常 fallback 验证

客户端设置：

- `iceTransportPolicy = all`
- 同时配置 STUN 和 TURN

预期：

- 网络好时优先直连
- 网络受限时自动回退 TURN

## 14. TLS 扩展步骤

如果后续需要启用 TLS：

1. 准备证书文件：
   - `fullchain.pem`
   - `privkey.pem`
2. 放到：
   - `/opt/coturn/certs/`
3. 在 `turnserver.conf` 中追加：

```conf
tls-listening-port=5349
cert=/etc/coturn/certs/fullchain.pem
pkey=/etc/coturn/certs/privkey.pem
```

4. 删除：

```conf
no-tls
```

5. 如需 DTLS，再删除：

```conf
no-dtls
```

6. 重启容器：

```bash
docker compose up -d
```

## 15. 回滚步骤

如果变更后服务不可用，可按以下方式回滚：

1. 回退 `turnserver.conf`
2. 回退证书文件或 TLS 配置
3. 重启容器：

```bash
docker compose up -d
```

如果需要完全停止：

```bash
docker compose down
```

## 16. 常见故障排查

### 故障 1：页面能建立 signaling，但 WebRTC 一直失败

优先检查：

- 安全组是否开放 relay UDP 端口范围
- 是否只开了 `3478`
- TURN 凭证是否过期
- `realm` 是否与签发逻辑一致

### 故障 2：能连上 TURN，但视频不出

优先检查：

- 是否真的选择到了 `relay` candidate
- 码率是否过高
- 服务器带宽是否过小

### 故障 3：容器启动失败

优先检查：

- `turnserver.conf` 语法
- 端口是否被占用
- 日志目录是否可写

### 故障 4：TLS 不工作

优先检查：

- 域名是否正确解析
- 证书路径是否正确
- `5349/tcp` 是否放行
- 客户端是否使用 `turns:`

## 17. 执行完成后的交付物

部署完成后，建议 SRE 向研发或项目负责人反馈以下信息：

- TURN 域名
- 已开放端口
- relay 端口范围
- 是否启用 TLS
- 当前日志路径
- 部署目录
- 是否为 host network 模式

## 18. 最终建议

对于验证环境，推荐先用：

- 非 TLS TURN
- 小 relay 端口范围
- 临时凭证
- 强制 relay 验证

验证通过后，再逐步增加：

- 正常 fallback 验证
- TLS
- 更大的 relay 端口范围
- 正式监控与告警
