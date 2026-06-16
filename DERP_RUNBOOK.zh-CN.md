# DERP 服务器部署执行手册

本文档面向 SRE / 运维执行人员，描述如何按当前线上方案部署一个自建 Tailscale DERP 服务器。

这份手册基于当前已验证的部署形态整理，核心特征是：

- Linux 宿主机直接运行 `derper`
- 使用 `systemd` 托管
- 使用手动证书模式 `--certmode=manual`
- 开启 STUN
- 开启 `--verify-clients=true`

本文档不包含：

- 真实公网 IP
- 真实证书私钥
- 真实 tailnet 名称

## 1. 目标

在一台具备公网访问能力的 Linux 主机上部署一个可被 tailnet 使用的 DERP 中继节点，供 Tailscale 在无法直连时回退使用。

当前方案的标准监听为：

- `80/tcp`
- `443/tcp`
- `3478/udp`

## 2. 当前线上部署特征

当前已运行实例的关键参数如下：

- 二进制路径：`/root/go/bin/derper`
- 证书目录：`/root/derper/certs`
- 服务文件：`/etc/systemd/system/derper.service`
- 运行参数：

```text
/root/go/bin/derper \
  --hostname=PUBLIC_IP_OR_HOSTNAME \
  --certmode=manual \
  --certdir=/root/derper/certs \
  --stun \
  --stun-port=3478 \
  -a=:443 \
  --verify-clients=true
```

当前监听端口为：

- `80/tcp`
- `443/tcp`
- `3478/udp`

## 3. 前提条件

执行前确认：

1. 服务器有公网访问能力
2. 已安装 `tailscale`
3. 服务器已经加入目标 tailnet
4. 本机可使用 `systemd`
5. 已准备 DERP 对应域名或公网 IP
6. 已准备证书和私钥

### 重要说明

由于当前部署使用了：

```text
--verify-clients=true
```

所以这台 DERP 服务器不仅仅是一个公开 listener，它会依赖本机 `tailscaled` 的本地授权状态来校验客户端。

如果本机没有正确登录到目标 tailnet，客户端会出现类似：

```text
not authorized (not found in local tailscaled)
```

## 4. 端口要求

必须开放：

- `80/tcp`
- `443/tcp`
- `3478/udp`

### 作用说明

- `443/tcp`：DERP 主服务 TLS 监听
- `80/tcp`：HTTP / 健康检查 / 某些证书与基础访问场景
- `3478/udp`：STUN

## 5. 安全组与防火墙

至少放行：

- `80/tcp`
- `443/tcp`
- `3478/udp`

如果使用云厂商安全组，请确认是入方向规则已放通。

## 6. 目录约定

推荐目录：

```text
/root/go/bin/derper
/root/derper/certs
/etc/systemd/system/derper.service
```

创建证书目录：

```bash
mkdir -p /root/derper/certs
```

## 7. 安装前检查

### 检查 tailscale 状态

```bash
tailscale status
```

预期：

- 本机已经登录
- 本机在目标 tailnet 中可见

### 检查关键端口未被占用

```bash
ss -lntup | egrep ':(80|443|3478)\s'
```

如果已有服务占用 `80` / `443` / `3478`，需要先协调端口规划。

## 8. 安装 derper 二进制

本方案当前使用的是宿主机二进制直装，不是 Docker。

你可以通过 Go 安装：

```bash
go install tailscale.com/cmd/derper@latest
```

安装完成后确认：

```bash
ls -l /root/go/bin/derper
```

如果 `go install` 的输出路径不同，请按实际路径调整 systemd 文件。

## 9. 准备证书

当前线上方案使用手动证书模式：

```text
--certmode=manual
--certdir=/root/derper/certs
```

所以需要把证书和私钥放到证书目录中。

### 文件要求

放置位置：

```text
/root/derper/certs/
```

当前线上实例中观察到的文件命名类似：

```text
HOSTNAME_OR_IP.crt
HOSTNAME_OR_IP.key
```

建议命名方式与 `--hostname` 参数保持一致。

### 注意

- 不要把证书私钥提交到 git
- 证书权限建议限制为 root 可读

## 10. 编写 systemd 服务文件

创建：

```text
/etc/systemd/system/derper.service
```

内容参考：

```ini
[Unit]
Description=Tailscale DERP Relay Server
After=network.target

[Service]
Type=simple
ExecStart=/root/go/bin/derper \
  --hostname=PUBLIC_IP_OR_HOSTNAME \
  --certmode=manual \
  --certdir=/root/derper/certs \
  --stun \
  --stun-port=3478 \
  -a=:443 \
  --verify-clients=true
Restart=on-failure
RestartSec=5
LimitNOFILE=65536

[Install]
WantedBy=multi-user.target
```

### 必改项

上线前至少替换：

- `PUBLIC_IP_OR_HOSTNAME`
- `ExecStart` 中的二进制路径（如果本机不是 `/root/go/bin/derper`）

## 11. 启动服务

```bash
systemctl daemon-reload
systemctl enable derper
systemctl restart derper
```

## 12. 启动后检查

### 查看服务状态

```bash
systemctl status derper --no-pager
```

预期：

- `Active: active (running)`

### 查看监听端口

```bash
ss -lntup | egrep ':(80|443|3478)\s'
```

预期：

- `80/tcp` 已监听
- `443/tcp` 已监听
- `3478/udp` 已监听

### 查看最近日志

```bash
journalctl -u derper -n 100 --no-pager
```

## 13. 关键配置说明

### `--certmode=manual`

表示 `derper` 不自动申请证书，而是直接使用本地证书目录。

适用于：

- 已有证书体系
- 不想让 `derper` 自己做自动签发
- 服务器环境不方便直接走自动签证

### `--stun --stun-port=3478`

启用 STUN，便于 Tailscale 网络探测。

### `-a=:443`

DERP 主服务监听 `443/tcp`。

### `--verify-clients=true`

开启客户端校验。  
这是当前方案的重要安全设置，但也意味着：

- 本机 `tailscaled` 必须正常工作
- 客户端必须是被本机 `tailscaled` 认可的 tailnet 成员

## 14. 验证步骤

### 基础验证

1. 服务状态为 `active`
2. `80/443/3478` 监听正常
3. 无明显配置错误日志

### 业务验证

在 tailnet 中让客户端尝试使用这个 DERP 节点，确认：

- 无法直连时可回退到 relay
- DERP 服务稳定运行

## 15. 回滚步骤

如果新变更导致 DERP 不可用：

1. 回退 `/etc/systemd/system/derper.service`
2. 回退证书文件
3. 执行：

```bash
systemctl daemon-reload
systemctl restart derper
```

如果需要临时停服：

```bash
systemctl stop derper
```

## 16. 常见故障排查

### 故障 1：服务启动失败

优先检查：

- `ExecStart` 路径是否正确
- 证书目录是否存在
- 证书文件名是否与 `--hostname` 匹配
- `443` 是否被占用

### 故障 2：端口未监听

优先检查：

- systemd 是否真的启动成功
- 安全组是否放通
- 本机是否已有服务占用 `80` / `443` / `3478`

### 故障 3：日志中大量出现 `not authorized`

优先检查：

- 本机 `tailscaled` 是否已登录目标 tailnet
- `--verify-clients=true` 下，本机是否能识别客户端 node key
- 客户端是否属于同一 tailnet

### 故障 4：证书相关错误

优先检查：

- `.crt` / `.key` 是否存在
- 权限是否允许 root 读取
- `--certdir` 是否正确

## 17. 执行完成后的交付物

部署完成后，建议 SRE 向研发或项目负责人回传：

- DERP 域名或公网入口
- 是否启用了 `verify-clients`
- 证书目录路径
- systemd 服务文件路径
- 二进制路径
- 安全组已开放的端口

## 18. 最终建议

这份 runbook 对应的是：

- 宿主机直装
- 手动证书
- systemd 托管
- `verify-clients=true`

## 19. 是否建议 Docker 化

对当前这套 DERP 方案，不建议优先做 Docker 化，建议继续保持：

- 宿主机直装二进制
- `systemd` 托管

原因如下：

1. `derper` 本身非常轻量
   - 直接运行单个二进制即可
   - 不依赖复杂运行时

2. 端口固定且和宿主机关系紧密
   - `80/tcp`
   - `443/tcp`
   - `3478/udp`
   - Docker 并不会显著简化这部分管理

3. 当前方案已经稳定运行
   - 线上已验证
   - 当前排障路径基于 `systemctl`、`journalctl`、`ss`
   - 改成 Docker 反而会引入新的运维变量

4. `verify-clients=true` 依赖本机 `tailscaled`
   - DERP 授权行为和宿主机上的 Tailscale 状态直接相关
   - 宿主机直装更直观

5. Docker 化收益有限
   - 对 `coturn` 这类更偏基础组件的服务，Docker 化有较大标准化收益
   - 对 `derper` 而言，当前收益不明显

### 什么时候可以考虑 Docker 化

只有在以下情况下，才建议认真评估：

- 公司内部所有服务强制要求容器化交付
- 需要统一镜像分发和版本治理
- 已有成熟的容器化运维体系，且能清晰处理：
  - 证书挂载
  - `tailscaled` 依赖
  - `80/443/3478` 的宿主端口占用

### 当前结论

对于这台 DERP 服务器，推荐继续采用当前方案：

- 宿主机安装 `derper`
- 手动证书
- `systemd` 托管

如果后续要做“更通用的可复制部署”，建议再拆一版：

- 基于域名的手动证书部署
- 基于自动证书的部署
- 基于 Docker 的部署

但对当前线上方案，这份文档已经足够作为执行和回放手册使用。
