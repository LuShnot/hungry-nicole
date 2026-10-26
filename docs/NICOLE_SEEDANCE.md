# 妮可连续视频制作方案

2026-10-26：按用户要求，独立生图逐帧版废弃搁置。原图片、旧 WebP、拒绝验收记录均保留，不再作为本轮动画输入。本轮只使用已经确认的透明 base 身份图。当前已完成 27 段视频的生成、转换与用户动作验收；下面命令用于后续创作。

## 本轮准备成果

- `tools/nicole_seedance_prompts.json`：原有 25 个动作，另加 `hungry` 饥饿提醒和 `feed` 吃面包反馈。25 段共 152 秒，含照料动作共 27 段、165 秒。
- `docs/NICOLE_SEEDANCE_PROMPTS.md`：全部完整中文 Prompt，可逐条检查。
- `tools/prepare_nicole_video_bases.py`：本地合成首尾底图，不生图、不消耗额度。
- `tools/nicole_seedance.py`：预览、创建异步任务、记录 ID、续查、下载原视频。
- `tools/extract_nicole_video.py`：按原时间轴抽帧、绿幕透明处理、WebP、独立 WAV、透明边缘联系表。

默认路径相对于源码根目录。参考图与 API 运行记录仅存本地 `build/`，不上传 Git。当前动画通过用户实际播放确认；后续重新生成的视频仍须独立验收。

## 底图与构图

输入 `references/nicole-base.png`，来源为已验收的卡通妮可 base。程序只裁掉透明边界、统一缩放一次，并合成 720×720 纯绿背景；不重新生成脸、服装或姿势。

普通动作共用同一站立首尾图；拖拽共用同一悬空首尾图；落地使用悬空首帧、站立尾帧。完整猫耳至鞋底占画布约 74%，留出摆尾、挥手和道具空间。悬空图只是同一角色的整体上移，具体腿部与身体动态由视频模型完成。

每个动作目录都有 `first.png`、`last.png` 和 `prompt.txt`，位于 `build/seedance/nicole/bases/<动作>/`。预览为 `build/seedance/nicole/bases/preview.jpg`。

检查首尾是否共用同一像素底图、画布是否为 720×720、绿幕角点是否完整，以及悬空模板是否只发生垂直位移：

```bash
PYTHONPATH=tools ./.venv/bin/python tools/check_nicole_video_bases.py \
  --input-dir build/seedance/nicole/bases \
  --report build/seedance/nicole/bases/alignment-report.json
```

当前检查结果应为 27 个动作、26 个动作首尾完全相同，只有 `landing` 使用悬空首帧和站立尾帧；站立包围框为 `[225, 112, 493, 643]`，悬空包围框为 `[225, 44, 493, 575]`。两种模板的左右边界相同，悬空模板整体上移 68 像素。

选择纯绿是因为妮可的主色为粉、蓝、白；后续能按颜色抠除背景。它不是原生透明视频。生成中如果出现绿光反射、变色、背景纹理或绿色道具，须先检查，不能直接宣布抠图合格。不能逐帧自动裁边再放大，否则会重新引入大小跳变。

## 接口选择

固定使用用户给定的 `doubao-seedance-2-0-260128`，不擅自切换模型。请求默认 `resolution=720p`、`ratio=adaptive`（方形输入）、`watermark=false`。每次只生成一段 4–8 秒的连续视频。

使用 `first_frame` + `last_frame`，两张图片可以相同。没有同时添加 `reference_image` / `reference_video` / `reference_audio`：官方将首帧、首尾帧、全模态参考列为互斥模式。未来如果需要参考声音锁定音色，应另做全模态样片，对比姿态约束效果后再选择。

视频生成适合连续动作，但仍可能变脸、变道具、跳帧或首尾不衔接；“用了视频模型”不是验收通过的依据。先生成 `idle` 和 `01`，看三轮循环、边缘和声音，再批量生成其余动作。

## 用户执行命令

在已设置 `ARK_API_KEY` 的同一个终端运行。不把 Key 发到聊天或写到源码；如需输入，zsh 可使用隐藏输入：

```bash
cd hungry-nicole
read -s 'ARK_API_KEY?请输入火山方舟 API Key: '
export ARK_API_KEY
```

本轮底图已经准备好；如更换 base 再执行：

```bash
./.venv/bin/python tools/prepare_nicole_video_bases.py \
  --input references/nicole-base.png
```

查看完整请求计划，不需要 Key，不联网：

```bash
./.venv/bin/python tools/nicole_seedance.py \
  --dry-run --all-actions --include-care
```

先生成两段样片（收费请求）：

```bash
./.venv/bin/python tools/nicole_seedance.py \
  --execute --only idle --only 01
```

原视频保存到 `build/seedance/nicole/videos/<动作>/source.mp4`，保留生成时的原音轨。任务逐个提交、查询、下载；不会一次提交 27 个任务。仅想提交后稍后查询可加 `--submit-only`，但这会按所选动作数创建收费任务。

抽帧并保留声音（本地处理，无 API 费用）：

```bash
./.venv/bin/python tools/extract_nicole_video.py \
  --video-dir build/seedance/nicole/videos --only idle --only 01 \
  --output-dir build/seedance/nicole/processed
```

先看每段目录中的 `conversion.json`，它给出透明帧目录、WebP、WAV 和联系表路径。联系表分别显示白底、深底和棋盘底；机械转换成功仍记录 `visualStatus=pending`。

样片通过后，全量生成和转换：

```bash
./.venv/bin/python tools/nicole_seedance.py \
  --execute --all-actions --include-care

./.venv/bin/python tools/extract_nicole_video.py \
  --video-dir build/seedance/nicole/videos --all-actions --include-care \
  --output-dir build/seedance/nicole/processed
```

不加 `--include-care` 为原有 25 动作。已下载且哈希一致的样片会跳过；重跑同一命令就是续跑，无需另开“断点”任务。不要让两个终端处理同一个动作；工具有进程锁，阻止重复提交。

## 失败、续查与费用

- 有任务 ID：重跑原命令会查询原任务，不重新创建。只查不提交可用 `--poll-only --all-actions --include-care`；如果某个动作未提交过，会报错停下，可以 `--only` 选择已有任务。
- 创建 POST 超时或中断：不能判断服务端是否已经创建，不自动重试收费请求。先到方舟控制台查任务 ID，再用 `--poll-only --only idle --adopt-task idle=cgt-实际任务ID` 补录。
- 已失败、过期或取消：不会自动花钱重生成。确认原因并修正后使用新目录，例如 `--output-dir build/seedance/nicole-take2/videos`。
- Prompt、底图或参数变化：拒绝复用已有目录，防止错拿旧视频；使用新的 take 目录。
- 轮询默认最多等待 30 分钟，超时保留任务 ID。服务器任务不会因为本地退出而自动取消。
- 回执只记录白名单字段、任务 ID 和文件哈希，不记录 Key、图片 base64、带签名的视频 URL 或可能回显密钥的服务端错误正文。

## 抽帧与循环

不再把所有视频强拉成白龙原来的 104 或 108 帧。保留 1 倍时间轴，并按 24fps 采样：例如 5 秒约 120 帧，8 秒约 192 帧。WebP 时间累计到毫秒，41/42ms 分配；相同画面可能被编码器合帧，但总播放时间不变。这里没有交叉淡化、打乱帧序或独立图片拼接。

实际分辨率和帧率从视频读取；非方形视频拒绝拉伸。完整输出统一为 720×720 RGBA。不会按每一帧的包围盒重新缩放角色。抽帧后人物有抖动时先看源视频，不能靠更换帧率掩盖生成问题。

`work02`、`work04` 的 2–6 秒是设计的循环候选，不是已验收接缝。拿到视频后检查实际动作位置，选择一个完整周期，单独转换，例如：

```bash
./.venv/bin/python tools/extract_nicole_video.py \
  --input build/seedance/nicole/videos/work02/source.mp4 \
  --start 2 --duration 2 \
  --output-dir build/seedance/nicole/loop-candidates/work02
```

必须检查进入→中段、中段重复三次、中段→收尾。若循环首尾速度不匹配，应调整切点或重做视频；不以静止帧或倒放冒充自然工作循环。验证后再把主动画和循环段接入角色包，本次工具不自动替换桌宠当前角色或公开发布素材。

## 声音可以保留

Seedance 原视频能包含音轨；WebP 本身没有声音。工具保留原 MP4，并对同一时间范围提取 `audio.wav`（PCM、48kHz、双声道），音画不变速，便于后续同步播放。

交互动作启用 `generate_audio=true`，只要求短音效或一句短反馈，无背景音乐；待机、自动工作、自动饥饿提醒使用静音视频。Prompt 使用相同声音描述，但没有声音参考时不同视频的音色可能不同，需要听审，不能声称锁定了同一声线。

**桌宠运行时已接入 WAV。** 手动点击人物或菜单动作时，在 1 倍速且声音开启的条件下播放一次；播放位置按动画时钟校正，自动动作无声。动作切换、隐藏、退出或关闭声音立即停止；非 1 倍速自动静音。设置 → 人物与动作 → 主动点击人物或手动动作时播放声音（1倍速）。

## 参考文档

- [用户提供的创建视频任务文档](https://console.volcengine.com/ark/region:cn-beijing/docs/create-video-generation-task-api)
- [官方公开文档：创建视频生成任务](https://docs.volcengine.com/docs/ark/create-video-generation-task-api?lang=zh)
- [官方 Seedance 2.0 能力、首尾帧与跳变说明](https://docs.volcengine.com/docs/ark/seedance-2-0?lang=en)
- [查询视频生成任务](https://docs.volcengine.com/docs/ark/get-video-generation-task-api?lang=zh)
- [FFmpeg fps / colorkey / despill 滤镜](https://ffmpeg.org/ffmpeg-filters.html)

本地离线验证覆盖底图身份共用、25+2 动作、任务续跑防重复收费、提交结果不明、失败任务、并发锁和脱敏回执；另用实际 FFmpeg 合成测试视频验证顺序、透明背景、裁切、24fps 时间以及同长 WAV。它们验证工具链，不替代真实 Seedance 样片的视觉和声音验收。

## 2026-10-26 全量本地预览

27 个动作已转换并逐帧解码检查，共 3,987 帧、19 段 WAV。每段保持源视频时间轴，预览速度为 1 倍；抽样检查不代替用户连续播放验收。

执行 `./.venv/bin/python tools/prepare_seedance_preview.py` 可校验转换回执、全部帧和音轨时长，并生成按内容命名的完整测试角色包。设置只切换当前预览角色，保留已有聊天与食物数据库。不会调用 API，也不会上传 GitHub。

```bash
cd hungry-nicole
./.venv/bin/python app/enhanced.py --quit
LOCALAPPDATA="$PWD/build/nicole-seedance-preview-state" \
APPDATA="$PWD/build/nicole-seedance-preview-state" \
./.venv/bin/python app/enhanced.py
```

右键人物 → 手动动作可逐一查看全部动作，包括饥饿（互动动作分类），但不包含喂食。本轮预览关闭随机动作、状态驱动动作与走动，便于逐项检查。喂食动画固定绑定「食物 → 面包」：只有成功扣除一个面包后才播放一次，并按声音设置播放对应 WAV；无库存或饱腹度已满时不播放。点击人物、随机动作和手动动作菜单均不会触发喂食。饥饿动画目前仍仅手动预览。WAV 已随包保留，并接入手动动作播放。工作动画目前播放完整片段，没有宣称中段 Seam 已验收。

检查报告：`build/seedance/nicole/review/preview-check.json`。当前素材已由用户确认动作效果；新的生成结果仍需重新检查。
