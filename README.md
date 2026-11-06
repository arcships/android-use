<div align="center">
  <h1><img src="docs/images/hero.png" alt="Android Use: Agent plans, optional Jev decides, Android Use validates and executes." width="100%" /></h1>
  <p><strong>Your agent. Now on Android.</strong><br/>Use it in Dim Agent. Follow the live screen. Add Jev when you need it.</p>
  <p>
    <a href="https://github.com/arcships/android-use/actions/workflows/test.yml"><img src="https://github.com/arcships/android-use/actions/workflows/test.yml/badge.svg" alt="Tests" /></a>
    <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-blue" alt="MIT License" /></a>
    <a href="pyproject.toml"><img src="https://img.shields.io/badge/python-3.11%2B-blue" alt="Python 3.11 or newer" /></a>
    <a href="#mcp-client-support"><img src="https://img.shields.io/badge/MCP-stdio-3DDC84" alt="Standard stdio MCP" /></a>
  </p>
  <p><strong>English</strong> · <a href="README.zh-CN.md">简体中文</a></p>
  <p><a href="#quick-start"><strong>Use in Dim Agent →</strong></a> · <a href="https://github.com/JackCaow/android-use/releases/tag/v0.6.2">Download v0.6.2</a> · <a href="#enable-jev-from-the-screen">Set up Jev</a> · <a href="docs/guide.md">User guide</a> · <a href="https://github.com/arcships/android-use/issues">Report an issue</a></p>
</div>

## Android tasks, inside Dim Agent

Android Use brings your own Android phone or emulator into **Dim Agent**, with **standard MCP** support for other compatible clients. Read the screen, find a control, tap, type, scroll, and check the result. Follow the process in a live phone view.

For workflows that need repeated UI decisions, enable **Jev**. Your Agent keeps planning and final verification; Jev selects the next candidate; Android Use validates and executes it.

> “Open Settings, find Battery, and read the current usage.”
>
> “Fill in the profile with the text I provide, verify it, and save a local draft.”

**No root, Appium, signing setup, or keyboard APK.** The compiled UI is included. Use USB or already-authorized wireless ADB.

## Standalone runtime (new source feature)

**Python, ADB, ffmpeg, the screen UI and the control service in one native package.** The lightweight Dim plugin installs it automatically on first use. Later MCP connections start the installed version immediately and check for updates in the background; verified updates take effect on the next connection. Failed updates retain the previous version; rollback rechecks it before switching.

**macOS OCR is an optional add-on**, omitted from the default package. Tree interaction, Agent screenshot inspection and Jev can be used without it. Ordinary users do not need Python, uv, Git, Node.js, a JDK or Xcode tools; the first-download bootstrap uses standard OS utilities.

This is newer than the published v0.6.2 source-plugin ZIP. Use native workflow artifacts for offline trials until a release containing the runtime assets is published. [Install, automatic updates, optional OCR and rollback →](docs/standalone.md)

## Quick start

### Use in Dim Agent

**Start in Dim Agent Desktop on the computer connected to your Android phone.** Install Android Use once, then describe tasks in the chat and follow the live phone view in the right panel. Jev is optional; your existing Agent can make decisions without an extra model key.

**1. Connect your phone.** Enable Developer options and USB debugging, connect a data cable, accept the debugging prompt, and keep the phone unlocked. ADB must be available on the computer where Dim executes commands. For a remote Dim session, connect the phone to that execution host.

**2. Ask Dim Agent to install and configure it.** Paste this into a chat with local terminal access:

```text
Install Android Use for Dim Agent on this computer:
https://github.com/arcships/android-use

Read README.md, docs/guide.md and skills/android-use/SKILL.md.
Reuse existing installations and preserve other MCP servers and uncommitted work.
Read docs/standalone.md. Prefer the matching standalone runtime/Dim plugin when
available; verify its release SHA256. For a source-install fallback, check Git, uv,
ADB and ffmpeg, then use scripts/install.py install --source . for a stable runtime.
Register its returned mcp_config as Dim's local stdio server named android_use;
use Dim's MCP management or dim mcp add. Check for duplicate registrations first.
Discover devices, select my intended phone, call aua_setup(action="status"),
start the service only if absent, then verify aua_ready reports ready=true.
Call aua_screen to open the live phone view in Dim's right panel and check for
fresh video. If tools are not loaded, reload MCP or start a new chat and recheck.
Please perform the setup and report the results. I will handle phone authorization
and unlocking. Leave Jev disabled for now.
```

<details>
<summary>Manual Dim setup — stable MCP entry</summary>

Prefer the [standalone package](docs/standalone.md), which returns a stable `mcp_config` without requiring a user Python/uv installation. For the source fallback, prepare dependencies and [clone the repository](#or-install-manually), then run from its root:

```bash
uv run --no-project --python 3.13 python scripts/install.py install --source .
```

The JSON result contains `mcp_config.command` and `mcp_config.args`. In Dim's **MCP management**, add a **local stdio** server named `android_use`, using that command and the arguments in their original order. The installer creates an independent runtime and verifies its MCP handshake before switching versions.

With the Dim CLI, register the returned command and **all** arguments in order. This example is the standalone macOS/Linux entry; replace the launcher path with your actual install result:

```bash
dim mcp add --scope user android_use -- /bin/sh "/absolute/path/runtime/launcher.sh" auto-serve
dim mcp test android_use --json
```

For Windows or the source installer, use their returned `mcp_config` instead of copying these arguments. See [native package installation](docs/standalone.md#offlinemanual-native-package).

Check that the MCP test returns a non-empty tool list. Some Dim CLI versions can return `success=true` with `tools=[]`; that alone does not confirm tools are loaded. In a new Dim chat, actually call `aua_setup` and `aua_ready`. If Android Use is already registered, inspect that entry before adding another. [Upgrade and rollback →](docs/upgrading.md)

</details>

**3. Give it a first task.** Once the tools are loaded, send:

> Use Android Use to check my phone is READY, open the live screen, then open Settings and report the battery level. Do not change any settings.

Dim Agent calls the Android tools and opens the screen through `aua_screen`. In Desktop, the view appears in the right panel; you can watch, tap or swipe, use Back/Home and copy a frame. If the host cannot embed the view, ask it to open the returned local preview URL in a browser on the same computer.

| Try in Dim Agent | Example request |
| --- | --- |
| Read and navigate | “Open Settings, find Battery, and read the current usage.” |
| Fill a draft | “Enter the text I provide, read it back, and save a local draft.” |
| Compare prices | “Compare the same drink in Meituan and Taobao for delivery, including delivery and packaging fees. Report the totals without ordering.” |
| Use Agent + Jev | “Use Jev for short UI subgoals, keep planning and recovery with the Agent, and verify the final result yourself.” |

**Optional: enable Jev.** Click **Jev** in the phone view, enter a TypeSafe API Key, then click **验证并启用** (Verify & enable). New tasks use the setting immediately; no `TEXT_MODEL_*` configuration is needed. Dim Agent keeps planning, text composition and final verification. [Jev settings and data sent →](#enable-jev-from-the-screen)

## A phone screen you can follow

<table>
  <tr><th align="center">Light</th><th align="center">Dark</th></tr>
  <tr>
    <td align="center"><img src="docs/images/sidebar-light.jpg" alt="Phone-frame Android Use preview in light mode" width="260" /></td>
    <td align="center"><img src="docs/images/sidebar-dark.jpg" alt="Phone-frame Android Use preview in dark mode" width="260" /></td>
  </tr>
</table>

The actual UI, rendered with synthetic phone content for documentation. The frame adapts to theme, window size and orientation. Tap or swipe inside the screen, use Back/Home, or copy a PNG frame. [Live-screen guide →](docs/guide.md#live-screen)

## Agent plans. Jev decides. Android Use acts.

| Owner | Responsibility |
| --- | --- |
| **Your Agent** | Understand the request, plan subgoals, compose text, recover and verify outcomes. |
| **Jev · optional** | Choose a code-owned candidate from the current UI. |
| **Android Use** | Observe, check scope and freshness, execute native actions, enforce bounds and pause. |

Known paths use `batch`, action-plus-wait or `scroll_until`. Delegate short workflows whose next action depends on new UI to `aua_execute_goal`. Without a TypeSafe key it runs in host-decider mode: your Agent picks each code-owned candidate with `aua_goal_decide`, and Android Use applies the same freshness, effect and no-replay checks. On uncertainty, control returns to the Agent with a step ledger and safe context. [Architecture and examples →](docs/jev.md)

## Enable Jev from the screen

1. Open the phone preview and click the **Jev button**.
2. Paste your **TypeSafe API Key**.
3. Click **验证并启用** (Verify & enable).

The server checks the connection and saves the key on this computer. New tasks pick up the setting immediately. You can test the connection, disable Jev, or remove the saved key from the same panel. **Jev is off by default.** [Setup and advanced environment configuration →](docs/jev.md#configure)

<details>
<summary>See the Jev settings panel</summary>

<img src="docs/images/jev-settings.jpg" alt="Jev settings with a write-only API key field and Verify and enable button" width="340" />

Enabling Jev sends the subgoal, visible UI text and supplied input values to TypeSafe for decisions. The server never returns the saved key to the UI. Screenshots are sent only for visual location (`aua_locate`), after you separately turn on 「允许发送截图（视觉定位）」.

</details>

## Built for real interaction

| Capability | What you get |
| --- | --- |
| **Understand the screen** | Accessibility trees, screenshots, selectors and bounded waits. |
| **Act precisely** | Tap, long press, double tap, swipe, drag, launch apps and use system buttons. |
| **See what the tree misses** | Opt-in Jev Box locates canvas, image and unlabeled targets; a visual tap rechecks pixels and taps once. |
| **Reduce round trips** | Combine action, wait and observation. Search lists with bounded auto-scroll. |
| **Input with verification** | Unicode/emoji, replace/append and native text readback. A stable field can offer one focus → input → readback decision. |
| **Keep control clear** | User takeover for authentication, partial/uncertain action ledgers and no automatic replay. |

## Other clients and manual setup

### Let your agent install it

Paste this into an agent that can use your local terminal:

```text
Install and configure Android Use on this computer:
https://github.com/arcships/android-use

Read README.md, docs/guide.md, and skills/android-use/SKILL.md first.
Reuse existing installs and preserve uncommitted changes and other MCP servers.
Check dependencies, install missing ones from official sources, and set up the
current client's local stdio MCP connection. For Codex, use scripts/install.py to install a stable managed plugin; diagnose conflicts before migration.
Discover devices, select the intended one, reuse a healthy service or start it once,
and actually verify ready=true. Open the live screen and check whether video works.
If new MCP tools are not loaded yet, use the CLI for setup and explain how to reload.
Perform the installation; do not only return instructions for me to follow.
Ask me to handle USB authorization, unlocking, and required system permissions.
Report what was installed, which checks passed, and any remaining steps.
```

For a client without terminal access, use the manual path below. [Stable upgrades and configuration migration →](docs/upgrading.md) [Detailed agent setup →](docs/guide.md#agent-led-installation)

### Or install manually

**1. Prepare the tools and clone.** Install [uv](https://docs.astral.sh/uv/getting-started/installation/), [ADB](https://developer.android.com/tools/releases/platform-tools), and [ffmpeg](https://ffmpeg.org/download.html). You also need Git; uv can provision Python 3.13.

```bash
git clone https://github.com/arcships/android-use.git
cd android-use
uv sync --frozen --python 3.13
```

**2. Connect your client.** Choose one route:

<details>
<summary><strong>Codex plugin</strong> — MCP tools, usage skill, and screen integration</summary>

For a prebuilt plugin ZIP, follow the [release installation steps](docs/guide.md#install-the-release-zip). To install from a source checkout, run from the repository root with the Codex CLI installed:

```bash
uv run --no-project --python 3.13 python scripts/install.py doctor
uv run --no-project --python 3.13 python scripts/install.py install --source . --codex
```

**Included in v0.6.2:** installs an independent runtime and a stable launcher, verifies a real MCP handshake before switching, and checks conflicting registrations. Existing installs need the explicit `--migrate-codex` flag. Keep keys/settings while removing versioned-cache dependencies. Reload tools afterward. [Upgrade, migration and rollback →](docs/upgrading.md)

</details>

<details>
<summary><strong>Standard MCP</strong> — Claude Desktop, Cursor, VS Code, and other local stdio clients</summary>

No Codex CLI or plugin build is needed. For clients using `mcpServers`, merge this entry into the existing config and replace the repository path:

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

Cursor also uses `"type": "stdio"`. VS Code's `.vscode/mcp.json` uses a top-level `servers` object instead. See [client-specific paths and complete examples →](docs/guide.md#other-mcp-clients).

</details>

**3. Connect your phone.** Enable USB debugging, connect a data cable, approve the prompt on the phone, and keep it unlocked:

```bash
adb devices -l
uv run android-use setup '{"action":"status"}'
# Only if the control service is not running:
uv run android-use setup '{"action":"start"}'
uv run android-use ready
```

Look for **`ready=true`**. With multiple devices, [select the target explicitly](docs/guide.md#select-a-device). Then ask your agent:

> “Connect to my Android, verify READY, and open the live screen.”

[Full installation and troubleshooting →](docs/guide.md)

## MCP client support

**This is a standard local stdio MCP server**, built with the [official Python MCP SDK](https://github.com/modelcontextprotocol/python-sdk). The Codex plugin is an optional installation bundle.

| Connection | What to expect |
| --- | --- |
| **Dim Agent Desktop** | Local stdio tools, an embedded phone view in the right panel, and optional Agent + Jev workflows. [Get started](#quick-start). |
| **Local stdio MCP** | Phone-control tools and standard text/image responses. [Claude Desktop](docs/guide.md#claude-desktop), [Cursor](docs/guide.md#cursor), [VS Code / Copilot](docs/guide.md#vs-code--github-copilot), and other clients can use their local-command configuration. |
| **MCP Apps** | An embedded live-screen view when the host supports the extension. Layout and clipboard permissions depend on the host. |
| **Local browser** | Run `uv run android-use screen` and open the returned URL when an embedded view is unavailable. |

The preview URL is a private local video endpoint, **not a remote MCP endpoint**. Remote-only MCP clients cannot connect to this stdio service. Client configuration examples are documented; full end-to-end acceptance has not been completed for every client/version.

In v0.5.1, supported devices record continuously; legacy segments reuse one connection and decoder. Brief known-health recovery keeps the picture visible. [Recovery behavior and troubleshooting →](docs/guide.md#live-screen).

## Optional interaction variation (source update)

Add `variation` to a gesture or local goal to sample bounded delays, selector touch points and swipe timing/endpoints. Fixed seeds reproduce samples; explicit coordinates and text stay exact. Added waits revalidate the page and share existing budgets. Default behavior is unchanged. This is interaction testing, with no anti-detection guarantee. [Parameters, audit and boundaries →](docs/interaction-variation.md)

Source development is synchronized to [arcships/android-use](https://github.com/arcships/android-use); published archives through v0.6.2 remain linked to their original release location.

## A smaller loop, with explicit outcomes

```bash
# One call: tap → wait for a visible condition → return the tree.
# Replace these labels with ones from the current screen.
uv run android-use tap '{"selector":{"text":"Display"},"wait_for":{"selector":{"text_contains":"Brightness"},"timeout_seconds":5},"observe":"tree"}'

# Search a list with a swipe count and time budget; do not click the result.
uv run android-use scroll_until '{"selector":{"text":"Display"},"direction":"up","max_swipes":8,"timeout_seconds":20}'
```

Actions support optional postconditions. A timeout reports what happened and stops the batch; it does not repeat the action. Auto-scroll stops on a match, its limits, or repeated lack of progress. [Gestures, waits, batches, and the original tools →](docs/guide.md#cli-reference) · [Optional Jev setup and benchmarks →](docs/jev.md)

## New in v0.6.0: no-key decisions and visual location

`aua_execute_goal` works without a TypeSafe key: your Agent picks each code-owned candidate through `aua_goal_decide`, with the same freshness, effect and no-replay checks. With Jev, verified emoji input is no longer retyped, each step makes fewer phone reads, and a goal head can stop once the goal is visibly achieved. New opt-in Jev Box (`aua_locate`, `visual` taps) finds canvas, image and unlabeled targets, and sends screenshots only after you turn on 「允许发送截图（视觉定位）」. Devices without `screenrecord` now say so instead of retrying the preview. v0.6.1: a Box that spans several labels in the tree is returned as `ambiguous` with `tree_candidates` instead of a tap point; use Box for icons, images and canvas, and selectors for text. [Host decider and Box →](docs/jev.md)

## New in v0.5.2: reliable reconnection

The preview entry point stays available when idle, MCP frame reads recover a stopped service, and Refresh waits for fresh video before reporting success. Jev settings now have a clearly labeled sparkle button. [Recovery behavior, validation and limits →](docs/preview-reconnection.md)

## New in v0.5.1: continuous live preview

Supported devices no longer restart recording every 170 seconds. Older devices reuse one ADB pipe and decoder across bounded segments. Brief known-health recovery retains the picture; display-metadata failures pause controls without dropping healthy video. Authentication and real disconnection still clear the image.

Android 16 physical capture passed a **190-second native run** with one encoder/connection/decoder and a 1.422-second maximum sampled gap. The earlier emulator motion stall remains unresolved; compatibility records retain a harness-seeded recovery error. [Results, retained failures and limits →](docs/preview-continuity.md)

## New in v0.5.0: safer actions and automatic OCR

**Included in the v0.5.0 plugin ZIP.** Android Use retains every MCP tool and adds page-based AX / local OCR routing to read-only `aua_read_fields`. Healthy text stays on the tree path; missing text or requested labels triggers a device ROI capture and local recognition, then one Jev request judges multiple fields. Acknowledged actions wait for an effect without repeating writes. Money keeps printed signs, grouping and currency.

During development acceptance, Meituan AX and OCR both produced **19 + 1.5 + 2.4 = ¥22.9**; Taobao automatically routed its empty semantic tree to OCR and produced **24 + 1.5 + 2.3 − 6 = ¥21.8**. The Agent independently inspected checkout and emptied temporary carts; no order or payment was made. Failed ROI, animation and missing-field attempts are retained. This is neither a whole-task speed benchmark nor an exhaustive cheapest-price result. Subsequent guard refinements were tested offline; phone testing stopped at the user's request.

v0.5.0 passes **277 Python tests and 17 UI tests**. Built-in OCR currently requires macOS and Xcode Command Line Tools. Automatic visual grounding/clicking remains future work. Reload your MCP connection after updating.

[Field tool and parameters](docs/jev.md#automatic-read-only-ocr) · [Live acceptance and retained failures](docs/benchmarks/real-app-ocr-v6.md) · [Implementation boundaries and roadmap — 中文](docs/visual-routing.zh-CN.md)

## Measured, with clear limits

Development comparisons include **18 source-level trials** and **12 live Agent / Agent + Jev trials**. Both live arms completed 6/6 tasks. Dynamic workflows needed fewer host replies with Jev; familiar batch paths did not improve. Sampling reduced repeated state queries, while input macros reduced decisions without an additional timing win in that sample. [Methodology, raw records and retained failures →](docs/benchmarks/optimizations-v4.md)


## Project status

**v0.6.2 · MIT · 392 Python tests · 24 browser tests.** Emulator acceptance covers 15 real stdio MCP scenarios. Earlier physical-device acceptance covers Android 16 / Xiaomi 15 Ultra. New Jev setup and frame changes have local/emulator validation; other manufacturers, host versions and operating systems have different coverage. [Validation evidence →](docs/validation.md)

Multi-touch, arbitrary gesture paths, fine-grained text selection and custom-editor input fallbacks are not implemented. A matched UI condition proves that condition; the Agent must verify business outcomes. Control and preview run locally, and tool results are handled by your chosen client/model.

## Acknowledgements

Thank you to [iPhone Use](https://github.com/zhongerxin/iPhone-use), its author and contributors for inspiring Android Use's phone-agent workflow, guided setup and live-screen experience.

## Explore and contribute

| Start here | Go deeper |
| --- | --- |
| [English user guide](docs/guide.md) · [中文使用指南](docs/guide.zh-CN.md) | [Implementation notes — 中文](docs/architecture.md) |
| [Client configuration](docs/guide.md#other-mcp-clients) · [Troubleshooting](docs/guide.md#troubleshooting) | [Device acceptance records — 中文](docs/validation.md) |
| [Development and tests](docs/guide.md#development) · [Open an issue](https://github.com/arcships/android-use/issues) | [MIT license](LICENSE) · [Third-party notices](THIRD_PARTY_NOTICES.md) |

Bug reports and contributions are welcome. Include your host OS, Android version, client, and a minimal reproduction; remove device identifiers, access tokens, and private screen content.
