# 妮可素材生产说明

项目角色为 Q 版粉发猫娘妮可。固定身份包括粉色长发、猫耳、蓝眼睛、白蓝裙装、蝴蝶结、金色铃铛、猫尾巴、白袜与棕色短靴。参考图中的文字、界面和背景不属于角色。

提示词：`tools/nicole_prompts.json`。请求脚本：`tools/nicole_seedream.py`。接口客户端：`tools/seedream_api.py`。

## 当前实际状态

- 基准图已生成；正式动作使用透明参考图 `build/seedream/nicole/base-master.png`。
- 完整帧目录为 `build/seedream/nicole-full-frames`，25 动作共 2,875 张。
- 生成脚本的 `--match-white-dragon` / `--full-animation` 对齐白龙的主动作帧数。每一帧是独立图生图请求，**没有时间连续性保证**。
- 当前抽样可见表情、形体和道具跳变。增加独立图片数量并不自动改善这些问题，不能把完整帧生成等同于精致流畅动画。
- 五阶段方案仅适合动作设计/关键姿势。现有短过渡后端是交叉淡化，不是光流插帧，不应以此宣称解决重影。

## 密钥与请求

API Key 仅从 `ARK_API_KEY` 环境变量读取，不应写入 Git、README 或聊天。若终端尚未配置，可用 zsh 的隐藏输入设置：

```zsh
read -s 'ARK_API_KEY?请输入 ARK API Key：'
export ARK_API_KEY
```

默认模型与参数以脚本为准。当前妮可生成器默认 `watermark=false`，命令中仍建议明确加 `--no-watermark`。透明背景需要接口实际返回 Alpha 通道，不能仅凭扩展名或 Prompt 判定成功。

只检查请求，不收费：

```bash
python tools/nicole_seedream.py \
  --dry-run --only idle --match-white-dragon \
  --reference base=build/seedream/nicole/base-master.png \
  --no-watermark --background transparent --output-format png \
  --output-dir build/seedream/nicole-full-frames
```

实际生成需明确使用 `--execute`。当前图片已经齐备，不需要为打包重复调用 API。

脚本支持 `--only` 多选动作，以及 `--start-frame`、`--max-frames` 指定范围。这是**手动续跑范围**，不是自动识别并跳过已有图片；重复运行相同生成命令可能再次收费并覆盖同名结果。并行终端必须选择不同动作/帧范围。

## 打包是什么意思

1. 识别文件名中 `frame-NNN` 的帧编号；末尾 `-01` 是接口样本编号，不是动画帧序号。
2. 检查序列数量、缺帧和重复编号，读取 PNG 并统一为 720×720 RGBA。
3. 完整帧直接编码，不再混合姿势；每帧延迟均匀安排为 41/42ms，动作总时长与白龙清单一致。
4. 无损编码 WebP，解码检查实际帧数和总时长，再原子替换目标文件。
5. 保存动作校验回执；全部所选动作完成后写入素材清单。

这一过程不调用生图 API。无损 WebP 压缩主要占用 CPU，`method=6` 是较慢的压缩等级；长时间没有旧脚本终端输出并不一定表示卡死。

先检查输入：

```bash
python tools/assemble_nicole_animation.py \
  --input-dir build/seedream/nicole-full-frames --check-only
```

推荐使用新目录生成候选包：

```bash
python tools/assemble_nicole_animation.py \
  --input-dir build/seedream/nicole-full-frames \
  --output-dir outputs/妮可桌宠素材候选 \
  --method 1
```

`--method 1` 与 `--method 6` 均为无损编码，差异主要是压缩耗时和体积。修正版会打印每个动作的进度、帧数、时长、体积和耗时。相同命令重跑时，输入和目标哈希均匹配的完整动作会跳过；需要强制重打时加 `--no-resume`。

修改脚本不会改变已经启动的 Python 进程。不要让两个打包进程同时写入同一个输出目录。旧脚本没有完成回执，不能将它留下的零字节文件或半成品当成可续用结果。

## 动作规格

| 动作 | 帧数 | 时长 |
| --- | ---: | ---: |
| idle | 104 | 4333ms |
| idle2、01、04、05、08、09、13、14、18、22、25、quota | 108 | 4500ms |
| 17、work03、work04 | 139 | 5792ms |
| 20 | 114 | 4750ms |
| 21、26、work01、work05 | 115 | 4792ms |
| 24、work02 | 163 | 6792ms |
| drag | 102 | 4250ms |
| landing | 56 | 2333ms |

工作动作的无缝中段必须从连续动画中设计和验证。当前候选包不再使用旧脚本从前几帧拼出的虚假接缝；没有合格接缝时播放完整动作。

## 验收要求

机械检查包括：25 动作覆盖、帧数、时长、画布、透明通道、路径和文件哈希。视觉检查包括：首尾三轮循环、连续相邻帧的身份/比例/道具稳定、透明边缘、动作衔接和实际播放器表现。

机械检查成功并不等于视觉验收通过。参考视频或真正的连续动画可以作为后续动作来源；是否采用 Seedance 及相关抠像流程，应先制作短片验证，不应再次直接批量生成数千张独立姿势图。
