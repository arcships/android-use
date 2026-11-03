<div align="center">
  <h1><img src="docs/images/hero.png" alt="Android Use：通过 MCP 将 AI agent 与真实 Android 手机连接起来。" width="100%" /></h1>
  <p>标准 MCP 服务，连接真实 Android；实时画面，让操作过程看得见。</p>
  <p>
    <a href="https://github.com/JackCaow/android-use/actions/workflows/test.yml"><img src="https://github.com/JackCaow/android-use/actions/workflows/test.yml/badge.svg" alt="自动化测试" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT 许可证" /></a>
    <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11 及以上" /></a>
    <a href="#mcp-客户端支持"><img src="https://img.shields.io/badge/MCP-stdio-3DDC84" alt="标准 stdio MCP" /></a>
  </p>
  <p><a href="README.md">English</a> · <strong>简体中文</strong></p>
  <p><a href="#快速开始"><strong>开始使用 →</strong></a> · <a href="docs/guide.zh-CN.md">完整指南</a> · <a href="docs/guide.zh-CN.md#其他-mcp-客户端">客户端配置</a> · <a href="https://github.com/JackCaow/android-use/issues">反馈问题</a></p>
</div>

---

## 从一句意图，到真实操作

Android Use 让 agent 能在你自己的 Android 手机或模拟器上完成 **读屏、定位、操作、等待与核对结果**。通过 USB 或已授权的无线 ADB 连接，在你熟悉的 MCP 客户端中使用。

> “打开设置，找到显示与亮度，读取当前设置，不修改它。”
>
> “在当前输入框中输入 `你好，Android 🐻`，核对内容，先不要发送。”

**不需要 root、Appium、签名配置或键盘 APK。** 仓库已包含编译好的实时屏幕界面，只有开发前端时才需要 Node.js。

## 为 Agent 的操作流程设计

| 能力 | 你能得到什么 |
| --- | --- |
| **理解当前界面** | 紧凑控件树、截图、语义选择器和有界等待。 |
| **执行精确操作** | 点击、长按、双击、滑动、拖拽、启动 App 与系统按钮。 |
| **减少工具往返** | 一次调用合并动作、控件出现 / 消失条件与观察；自动滚动查找受次数和时间限制。 |
| **输入真实文本** | 中文、emoji、替换、追加与原生回读验证，默认不提交。 |
| **实时查看过程** | 独立 H.264 视频，在 MCP Apps 界面或本机浏览器查看；当前画面直接复制到剪贴板。 |
| **清楚地交接控制** | 认证时暂停读屏、操作与视频；条件未满足就停止批处理，不自动重放不确定动作。 |

## 看得见的实时画面

<table>
  <tr><th align="center">浅色</th><th align="center">深色</th></tr>
  <tr>
    <td align="center"><img src="docs/images/sidebar-light.jpg" alt="Android Use 浅色实时屏幕界面" width="260" /></td>
    <td align="center"><img src="docs/images/sidebar-dark.jpg" alt="Android Use 深色实时屏幕界面" width="260" /></td>
  </tr>
</table>

上图使用实际前端渲染，手机内容为文档示例画面。界面随系统明暗主题切换，支持点击、滑动、返回、Home，以及复制 PNG 画面到聊天或文档。[实时屏幕使用说明 →](docs/guide.zh-CN.md#实时屏幕与图片复制)

## 快速开始

### 让 Agent 自己安装

将下面整段发送给**能使用本机终端的 agent**：

```text
请在我的电脑上安装并配置 Android Use：
https://github.com/JackCaow/android-use

先阅读 README.md、docs/guide.zh-CN.md 和 skills/android-use/SKILL.md。
复用已有安装，保留未提交修改和其他 MCP 服务配置。
检查依赖，从官方来源补齐缺项，为当前客户端配置本地 stdio MCP；
如果使用 Codex，则打包并安装插件，其他客户端跳过插件打包与市场注册。
发现设备、明确选择目标，复用健康服务或启动一次，实际验证 ready=true。
打开实时屏幕并检查画面；工具尚未加载时先用 CLI 配置，再说明如何重新加载。
请实际完成安装，不要只给我手动操作教程。
USB 授权、解锁和必要系统权限由我本人在手机上处理。
最后报告安装结果、已通过的检查和剩余步骤。
```

客户端不能使用终端时，按下方步骤手动安装。[Agent 安装详细流程 →](docs/guide.zh-CN.md#让-agent-自动安装)

### 或者手动安装

**1. 准备工具并克隆仓库。** 安装 [uv](https://docs.astral.sh/uv/getting-started/installation/)、[ADB](https://developer.android.com/tools/releases/platform-tools)、[ffmpeg](https://ffmpeg.org/download.html) 和 Git；uv 可按需安装 Python 3.13。

```bash
git clone https://github.com/JackCaow/android-use.git
cd android-use
uv sync --frozen --python 3.13
```

**2. 连接客户端。** 选择一种方式：

<details>
<summary><strong>Codex 插件</strong> — MCP 工具、使用技能与实时屏幕集成</summary>

安装 Codex CLI 后，在仓库根目录运行：

```bash
uv run python scripts/package.py
codex plugin marketplace add .
codex plugin add android-use@android-use-local
```

打包会生成本地市场所需的 `dist/android-use`。在新聊天中启用 Android Use，或重新加载工具。[Codex 安装指南 →](docs/guide.zh-CN.md#安装-codex-插件)

</details>

<details>
<summary><strong>标准 MCP</strong> — Claude Desktop、Cursor、VS Code 及其他本地 stdio 客户端</summary>

无需安装 Codex CLI 或打包插件。使用 `mcpServers` 的客户端可合并下方配置，并替换仓库绝对路径：

```json
{
  "mcpServers": {
    "android_use": {
      "command": "uv",
      "args": ["run", "--frozen", "--directory", "/absolute/path/android-use", "android-use-mcp"]
    }
  }
}
```

Cursor 还需 `"type": "stdio"`；VS Code 的 `.vscode/mcp.json` 顶层字段是 `servers`。参见[各客户端的文件位置与完整配置 →](docs/guide.zh-CN.md#其他-mcp-客户端)。

</details>

**3. 连接手机。** 开启 USB 调试，使用数据线连接，在手机上接受授权，并保持亮屏、解锁：

```bash
adb devices -l
uv run android-use setup '{"action":"status"}'
# 仅当控制服务未启动时执行：
uv run android-use setup '{"action":"start"}'
uv run android-use ready
```

实际返回 **`ready=true`** 后才代表控制通道就绪。多设备时[明确选择目标](docs/guide.zh-CN.md#3-多设备或切换-usb--无线连接)，然后对 agent 说：

> “连接我的 Android，验证 READY，再打开实时手机屏幕。”

[完整安装与故障排查 →](docs/guide.zh-CN.md)

## MCP 客户端支持

核心是基于[官方 Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)的**标准本地 stdio MCP Server**。Codex 插件只是可选的安装封装。

| 接入方式 | 能力与要求 |
| --- | --- |
| **本地 stdio MCP** | 手机控制工具及标准文本 / 图片返回。Claude Desktop、Cursor、VS Code / Copilot 等使用各自的[本地命令配置](docs/guide.zh-CN.md#其他-mcp-客户端)。 |
| **MCP Apps** | 宿主支持该扩展时，可内嵌实时屏幕；布局和剪贴板权限取决于宿主。 |
| **本机浏览器** | 无内嵌界面时，执行 `uv run android-use screen`，打开返回的本机 URL。 |

视频预览 URL 是私有的本机画面地址，**不是远程 MCP 端点**；仅支持远程 URL 的客户端不能直接接入本版。各客户端已有配置示例，但尚未对所有客户端 / 版本完成端到端验收。

## 更短的操作循环，明确的执行结果

```bash
# 一次调用：点击 → 等待可见条件 → 返回控件树。
# 标签需按当前页面填写。
uv run android-use tap '{"selector":{"text":"显示与亮度"},"wait_for":{"selector":{"text_contains":"亮度"},"timeout_seconds":5},"observe":"tree"}'

# 有次数和时间上限的列表查找；找到后不自动点击。
uv run android-use scroll_until '{"selector":{"text":"显示与亮度"},"direction":"up","max_swipes":8,"timeout_seconds":20}'
```

动作可携带后置条件；等待超时会报告结果并停止批处理，不重复执行动作。自动滚动在找到目标、达到上限或连续无进展时停止。[手势、等待、批处理与全部 22 个工具 →](docs/guide.zh-CN.md#cli-使用)

## 项目状态

**v0.3.0 · MIT · 69 项 Python 测试 · 6 项浏览器测试。** 已在 Android 16 / Xiaomi 15 Ultra 上验收原生手势事件、延迟控件、自动滚动与暂停恢复。[验收证据与范围 →](docs/validation.md)

多指缩放、任意轨迹、精细文本选区、自绘编辑器输入兜底尚未实现。设备和 App 行为存在差异，UI 条件满足不等于业务已完成。控制服务和预览运行于本机；工具结果由你选择的 MCP 客户端 / 模型处理。

## 文档与贡献

| 开始使用 | 深入了解 |
| --- | --- |
| [中文使用指南](docs/guide.zh-CN.md) · [English guide](docs/guide.md) | [实现说明](docs/architecture.md) |
| [客户端配置](docs/guide.zh-CN.md#其他-mcp-客户端) · [故障排查](docs/guide.zh-CN.md#常见问题) | [真机验收记录](docs/validation.md) |
| [开发与测试](docs/guide.zh-CN.md#开发与验证) · [提交问题](https://github.com/JackCaow/android-use/issues) | [MIT 许可证](LICENSE) · [第三方声明](THIRD_PARTY_NOTICES.md) |

欢迎反馈问题与贡献代码。请提供电脑系统、Android 版本、客户端和最小复现步骤，并移除设备标识、访问令牌和私人画面。
