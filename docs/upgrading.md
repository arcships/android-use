# Stable installation and upgrades

For the new self-contained runtime, automatic background updates and optional OCR, use the [standalone guide](standalone.md). The uv-based source/Codex installer described below remains supported; its external Python/uv prerequisites do not apply to the standalone package.

This installer ships in **v0.6.2** and later source/ZIP installations. Verify the release checksum, extract it, and run its `scripts/install.py`. The older v0.6.1 ZIP does not contain the installer; a current installer can also install an older extracted release with `--source /path/to/extracted-release`.

## Codex: one command, one registration

Prepare `uv`, ADB, ffmpeg and the Codex CLI. From a clean current checkout:

```bash
uv run --no-project --python 3.13 python scripts/install.py doctor
uv run --no-project --python 3.13 python scripts/install.py install --source . --codex
```

An existing Android Use plugin or direct MCP entry requires an explicit migration:

```bash
uv run --no-project --python 3.13 python scripts/install.py install --source . --codex --migrate-codex
```

Migration removes only recognized user-level `android_use` / `android-use` direct MCP overrides and disables other Android Use plugin registrations. It installs `android-use@android-use-managed`. Other servers/plugins, TOML comments and device/Jev state are preserved. Unknown commands, unsupported inline config layouts and project-level overrides are refused with an actionable error; no guessed configuration is deleted. Review `doctor` output before migrating. The installer backs up the original user configuration.

If the old direct entry used `--env-file`, the installer copies that file privately into the managed installation. Its explicit `env` values are retained. You can also supply `--env-file /path/to/private.env`. Future upgrades preserve that snapshot unless you explicitly provide another file. Credentials are never printed in the install result. Configure optional Jev in the preview as usual; saved Jev settings and screenshot consent are not changed.

The managed marketplace and runtime live under `~/.local/share/android-use/install` by default:

- `launcher.py`: the stable stdio entry point.
- `current.json`: an atomically replaced pointer to a prepared runtime.
- `releases/`: complete independent runtime copies with their own prepared Python environments.
- `plugin/`: plugin metadata/skill; its MCP command points to the stable launcher, with no cache version in its path.
- `launch.json` / `launch.env`: private launch preferences/environment, outside release and Codex cache directories.
- `backups/`: previous pointers, launch preferences and original Codex configuration. Treat backups as private.

Do not delete the managed directory. Original ZIP/source directories and obsolete Codex plugin caches can be removed after installation: the stable entry does not use them. The Python/uv installations used by the launcher remain prerequisites; uninstalling them is outside plugin upgrade recovery.

## Updating

Fetch a clean new checkout or verify and extract a new release. Run the same command against it:

```bash
uv run --no-project --python 3.13 python scripts/install.py install --source . --codex
```

For an older ZIP without the installer, invoke the installer from your current checkout and pass the extracted directory with `--source`. Existing managed installs do not need `--migrate-codex` again unless another conflicting registration was introduced. Reconnect/reload the host's MCP tools after upgrading. A running process can keep its old code until then; an installed version is not evidence that the current chat loaded it.

Upgrade prepares the new copy and frozen dependencies, then performs an actual MCP `initialize` and `tools/list` check, including the expected version and required Android Use tools. Only a verified candidate becomes current. Failed preparation/probes leave the active pointer unchanged. Failed Codex registration restores the previous pointer, launch settings, plugin wrapper and original user config. An installer lock prevents competing upgrades. Earlier runtime copies are retained; reinstall a known-good release through the same command to switch back after a functional regression.

A forced process/OS termination can leave `upgrade.lock`; confirm no installer is running before removing that lock and rerunning. Cross-file/host registration is not a database transaction or a system service. Network failures, removed uv/Python installations, external config edits and host reload behavior cannot be promised away. The installer refuses detected concurrent config edits rather than overwriting them. If an unrelated edit occurs after registration has started and registration fails, it keeps the already-verified runtime and reports `concurrent_configuration_edit_runtime_retained`; inspect the private backup and retry instead of replacing that external edit.

## Other MCP clients

No Codex CLI is required:

```bash
uv run --no-project --python 3.13 python scripts/install.py install --source .
```

Copy the returned `mcp_config` (`command` and `args`) into your client's existing `mcpServers` entry (VS Code uses `servers`). It points to the same stable launcher on every upgrade. Do not register a client against `.codex/plugins/cache/.../<version>` or a disposable ZIP directory. Update via the installer, then reconnect that client's MCP service.

## Checks and validation

`doctor` and `aua_doctor`'s `installation` field report duplicate Android Use plugins, manual-MCP/plugin conflicts, project overrides, versioned cache paths and missing explicit launch paths without reading or printing keys. The standalone doctor works even when the MCP server cannot start. Checks inspect user/project Codex TOML; they cannot discover arbitrary configurations inside every other MCP client. Projects must be diagnosed from their own working directory (or with `--project`).

Regression coverage includes installation roots and environment-file paths with spaces (including a uv argument-splitting regression), source directories with spaces, deleting old and new source directories after promotion, deleting simulated plugin caches, fresh host processes, dependency/handshake/registration failures, preserving private launch settings and unrelated config, concurrent edits and concurrent upgrades. A real Android Use stdio regression prepares two source snapshots and verifies the new version through the unchanged stable command after deleting both sources. No phone/model calls are made by the installer or these tests. Historical-release and local-device acceptance are reported separately in the [validation record](validation.md) and [sanitized metadata](benchmarks/stable-upgrade.json).

## 中文

安装器已纳入 v0.6.2；校验并解压发行包后，或在当前仓库中执行（旧 v0.6.1 ZIP 不含安装器）：

```bash
uv run --no-project --python 3.13 python scripts/install.py doctor
uv run --no-project --python 3.13 python scripts/install.py install --source . --codex
# 从已有手动 MCP / 其他 Android Use 插件迁移时显式加：--migrate-codex
```

安装器把完整运行时和独立依赖环境复制到固定的本机管理目录；准备和真实 MCP 握手通过后才切换指针。入口不再依赖版本缓存或解压目录，升级时启动命令不变。失败保留旧运行时，注册失败会恢复原配置；其他 MCP、插件、设备选择、Jev Key 和截图授权不改动。旧 `--env-file` 自动复制为私有快照，后续升级不覆盖；更新此快照需显式传 `--env-file`。

冲突默认只报告、不删除。显式迁移只处理可识别的用户级 Android Use 配置；项目覆盖、未知命令、无法安全编辑的 TOML 布局会停止。手动操作其他 MCP 客户端时，使用安装结果里的固定 `mcp_config`，不要写死 Codex 缓存版本目录。升级完成后重载工具，再核对版本、READY 和实际画面。

旧源码、解压目录、旧缓存可删除，固定管理目录与所依赖的 Python/uv 不可删除。强制终止、断电、外部配置改动和不同宿主的重载行为仍有边界；锁残留时先确认安装器已退出。自动化测试覆盖完整升级与失败恢复，不等于所有操作系统和宿主版本均已实测。
