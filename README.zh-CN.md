# WebRTC TURN Relay Lab

一个用于验证 WebRTC 在受限网络下通过 TURN 中继回退的最小示例项目。

它适合用于验证以下链路：

- 本地运行的 WebRTC 页面
- 公网 `coturn` 服务
- 手机通过 tailnet 或其他私有网络入口访问页面

项目刻意保持很小：

- `server.mjs`：静态文件服务 + WebSocket 信令
- `public/index.html`：单页 publisher/viewer 验证界面
- `Dockerfile` 和 `docker-compose.yml`：本地容器运行方式
- `INTEGRATION_DESIGN.md`：如何接入现有前端和现有 BFF

许可证：MIT

## 这个项目验证什么

这个项目主要用来验证：

1. 网络良好时，WebRTC 能否优先直连
2. 网络受限时，媒体能否自动回退到 TURN
3. 你的 `coturn` 是否真的承载了媒体流

页面会显示最终选中的候选对。如果看到类似：

```text
relay / udp / relay->relay
```

就说明媒体链路已经通过 TURN relay 建立成功。

## 快速开始

### 方式一：直接用 Node.js 启动

```bash
npm install
npm start
```

打开：

```text
http://localhost:9001
```

### 方式二：使用 Docker

```bash
docker compose up --build -d
```

打开：

```text
http://localhost:9001
```

停止：

```bash
docker compose down
```

## 配置方式

默认配置通过环境变量注入。先复制一份 `.env.example`：

```bash
cp .env.example .env
```

支持的变量有：

- `PORT`：本地 HTTP 端口
- `DEFAULT_ROOM`：页面默认房间名
- `DEFAULT_TURN_URLS`：逗号分隔的 TURN 地址列表
- `DEFAULT_TURN_USERNAME`：可选的默认 TURN 用户名
- `DEFAULT_TURN_CREDENTIAL`：可选的默认 TURN 密码
- `DEFAULT_FORCE_RELAY`：`1` 或 `0`
- `DEFAULT_TURN_ONLY`：`1` 或 `0`
- `DEFAULT_AUTO_START`：`1` 或 `0`

示例：

```env
PORT=9001
DEFAULT_ROOM=demo-room
DEFAULT_TURN_URLS=turn:turn.example.com:3478?transport=udp,turn:turn.example.com:3478?transport=tcp
DEFAULT_TURN_USERNAME=
DEFAULT_TURN_CREDENTIAL=
DEFAULT_FORCE_RELAY=1
DEFAULT_TURN_ONLY=1
DEFAULT_AUTO_START=1
```

如果用 Docker Compose，项目根目录下存在 `.env` 时会自动读取。

## 典型验证流程

### 1. 强制 TURN relay 验证

1. 在笔记本上打开页面
2. 选择 `publisher`
3. 保持 `force relay` 开启
4. 保持 `turn only` 开启
5. 加入房间
6. 在手机上打开同一个页面并指定 `role=viewer`
7. 手机加入同一个房间
8. 由 publisher 发起连接

如果连接建立成功，且候选对显示为 `relay`，说明 TURN 可用。

### 2. 正常 fallback 验证

1. 关闭 `force relay`
2. 关闭 `turn only`
3. 两端重新连接

这一步验证的是正常策略：

- 好网络优先直连
- 直连失败再走 TURN

## 可分享的 viewer 链接

页面会生成类似这样的 viewer 链接：

```text
https://device.example.ts.net/?room=demo-room&role=viewer&relay=1&turnOnly=1
```

当 URL 里带了 `role=viewer` 时，viewer 角色会被锁定，避免手机端误切成 publisher。

## Tailscale Serve

如果你想在 tailnet 内获得 HTTPS 访问入口，而不额外加反向代理，可以使用 Tailscale Serve，把本地 `9001` 暴露为 HTTPS：

```bash
tailscale serve 9001
```

然后访问：

```text
https://your-device.your-tailnet.ts.net
```

注意：

- 不要直接使用 `https://...:9001`
- HTTPS 终止发生在 Tailscale Serve
- 本地应用本身仍然跑在 `http://127.0.0.1:9001`

## coturn 说明

这个仓库**不会**包含真实的：

- TURN 域名
- TURN 共享密钥
- TURN 用户名/密码

正式接入时建议：

- 只发临时 TURN 凭证
- TURN shared secret 只保留在服务端
- 低带宽 VPS 上先降低视频码率

这个 demo 默认使用的低码率参数是：

- 视频码率 `280 kbps`
- 帧率 `12 fps`
- 高度 `360p`

## 安全建议

- 不要提交真实 TURN 用户名和密码
- 不要提交 TURN shared secret
- 不要提交个人 tailnet 名称、设备名或内部域名
- `.env` 应视为本地私有配置

## 发布前检查

在公开仓库前，确认：

- `.env` 没有被提交
- 源码和截图里没有真实 TURN 凭证
- 仓库里没有个人域名、tailnet 名称或 VPS IP
- 本地验证残留文件没有进入 git 历史

## 设计文档

如果你要把这套方案接入现有前端和现有 BFF，请看：

- [英文版集成设计文档](./INTEGRATION_DESIGN.md)
- [中文版集成设计文档](./INTEGRATION_DESIGN.zh-CN.md)
- [英文版 coturn Docker 部署文档](./COTURN_DOCKER_DEPLOYMENT.md)
- [中文版 coturn Docker 部署文档](./COTURN_DOCKER_DEPLOYMENT.zh-CN.md)
- [中文版 coturn Docker 执行手册](./COTURN_DOCKER_RUNBOOK.zh-CN.md)

## 发布说明

这个仓库的定位是“最小验证实验室”，不是生产级信令服务。

如果你要公开发布，建议再补充：

- 一两张页面截图或短 GIF
- 一张简短架构图
- 真实验证过的网络拓扑说明
