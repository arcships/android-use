# Standalone runtime / 独立运行包

**Source feature after v0.6.2.** Historical v0.6.2 source-plugin ZIPs do not contain this runtime. The **Standalone runtimes** workflow builds native archives and lightweight Dim plugins; tagged releases publish those artifacts only after their acceptance checks pass. Before a release containing these assets exists, use the workflow artifacts for offline installation. The lightweight plugin's production download URL cannot provision a runtime from an older release that lacks it.

## What's included

| Component | Default runtime |
| --- | --- |
| CPython + locked Python packages | Included. No user Python, pip or uv installation. |
| ADB / Android Platform Tools | Included; used by both shell transport and adbutils. |
| FFmpeg | Included; built from pinned source for H.264 input, screen filters and JPEG output. |
| Compiled screen and UiAutomator2 JAR | Included. No Node.js or JDK at runtime. |
| macOS OCR | **Separate optional component**, not in the default runtime. No Swift compiler needed when installed. |
| Git | Not required for runtime installation or updates. |

Build targets are `macos-arm64`, `macos-x86_64`, `linux-x86_64` (Ubuntu 22.04 build environment) and `windows-x86_64`. Native acceptance results are reported per platform; a configured matrix is not itself a passing result. The Windows entry uses a fixed native bridge and inherits raw stdio, avoiding PowerShell text conversion in regular MCP calls.

The phone still needs USB debugging, authorization and unlocking. OEM USB drivers/security settings and the phone's `screenrecord` capability are not supplied by a desktop package. Dim Agent must execute on the host with ADB access. Jev remains optional and uses the user's own TypeSafe key in the settings panel.

## Install in Dim Agent

### Lightweight plugin

From a release containing native assets, download the matching `android-use-VERSION-PLATFORM-dim-plugin.zip`, verify its SHA256 from `SHA256SUMS`, extract it, and install that directory using Dim's plugin management or:

```bash
dim plugin install /absolute/path/to/extracted-dim-plugin
```

This is a **Dim** plugin: its MCP root placeholder follows Dim's plugin loader. It is separate from the historical Codex/source plugin ZIP. Keep one Android Use registration enabled to avoid duplicate tools and competing device operations.

On first use, its OS bootstrap downloads the matching runtime and checks SHA256 before executing its installer. POSIX bootstrap uses `/bin/sh`, curl, unzip and sha256sum/shasum; Windows uses the built-in PowerShell/.NET tools. Only the initial download needs these OS utilities; managed launches use the native runtime. Bootstrap diagnostics go to stderr, keeping stdout reserved for MCP. Initial provisioning needs network access and may take longer than the client's startup timeout; let the Agent run the bootstrap/setup first, then reconnect MCP if necessary.

### Offline/manual native package

Download `android-use-runtime-PLATFORM.zip` and the release's `SHA256SUMS`. For macOS/Linux, from the download directory:

```bash
unzip android-use-runtime-macos-arm64.zip -d android-use-runtime
./android-use-runtime/app/android-use-runtime install \
  --archive android-use-runtime-macos-arm64.zip \
  --sha256 REPLACE_WITH_THE_64_HEX_VALUE_FROM_SHA256SUMS
```

Replace the platform in the filename for Intel macOS or Linux. Windows PowerShell:

```powershell
Expand-Archive .\android-use-runtime-windows-x86_64.zip .\android-use-runtime
& .\android-use-runtime\app\android-use-runtime.exe install `
  --archive .\android-use-runtime-windows-x86_64.zip `
  --sha256 REPLACE_WITH_THE_64_HEX_VALUE_FROM_SHA256SUMS
```

Use the returned `mcp_config.command` and **all** `mcp_config.args`, unchanged, in Dim's local stdio MCP configuration. Then verify the actual tools and phone with `aua_setup(action="status")`, start once if absent, `aua_ready`, and `aua_screen`. A CLI response with `success=true` and `tools=[]` is not evidence of tool discovery.

The install root defaults to `~/.local/share/android-use/runtime`. Set `ANDROID_USE_RUNTIME_DIR` to select another location. Managed launch paths remain stable; original ZIPs/extraction directories may be removed after installation. Keep the managed directory. Existing device and Jev state remain in the usual Android Use state directory, or the client's explicit `ANDROID_USE_STATE_DIR`.

**Migrating from a source installation:** register the returned stable command once, keeping the existing MCP entry's `env` settings and intended state directory. The old source launcher's private `launch.env` is not silently imported: migrate required values privately into the new client's environment or configure Jev through the screen. No old installation or unrelated client configuration is deleted automatically. A full app restart is not intrinsic to this runtime; the host must reconnect/reload MCP for a new process to use an upgraded version.

## Updates and rollback

At a new MCP connection, the launcher starts the installed version immediately. If enabled and due, an independent background process checks the repository's latest stable GitHub release, at most once per 24 hours. It downloads to a temporary directory, checks SHA256, validates archive paths/platform/version, runs bundled dependency health checks and a real MCP `initialize` + `tools/list` probe, then switches one atomic pointer. The current task/process is not stopped. **The next MCP connection uses the promoted version.** This is not live replacement of an already-connected MCP process.

Network/checksum/health failures leave the active pointer unchanged. Newer local versions are not automatically downgraded. A download cannot overwrite an installation that changed while it was downloading. Update-process/log/auxiliary-metadata failures do not prevent a healthy installed MCP from serving. An offline initial install requires a local runtime archive; offline updates continue using the installed version.

On macOS/Linux, use the same fixed launcher for management:

```bash
/bin/sh "$HOME/.local/share/android-use/runtime/launcher.sh" status
/bin/sh "$HOME/.local/share/android-use/runtime/launcher.sh" update
/bin/sh "$HOME/.local/share/android-use/runtime/launcher.sh" update --version 0.7.0
/bin/sh "$HOME/.local/share/android-use/runtime/launcher.sh" rollback
/bin/sh "$HOME/.local/share/android-use/runtime/launcher.sh" cli tools
```

`0.7.0` is an example, not a claim that this version has been released. On Windows, replace the command with the `mcp_config.command` returned at installation, keep its `--root ROOT` arguments, and replace the final `auto-serve` with `status`, `update`, `rollback`, or `cli tools`.

Rollback rechecks the previous runtime before switching. It pauses automatic updates to keep that selected version in place; an explicit successful `update` resumes checking. `ANDROID_USE_AUTO_UPDATE=0` in the MCP environment also disables automatic checks. Runtime directories are retained, including versions used by existing processes. No automatic deletion of potentially active versions occurs.

Installation/promotion and rollback are serialized by `runtime.lock`; background downloads use `download.lock`. A forced process/OS termination can leave a lock. Confirm that no corresponding installer/updater is running before removing it. Pointer replacement is atomic; filesystem corruption, host process termination and external edits remain outside that guarantee. Release SHA256 fetched from the same trusted HTTPS release protects transfer integrity, not a compromised publishing account. The update repository is fixed to `arcships/android-use`.

## Optional macOS OCR

Tree-based interaction, Agent screenshot inspection and Jev decision/visual-location paths do not require the local OCR program. Automatic local OCR of text on pages without useful accessibility text does require it. **Jev Box location is not an OCR replacement.** Without the component, local OCR reports `ocr_unavailable`; it does not invoke Swift or install a compiler.

Install only when needed:

```bash
/bin/sh "$HOME/.local/share/android-use/runtime/launcher.sh" install-ocr
```

This downloads the matching release's `android-use-ocr-PLATFORM.zip`, checks its SHA256 and `vision-v1` interface/platform, runs the native helper against a local test image, then atomically activates it. Reconnect MCP afterward. Offline installation uses the same command with `--archive /path/to/android-use-ocr-macos-arm64.zip --sha256 VALUE`. Components and preferences live outside runtime versions and survive upgrades/rollback; failed component updates retain the old component. Linux/Windows do not gain the macOS Vision provider; the Agent can inspect screenshots or supply OCR text through the existing field interface.

## Build and acceptance

Build on the target OS with Python 3.13.7, uv, a C compiler, make and Bash; macOS additionally needs Swift for the optional component. Windows CI provisions a MinGW toolchain. End users do not need these build tools.

```bash
uv sync --frozen --python 3.13.7
uv pip install -r scripts/runtime-build-requirements.txt
uv run --no-sync python scripts/build_runtime.py
uv run --no-sync python scripts/build_runtime_upgrade_fixture.py
uv run --no-sync python scripts/accept_runtime.py \
  --archive dist/runtime/android-use-runtime-macos-arm64.zip \
  --upgrade-archive dist/runtime-upgrade/android-use-runtime-macos-arm64.zip
```

The upgrade fixture builds a distinct `+upgrade-test` version in a disposable source tree; it is never published. Native acceptance removes system Python/uv/ADB/ffmpeg from PATH, checks packaged tools and UI resources, retains a live MCP connection across install/rollback, deletes the original extraction, rejects corrupted/mismatched upgrades, preserves private settings, tests the frozen daemon dispatch and actual H.264-to-JPEG conversion. macOS also checks optional OCR installation and recognition. The lightweight plugin's first-install test stubs only download transport with local artifacts while checking production URLs; it is not a live GitHub release-download test. No phone or model calls are made.

Inputs are pinned in `scripts/runtime-inputs.json`. Archives carry Python/ADB notices and corresponding FFmpeg source/configuration. The legacy source/Codex package and its installer continue to work independently. [Recorded results and limits](validation.md).

## 中文

这是 v0.6.2 之后的新源码功能，旧 v0.6.2 插件包不包含独立运行时。跨平台工作流生成原生运行包、轻量 Dim 插件与 SHA256；正式发布需要对应 Release 中实际有这些资产，不能把旧 ZIP 当成新包。发布前可使用工作流产物离线安装。

默认包内置 Python、Python 依赖、ADB、ffmpeg、界面和控制 JAR，普通用户无需 Git、uv、Node.js 或 JDK。**macOS OCR 不默认携带**，需要本地识字时用固定入口的 `install-ocr` 安装可选组件，安装后也不需要 Xcode/Swift。未安装时明确返回不可用，不悄悄编译。Jev 决策和视觉定位不等同于 OCR；树缺失页面的自动离线文字提取仍需此组件。

轻量 Dim 插件首次自动下载匹配平台的运行包、校验并安装；离线包安装命令见上文，安装结果中的 `mcp_config` 可直接用于 Dim 的本地 stdio 配置。首次安装的网络等待可能超过客户端启动超时，可先由 Agent 完成准备再重新连接。手机 USB 授权、解锁、厂商驱动及系统录屏能力仍由设备和用户提供。

后续连接立即使用已安装版本，每 24 小时最多后台检查一次更新。新包验证和真实 MCP 握手通过后才切换单一指针；当前会话继续执行，下一次 MCP 连接使用新版。失败保留旧版，不自动降级，不覆盖下载期间发生的其他安装。回滚先验证旧包，再切换，并暂停自动更新；显式成功执行 `update` 后恢复检查，也可用 `ANDROID_USE_AUTO_UPDATE=0` 禁用。没有实现正在运行的 MCP 进程热替换。

固定目录、已有版本和可选组件保留，设备/Jev 设置不清除。迁移旧源码入口时要保留 MCP 的 `env` 和 `ANDROID_USE_STATE_DIR`；旧安装器的 `launch.env` 不会自动导入，应私下迁移必要配置，或在界面重新设置 Jev。普通升级不依赖解压目录或系统 Python；宿主仍需重连以加载新版。强制终止残留的锁必须确认进程已退出后处理。
