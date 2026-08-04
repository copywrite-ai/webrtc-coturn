# ORTM Sparse-Finder 实验进展（2026-08-04）

## 目标

验证 ORTM 是否可以依次移除右下角、左下角 finder，在降低视觉存在感的同时保持固定 ROI 链路中的解码稳定性。

本实验比较两种稀疏布局：

- `three`：移除右下角 4x4 finder 的绘制和校验。
- `two-top`：移除左下角和右下角，只保留顶部两个 finder。

- 四角 finder 区域始终保留，不写入 payload。
- payload 位序、CRC、timing row/column 均保持不变。
- 默认 four-finder 行为保持兼容。
- 稀疏布局必须由显式配置了相同 finder layout 的解码器读取。

## ORTM 工程

仓库：`copywrite-ai/ortm`

- `main` 已加入稀疏 GStreamer renderer、720p minimal profile 和动态背景 benchmark。
- 实验分支：`experiment/three-finder-marker`
- three-finder 提交：`6f9dc7c experiment: evaluate three-finder marker layout`
- two-top 提交：`aa5f38a experiment: evaluate two-top finder layout`

three-finder 和 two-top 支持已覆盖 Python、JavaScript、GStreamer cairooverlay、离线 benchmark 和报告生成器。默认 API 仍使用 four-finder。

## 离线正式矩阵

配置：

- 1280x720，60 fps，H.264 ultrafast
- 目标码率 2500 kbps，key-int 120
- ORTM：x=32，y=32，cell=8，padding=16
- background alpha=0，cell alpha=0.70，border alpha=0
- 背景：stripes、moving-checker、deterministic snow
- three layouts x five repetitions x 600 frames x three backgrounds

结果：

| Layout | 成功帧 | Finder 错误 | Timing 错误 | CRC/身份错误 |
| --- | ---: | ---: | ---: | ---: |
| Four finder | 9000 / 9000 | 0 | 0 | 0 |
| Three finder | 9000 / 9000 | 0 | 0 | 0 |
| Two top finder | 9000 / 9000 | 0 | 0 | 0 |

三种布局的解码 p95 均约为 1.96-1.98 ms。three-finder 将实际绘制单元从 195 减少到 179，下降 8.2%；two-top 降至 163，下降 16.4%。离线实验未观察到恢复率下降，也没有可解释为吞吐或时延收益的差异。

## RemoteControl Live A/B

发送链路：

```text
rolling checkers -> ORTM cairooverlay -> x264enc -> whipclientsink
  -> MediaMTX -> WHEP -> remoteControl browser viewer
```

运行条件与离线矩阵一致。Viewer 通过 WebSocket 控制接口在以下 profile 间切换，并在每次重连时清空解码统计：

- `720p-minimal`：four-finder baseline
- `720p-three-finder`：three-finder experiment
- `720p-two-top`：two-top fixed-ROI experiment

实测结果：

| Layout | 解码尝试 | 成功 | CRC 失败 | 结构失败 | 低对比失败 |
| --- | ---: | ---: | ---: | ---: | ---: |
| Three finder | 1494 | 1491 | 3 | 0 | 0 |
| Four finder | 992 | 991 | 1 | 0 | 0 |

three-finder 成功率约 99.8%，four-finder 约 99.9%。两边都出现了极少量 live CRC 损伤，当前样本量不足以证明两者存在显著差异。重要的是，three-finder 没有引入 structure mismatch 或 low-contrast failure。

恢复 three-finder 后的短窗口为 154 / 154 成功，分辨率 1280x720，接收帧率 60 fps，对比度约 178。

two-top 在 `checkers-8 -> GStreamer -> H.264 -> WHIP -> MediaMTX -> WHEP -> browser` 实际链路中通过 RemoteControl 运行超过一分钟：

| 解码尝试 | 成功 | CRC 失败 | 结构失败 | 低对比失败 | Freeze | Drop |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 576 | 576 | 0 | 0 | 0 | 0 | 0 |

最终样本为 1280x720、60 fps，finder/timing error 均为 0，contrast 约 176.5。Publisher 实测约 2517 kbps，ORTM render 平均 0.2 ms，overlay-to-send 平均 2.3 ms。

## Timing Column Alpha 边界

为了降低 two-top 纵向 timing column 的视觉存在感，publisher 增加了独立参数 `ORTM_TIMING_COLUMN_ALPHA`。它只影响 `col == 4 && row != 4`，不改变 timing row、finder、payload、CRC 或固定 ROI 几何。未设置时继承 `ORTM_CELL_ALPHA`，因此默认行为保持不变。

在其他条件保持不变时，通过 RemoteControl 重连并清空每轮统计：

| Timing column alpha | 解码结果 | CRC 失败 | Freeze / Drop | 判定 |
| ---: | ---: | ---: | ---: | --- |
| 0.70 | 461 / 461 | 0 | 0 / 0 | 对照通过 |
| 0.625 | 276 / 276 | 0 | 0 / 0 | 短窗口通过 |
| 0.625 | 546 / 546 | 0 | 0 / 0 | 90 秒确认通过 |
| 0.60625 | 224 / 254 | 30 | 9 / 2 | 失败 |
| 0.5875 | 322 / 339 | 17 | 4 / 2 | 失败 |
| 0.55 | 463 / 479 | 16 | 11 / 31 | 失败 |

当前实验推荐值为 `0.625`。它不是通用安全下限；摄像头内容、其他编码器、弱网和 relay 仍需单独验证。低 alpha 下主要表现为 CRC failure，而非 structure failure，推测是 timing 证据变弱后候选定位或阈值选择不稳定，最终污染 payload 采样。

## 当前运行状态

- `fish_front` 正在发布 two-top 滚动棋盘流。
- Viewer 页面：`whep-quad-direct.html?remoteControl=1`
- WHEP：`https://peng-mbp14.li-adder.ts.net/fish_front/whep`
- 当前 viewer profile：`720p-two-top`
- Publisher 默认仍为 four-finder；只有显式设置 `ORTM_FINDER_LAYOUT=three|two-top` 才启用实验布局。

## 验证

- ORTM 实验分支：Python 53 项、JavaScript 21 项测试通过。
- Tunnel：Node.js 19 项测试通过。
- Publisher Python 编译检查、shell 语法、Compose 配置和 `git diff --check` 通过。
- 离线正式矩阵：27000 / 27000 帧成功。

## 当前结论

在固定 ROI、固定尺度、720p、H.264 和当前动态背景范围内，底部两个 finder 都不是稳定解码的必要条件。two-top 进一步降低了视觉单元数量，但顶部 finder 共线，不适合旋转、透视恢复或全图搜索，因此仍不能替代 ORTM v0 的 four-finder 默认布局。

下一阶段应依次验证：

1. 真实摄像头内容和更长的 live A/B 窗口。
2. 平移、缩放、旋转和透视变化。
3. 弱网、relay 和不同编码器。
4. 全图搜索时的误识别率与定位成功率。
