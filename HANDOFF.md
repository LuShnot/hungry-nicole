# 0.10.2 网页入口

新增右键游戏入口和 magicneko 首页快捷功能，新用户默认选 magicneko；已有配置不强制覆盖。链接走 QDesktopServices.openUrl，只在主动点击时打开。

# 0.10.1 面板焦点修复

- codex_desktop_open 原来只检测 Windows 的 exe，macOS 每隔五秒会误判未运行。现在识别 Codex.app / ChatGPT.app 主进程；不可读取返回未知。
- 自动收起的面板不再因工具、思考、完成事件重新展开；运行中不按进程探测结果反复收起。空闲时仍可收起，点击胶囊或查看状态可主动展开。
- 状态窗口创建时禁用输入焦点，编辑页通过 QWindow.setFlag 临时允许输入。不要在 PanelTransition 内调用 QWidget.setWindowFlag：它会触发 hideEvent/cancel 并破坏动画快照。
- 52 项面板、照料和事件回归通过；测试应同时设置 LOCALAPPDATA 与 APPDATA，并使用隔离目录。

# 项目交接

当前版本 0.10.0，仅发布 Seedance 妮可。入口 `run.py`，内置资源 `outputs/妮可桌宠素材`，上游来源与许可保留于 NOTICE 和 docs/upstream。

- 27 段视频素材、3987 帧、19 个 WAV；用户确认动作可用。保留完整工作片段，不宣称工作 Seam 无缝。
- 食物喂面包成功后只播放 `17` 偷吃面包；`feed` 是备用素材，二者都禁止通过通用动作入口触发。SQLite 成功扣库存后才允许专用调用。
- 自动动作不发声。主动动作在声音开启且 1 倍速时播放一次 WAV，使用动画时钟同步；隐藏/切换/关闭声音停止音轨。
- macOS 工具窗口设置 WA_MacAlwaysShowToolWindow；人物和状态面板不接受键盘焦点。状态刷新禁止周期性 raise；打开聊天/设置后才允许输入。
- 数据通过 run.py 隔离到 HungryNicole，内部 BailongEnhanced 名称为兼容实现，不要无故迁移现有用户数据。
- 更新地址为 heyuyang04-ui/hungry-nicole；旧上游资源发布工作流已移出当前仓库。自动更新默认关闭。
- 旧独立生图素材与脚本不再发布，本地归档保留。视频转换只用 tools/video_webp.py 编码，不做姿态插值。
- 参考图、API 回执、日志、聊天、数据库不上传。动画仅放 Releases；源码版不是验证过的 Windows/macOS 安装器。
