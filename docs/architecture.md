# 实现说明

## 从 iPhone Use 学到的部分

阅读了本机安装版本 0.3.17 的 README、技能、MCP 入口 `server/iphone_use.py`、传输 `wda_client.py` 和插件清单。它用本机 MCP 组织手机任务，通过 USB 把 HTTP 请求送给 XCTest/WebDriverAgent，读取 accessibility snapshot 和截图，执行原生手势；预览使用独立的 MJPEG 通道。setup 负责构建/签名/启动与转发，READY 比单纯的端口存活检查更严格。宿主侧负责选择器消歧、观察压缩、截图坐标映射、串行化与不确定动作语义。

Android 可以用 ADB 承担设备发现、连接和启动，UiAutomator2 提供 UiAutomation HTTP RPC。采用现有开源设备服务，避免重新开发一套权限、输入与树采集能力。这里独立实现 Android 宿主逻辑与技能，没有复制 iPhone Use 的 Python 源码或 UI。

## 模块边界

- `backend.py`：ADB 参数引用、设备选择、服务启动、跨进程锁，UiAutomator2 单次 RPC 适配。
- `tree.py`：XML → 紧凑节点、可见范围、精确/包含选择器、屏幕坐标。
- `models.py`：共享 Pydantic 参数模型，禁止额外字段；batch 在动作前完整校验。
- `runtime.py`：READY、认证暂停、上下文令牌、动作、不确定错误与批处理续接位置。
- `server.py` / `cli.py`：同一个 Runtime 的 MCP / CLI 两种入口。MCP 返回文字和原生图片块，CLI 保存图片后返回路径。
- `screen.py`：独立 screenrecord H.264 → ffmpeg JPEG 视频管道、仅内存帧缓存、可见 viewer 租约、锁屏/认证暂停、按 PID 与内核启动时间核验的远端进程清理。
- `preview_daemon.py`：带随机令牌、Host/Origin 检查的 127.0.0.1 传输层，同一物理设备的 MCP 与浏览器预览共享同一个视频流；不对局域网提供服务。
- `ui/`：使用 MCP Apps SDK 的手机屏幕资源，READY/screen 通过 resourceUri 与稳定 widgetSessionId 关联到同一个聊天侧边栏；独立浏览器模式复用相同前端。

服务启动与每次 UI 动作分离；普通读和动作都不会自动启动或重启服务。依赖固定为 uiautomator2 3.7.1，因为单次 RPC 适配调用了上游内部接口。升级前必须复验错误/断连不重放，而不是只测成功路径。

设备端使用默认 9008 端口，通过 ADB socket 传输，不对局域网额外开启宿主 HTTP 服务。若已有兼容 UiAutomator2 则复用；其他自动化工具可能竞争同一个 UiAutomation 会话，应先关闭冲突工具，不按端口随意杀进程。

视频绕过 UiAutomator2 控制通道，不用 XML 或 screenshot 轮询。屏幕长边限制在 1200 编码像素、2Mbps H.264，解码后按输入时间戳选取约 10fps，并立即输出 JPEG。显式使用 full-range yuvj420p 和单线程 MJPEG 编码，避免不同手机编码器的色彩范围差异导致编码失败；不使用会等待后续帧的 fps 滤镜来生成首帧。帧和视频都不落盘；用户点击“复制画面”时，前端将当前显示帧转为 PNG 并通过用户手势写入剪贴板，MCP UI 资源声明 clipboardWrite 权限。

## 依据

- [Android ADB 文档](https://developer.android.com/tools/adb)
- [UiAutomator2 源码与设备服务原理](https://github.com/openatx/uiautomator2)
- [Android UiAutomator 服务](https://github.com/openatx/android-uiautomator-server-jar)
- [MCP Python SDK](https://github.com/modelcontextprotocol/python-sdk)
- [OpenAI MCP App UI 接入说明](https://developers.openai.com/plugins/build/chatgpt-ui)

这些项目的许可证仍由各自项目提供。JAR 来自已锁定的 UiAutomator2 依赖包，不在本仓库另行分发二进制。
