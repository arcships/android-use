# 验证记录

日期：2026-11-03。手机序列号、账号内容和截图不提交到源码中。

## 已通过

- macOS / Python 3.13.7，`uv sync --frozen`、构建 wheel/sdist、Ruff 检查。
- `uv run pytest -q`：29 项通过。除控制通道原有覆盖外，增加视频分帧/大小边界、无 viewer 不启动采集、缓存帧不占手机操作锁、暂停清除帧及拒绝迟到帧、锁屏不采集、可见租约到期停流、旋转后重建编码尺寸，以及 MCP UI 资源和 App 专属工具元数据验证。
- 官方 MCP 客户端子进程完成 initialize、list_tools、doctor、无效参数和缺少设备错误；不是仅直接调用 Python 函数。
- Android 16 / API 36 / Xiaomi 15 Ultra 真机：USB 设备发现、JAR 冷启动、跨 CLI 调用服务复用、READY、控件树与截图、App 启动、选择器点击设置搜索框、真实焦点回读。
- 无线 ADB 连接经硬件序列号确认与 USB 是同机；工具拒绝不明确的多端点自动选择，断开的指定端点也不自动换机。显式指定无线连接后 READY 通过。
- `uv run python scripts/smoke_mcp.py`：真实 MCP stdio 通道返回 READY、18 个工具、1080×2400 视口及 1 个原生 MCP 图片块；观察 JSON 中没有图片 base64 字段。0.2 增加 MCP App HTML resource、稳定 widgetSessionId、原生视频帧工具的端到端检查；视频帧通过 structuredContent 返回给 App，content 为空，不产生模型消息中的 base64。
- Android Use 技能通过 skill-creator 的 `quick_validate.py`（验证器临时补充 PyYAML 依赖）。
- 插件 ZIP 包内不含虚拟环境、设备配置、手机截图、node_modules 或浏览器测试产物。包含已编译的 MCP App HTML，无需在用户机器上安装 Node 才能显示。
- 用户确认可短暂独占手机后，完成设置搜索框实测：“蓝牙”完整回读成功，追加“设置🐻”后截图正确，后续原生 getText 确认 emoji 保留；继续追加“追加”且精确验收通过。重新输入“中文🐻”及清空，分别回读 3 个 Unicode 字符和 0 个字符，批处理三步完整执行。
- 设置列表单次 up 滑动成功：WLAN 的 y 从 1257 → 92，移动网络从 1565 → 400，多个锚点同向移动约 1165 显示像素；出现“系统个性化、锁屏、通知与状态栏、桌面、显示与亮度”等新行，截图人工核对通过。没有点击或改变这些设置值。
- 前端 `npm test`：6 项通过，实际 Chrome 验证 Live 显示/坐标映射、认证时清除图像并禁用动作、工具栏和 PNG 图片复制、断线重连、原生 MCP App 在 iframe 中的握手与 App 专属工具调用。
- 在 ui 目录运行 `ANDROID_SERIAL=<设备> node scripts/live-smoke.mjs`：真机视频序号持续增加、540×1200 视频被 Chrome 正确解码、缓存帧保存成功、认证 pause 后前端移除图像且手机端 screenrecord 进程退出、resume 恢复 Live、Home 后视频画面变化，最后恢复 DimRemote。测试关闭后超过 5 秒，确认手机端采集进程自动停止。
- 本地插件市场 android-use-local 已注册，android-use 0.2.1 已安装；当前已有聊天的工具列表需要宿主加载更新后才能直接调用，右侧浏览器面板可立即使用相同预览服务。
- 已在当前聊天的可见 in-app browser 打开同一视频前端，实际 UI 显示 Live、设备型号、真实 DimRemote 画面和可用工具栏，面板已标记为交付物以在本轮结束后保留。
- 侧边栏 UI 改为中性色、细边框、大画面、统一 SVG 图标与紧凑工具栏；操作提示以自动消失的浮层显示，复制画面后提示“画面已复制”。前端交互检查通过，当前面板刷新后仍为 Live。

## 实机发现并修复的差异

UiAutomator 的 XML dump 将非 BMP emoji 替换为两个点，例如“蓝牙设置🐻”变成“蓝牙设置..”。如果使用 XML 字段做追加，会损坏草稿；用它做精确验收也会误报。现改用原生 getText 读取追加前文本和输入后内容，保留树查询对焦点、包名、resource ID 和密码属性的检查。另将原生 getText 对空字段返回的 null 规范化为空字符串。两种差异均有实机结果与回归测试。

视频采用全范围 yuvj420p、单线程 MJPEG 编码及低延迟时间戳选帧，避免部分输入色彩范围被 MJPEG 拒绝和 fps 滤镜等待后续帧的问题。ADB exec-out 与 shell 的参数处理不同，设备视频包装脚本按 exec-out 参数原样传递，避免将整段脚本误当成一个被引用的可执行文件名。

## 实测范围外

Windows、Linux、模拟器与其他厂商设备尚未实测。自绘/富文本输入、超长文本与旋转中的动作没有完整验收；视频旋转的重建逻辑已通过模拟元数据测试，未实际旋转此手机。控件树中的 emoji 仍可能显示为点，视觉内容应以截图为准。

## 0.2.1 剪贴板验收

在当前可见侧边栏点击“复制画面”，提示“画面已复制”；随后读取 macOS NSPasteboard 的类型与数据大小，确认 public.png 类型存在且 PNG 数据非空。前端测试校验写入的是 PNG 文件签名，并且没有触发保存文件请求。复制功能使用当前显示帧，不请求额外手机截图。
