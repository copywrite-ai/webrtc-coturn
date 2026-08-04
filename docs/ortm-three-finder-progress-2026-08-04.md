# ORTM Three-Finder 实验进展（2026-08-04）

## 目标

验证 ORTM 是否可以移除右下角 finder，在降低视觉存在感的同时保持固定 ROI 链路中的解码稳定性。

本实验只移除右下角 4x4 finder 的绘制和校验：

- 右下角 16 个 cell 继续保留，不写入 payload。
- payload 位序、CRC、timing row/column 均保持不变。
- 默认 four-finder 行为保持兼容。
- three-finder 必须由显式配置了相同 finder layout 的解码器读取。

## ORTM 工程

仓库：`copywrite-ai/ortm`

- `main` 已加入稀疏 GStreamer renderer、720p minimal profile 和动态背景 benchmark。
- 实验分支：`experiment/three-finder-marker`
- 实验提交：`6f9dc7c experiment: evaluate three-finder marker layout`

three-finder 支持已覆盖 Python、JavaScript、GStreamer cairooverlay、离线 benchmark 和报告生成器。默认 API 仍使用 four-finder。

## 离线正式矩阵

配置：

- 1280x720，60 fps，H.264 ultrafast
- 目标码率 2500 kbps，key-int 120
- ORTM：x=32，y=32，cell=8，padding=16
- background alpha=0，cell alpha=0.70，border alpha=0
- 背景：stripes、moving-checker、deterministic snow
- two layouts x five repetitions x 600 frames x three backgrounds

结果：

| Layout | 成功帧 | Finder 错误 | Timing 错误 | CRC/身份错误 |
| --- | ---: | ---: | ---: | ---: |
| Four finder | 9000 / 9000 | 0 | 0 | 0 |
| Three finder | 9000 / 9000 | 0 | 0 | 0 |

两种布局的解码 p95 均约为 1.96-1.97 ms。移除右下 finder 后，实际绘制单元从 195 减少到 179，下降 8.2%。离线实验未观察到鲁棒性下降，也没有可解释为吞吐或时延收益的差异。

## RemoteControl Live A/B

发送链路：

```text
rolling checkers -> ORTM cairooverlay -> x264enc -> whipclientsink
  -> MediaMTX -> WHEP -> remoteControl browser viewer
```

运行条件与离线矩阵一致。Viewer 通过 WebSocket 控制接口在以下 profile 间切换，并在每次重连时清空解码统计：

- `720p-minimal`：four-finder baseline
- `720p-three-finder`：three-finder experiment

实测结果：

| Layout | 解码尝试 | 成功 | CRC 失败 | 结构失败 | 低对比失败 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Three finder | 1494 | 1491 | 3 | 0 | 0 |
| Four finder | 992 | 991 | 1 | 0 | 0 |

three-finder 成功率约 99.8%，four-finder 约 99.9%。两边都出现了极少量 live CRC 损伤，当前样本量不足以证明两者存在显著差异。重要的是，three-finder 没有引入 structure mismatch 或 low-contrast failure。

恢复 three-finder 后的短窗口为 154 / 154 成功，分辨率 1280x720，接收帧率 60 fps，对比度约 178。

## 当前运行状态

- `fish_front` 正在发布 three-finder 滚动棋盘流。
- Viewer 页面：`whep-quad-direct.html?remoteControl=1`
- WHEP：`https://peng-mbp14.li-adder.ts.net/fish_front/whep`
- 当前 viewer profile：`720p-three-finder`
- Publisher 默认仍为 four-finder；只有设置 `ORTM_FINDER_LAYOUT=three` 才启用实验布局。

## 验证

- ORTM 实验分支：Python 48 项、JavaScript 19 项测试通过。
- Tunnel：Node.js 18 项测试通过。
- Publisher Python 编译检查、shell 语法、Compose 配置和 `git diff --check` 通过。
- 离线正式矩阵：18000 / 18000 帧成功。

## 当前结论

在固定 ROI、固定尺度、720p、H.264 和当前动态背景范围内，右下 finder 不是稳定解码的必要条件。three-finder 值得继续进入真实摄像头和 WebRTC 长时间实验，但还不能替代 ORTM v0 的 four-finder 默认布局。

下一阶段应依次验证：

1. 真实摄像头内容和更长的 live A/B 窗口。
2. 平移、缩放、旋转和透视变化。
3. 弱网、relay 和不同编码器。
4. 全图搜索时的误识别率与定位成功率。
