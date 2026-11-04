# Android Use user guide

Optional feature in v0.4.0: [optional Jev decision integration and comparison](jev.md). Default off; original tools remain compatible. Included in the v0.4.0 release ZIP.

v0.5.1 includes [read-only automatic OCR and execution guards](jev.md#automatic-read-only-ocr), plus automatic preview recovery. Built-in OCR requires macOS and Xcode Command Line Tools.

**English** · [简体中文](guide.zh-CN.md) · [Project home](../README.md)

[Install](#installation) · [Connect a device](#connect-a-device) · [Codex](#codex-plugin) · [Other clients](#other-mcp-clients) · [CLI](#cli-reference) · [Troubleshooting](#troubleshooting)

## Standard MCP and compatibility

Android Use is a **standard local stdio MCP server**, implemented with the [official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk). Clients launch `android-use-mcp` and use MCP initialization, tool discovery/calls, and resource reads. The Codex plugin bundles this server, a usage skill, and screen integration; it is optional.

| Layer | Requirements |
| --- | --- |
| Phone control | A client that can launch local stdio MCP servers. Screenshots are standard MCP image content; display and vision support depend on the client. |
| Embedded live screen | A host supporting [MCP Apps](https://modelcontextprotocol.io/extensions/apps/overview). The host decides the layout and clipboard permissions. |
| Browser preview | Run `uv run android-use screen` and open the returned URL on the same computer. |
| Remote MCP | Not provided. The preview HTTP URL is not an HTTP/SSE MCP endpoint. |

The process running the server must have ADB access to the device. A cloud agent does not gain access to a local USB phone just because a configuration file exists on your computer. The documented Claude Desktop, Cursor, and VS Code configurations have not each been fully accepted across client versions; see the [validation record — 中文](validation.md).

## Agent-led installation

Use the [copyable installation prompt](../README.md#let-your-agent-install-it) with an agent that has local terminal access. The agent should perform these steps:

1. Inspect the host, repository, installed tools, and existing MCP/plugin config. Reuse suitable installations; preserve uncommitted work and unrelated server entries. Install missing Git, uv, ADB, ffmpeg, and Python from official sources. Codex CLI is needed only for the Codex routes.
2. Clone or reuse the repository. Run `uv sync --frozen --python 3.13` in its root. Node.js is not required for normal use.
3. Configure the current client. For Codex, run the packaging step before registering the local marketplace and installing the plugin. For other clients, configure the local stdio command directly. Check existing registrations before changing them.
4. Discover ADB devices. Reuse the user's intended target, or select a clearly identifiable unique device. Ask which device to use when the target is ambiguous. The user handles USB trust, unlocking, and system permission prompts.
5. Save the chosen serial with `setup configure`, check service status, and reuse a healthy service. If absent, start it once. Diagnose errors rather than repeatedly reinstalling or restarting.
6. Actually call `ready`. Only `ready=true` proves the control channel is ready; dependency installation, a reachable port, or a tool listing is not enough.
7. Open and inspect the live screen. A URL alone does not prove video works. If the current chat has not loaded new MCP tools, use the CLI for setup and explain how to reload the client or start a new chat.
8. Report each result separately: dependencies, client/plugin installation, selected device, READY, live video, and outstanding user actions. With no connected device, finish what can be installed and report device acceptance as pending.

Installation authorizes setup and read-screen acceptance. Wait for a concrete phone task afterward. Passwords, PINs, one-time codes, and biometric authentication remain with the user; do not request these credentials.

## Installation

### Dependencies

| Dependency | Purpose |
| --- | --- |
| Git | Clone and update the repository. |
| Python 3.11+ | Run the server; the walkthrough uses tested Python 3.13. |
| [uv](https://docs.astral.sh/uv/getting-started/installation/) | Provision Python, install locked dependencies, and launch commands. |
| [Android Platform Tools](https://developer.android.com/tools/releases/platform-tools) | Provide `adb` for authorized USB/wireless connections. |
| [ffmpeg](https://ffmpeg.org/download.html) | Decode live video; control tools can be used without live preview. |
| Codex client / CLI | Needed for Codex plugin installation; skip for other MCP clients or standalone CLI use. |

On macOS with Homebrew:

```bash
brew install uv ffmpeg
brew install --cask android-platform-tools
```

On Windows/Linux, install the tools from their official sources and make `uv`, `adb`, and `ffmpeg` visible to the client process. Shell examples below use Bash/zsh.

```bash
uv --version
adb version
ffmpeg -version

git clone https://github.com/JackCaow/android-use.git
cd android-use
uv sync --frozen --python 3.13
uv run android-use --help
```

uv can download Python and dependencies as needed. Run subsequent commands from this repository root unless a command explicitly sets `--directory`.

Native-device acceptance currently covers **macOS + Android 16 / Xiaomi 15 Ultra**. API 35 ARM64 emulator acceptance also covers Agent + Jev and first-time screen settings. Linux CI covers automated tests; Windows/Linux device operation and other manufacturers have not completed end-to-end acceptance.

## Connect a device

### Authorize USB debugging

1. Enable Developer options and USB debugging on the phone.
2. Connect a data-capable USB cable and approve the computer's debugging authorization on the phone.
3. Keep the phone awake and unlocked. Some manufacturers also require their USB debugging security setting for input injection.

```bash
adb devices -l
```

The target should show `device`. For `unauthorized`, accept the phone's prompt. If no device appears, inspect the cable, USB mode, and drivers. See the [Android ADB documentation](https://developer.android.com/tools/adb).

### Check the service and READY

```bash
uv run android-use doctor
uv run android-use setup '{"action":"status"}'
```

Only if the service is absent:

```bash
uv run android-use setup '{"action":"start"}'
```

This pushes the pinned dependency's UiAutomator2 JAR and starts the service, or reuses a healthy existing service. Do not repeat installation/startup for every task.

```bash
uv run android-use ready
```

The result should include:

```json
{
  "ok": true,
  "ready": true,
  "state": "ready"
}
```

The full result includes foreground app, viewport, and current controls. READY checks unlock/screen state, the service, and a nonempty tree.

### Select a device

```bash
uv run android-use setup '{"action":"discover"}'
uv run android-use setup '{"action":"configure","serial":"SERIAL"}'
uv run android-use ready
```

Replace `SERIAL` with the actual USB serial or authorized wireless ADB endpoint. The choice is saved locally. For a single CLI call, put global options before the subcommand:

```bash
uv run android-use --serial SERIAL ready
```

`ANDROID_SERIAL` is also supported. Multiple devices are not resolved by choosing the first; a disconnected explicit target never silently falls back to another phone. USB/wireless endpoints for the same physical phone share operation locks and pause state.

Wireless debugging must already be paired and visible to ADB. Android Use does not enable wireless debugging or enter pairing credentials for you.

## Codex plugin

Choose a release ZIP or a source checkout. If you already use `android-use-local` from a checkout, update that installation instead of enabling a second copy.

### Install the release ZIP

Download `android-use-0.5.1-plugin.zip` and `SHA256SUMS` from [GitHub Release v0.5.1](https://github.com/JackCaow/android-use/releases/tag/v0.5.1). The ZIP includes the compiled UI, Python source/lockfile, skills, bilingual docs, and a ready-to-register local marketplace. It does not bundle Python, ADB, or ffmpeg; prepare the dependencies above. First installation needs network access to install Python dependencies.

On macOS/Linux, extract it and run:

```bash
# Check the downloaded archive first (Linux can use sha256sum -c SHA256SUMS).
shasum -a 256 -c SHA256SUMS
unzip android-use-0.5.1-plugin.zip -d android-use-v0.5.1
cd android-use-v0.5.1
uv sync --frozen --python 3.13
codex plugin marketplace add .
codex plugin add android-use@android-use-release
```

On Windows, extract the ZIP with your archive tool, open a terminal in that directory, and run the uv/Codex commands. Windows device operation remains outside completed native acceptance. Reload tools/start a new chat and enable Android Use, then complete [device setup and READY](#connect-a-device). The release marketplace is named `android-use-release`; the source-checkout marketplace remains `android-use-local`. Registering the ZIP does not require cloning the repository or running `scripts/package.py`.

### Install from source

After installing dependencies, run from the repository root:

```bash
uv run python scripts/package.py
codex plugin marketplace add .
codex plugin add android-use@android-use-local
```

**Do not skip packaging.** The local marketplace points to `dist/android-use`, generated by `scripts/package.py`; that directory is not committed.

Start a new chat or reload the client tools, then enable Android Use. Check the installation with:

```bash
codex plugin marketplace list
codex plugin list --json
```

If an existing marketplace with the same name points elsewhere, inspect it before changing it. Packaging and installation follow the [OpenAI plugin documentation](https://developers.openai.com/plugins/build/plugins); these CLI commands have been exercised in this project.

Try: “Connect to my Android, verify READY, and open the live screen.” `aua_ready` links the screen to the chat; use `aua_screen` to open/reopen it without repeating READY. Embedded display requires MCP Apps support.

To register only the MCP server in Codex CLI:

```bash
codex mcp add android_use -- uv run --frozen --directory /absolute/path/android-use android-use-mcp
```

Choose the plugin or direct MCP registration; do not register the same server twice.

## Other MCP clients

Complete installation and device preparation first. **No Codex CLI, marketplace, or plugin packaging is needed.** The client launches the stdio process; do not leave a separate server running in another terminal expecting clients to connect to it.

Use the actual repository's absolute path. On Windows, `C:/tools/android-use` is convenient; backslashes in JSON must be escaped as `C:\\tools\\android-use`. `${PLUGIN_ROOT}` belongs to the Codex plugin environment and should not be copied into these configurations.

GUI applications may have a different `PATH` from your terminal. If necessary, set `command` to uv's absolute executable path. The server also accepts `ANDROID_USE_ADB` and `ANDROID_USE_FFMPEG` in the entry's `env` object as absolute executable paths.

### Claude Desktop

Open **Settings → Developer → Edit Config**, merge `android_use` into the existing `mcpServers` object, save, then fully quit and restart Claude Desktop. Look for the server's tools in the connectors/tool list. See the [official local-server guide](https://modelcontextprotocol.io/docs/develop/connect-local-servers).

| OS | Configuration file |
| --- | --- |
| macOS | `~/Library/Application Support/Claude/claude_desktop_config.json` |
| Windows | `%APPDATA%\Claude\claude_desktop_config.json` |

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

### Cursor

Merge the following into user-level `~/.cursor/mcp.json` or project-level `.cursor/mcp.json`. Enable the server in **Customize**, restart if needed, and confirm the agent has `aua_*` tools. See [Cursor's MCP documentation](https://cursor.com/docs/mcp).

```json
{
  "mcpServers": {
    "android_use": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--frozen", "--directory", "/absolute/path/android-use", "android-use-mcp"]
    }
  }
}
```

This is a local connection. Cloud agents do not acquire access to your local USB device through this file.

### VS Code / GitHub Copilot

In a local workspace, run **MCP: Add Server** from the Command Palette, choose **Command (stdio)**, enter the command below, name it `android_use`, and select the config scope. Follow the client's startup/trust prompts and verify a real tool call. See the [VS Code setup guide](https://code.visualstudio.com/docs/agent-customization/mcp-servers).

```bash
uv run --frozen --directory /absolute/path/android-use android-use-mcp
```

For workspace `.vscode/mcp.json`, use **`servers`**, not `mcpServers`:

```json
{
  "servers": {
    "android_use": {
      "type": "stdio",
      "command": "uv",
      "args": ["run", "--frozen", "--directory", "/absolute/path/android-use", "android-use-mcp"]
    }
  }
}
```

If the guided setup uses **Copilot Global**, its default `~/.copilot/mcp-config.json` (changed by `COPILOT_HOME`) uses `mcpServers`. Workspace-root `.mcp.json` uses that portable format too. See the [configuration reference](https://code.visualstudio.com/docs/agents/reference/mcp-configuration); do not mix the formats. In Remote SSH, WSL, or containers, dependencies, paths, and ADB access belong to the environment actually running the process.

### Other local-command clients

Use `uv` as the command and these separate arguments: `run`, `--frozen`, `--directory`, the repository's absolute path, `android-use-mcp`. Follow the client's documentation for the filename, top-level key, and whether `type: stdio` is needed.

A remote-URL-only configuration cannot use this command or the video preview URL.

### Verify the first connection

Ask your agent:

```text
Use Android Use MCP to connect to my Android.
Call aua_setup with action=status; use discover if device discovery is needed.
Ask me to select an ambiguous target. If the service is absent, start it once.
Call aua_ready and report the actual ready flag and foreground app.
Then read the current screen once. Do not perform other phone actions.
```

Some clients prefix tool names with the server ID. Installation is confirmed by successful tool discovery/calls; phone readiness requires actual `ready=true`. The usage skill is not automatically installed by a raw MCP registration, so the agent can also read `skills/android-use/SKILL.md`.

## Live screen

```bash
uv run android-use screen
```

Open the returned `url` in a local browser or the client's browser panel. It listens only on `127.0.0.1` and includes a random access token; do not share it. MCP Apps hosts can show the same interface through `aua_screen`.

The toolbar provides Back, Home, Copy frame, and Reconnect. Copy uses the currently displayed frame, writes a PNG directly to the clipboard, and does not create a screenshot file. Paste with **⌘V / Ctrl+V** in an app supporting image pasting. Clipboard writing must be supported and allowed by the browser/host; failures are reported.

Manual preview gestures remain tap and directional swipe. Precision drag, long press, and double tap are MCP/CLI operations. The theme follows the system.

Video uses `screenrecord → H.264 → ffmpeg → in-memory frames`, independent of screenshot/XML polling. After the last visible viewer stops renewing its lease for 5 seconds, capture stops. Authentication pause clears the frames and stops capture. A live sidebar is not a substitute for the agent explicitly observing the screen when needed.

**Included in v0.5.1:** devices that document unlimited recording use it; older builds keep one ADB pipe and decoder across bounded segments. Brief known-health recovery retains the displayed picture without a blocking overlay, with phone controls disabled until fresh capture/geometry is available. Display-metadata errors no longer declare a healthy video disconnected. After five seconds without recovery, show unavailability and continue retries; unknown lock state, authentication and real disconnection still clear the image. Reload the MCP process after updating, then reopen preview to upgrade its authenticated daemon.

[Continuous preview implementation, physical/emulator evidence and retained limits](preview-continuity.md). Physical native capture passed 190 seconds; the earlier emulator motion-gap failure remains unresolved.

## CLI reference

MCP tools use the same operation names prefixed with `aua_`. All following examples run in the repository root. Screen labels and coordinates are illustrative; replace them with values from a current observation.

### Observe, find, and launch

```bash
uv run android-use observe '{"mode":"tree"}'
uv run android-use observe '{"mode":"both"}'
uv run android-use launch_app '{"package":"com.android.settings","observe":"tree"}'
uv run android-use find '{"selector":{"text":"Display"}}'
uv run android-use tap '{"selector":{"text":"Display"},"observe":"tree"}'
```

Observation modes are `tree`, `screenshot`, and `both`. CLI screenshots return absolute `image_paths`; MCP returns native image blocks. Multiply screenshot coordinates by `image.pixel_to_device` for Android display pixels; do not use dp or host-window coordinates.

Selectors include exact/substring text and description, resource ID, class, and package. Ambiguity returns candidates: choose an explicit zero-based `index`. A tree does not prove a control is unobstructed or that a virtualized list is complete.

`apps` lists installed third-party **package IDs**, not display names. Launch uses a confirmed package's launcher activity; it does not force-stop or clear app data.

### Action + wait + observation

```bash
uv run android-use tap '{"selector":{"text":"Display"},"wait_for":{"selector":{"text_contains":"Brightness"},"state":"visible","timeout_seconds":5},"observe":"tree"}'
uv run android-use wait '{"selector":{"text":"Loading"},"state":"gone","timeout_seconds":5}'
```

`tap`, `long_press`, `double_tap`, `drag`, `swipe`, `type_text`, `press_button`, and `launch_app` accept optional `wait_for`. `state` is `visible` (default) or `gone`; timeout is 0–20 seconds, default 5. Zero checks once. `gone` means no visible-tree matches, not deletion from app data; an empty tree is not evidence of disappearance.

`wait.condition_met` reports the postcondition. An action timeout returns `ok=false`, `error.code=postcondition_timeout`, `action_executed=true`, and `replay_action=false`: the action was acknowledged, but the requested condition did not arrive. Inspect the screen; do not repeat the action. A matched condition is not proof of animation completion or business success.

Without `wait_for`, behavior is unchanged; `observe` defaults to `none`. Tree observation reuses the last wait snapshot. Standalone `wait` reports both `found` (visible matches now) and `condition_met` (the requested condition); successful `gone` yields `false` and `true` respectively.

### Auto-scroll search

```bash
uv run android-use scroll_until '{"selector":{"text":"Display"},"direction":"up","max_swipes":8,"timeout_seconds":20,"observe":"tree"}'
```

A currently visible target returns with zero swipes. Otherwise it swipes, observes, and stops when the target is visible; **it never clicks the result**. Multiple matches remain candidates. For nested/horizontal lists, use `region={x,y,width,height}` inside the intended scroll container, in Android display pixels.

| Field | Meaning |
| --- | --- |
| `max_swipes` | 0–20, default 8; zero searches the current page only. |
| `timeout_seconds` | 0–20, default 20. |
| `duration` / `settle_seconds` | Swipe duration / post-swipe delay, each default 0.4 seconds. |
| `observe` | Defaults to `tree`; also `none`, `screenshot`, or `both`. |
| `found` / `matches` / `swipes` | Target visibility, candidates, and completed swipe count. |
| `stop_reason` | `found`, `max_swipes`, `timeout`, `no_progress`, or `empty_tree`. |

Two consecutive unchanged regional snapshots after swipes stop with `no_progress`; that does not prove the list ended or was fully traversed. Foreground app, activity, or display-size changes raise `scroll_context_changed`. Lock, pause, or uncertain errors also stop the operation. `ok=true, found=false` means the search ended without a match.

### Long press, double tap, and drag

```bash
uv run android-use long_press '{"selector":{"text":"Test item"},"duration":0.8,"observe":"tree"}'
uv run android-use double_tap '{"x":300,"y":600,"interval":0.08,"observe":"tree"}'
uv run android-use drag '{"start":{"x":200,"y":600},"end":{"x":700,"y":600},"duration":0.6,"observe":"tree"}'
```

Long press/double tap accept the same selector-or-coordinate target as `tap`. Drag needs distinct start/end points within the display. All support `observation_id`, `wait_for`, `observe`, and batch use. The optional observation ID checks the same MCP runtime's app/display context, not exact page freshness; do not reuse it across CLI processes.

Long-press duration is 0.5–3 seconds (default 0.8); drag duration is 0.1–3 (default 0.6). Double-tap interval is 0.04–0.25 (default 0.08), excluding RPC latency. High latency/app policies may result in two single taps. A failed double tap may already have executed its first click; never replay the pair automatically.

### Unicode input

Tap a visible, editable, non-password field first:

```bash
uv run android-use type_text '{"text":"Hello, Android 🐻","verify":true}'
uv run android-use type_text '{"text":" — more text","replace":false,"verify":true}'
```

`replace=true` is the default. Empty replacement clears the field; `replace=false` appends to native text, preserving emoji. `verify=true` reads back with native `getText`. XML may render non-BMP emoji as dots, so do not overwrite text based on those dots. No implicit submission occurs.

Newlines require `allow_newlines=true`; some apps treat them as Send, so establish the editor behavior and user intent first. Custom/rich-text editors may not support `setText`; no keyboard APK fallback is included.

### Swipe, buttons, and batches

```bash
uv run android-use swipe '{"direction":"up","observe":"tree"}'
uv run android-use press_button '{"name":"back"}'
uv run android-use press_button '{"name":"home"}'
uv run android-use batch '{"steps":[{"op":"launch_app","args":{"package":"com.android.settings"}},{"op":"observe","args":{"mode":"tree"}}]}'
```

Direction is finger motion: `up` usually reveals later rows. Buttons include Home, Back, Recents, Enter, and volume up/down. Enter may submit.

Batches prevalidate up to 20 steps, including new gestures, action waits, and scroll search. They stop on errors, unmet conditions, scroll misses, Enter submission, or a 25-second shared budget. Wait/scroll deadlines respect the remaining batch budget; in-flight ADB/RPCs may overrun it.

An action wait timeout stops with `postcondition_timeout`; a scroll miss with `scroll_not_found`. Returned steps count toward `completed_steps`, with `stopped_at` pointing to the next step; that count does not mean every business condition succeeded. On uncertain action errors, `stopped_at` points to the failed step. Inspect `ok`, `stop_reason`, `results`, and the real screen before resuming; never replay the full batch.

### Authentication handoff

```bash
uv run android-use pause
# After the user completes authentication and explicitly confirms:
uv run android-use resume
uv run android-use observe '{"mode":"tree"}'
```

Pause is shared by CLI, MCP, and preview for the same physical phone. It interrupts waits/scrolling between operations; an in-flight RPC cannot be recalled. While paused, do not read, screenshot, or control the phone. After resuming, observe fresh state. Passwords, PINs, codes, and biometrics belong to the user.

### All tools

```bash
uv run android-use tools
```

| Tools | Purpose |
| --- | --- |
| `aua_doctor / aua_setup / aua_ready` | Discover, configure, start, and verify readiness. |
| `aua_observe / aua_find / aua_wait` | Read the screen, locate controls, wait for conditions. |
| `aua_tap / aua_swipe / aua_type_text` | Tap, swipe, and Unicode input. |
| `aua_long_press / aua_double_tap / aua_drag` | Long press, double tap, and precision drag. |
| `aua_scroll_until` | Bounded visible-target search without clicking. |
| `aua_apps / aua_launch_app / aua_press_button` | Package IDs, app launch, system buttons. |
| `aua_batch` | Bounded serial batches. |
| `aua_pause / aua_resume` | User authentication handoff. |
| `aua_screen` | Open/reuse live preview. |
| `aua_screen_frame / aua_screen_action` | App-only cached video/UI interfaces; not agent refresh tools. |

## Optional Jev setup

Open the phone view, choose the settings gear, enter your TypeSafe API Key, then click **验证并启用** (Verify & enable). The connection is tested and saved locally. New tasks use it immediately; no `.env` edits or MCP restart are needed. Test, disable or remove the saved key in the same panel. [Full setup, privacy and advanced environment options](jev.md#configure).

## Updating

For CLI/direct MCP users:

```bash
git pull --ff-only
uv sync --frozen
```

For an installed Codex plugin, then refresh its copy:

```bash
uv run python scripts/package.py
codex plugin add android-use@android-use-local
```

Restart/reconnect the MCP server or reload tools/start a new chat. A direct MCP config pointing to the same repository normally needs no path change.

## Troubleshooting

| Symptom | Next step |
| --- | --- |
| Missing `adb`, `ffmpeg`, or `uv` | Install it and check the client's environment, not only the terminal's. Absolute executable paths can help. |
| `unauthorized` | Accept debugging authorization on the phone; rerun `adb devices -l`. |
| `device_selection_required` | Discover devices, then configure the intended serial explicitly. |
| `device_unavailable` | Check the chosen USB/wireless connection; do not silently switch phones. |
| `service_not_started` | Run `setup` with `action=start`, then verify READY. |
| `phone_locked` / `screen_off` | Wake/unlock on the phone, then READY. |
| `preview_dependency_missing` | Install ffmpeg and expose it to the client process. |
| Video briefly says it is recovering | Wait for automatic recovery. Normal encoder renewal does not disconnect ADB or restart the phone-control service. If it persists, check connection/unlock/ffmpeg, then Reconnect. |
| Control works but video does not | Check connection/unlock/ffmpeg, then Reconnect; do not repeat business actions. |
| Copy failed | Use an image-clipboard-capable browser/host and allow clipboard writing. |
| `ambiguous_target` / missing control | Inspect candidates/current screenshot and occlusion; do not blindly select the first. |
| Uncertain input/submission | Observe actual state before continuing; never resend automatically. |
| `postcondition_timeout` | Action acknowledged, condition unmet. Inspect rather than replay. |
| Scroll search did not find a target | Read `stop_reason`, final observation, and region. `no_progress` is not proof of list end. |

## Architecture and data

Control: `Agent → MCP stdio → Android Use → ADB / UiAutomator2 → Android app`.

Video: `Android screenrecord → H.264 / ADB → ffmpeg → in-memory frames → preview`.

Setup pushes the dependency's JAR to `/data/local/tmp/android-use-u2.jar`. Action timeouts never automatically restart the service and replay RPCs. See [implementation notes — 中文](architecture.md).

- `verified=false` means no extra verification, not action failure. Final business outcomes need actual UI evidence.
- Trees can miss custom drawing, WebView content, and unloaded rows. They do not prove unobstructed controls or full list coverage.
- State and CLI screenshots use `~/.local/share/android-use`; override with `ANDROID_USE_STATE_DIR`. Device startup logs use `/data/local/tmp/android-use-u2.log`.
- The control service and video preview run locally. No project telemetry is uploaded. Tool text/screenshots are handed to your chosen MCP client/model, whose data transmission policies apply. Screenshots and copied frames are not written to the phone's gallery.
- Multi-touch, arbitrary paths, fine-grained text selection, custom-editor input fallback, system-gallery capture, and long-text continuation are not provided.

## Development

```bash
uv sync --frozen
uv run ruff check src tests scripts
uv run pytest
uv build
```

Only frontend work requires Node.js:

```bash
cd ui
npm ci
npm run build
npx playwright install --with-deps chromium
PLAYWRIGHT_CHANNEL=chromium npm test
```

The build writes `src/android_use/assets/phone-screen.html` and updates third-party notices. Native acceptance scope and differences are in the [validation record — 中文](validation.md).

### Native gesture acceptance

Requires an authorized unlocked phone, a JDK, and Android SDK with a stable API 28+ platform plus Build Tools providing aapt, d8, zipalign, and apksigner. Start/verify the control service first:

```bash
uv run python scripts/accept_device.py --serial SERIAL --sdk /absolute/path/android-sdk
```

Replace the placeholders. The script builds and installs a temporary permission-free app, exercises real MCP stdio calls, checks native gesture events and delayed UI/scroll/batch behavior, uninstalls the fixture, and attempts to restore the previous foreground activity. It refuses to replace an existing fixture package. Give it exclusive phone access during the run and handle any OS installation authorization yourself. No Gradle is required; build artifacts/device data are not committed.

### Documentation screenshots

From the repository root:

```bash
node ui/scripts/readme-preview.mjs
```

This renders the compiled UI with `docs/images/demo-screen.svg`, without ADB or real phone content. The hero illustration is conceptual; the light/dark UI images are actual frontend renders. [Hero generation brief](images/hero-prompt.md).

## License

Original project code is [MIT](../LICENSE). Third-party components retain their licenses; see [third-party notices](../THIRD_PARTY_NOTICES.md).
