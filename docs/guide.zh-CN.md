# Android Use 使用指南

[English](guide.md) · **简体中文** · [返回项目首页](../README.zh-CN.md)

[安装](#安装) · [首次连接手机](#首次连接手机) · [Codex 插件](#安装-codex-插件) · [其他客户端](#其他-mcp-客户端) · [CLI](#cli-使用) · [故障排查](#常见问题)

## 标准 MCP 与兼容性

Android Use 的核心是使用[官方 Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk)实现的**标准本地 stdio MCP Server**。客户端启动 `android-use-mcp`，通过 MCP 初始化、工具发现与调用、资源读取等标准接口连接；无需安装 Codex 插件即可使用手机控制工具。

| 层次 | 接入要求与行为 |
| --- | --- |
| MCP 手机控制 | 支持本地 stdio MCP 的客户端可调用 `aua_*` 工具；截图以 MCP 图片内容返回，显示与模型识图能力取决于客户端 |
| MCP Apps 实时界面 | 使用 [MCP Apps 扩展](https://modelcontextprotocol.io/extensions/apps/overview)的 `ui://` 资源；宿主支持该扩展时可内嵌手机画面，具体呈现为聊天卡片、面板还是侧边栏由宿主决定 |
| 本机浏览器预览 | 客户端不渲染 MCP Apps 时，在仓库根目录执行 `uv run android-use screen`，用本机浏览器打开返回的 URL，控制工具仍通过 MCP 使用 |
| Codex 插件 | 将 MCP 配置、使用技能和界面信息打包，方便在 Codex 安装；其他 MCP 客户端不需要本地市场或插件打包步骤 |

当前只提供 **stdio** MCP 传输，未提供远程 HTTP / SSE MCP 端点。本机视频预览的 HTTP URL 不是 MCP 服务地址，不能填入客户端的远程 MCP URL 配置。运行 MCP 进程的电脑需要能通过 ADB 访问目标设备；仅支持远程 URL 的客户端不能直接接入本版。

下文提供各客户端官方配置格式的示例。项目已有标准 MCP stdio 握手、工具调用和资源读取测试；Claude Desktop、Cursor、VS Code 的具体版本尚未逐一完成端到端验收，内嵌视频、剪贴板权限和界面布局需以实际宿主表现为准。

## 让 Agent 自动安装

推荐把下面整段指令发给**能够使用本机终端的 Codex agent**。依赖检查、下载、打包、插件注册和设备配置由 agent 完成；你只需配合连接手机、接受 USB 调试授权，以及在需要时亲自解锁。

```text
请在我的电脑上安装并配置 Android Use：
https://github.com/JackCaow/android-use

先阅读 README.md、docs/guide.zh-CN.md 和 skills/android-use/SKILL.md，然后实际完成安装与验收。
检查当前系统、已有仓库、插件和依赖；复用现有安装，只补齐缺项，不覆盖未提交修改。
从官方来源安装缺失的 Git、uv、Python 3.11+、ADB、ffmpeg 和所需的 Codex CLI。
克隆或复用仓库，安装锁定依赖，生成 dist/android-use，再注册本地市场并安装插件。
检查 ADB 设备，只有一个明确目标时直接配置；目标不明确时让我选择。
复用健康控制服务，缺少服务时启动一次，完成 READY 验证并打开实时手机屏幕。
如果当前聊天还没加载新工具，先用仓库 CLI 完成可执行的配置和验收，并说明如何在新聊天使用插件。
不要只返回一份让我手动操作的教程；请完成你能执行的步骤。
只有 USB 授权、解锁、系统权限或确实缺少必要信息时再让我接管。
最后报告安装目录、插件版本、选定设备、READY 结果、实时画面是否已验证，以及仍需我完成的步骤。
```

### Agent 的执行顺序

1. **检查并复用环境。** 检查 `git`、`uv`、`adb`、`ffmpeg` 和 `codex`，按本机系统补齐缺失依赖。Python 推荐使用下方已验证的 3.13；普通安装无需 Node.js。仓库已存在时先检查状态，保留用户未提交内容。
2. **在仓库根目录安装并打包。** 执行 `uv sync --frozen --python 3.13`，再执行 `uv run python scripts/package.py`。确认 `dist/android-use/.codex-plugin/plugin.json` 和 `.mcp.json` 已生成；市场指向此目录，不能省略打包步骤。
3. **安装并核对插件。** 检查现有市场后，在该仓库根目录执行 `codex plugin marketplace add .` 和 `codex plugin add android-use@android-use-local`；用下面的命令核对本地市场来源、已安装插件版本及路径。若已有同名市场指向另一个目录，先核对已有安装，避免重复克隆或覆盖无关配置。

   ```bash
   codex plugin marketplace list
   codex plugin list --json
   ```

4. **发现并选择设备。** 执行 `adb devices -l` 和 `uv run android-use setup '{"action":"discover"}'`。复用用户已指定的目标；没有明确目标时，仅在唯一可用设备可确认的情况下直接选择。多台手机需要用户选择，`unauthorized` 需要用户在手机上授权；不要猜序列号或将设备信息提交到仓库。
5. **保存设备配置。** 确定实际目标后执行 `uv run android-use setup '{"action":"configure","serial":"SERIAL"}'`，将 `SERIAL` 替换为真实设备标识，再执行 `uv run android-use setup '{"action":"status"}'`。配置只保存在用户本机。
6. **启动并验证控制通道。** 复用健康服务；确认服务未启动时调用一次 `setup '{"action":"start"}'`，随后执行 `uv run android-use ready`。必须实际得到 `ready=true`，不能把“插件已安装”当作“手机已就绪”。启动报错时按日志处理缺项，不循环重装或盲目重启。
7. **打开并核对实时画面。** 已加载 MCP 工具时调用 `aua_screen()`；尚未加载时执行 `uv run android-use screen`，在可用的浏览器 / Codex 右侧面板中打开返回的本机 URL，并检查实际画面与 Live 状态。不能只凭拿到 URL 宣称视频已验证；预览问题按下方故障排查处理。
8. **报告实际验收状态。** 分别说明依赖、插件安装、设备配置、控制 READY、视频画面的结果；没有手机连接时，仍可完成插件安装，并准确列出待连接 / 授权的步骤。当前聊天未热加载插件时，说明新聊天需启用 Android Use，不把 CLI 成功当作原生 MCP 工具已经加载。

自动配置只完成安装、连接与读屏验收；完成后等待用户给出具体手机任务。密码、PIN、验证码和生物认证始终由用户本人完成，不索取这些凭据。

如果你希望亲自操作，可以继续阅读下面的手动安装与使用步骤。

## 安装

### 1. 准备依赖

| 依赖 | 用途 |
| --- | --- |
| Git | 克隆和更新仓库 |
| Python 3.11+ | 运行本机服务；下面使用已验证的 Python 3.13 |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | 安装 Python、管理锁定依赖和启动命令 |
| [Android SDK Platform Tools](https://developer.android.com/tools/releases/platform-tools) | 提供 `adb`，连接 USB / 无线调试设备 |
| [ffmpeg](https://ffmpeg.org/download.html) | 解码实时屏幕视频 |
| Codex 客户端及 CLI | 安装 Codex 插件时需要；只使用 CLI 或其他 MCP 客户端时可跳过 |

macOS 已安装 Homebrew 时，可以执行：

```bash
brew install uv ffmpeg
brew install --cask android-platform-tools
```

Windows / Linux 可以按上表官方链接安装依赖，将 `uv`、`adb` 和 `ffmpeg` 加入运行客户端的 `PATH`。以下终端示例使用 Bash / zsh。

检查依赖是否可用：

```bash
uv --version
adb version
ffmpeg -version
```

### 2. 克隆并安装项目

```bash
git clone https://github.com/JackCaow/android-use.git
cd android-use

uv sync --frozen --python 3.13
uv run android-use --help
```

`uv` 会按需下载 Python 和项目依赖。后续命令默认在此仓库根目录运行。

目前已在 **macOS + Android 16 / Xiaomi 15 Ultra** 上完成真机验证；Linux CI 覆盖自动化测试，Windows、Linux 真机和其他厂商设备尚未验收，详见[验证记录](validation.md)。

## 首次连接手机

### 1. 开启并授权 USB 调试

1. 在手机上开启“开发者选项”和“USB 调试”。
2. 用支持数据传输的 USB 线连接电脑，在手机上确认信任 / 调试授权。
3. 保持手机亮屏并解锁。部分厂商还需要开启“USB 调试（安全设置）”才能注入点击和输入。

在电脑上检查：

```bash
adb devices -l
```

目标设备应显示 `device`。如果显示 `unauthorized`，先在手机上接受调试授权；没有设备则检查线缆、USB 模式或驱动。完整连接步骤参考 [Android ADB 官方文档](https://developer.android.com/tools/adb)。

### 2. 检查并启动控制服务

```bash
uv run android-use doctor
uv run android-use setup '{"action":"status"}'
```

服务未启动时执行：

```bash
uv run android-use setup '{"action":"start"}'
```

`start` 会推送依赖包内的 UiAutomator2 JAR 并启动服务；已有可用服务会被复用。无需每次任务重复安装或启动。

最后验证：

```bash
uv run android-use ready
```

输出中应包含：

```json
{
  "ok": true,
  "ready": true,
  "state": "ready"
}
```

完整结果还会返回设备、前台 App、视口和当前控件树。只有 `ready=true` 才证明控制通道可用；单纯发现设备或端口开放不足以证明 READY。

### 3. 多设备或切换 USB / 无线连接

列出候选设备，再明确选择目标：

```bash
uv run android-use setup '{"action":"discover"}'
uv run android-use setup '{"action":"configure","serial":"SERIAL"}'
uv run android-use ready
```

把 `SERIAL` 替换为 `adb devices -l` 显示的设备序列号或无线 ADB 地址。配置会保存在本机，后续调用复用该目标。

也可以只为单次 CLI 指定设备；全局选项放在子命令之前：

```bash
uv run android-use --serial SERIAL ready
```

工具也支持 `ANDROID_SERIAL` 环境变量。多台设备不会自动选择第一台；指定目标断开时，不会静默切到另一台。USB 与无线地址若属于同一台手机，会共享操作锁和认证暂停状态。

无线方式需要先按 Android 的无线调试流程完成配对，并确认设备已出现在 `adb devices -l` 中；本工具不替你开启无线调试或输入配对凭据。

## 安装 Codex 插件

可选发行包或源码安装。已从源码使用 `android-use-local` 时，直接更新原安装，避免同时启用两份工具。

### 安装发行包 ZIP

从 [GitHub Release v0.3.0](https://github.com/JackCaow/android-use/releases/tag/v0.3.0) 下载 `android-use-0.3.0-plugin.zip` 和 `SHA256SUMS`。ZIP 已含编译好的界面、Python 源码与锁文件、技能、双语文档和可注册的本地市场；不包含 Python、ADB、ffmpeg，先按上文准备依赖。首次安装需要联网下载 Python 依赖。

macOS / Linux 下载后执行：

```bash
# 先校验下载文件；Linux 也可使用 sha256sum -c SHA256SUMS。
shasum -a 256 -c SHA256SUMS
unzip android-use-0.3.0-plugin.zip -d android-use-v0.3.0
cd android-use-v0.3.0
uv sync --frozen --python 3.13
codex plugin marketplace add .
codex plugin add android-use@android-use-release
```

Windows 可用解压工具展开 ZIP，在该目录打开终端执行 uv / Codex 命令；Windows 真机操作尚未完成验收。安装后重新加载工具或新建聊天，启用 Android Use，再完成[手机连接与 READY](#首次连接手机)。发行包市场名为 `android-use-release`，源码市场仍为 `android-use-local`。ZIP 安装无需克隆仓库或再次运行打包脚本。

### 从源码安装

先完成上面的项目安装。在仓库根目录执行：

```bash
uv run python scripts/package.py
codex plugin marketplace add .
codex plugin add android-use@android-use-local
```

**不要跳过打包。** 本地市场的插件目录指向 `dist/android-use`，由 `scripts/package.py` 生成；该目录不随 Git 仓库提交。

安装后在客户端开启新聊天，选择 / 启用 Android Use。若工具未出现，重新加载客户端；支持本地插件目录的桌面客户端也可以在插件设置中选择 `Android Use Local` 来源。可用以下命令检查市场：

```bash
codex plugin marketplace list
```

本项目的本地市场与插件结构遵循 [OpenAI 插件文档](https://developers.openai.com/plugins/build/plugins)；上面的 CLI 安装命令也已在本项目验证。

可以直接对 agent 说：

> 连接我的 Android，检查 READY，然后打开实时手机屏幕。

> 打开系统设置，找到显示与亮度页面，读取当前设置，不修改它。

> 在当前已聚焦的搜索框中输入“中文测试 🐻”，核对文本，不提交。

`aua_ready()` 会关联本聊天的实时屏幕；单独打开或重新打开面板用 `aua_screen()`。原生侧边栏需要客户端支持 MCP Apps，普通 stdio MCP 客户端仍可使用控制工具和下面的本机浏览器预览。

## 实时屏幕与图片复制

也可以直接从终端打开预览服务：

```bash
uv run android-use screen
```

把返回的 `url` 在本机浏览器或 Codex 右侧浏览器面板中打开。预览地址只监听 `127.0.0.1`，包含随机访问令牌，请勿公开分享该地址。

工具栏从左到右：

| 控件 | 行为 |
| --- | --- |
| 返回 | 发送一次 Android 返回操作 |
| 主屏幕 | 回到手机 Home |
| 复制画面 | 将当前显示帧转为 PNG，直接写入系统剪贴板 |
| 重新连接 | 重建视频连接 |

复制成功会提示“画面已复制”，随后可在支持图片粘贴的聊天、文档或编辑器中按 **⌘V / Ctrl+V**。这不是复制文件路径，也不会通过工具栏生成截图文件；若浏览器阻止剪贴板写入，界面会提示失败。

点击画面可定位，拖动可发出滑动操作；坐标由当前画面映射到 Android 显示像素。

视频使用独立的 `screenrecord → H.264 → ffmpeg` 通道，前端读取内存中的视频帧缓存，不循环请求手机截图或控件树。关闭 / 隐藏面板后，5 秒无人续租即停止采集；认证暂停时也会清除画面并停止采集。

## CLI 使用

### 读取界面与截图

```bash
uv run android-use observe '{"mode":"tree"}'
uv run android-use observe '{"mode":"both"}'
```

`tree` 返回控件树，`screenshot` 返回图片，`both` 同时返回两者。CLI 截图文件的绝对路径在 `image_paths` 中；MCP 则直接返回图片块。

截图像素乘 `image.pixel_to_device` 得到动作需要的 **Android 显示像素**，不能直接使用 dp 或宿主窗口坐标。

### 启动 App、定位与点击

```bash
uv run android-use launch_app '{"package":"com.android.settings","observe":"tree"}'
uv run android-use find '{"selector":{"text":"显示与亮度"}}'
uv run android-use tap '{"selector":{"text":"显示与亮度"},"observe":"tree"}'
```

`text`、`description`、`resource_id`、`class_name` 等选择器值应照当前控件树填写；上面的中文文字仅为示例，不同系统可能不同。同名目标会返回候选项，用 `index` 明确选择。

已知坐标时也可以点击：

```bash
uv run android-use tap '{"x":200,"y":400,"observe":"tree"}'
```

`apps` 只查询已安装的第三方**包名**，不按 App 显示名称查找；启动 App 时请使用已确认的包名。

### 动作、条件等待与观察合并

从 0.3.0 起，`tap`、`long_press`、`double_tap`、`drag`、`swipe`、`type_text`、`press_button` 和 `launch_app` 都支持可选的 `wait_for`。无需分别调用动作、等待和读树三个工具：

```bash
uv run android-use tap '{"selector":{"text":"显示与亮度"},"wait_for":{"selector":{"text_contains":"亮度"},"state":"visible","timeout_seconds":5},"observe":"tree"}'
```

上例先点击，再等待目标出现在当前可见控件树中，最后返回该次观察。选择器需要按实际页面填写。`state` 为 `visible`（默认）或 `gone`，`timeout_seconds` 为 0–20 秒，默认 5 秒；0 表示只检查一次。`gone` 表示当前可见树中不再有匹配，不代表对象已从 App 数据中删除；空树也不作为消失证据。

查看 `wait.condition_met` 判断条件是否满足。超时时返回 `ok=false`、`error.code=postcondition_timeout`、`action_executed=true` 和 `replay_action=false`：动作已经得到执行确认，只是期望的 UI 条件未在时间内出现，**不要再次点击或重放整批动作**。条件满足也不自动证明消息已送达、订单已完成等业务结果。

不传 `wait_for` 时保持原有行为，不增加等待；`observe` 仍默认 `none`。树观察会复用等待结束时的快照，避免再 dump 一遍。等待并不保证动画已完全结束。需要独立等待时也支持相同的出现 / 消失条件：

```bash
uv run android-use wait '{"selector":{"text":"加载中"},"state":"gone","timeout_seconds":5}'
```

独立 `wait` 的 `found` 表示当前是否有可见匹配，`condition_met` 表示请求的条件是否满足；等待消失成功时两者分别为 `false`、`true`。

### 自动滚动查找

```bash
uv run android-use scroll_until '{"selector":{"text":"显示与亮度"},"direction":"up","max_swipes":8,"timeout_seconds":20,"observe":"tree"}'
```

`scroll_until` 先检查目标是否可见，已可见时零滑动返回；未找到时逐次滑动、观察，找到后停止，**不自动点击结果**。多处匹配会返回候选项，后续点击仍需消歧。嵌套列表或横向列表可传 `region={x,y,width,height}`，坐标为 Android 显示像素；区域应位于当前可滚动容器内。

| 参数 / 返回值 | 含义 |
| --- | --- |
| `max_swipes` | 0–20，默认 8；0 表示仅查询当前页 |
| `timeout_seconds` | 0–20，默认 20；限制查找时间 |
| `duration` / `settle_seconds` | 单次手势时长 / 手势后等待，均默认 0.4 秒 |
| `observe` | 默认 `tree`，返回最终观察；也可选 `none`、`screenshot`、`both` |
| `found` / `matches` / `swipes` | 是否找到、可见候选项、实际完成的滑动次数 |
| `stop_reason` | `found`、`max_swipes`、`timeout`、`no_progress` 或 `empty_tree` |

连续两次滑动后的区域快照不变时，以 `no_progress` 停止；这只是无进展保护，不证明列表到底或已遍历全量。前台 App、Activity 或屏幕尺寸变化会报 `scroll_context_changed`，锁屏、用户接管或不确定错误也会中止，不自动换页面继续。`ok=true, found=false` 表示查找已结束但未找到，不能当作目标存在。

### 长按、双击与拖拽

```bash
uv run android-use long_press '{"selector":{"text":"测试项目"},"duration":0.8,"observe":"tree"}'
uv run android-use double_tap '{"x":300,"y":600,"interval":0.08,"observe":"tree"}'
uv run android-use drag '{"start":{"x":200,"y":600},"end":{"x":700,"y":600},"duration":0.6,"observe":"tree"}'
```

这些坐标与控件文字仅为示例，执行前按当前观察确定目标。长按和双击接受与 `tap` 相同的选择器 / 坐标参数，拖拽接受明确的 `start` / `end` 坐标；都支持 `observation_id`、`wait_for` 和 `observe`，也可放入 `batch`。

长按 `duration` 为 0.5–3 秒（默认 0.8），拖拽为 0.1–3 秒（默认 0.6）。双击 `interval` 为 0.04–0.25 秒（默认 0.08），它不含两次 RPC 的传输耗时；高延迟连接或 App 自身手势策略可能使其被识别为两次单击。双击是一个有意的点击对，若中途失败可能已完成第一击，不能自动重试整对。

新手势提供于 MCP / CLI；实时预览的手动交互仍为点击和方向滑动。多指缩放、任意轨迹、键盘选区和自绘编辑器输入兜底尚未实现，不能视作覆盖所有 Android 交互。

### 输入中文、emoji 或追加文本

先通过手机界面或 `tap` 聚焦一个可编辑、非密码输入框，再执行：

```bash
uv run android-use type_text '{"text":"中文测试 🐻","verify":true}'
uv run android-use type_text '{"text":"，继续追加","replace":false,"verify":true}'
```

默认 `replace=true`，只输入、不提交。`verify=true` 使用原生 `getText` 回读完整内容；追加会保留已有 Unicode 文本。空字符串加默认替换可清空输入框。

换行需要明确设置 `allow_newlines=true`，但部分聊天 App 会把换行当作发送；密码、PIN、验证码和生物认证由用户本人完成。

### 滑动、返回与已知短路径

```bash
uv run android-use swipe '{"direction":"up","observe":"tree"}'
uv run android-use press_button '{"name":"back"}'
uv run android-use press_button '{"name":"home"}'
```

`direction` 是手指移动方向，`up` 通常查看列表后续内容。每次只滑一次；列表不再移动不等于已证明全量或到底。

已知短路径可合并为批处理：

```bash
uv run android-use batch '{"steps":[{"op":"launch_app","args":{"package":"com.android.settings"}},{"op":"observe","args":{"mode":"tree"}}]}'
```

批处理最多 20 步，先校验整批参数，支持上述新手势、动作等待和 `scroll_until`；遇错误、未满足等待条件、滚动未找到、Enter 提交或 25 秒时间预算即停止。等待和滚动共享批处理剩余预算；时间限制在操作之间检查，已经发出的 ADB / RPC 可能超出预算。

动作后的等待超时以 `postcondition_timeout` 停止，滚动未找到以 `scroll_not_found` 停止。已经返回结果的步骤会计入 `completed_steps`，`stopped_at` 指向下一步；因此计数不代表每步的业务条件都成功。动作发生不确定错误时，`stopped_at` 指向报错步骤，先观察实际结果。检查 `ok`、`stop_reason` 和 `results` 后续接，不能重放整批。

### 认证接管

```bash
uv run android-use pause
# 用户在手机上完成认证，并明确通知完成后：
uv run android-use resume
uv run android-use observe '{"mode":"tree"}'
```

`pause` 在该物理设备的 CLI、MCP 和视频预览之间共享。暂停期间不进行屏幕读取或手机控制；恢复后读取新状态，不使用接管前的坐标继续操作。

### 查看全部参数

```bash
uv run android-use tools
```

MCP 工具以 `aua_` 为前缀：

| 工具 | 用途 |
| --- | --- |
| `aua_doctor / aua_setup / aua_ready` | 设备发现、配置、启动与就绪检查 |
| `aua_observe / aua_find / aua_wait` | 控件树、截图、目标查询与有界等待 |
| `aua_tap / aua_swipe / aua_type_text` | 点击、滑动与 Unicode 输入 |
| `aua_long_press / aua_double_tap / aua_drag` | 长按、双击与明确起终点拖拽 |
| `aua_scroll_until` | 有界滚动查找可见目标，不自动点击 |
| `aua_apps / aua_launch_app / aua_press_button` | 包名查询、启动与系统按钮 |
| `aua_batch` | 有界批处理 |
| `aua_pause / aua_resume` | 用户认证接管 |
| `aua_screen` | 打开 / 复用实时屏幕侧边栏 |
| `aua_screen_frame / aua_screen_action` | App 专属视频缓存和工具栏接口，供 UI 使用 |

## 其他 MCP 客户端

### 1. 完成本机安装与设备准备

先按[安装](#安装)章节准备 Git、uv、Python、ADB 和 ffmpeg，克隆仓库并执行 `uv sync --frozen --python 3.13`，再按[首次连接手机](#首次连接手机)完成设备选择、USB 授权及 READY 验证。**无需安装 Codex CLI、生成 `dist/android-use` 或注册 Codex 插件。**

MCP 客户端负责启动 stdio 服务，不必先在另一个终端常驻运行 `android-use-mcp`。MCP 工具不会自动附带 Codex 插件中的技能；可以让 agent 阅读仓库的 `skills/android-use/SKILL.md`，按后面的首次使用提示完成连接。

希望由 agent 代装时，可在前面的[自动安装指令](#让-agent-自动安装)末尾补充：“我使用的是 Claude Desktop / Cursor / VS Code，请按 README 的对应客户端配置本地 stdio MCP，跳过 Codex CLI、插件打包与市场注册。合并现有配置，保留其他 MCP 服务。”将客户端名称改为实际使用的一个。

### 2. 配置客户端

以下路径均为占位示例。将 `/absolute/path/android-use` 改为实际仓库的**绝对路径**；Windows 可以写成 `C:/tools/android-use`，使用反斜杠时需按 JSON 规则写成 `C:\\tools\\android-use`。不要使用 `${PLUGIN_ROOT}`，它属于 Codex 插件环境。

GUI 客户端的 `PATH` 可能与终端不同。找不到 `uv` 时，将 `command` 改为 uv 可执行文件的绝对路径；ADB 和 ffmpeg 也必须对该进程可见。本项目支持用 `env` 中的 `ANDROID_USE_ADB`、`ANDROID_USE_FFMPEG` 指定这两个可执行文件的绝对路径。

#### Claude Desktop

在 **Settings → Developer → Edit Config** 打开 `claude_desktop_config.json`，把下面的 `android_use` 条目合并到已有的 `mcpServers` 中；若文件为空，可直接使用完整示例。保存后完全退出并重新启动 Claude Desktop，在连接器 / 工具列表中确认 Android Use 已加载。配置流程见 [MCP 官方本地服务指南](https://modelcontextprotocol.io/docs/develop/connect-local-servers)。

常见配置文件位置：

| 系统 | 路径 |
| --- | --- |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

```json
{
  "mcpServers": {
    "android_use": {
      "command": "uv",
      "args": [
        "run",
        "--frozen",
        "--directory",
        "/absolute/path/android-use",
        "android-use-mcp"
      ]
    }
  }
}
```

#### Cursor

按 [Cursor 官方 MCP 文档](https://cursor.com/docs/mcp)，可将配置保存到用户级 `~/.cursor/mcp.json`，或当前项目的 `.cursor/mcp.json`。合并下面的条目，保留已有服务；在 **Customize** 中启用该服务，必要时重启 Cursor，并确认 agent 的工具列表出现 `aua_*`。这是本机 stdio 连接；云端 agent 不会因此获得本机的 USB 设备访问能力。

```json
{
  "mcpServers": {
    "android_use": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run",
        "--frozen",
        "--directory",
        "/absolute/path/android-use",
        "android-use-mcp"
      ]
    }
  }
}
```

#### VS Code / GitHub Copilot

打开本地工作区，运行命令面板中的 **MCP: Add Server**，选择 **Command (stdio)**，输入下面的启动命令，服务 ID 填 `android_use`，再选择配置范围。按客户端提示确认启动并检查工具是否可用。见 [VS Code 官方接入指南](https://code.visualstudio.com/docs/agent-customization/mcp-servers)。

```bash
uv run --frozen --directory /absolute/path/android-use android-use-mcp
```

也可以手动编辑工作区的 `.vscode/mcp.json`。**这个文件的顶层字段是 `servers`，不是 `mcpServers`：**

```json
{
  "servers": {
    "android_use": {
      "type": "stdio",
      "command": "uv",
      "args": [
        "run",
        "--frozen",
        "--directory",
        "/absolute/path/android-use",
        "android-use-mcp"
      ]
    }
  }
}
```

若向导选择的是新版 **Copilot Global**，其默认文件为 `~/.copilot/mcp-config.json`（设置 `COPILOT_HOME` 时随之改变），使用前面的 `mcpServers` 格式；工作区根目录 `.mcp.json` 也使用该格式。不要把两种文件格式混用，详见[官方配置参考](https://code.visualstudio.com/docs/agents/reference/mcp-configuration)。在 Remote SSH、WSL 或容器中，配置路径、依赖与 ADB 都属于实际运行服务的环境，需要该环境能访问手机。

#### 其他支持本地 stdio 的客户端

使用客户端提供的“本地命令 / stdio”入口，启动命令为 `uv`，参数依次为 `run`、`--frozen`、`--directory`、仓库绝对路径、`android-use-mcp`。支持 `mcpServers` 配置的客户端可参考 Claude Desktop 示例；具体文件位置及是否需要 `type` 字段按其官方文档填写。

只提供“远程 MCP URL”的入口不能使用这条本地命令，也不能使用手机预览 URL 代替。

### 3. 首次使用与验收

重新加载客户端工具后，可以直接对 agent 说：

```text
请使用 Android Use MCP 连接我的 Android。
先调用 aua_setup，action 为 status；需要发现设备时使用 discover。
有多台设备且目标未指定时让我选择；服务未启动时只 start 一次，然后调用 aua_ready。
请实际调用工具并报告 ready 是否为 true、前台应用和工具返回的状态。
验证完成后读取一次当前界面，不执行其他手机操作。
```

客户端可能给工具加服务名前缀，以实际列出的名称为准。**MCP 已加载**以能发现并成功调用工具为准；**手机已就绪**以实际返回 `ready=true` 为准。没有设备时也能完成 MCP 安装，但不能报告手机验收成功。

需要实时画面时调用 `aua_screen`；宿主支持 MCP Apps 时可显示内嵌界面。若没有显示，在仓库根目录执行 `uv run android-use screen`，用本机浏览器打开返回的 URL。图片复制还需要浏览器或宿主允许图片剪贴板写入，具体操作见[实时屏幕与图片复制](#实时屏幕与图片复制)。

### 仅在 Codex CLI 注册 MCP

只想在 Codex CLI 注册 MCP、而不安装技能和插件清单时，也可以使用：

```bash
codex mcp add android_use -- uv run --frozen --directory /absolute/path/android-use android-use-mcp
```

如果已经安装 Codex 插件，不要再重复注册同一个 MCP 服务。

## 更新

在原来的本地仓库根目录执行：

```bash
git pull --ff-only
uv sync --frozen
uv run python scripts/package.py
codex plugin add android-use@android-use-local
```

更新插件安装副本后，在客户端新建聊天或重新加载工具。只使用 CLI 或其他 MCP 客户端的用户执行前两条即可；其他 MCP 客户端还需重启 / 重新连接该服务，通常无需修改指向原仓库的启动配置。

## 常见问题

| 现象 | 处理 |
| --- | --- |
| `adb` / `ffmpeg` 找不到 | 安装对应依赖并检查启动客户端时的 `PATH`；终端可见不代表 GUI 进程可见 |
| `unauthorized` | 在手机上接受 USB 调试授权，然后重新检查 `adb devices -l` |
| `device_selection_required` / 多个连接 | 用 `setup discover` 列出目标，再用 `configure` 明确选择序列号 |
| `device_unavailable` | 指定连接已断开；检查 USB / 无线连接，必要时重新选择已确认的同一设备 |
| `service_not_started` | 执行 `setup '{"action":"start"}'`，然后重新 `ready` |
| `phone_locked` / `screen_off` | 用户在手机上唤醒、解锁后，再验证 READY |
| `preview_dependency_missing` | 安装 ffmpeg，确保客户端能找到它，再重新打开预览 |
| 控制可用，视频未显示 | 检查连接、解锁状态及 ffmpeg；点击“重新连接”恢复视频，不因预览问题重复执行手机业务动作 |
| 复制失败 | 在支持图片剪贴板的浏览器 / MCP App 宿主中使用，允许剪贴板写入，再点击“复制画面” |
| `ambiguous_target` / 找不到控件 | 检查候选项或当前截图，处理遮挡；不要连续猜标签或盲点第一项 |
| 输入 / 提交超时，结果不确定 | 先读取实际状态，确认已完成部分后继续，不自动重发输入、消息或订单 |
| `postcondition_timeout` | 动作已执行，但后续 UI 条件未满足；观察当前页面并处理原因，不重放动作 |
| `scroll_until` 未找到 | 检查 `stop_reason`、最终观察与滚动区域；`no_progress` 不等于列表到底 |

## 原理、数据与边界

控制通道：`Agent → MCP stdio → Android Use → ADB / UiAutomator2 → 前台 App`。

视频通道：`Android screenrecord → H.264 / ADB → ffmpeg → 内存帧缓存 → 侧边栏`。

服务启动时将依赖包中的 JAR 推送到 `/data/local/tmp/android-use-u2.jar`；它与普通 UI 动作分离，动作超时不会自动重启并重放 RPC。详细实现见[架构说明](architecture.md)。

- 默认 `verified=false` 表示未额外验收，不等于动作失败；最终业务结果仍需要实际界面证据。
- 控件树可能缺少自绘内容、WebView 信息和未加载列表项，也不证明目标无遮挡；emoji 在 XML 中还可能被替换为点，视觉内容以截图为准。
- `setText` 需要 App 支持原生可编辑控件，自绘 / 富文本编辑器可能不支持；本版不内置输入法兜底。
- `observation_id` 只检查同一 Runtime 内的前台与视口上下文，不能跨 CLI 进程复用。
- 配置与 CLI 截图保存在 `~/.local/share/android-use`，可用 `ANDROID_USE_STATE_DIR` 调整；手机端启动日志在 `/data/local/tmp/android-use-u2.log`。
- 控制服务和视频预览运行在本机，本项目不上传遥测；工具返回的界面文本与截图会交由所选 MCP 客户端 / 模型处理，传输策略取决于该客户端。工具截图和侧边栏复制不会写入手机相册。
- 本版尚不包含相册系统截屏、长文本续传或 iOS 开发热重载。

## 开发与验证

Python 检查：

```bash
uv sync --frozen
uv run ruff check src tests scripts
uv run pytest
```

真机手势验收需要已授权、解锁的 Android、JDK 和 Android SDK（至少一个 API 28+ 稳定平台及包含 aapt、d8、zipalign、apksigner 的 Build Tools），先完成控制服务启动与 READY，然后运行：

```bash
uv run python scripts/accept_device.py --serial SERIAL --sdk /absolute/path/android-sdk
```

替换为实际设备和 SDK 路径。脚本临时构建并安装无权限测试 App，通过真实 MCP stdio 调用验收延迟控件出现 / 消失、原生长按 / 双击 / 拖拽事件、滚动查找及批处理停止；结束后卸载测试 App 并尝试恢复原前台 Activity。若设备已有同名验收包，脚本拒绝覆盖；运行期间请让测试独占手机。它不需要 Gradle，也不把手机数据或构建产物提交到仓库。

只有修改前端时才需要 Node.js。重建 UI 和运行浏览器测试：

```bash
cd ui
npm ci
npm run build
npx playwright install --with-deps chromium
PLAYWRIGHT_CHANNEL=chromium npm test
```

前端产物写入 `src/android_use/assets/phone-screen.html`，构建同时更新第三方许可声明。设备实测范围、已知差异和验收证据见[验证记录](validation.md)。

文档示例页可在仓库根目录启动，不连接真实手机：

```bash
node ui/scripts/readme-preview.mjs
```

它复用当前前端和 `docs/images/demo-screen.svg`，用于重新截取本 README 的明暗界面图。

## 许可证

项目原创代码使用 [MIT](../LICENSE) 许可证。浏览器 SDK 等第三方代码保留原有许可，详见[第三方声明](../THIRD_PARTY_NOTICES.md)。
