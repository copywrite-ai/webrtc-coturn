# coturn 与 WHEP 反代同机部署执行手册

本文档面向 SRE / 运维执行人员，目标是提供一份可操作的 `coturn + nginx + WHEP` 同机部署手册。

适用场景：

- 浏览器侧不安装 `Tailscale`
- 需要通过公网 `HTTPS` 地址访问 WHEP
- 需要通过 `coturn` 为 WebRTC 提供 TURN relay 能力
- 计划把 `nginx` 与 `coturn` 部署在同一台公网 Linux 主机

本文档尽量采用“执行手册”写法，而不是概念说明写法。

## 编写与实施原则

本文件默认采用“旁路验证 / 增量扩展”策略，目标是在尽量不改动现有服务的前提下，新增加一套可验证的 `WHEP + coturn + 回源` 链路。

默认原则如下：

1. 不直接替换现有生产服务
2. 不直接抢占现有已使用端口
3. 不直接覆盖现有证书、主配置和主容器
4. 优先通过新增容器、新端口、新配置文件完成验证
5. 回滚应尽量只涉及新增组件，而不是恢复被覆盖的原服务

如果现网已经存在：

- `derper`
- 现有 `coturn`
- 现有 `nginx`
- 现有 `mediamtx`

那么扩展 runbook 默认应采用：

- 独立容器名
- 独立监听端口
- 独立配置文件
- 独立防火墙规则

例如：

- 现网端口继续保留：
  - `80/443/3478`
- 验证链路新增：
  - `8443`
  - `3480`
  - `49160-49200/udp`

只有在验证通过并明确进入正式收编阶段时，才建议评估是否把旁路验证配置并入现网主服务。

## 部署模式选择

本文件默认只描述一种部署模式：

- 复用现有 `coturn`
- 复用现有 `mediamtx`
- 仅新增一个旁路 `nginx` / gateway
- 默认通过 `docker run` 启动验证版 `nginx`

该模式适用于：

- 现网已经存在可用的 `coturn`
- 现网 `coturn` 的监听端口与凭证签发逻辑已经稳定
- 本次只需要新增：
  - `WHEP/WHIP` 反代
  - 公网 `HTTPS` 入口
  - 端侧回源链路

如果现网没有 `coturn`，或者必须新起一套独立 TURN 实例，不建议在本文件里继续扩展，直接回到基础文档：

- [COTURN_DOCKER_RUNBOOK.zh-CN.md](/Users/peng/Documents/tunnel/COTURN_DOCKER_RUNBOOK.zh-CN.md)

## 1. 目标

在一台具备公网访问能力的 Linux 主机上，部署以下能力：

- `nginx`：对外提供 `HTTPS` 入口，并反向代理 `WHEP`
- `coturn`：对外提供 `TURN/STUN`
- 可选：`mediamtx` 也部署在同机，直接承接 WHEP / WebRTC

默认部署目标：

- `nginx` 监听 `80/tcp`、`443/tcp`
- `coturn` 监听 `3478/tcp`、`3478/udp`
- `coturn` 使用临时凭证模式（`use-auth-secret`）
- `coturn` 开放小规模验证用 relay UDP 端口范围

## 2. 关键边界

这类部署里最容易混淆的点如下：

1. `nginx` 反代的是 `WHEP HTTP/HTTPS signaling`
2. `coturn` 承载的是 `WebRTC ICE / TURN relay`
3. `TURN` 不是普通网页流量，不应按“一个 `/turn` 路径交给 `nginx` 反代”的思路处理
4. 浏览器访问的播放地址应为公网域名，例如：
   - `https://play.example.com/fish_right/whep`
5. 浏览器拿到的 ICE server 应直接指向 TURN 域名，例如：
   - `turn:turn.example.com:3478?transport=udp`

结论：

- `WHEP` 可以由 `nginx` 反代
- `TURN` 需要由 `coturn` 直接监听并对外开放端口

## 3. 前提条件

执行前确认：

1. 服务器可以访问公网
2. 已安装 Docker
3. 已确定公网域名，例如：
   - `play.example.com`：浏览器访问 WHEP
   - `turn.example.com`：浏览器访问 TURN
4. `play.example.com` 与 `turn.example.com` 可以解析到同一台公网服务器
5. 已准备 `HTTPS` 证书
6. 已准备随机生成的 TURN shared secret
7. 已确认安全组 / 防火墙可开放所需 TCP / UDP 端口

## 4. 推荐拓扑

```text
Browser
  -> https://play.example.com/fish_right/whep
  -> nginx:443
  -> mediamtx:8889

Browser
  -> turn:turn.example.com:3478?transport=udp
  -> coturn:3478/udp
  -> coturn relay ports:49152-49252/udp
```

如果 `mediamtx` 不与 `nginx` / `coturn` 同机，也可以改为：

```text
Browser
  -> nginx:443
  -> upstream mediamtx over private IP / Tailscale / LAN
```

## 5. 输入参数

执行前请先明确以下参数。

| 参数 | 示例 | 说明 |
|---|---|---|
| WHEP 域名 | `play.example.com` | 浏览器访问地址 |
| TURN 域名 | `turn.example.com` | `realm` 和 ICE server 使用 |
| TURN shared secret | 长随机串 | 只保存在服务端/BFF |
| TURN 监听端口 | `3478` | 非 TLS TURN |
| TURN TLS 端口 | `5349` | 如果启用 TLS |
| relay 端口范围 | `49152-49252` | 小规模验证可先用 100 个 UDP 端口 |
| mediamtx WHEP 监听端口 | `8889` | 示例值，按实际配置填写 |
| 部署目录 | `/opt/edge-media` | 同机部署总目录 |
| nginx 证书目录 | `/opt/edge-media/nginx/certs` | 存放 `fullchain.pem` / `privkey.pem` |

## 6. 端口规划

### 默认推荐

- `80/tcp`：`nginx`
- `443/tcp`：`nginx`
- `3478/tcp`：`coturn`
- `3478/udp`：`coturn`
- `49152-49252/udp`：`coturn relay`
- `8889/tcp`：`mediamtx` 内部监听，可仅内网暴露

### 如果启用 TURN TLS

再增加：

- `5349/tcp`

### 端口归属原则

- `443/tcp` 优先留给 `nginx`
- `coturn` 默认不要抢占 `443`
- 除非有强需求，否则先不要做 `TURN over TLS on 443`

这样最简单，也最不容易与现有 `HTTPS` 服务冲突。

## 7. 安全组与防火墙

至少放行：

- `80/tcp`
- `443/tcp`
- `3478/tcp`
- `3478/udp`
- `49152-49252/udp`

启用 TURN TLS 时，再放行：

- `5349/tcp`

注意：

- 只开放 `3478` 不足以承载 TURN relay 媒体流
- relay UDP 端口范围必须完整放通

## 8. 目录结构

推荐目录结构：

```text
/opt/edge-media-verify/
  nginx/
    default.conf
    certs/
```

创建目录：

```bash
mkdir -p /opt/edge-media-verify/nginx/certs
cd /opt/edge-media-verify
```

## 9. 默认使用 docker run 启动旁路 nginx

如果当前目标是：

- 复用现有 `coturn`
- 复用现有 `mediamtx`
- 只新增一个公网 `HTTPS` 入口

那么默认更推荐使用 `docker run`。

原因：

- 不容易误碰现网主 compose
- 更适合“只新增一个验证版 `nginx`”的旁路验证方式

### 9.1 目录准备
沿用第 `8` 节目录结构即可，不再重复创建。

### 9.2 启动验证版 nginx

准备好：

- `./nginx/default.conf`
- `./nginx/certs/fullchain.pem`
- `./nginx/certs/privkey.pem`

然后执行：

```bash
docker run -d \
  --name edge-nginx-verify \
  --restart unless-stopped \
  -p 8443:443 \
  -v /opt/edge-media-verify/nginx/default.conf:/etc/nginx/conf.d/default.conf:ro \
  -v /opt/edge-media-verify/nginx/certs:/etc/nginx/certs:ro \
  nginx:1.27-alpine
```

如果后续要回滚：

```bash
docker stop edge-nginx-verify
docker rm edge-nginx-verify
```

### 9.3 适用边界

该模式默认适用于：

- 现网 `coturn` 已经可用
- 端侧 `mediamtx` 已经可用
- 只需要新增公网入口和反代逻辑

## 10. 编写 nginx 配置

创建 `/opt/edge-media-verify/nginx/default.conf`：

```nginx
server {
    listen 80;
    server_name play.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name play.example.com;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    client_max_body_size 16m;

    location / {
        proxy_pass http://mediamtx:8889;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

### 说明

- 这里反代的是 `WHEP` 的 `HTTP/HTTPS` 请求
- 不是把 `TURN` 代理到 `coturn`
- 如果你的实际路径是 `/{stream}/whep` 或 `/{stream}/whip`，推荐直接按整站透传方式配置

## 10.1 `/{stream}/*` 通用映射关系

如果 `mediamtx` 不在公网机本地，而是在另一台已经加入 `tailnet` 的设备上，更推荐直接做整站路径透传：

- 公网入口：
  - `https://public.example.com:8443/...`
- tailscale 回源：
  - `https://source-device.example.ts.net:8889/...`

这样做的意义是：

- 公网访问地址保持稳定
- `fish_right`、`demo-stream`、`cam-01` 等路径都可复用
- 将来更换真实 5G 回源设备时，只需要改 upstream，不需要改浏览器播放地址

对应 nginx 配置可写成：

```nginx
server {
    listen 8443 ssl http2;
    server_name public.example.com;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    client_max_body_size 16m;

    location / {
        proxy_pass https://source-device.example.ts.net:8889;
        proxy_http_version 1.1;

        proxy_ssl_server_name on;
        proxy_ssl_name source-device.example.ts.net;

        proxy_set_header Host source-device.example.ts.net;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;

        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

上面这段配置表达的就是：

- `https://public.example.com:8443/fish_right/whep`
  -> `https://source-device.example.ts.net:8889/fish_right/whep`
- `https://public.example.com:8443/fish_right/whip`
  -> `https://source-device.example.ts.net:8889/fish_right/whip`

也就是说，公网机只负责入口暴露和 TLS 终止，真正的 `WHIP/WHEP` 业务仍由 tailscale 回源端上的 `mediamtx` 处理。

## 10.2 本次验证环境的实际映射

本次验证环境可以直接套用如下关系：

- 公网入口：
  - `https://106.14.26.23:8443/fish_right/whep`
  - `https://106.14.26.23:8443/fish_right/whip`
- tailscale 回源：
  - `https://ubuntu-1.li-adder.ts.net:8889/fish_right/whep`
  - `https://ubuntu-1.li-adder.ts.net:8889/fish_right/whip`

如果希望做成“整站路径自动映射”，规则应理解为：

- `https://106.14.26.23:8443/<path>`
  -> `https://ubuntu-1.li-adder.ts.net:8889/<path>`

对应配置要点：

- `proxy_pass` 指向 tailscale 回源机的根路径
- `proxy_ssl_server_name on`
- `Host` 头显式改为 `ubuntu-1.li-adder.ts.net`

否则上游如果基于 `Host` 或 SNI 做证书校验，容易出现 TLS 或路由异常。

## 10.3 推荐默认模型：动态设备路径

当后续存在多台已经加入同一 tailnet 的端侧设备时，更推荐单独做一层“设备名到回源主机”的公网聚合路径：

- `/mtx/{device}/{stream}/whep`
- `/mtx/{device}/{stream}/whip`

例如：

- `https://106.14.26.23:8443/mtx/ubuntu-1/fish_right/whep`
  -> `https://ubuntu-1.li-adder.ts.net:8889/fish_right/whep`
- `https://106.14.26.23:8443/mtx/ubuntu-1/fish_right/whip`
  -> `https://ubuntu-1.li-adder.ts.net:8889/fish_right/whip`
- `https://106.14.26.23:8443/mtx/another/fish_right/whep`
  -> `https://another.li-adder.ts.net:8889/fish_right/whep`

这个模型的优点是：

- 公网入口地址结构统一
- 不需要为每一台设备单独开新端口
- 更适合做动态 upstream
- 后续可以把 `device` 解析逻辑放到 Node / Go 网关里，而不是把所有设备写死在静态 nginx 配置里

如果采用该模型，建议明确约束：

- `device` 只允许小写字母、数字、短横线
- 网关只允许拼接到受控后缀，例如：
  - `.beago-fish.ts.net`
- 生产环境建议增加白名单，而不是允许任意字符串直接拼 upstream

## 10.4 当前验证环境的推荐默认地址

结合本次实际验证，推荐默认使用如下地址：

- 发布端：
  - `https://106.14.26.23:8443/fish_right/whip`
- 播放端：
  - `https://106.14.26.23:8443/fish_right/whep`

如果未来要切换到另一台回源机，例如 `another.li-adder.ts.net`，直接把整站 upstream 切到新主机即可；如果采用动态设备路径模型，则只需要把路径中的 `ubuntu-1` 替换为 `another`：

- `https://106.14.26.23:8443/mtx/another/fish_right/whip`
- `https://106.14.26.23:8443/mtx/another/fish_right/whep`

## 11. 现有 coturn 的前提要求

本文件默认不新部署 `coturn`，而是复用现有实例。

执行本 runbook 前，应先确认现有 `coturn` 至少满足以下条件：

- 已对外开放 `3478/udp`
- 已开放 relay 端口范围
- 已启用 `use-auth-secret`
- `realm` 与前端使用的 TURN 域名一致

参考最小配置特征：

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
```

如果服务器本身没有直接绑定公网 IP，而是位于 NAT 后，还应确认存在：

```conf
external-ip=PUBLIC_IP
```

如果当前没有现成 `coturn`，或者需要新起一套独立 TURN 实例，请回到基础文档：

- [COTURN_DOCKER_RUNBOOK.zh-CN.md](/Users/peng/Documents/tunnel/COTURN_DOCKER_RUNBOOK.zh-CN.md)

## 12. 现有 mediamtx 的前提要求

本文件默认不新部署 `mediamtx`，而是复用现有实例。

执行本 runbook 前，应先确认现有 `mediamtx` 满足以下条件：

- 已启用 `WHEP/WHIP`
- 公网入口机可以访问它的回源地址
- 目标路径存在，例如：
  - `/fish_right/whep`
  - `/fish_right/whip`

参考最小配置特征：

```yaml
webrtc: true
webrtcAddress: :8889
webrtcEncryption: false
webrtcAllowOrigins:
  - https://play.example.com

webrtcAdditionalHosts:
  - play.example.com
```

补充说明：

- `webrtcAdditionalHosts` 应写浏览器实际访问的公网域名
- 如果需要由 `mediamtx` 下发 TURN，可配置 `webrtcICEServers2`
- 生产环境不建议把长期固定 TURN 用户名密码直接写给浏览器

## 13. 启动服务

在验证模式下，只启动新增的 `nginx` 容器。

直接执行第 `9.2` 节中的 `docker run` 命令即可。

## 14. 启动后检查

### 查看容器状态

```bash
docker ps --filter name=edge-nginx-verify
```

预期：

- `edge-nginx-verify` 容器状态为 `Up`
- 现网 `coturn` 容器持续保持原状
- 现网 `mediamtx` 容器持续保持原状

### 查看最近日志

```bash
docker logs --tail=100 edge-nginx-verify
```

预期：

- 无持续报错
- 无配置解析错误
- 无端口占用错误

### 查看端口监听

```bash
ss -lntup | egrep ':(80|443|3478|5349|49152|49252|8889|8189)\s'
```

预期：

- `8443/tcp` 被 `edge-nginx-verify` 占用
- `3478/tcp`、`3478/udp` 被现网 `coturn` 占用
- `8889/tcp` 被 `mediamtx` 占用
- relay 端口范围在运行时会被动态使用

## 15. 浏览器侧接入方式

浏览器访问地址示例：

```text
https://play.example.com/fish_right/whep
```

浏览器或后端下发的 TURN URL 参考：

```text
turn:turn.example.com:3478?transport=udp
turn:turn.example.com:3478?transport=tcp
```

如果启用 TLS：

```text
turns:turn.example.com:5349?transport=tcp
```

## 16. TURN 凭证模式

推荐使用临时凭证模式：

- `username = expiry:user-id`
- `credential = base64(HMAC-SHA1(shared-secret, username))`

shared secret 必须只保存在：

- BFF
- 或安全的后端服务

不要：

- 把 shared secret 写到前端
- 把长期固定 TURN 用户名密码直接给浏览器

## 17. 验证步骤

### 方式一：先验证 WHEP 入口

执行：

```bash
curl -vk https://play.example.com/fish_right/whep
```

预期：

- 能建立 TLS 连接
- 返回 `404`、`405` 或业务定义的非成功状态也可以接受
- 关键是域名、证书、反代链路已打通

### 方式二：强制 relay 验证

客户端设置：

- `iceTransportPolicy = relay`
- 只配置 TURN，不带公共 STUN

如果页面连接成功，并显示：

```text
relay / udp / relay->relay
```

说明 TURN relay 工作正常。

### 方式三：正常 fallback 验证

客户端设置：

- `iceTransportPolicy = all`
- 同时配置 STUN 和 TURN

预期：

- 网络好时优先直连
- 网络受限时自动回退 TURN

## 18. TLS 扩展步骤

如果后续需要启用 TURN TLS：

1. 准备证书文件：
   - `fullchain.pem`
   - `privkey.pem`
2. 放到：
   - `/opt/edge-media/coturn/certs/`
3. 如果现有 `coturn` 需要启用 TLS，在它自己的 `turnserver.conf` 中追加：

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

6. 按现网运维方式重启对应的 `coturn` 服务，使 TLS 配置生效。

这里不要重启本文件新增的验证版 `nginx`，因为 TURN TLS 配置不在 `nginx` 内生效。

## 19. 回滚步骤

如果变更后服务不可用，可按以下方式回滚：

1. 停止并删除验证版 `nginx` 容器：
   直接执行第 `9.2` 节中的回滚命令即可。

2. 如果你额外改动过现有 `coturn`，按现网运维方式回退其配置并重启 `coturn`
3. 如果你额外改动过现有 `mediamtx`，按现网运维方式回退其配置并重启 `mediamtx`

## 20. 常见故障排查

### 故障 1：浏览器打不开 WHEP 地址

优先检查：

- `play.example.com` 是否解析正确
- `443/tcp` 是否放行
- `nginx` 证书路径是否正确
- `proxy_pass` 路径是否与 `mediamtx` 路径匹配

### 故障 2：页面能请求 WHEP，但 WebRTC 一直失败

优先检查：

- 安全组是否开放 relay UDP 端口范围
- 是否只开了 `3478`
- TURN 凭证是否过期
- `realm` 是否与签发逻辑一致
- `mediamtx` 下发的 ICE server 是否指向公网 TURN 域名

补充说明：

- 如果页面日志里出现：
  - `TURN host lookup received error`
  - `STUN host lookup received error`
- 这通常不是 `coturn` 已经被打到但拒绝，而是浏览器当前连域名解析或目标可达性都没过
- 在验证环境里，更建议先使用 `IP` 形式的 TURN 预置，而不是依赖单独的 TURN 域名

### 故障 3：误以为 TURN 也能走 nginx 反代

现象通常是：

- `https://play.example.com/turn` 可访问
- 但浏览器仍拿不到可用 relay candidate

原因：

- `TURN` 不是普通路径路由问题
- 浏览器 ICE 需要直接访问 `turn:` 或 `turns:` 对应的监听端口

处理：

- 保持 `coturn` 直接监听
- 检查 `3478` / `5349` 和 relay 端口范围

### 故障 4：能连上 TURN，但视频不出

优先检查：

- 是否真的选择到了 `relay` candidate
- 服务器带宽是否过小
- 码率是否过高
- `mediamtx` 的 `webrtcAdditionalHosts` 是否正确

补充说明：

- 如果日志中出现：
  - `turn:...transport=tcp code=701 text=Address not associated with the desired network interface`
- 这通常只代表某一条 `TCP TURN` 候选失败
- 只要后续仍然拿到了：
  - `relay/udp`
  - `iceConnectionState=connected`
- 那么媒体仍可能已经通过 `UDP TURN` 正常连通

### 故障 5：容器启动失败

优先检查：

- `nginx` 配置语法
- `turnserver.conf` 语法
- 端口是否被占用
- 证书文件是否存在

### 故障 6：WHEP / WHIP 地址能打开，但请求长时间无响应

优先检查：

- 公网反代机 `/*` 是否正确回源到端侧设备
- 端侧设备本地 `/*` 是否又被错误配置成继续回到公网地址或自己的 `.ts.net` 域名
- 是否形成了“入口机 -> 回源机 -> 再回入口机 / 再回自己”的自循环

典型错误链路如下：

- `106.14.26.23:8443/*`
  -> `ubuntu-1.li-adder.ts.net:8889/*`
- 但 `ubuntu-1.li-adder.ts.net:8889/*`
  又被错误代理到
  -> `ubuntu-1.li-adder.ts.net:8889/*`

这会导致：

- 浏览器端表现为 `Failed to fetch`
- `curl` 看起来能完成 TLS 握手，但 HTTP 请求迟迟不返回
- `mediamtx` 根本收不到对应的 `WHIP/WHEP` 请求

正确做法应为：

- 公网入口机 `/*`
  -> 端侧回源机 `.ts.net`
- 端侧回源机本地 `/*`
  -> 本机 `mediamtx`

也就是说，端侧本地的业务路径不能再继续回源到自己的 `.ts.net` 地址。

## 21. 执行完成后的交付物

部署完成后，建议 SRE 向研发或项目负责人反馈以下信息：

- WHEP 域名
- TURN 域名
- 已开放端口
- relay 端口范围
- 是否启用 TURN TLS
- `nginx` 证书路径
- 当前日志路径
- 部署目录
- `coturn` 是否为 host network 模式

## 22. 最终建议

对于验证环境，推荐先用：

- `nginx` 承接 `HTTPS WHEP`
- `coturn` 非 TLS TURN
- 小 relay 端口范围
- 临时凭证
- 强制 relay 验证

验证通过后，再逐步增加：

- 正常 fallback 验证
- TURN TLS
- 更大的 relay 端口范围
- 更严格的证书和域名治理
- 正式监控与告警

## 23. 专用场景：mediamtx 位于 5G 端侧

如果当前架构不是“`mediamtx` 与 `nginx` / `coturn` 同机”，而是：

- `mediamtx` 部署在 `5G` 端设备
- 公网服务器只部署 `nginx`
- 公网服务器可选同时部署 `coturn`

那么建议按本节执行。

### 23.1 推荐拓扑

```text
Browser
  -> https://play.example.com/fish_right/whep
  -> nginx on public server
  -> mediamtx on 5G device over Tailscale / VPN / private tunnel

Browser
  -> turn:turn.example.com:3478?transport=udp
  -> coturn on public server
```

### 23.2 部署原则

这个场景要遵守以下原则：

1. `5G` 端 `mediamtx` 不直接暴露给公网
2. 浏览器只访问公网 `HTTPS` 域名
3. 公网反代机通过受控链路回源到 `5G` 设备
4. `TURN` 仍由公网 `coturn` 直接提供，不通过 `nginx` 转发

补充说明：

- `Tailscale` 在本方案里的职责是：
  - 给公网反代机提供到端侧设备的私网回源路径
  - 提供稳定设备寻址，例如 `ubuntu-1.li-adder.ts.net`
- `Tailscale` 不负责最终的 WebRTC 媒体中继
- 最终媒体链路仍可能是：
  - 浏览器 <-> `coturn` <-> `mediamtx`
- 因此应把“回源网络”和“WebRTC 媒体路径”分开理解

### 23.3 回源链路建议

推荐优先级如下：

1. `Tailscale`
2. 专线 / VPN
3. 其他私有隧道

如果继续使用 `Tailscale` 回源，建议：

- `5G` 设备保留 `tailscale`
- 公网 `nginx` 服务器也加入同一 tailnet
- `nginx` 使用 `5G` 设备的 tailnet IP 或 `MagicDNS` 访问 `mediamtx`

示例：

- `http://100.x.y.z:8889/fish_right/whep`
- `https://ubuntu-1.li-adder.ts.net:8889/fish_right/whep`

### 23.4 公网反代机网络规则

如果公网反代机只跑 `nginx`，入站至少放行：

- `80/tcp`
- `443/tcp`

如果公网反代机同时跑 `coturn`，再放行：

- `3478/tcp`
- `3478/udp`
- `49152-49252/udp`
- `5349/tcp`，仅在启用 TURN TLS 时

出站至少保证：

- 可访问 `5G` 端 `mediamtx` 的 WHEP 端口
- 可访问证书更新、镜像拉取等基础公网依赖

### 23.5 5G 端设备网络规则

`5G` 端 `mediamtx` 的核心要求是：

- 不对公网开放 `8889/tcp`
- 只允许受控来源访问 `8889/tcp`

如果通过 `Tailscale` 回源，建议规则为：

- 仅允许 `tailscale0` 接口访问 `8889/tcp`
- 或仅允许 tailnet 来源地址访问 `8889/tcp`

不要做的事：

- 不要把 `8889/tcp` 直接暴露给公网
- 不要让浏览器直接访问 `5G` 设备的私网地址或 tailnet 地址

### 23.6 nginx 回源配置示例

如果公网反代机通过 tailnet IP 回源：

```nginx
server {
    listen 80;
    server_name play.example.com;
    return 301 https://$host$request_uri;
}

server {
    listen 443 ssl http2;
    server_name play.example.com;

    ssl_certificate     /etc/nginx/certs/fullchain.pem;
    ssl_certificate_key /etc/nginx/certs/privkey.pem;

    location / {
        proxy_pass http://100.64.10.20:8889;
        proxy_http_version 1.1;
        proxy_set_header Host $host;
        proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
        proxy_set_header X-Forwarded-Proto $scheme;
        proxy_read_timeout 3600s;
        proxy_send_timeout 3600s;
    }
}
```

如果通过 `MagicDNS` 回源：

```nginx
location / {
    proxy_pass https://ubuntu-1.li-adder.ts.net:8889;
    proxy_http_version 1.1;
    proxy_ssl_server_name on;
    proxy_ssl_name ubuntu-1.li-adder.ts.net;
    proxy_set_header Host ubuntu-1.li-adder.ts.net;
    proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
    proxy_set_header X-Forwarded-Proto $scheme;
    proxy_read_timeout 3600s;
    proxy_send_timeout 3600s;
}
```

注意：

- 使用 `MagicDNS` 时，公网反代机自身必须安装并登录 `tailscale`
- 回源域名解析能力来自反代机本身，不来自浏览器

### 23.7 mediamtx 配置注意事项

当 `mediamtx` 在 `5G` 端时，要特别关注它返回给浏览器的 WebRTC 候选信息。

至少检查：

1. `webrtcAdditionalHosts` 是否包含浏览器实际访问的公网域名
2. `webrtcICEServers2` 是否指向公网可达的 TURN 域名
3. 是否错误地向浏览器暴露了仅 tailnet 可达的地址

如果浏览器访问的是：

- `https://play.example.com/fish_right/whep`

那 `mediamtx` 的对外宣告地址也应围绕公网访问路径和公网 TURN 展开，而不是 `5g.xxx.ts.net`

### 23.8 验证顺序

推荐按以下顺序验证：

1. 在公网反代机上验证可回源到 `5G` 端：

```bash
curl -v http://100.64.10.20:8889/fish_right/whep
```

或：

```bash
curl -vk https://ubuntu-1.li-adder.ts.net:8889/fish_right/whep
```

2. 在公网或办公网终端验证外部 `HTTPS` 入口：

```bash
curl -vk https://play.example.com/fish_right/whep
```

3. 浏览器端强制 `relay` 验证 TURN

4. 浏览器端恢复 `iceTransportPolicy = all`，验证正常 fallback

### 23.9 常见误区

#### 误区 1：只要 nginx 能回源，浏览器就一定能播

不对。

`nginx` 回源成功只代表 `WHEP signaling` 打通。  
后续 WebRTC 媒体是否能通，还取决于：

- `ICE` candidate 是否正确
- `TURN` 是否可达
- `mediamtx` 是否向浏览器暴露了正确的公网信息

#### 误区 2：既然 5G 端有 Tailscale，就让浏览器直接访问 `ts.net`

不对。

浏览器所在办公网设备如果不安装 `Tailscale`：

- 无法解析 `MagicDNS`
- 无法访问 tailnet 地址

所以浏览器必须访问公网域名，而不是 `tailnet` 域名。

#### 误区 3：把 5G 端的 mediamtx 端口直接开放到公网更简单

通常不建议。

这样会增加：

- 暴露面
- 证书与域名治理复杂度
- 边缘设备直接承压风险

更推荐让公网服务器承接入口，再通过受控回源链路访问端侧服务。

#### 误区 4：Tailscale 承载了最终 WebRTC 媒体

不对。

在本方案中，`Tailscale` 主要承担的是：

- 公网入口机到端侧设备的回源网络
- 设备寻址与私网互通

它不等价于：

- 浏览器之间通过 `Tailscale` 直连媒体
- 或 `Tailscale` 替代 `coturn`

如果最终 ICE candidate 显示为：

- `relay/udp`

那说明媒体是在走公网 `coturn` relay，而不是在走 `Tailscale`。
