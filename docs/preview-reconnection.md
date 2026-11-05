# Preview reconnection (source fix after v0.5.1)

This is an unreleased source/local hotfix. The published v0.5.1 ZIP does not include it.

## Why plugging the phone back in did not recover the panel

The device and ADB were healthy, but the saved browser tab still used the address of a preview daemon that had exited. v0.5.1 shut down that HTTP listener after ten minutes without a viewer and deleted its endpoint metadata. A later daemon could receive a different port/token. A browser Refresh request only contacted the original address; it could not start the missing Python process or discover its replacement.

## Recovery behavior

- Keep the token-protected loopback HTTP entry point alive. Recording still stops after the existing **five-second viewer lease**; idle time does not add phone screenshot/tree polling or leave recording running.
- Preserve private endpoint metadata on normal exit. Upgrade startup passes the endpoint independently of the old owner's metadata cleanup and waits briefly for its port to be released. The wire protocol remains 3; the implementation revision becomes 3.
- An MCP App frame read that loses the service rediscovers/starts its authenticated endpoint and retries that **read once**. Phone actions are never automatically replayed. Lost tap/swipe/Home/Back responses are marked uncertain.
- Refresh reports “正在重连…” while waiting. An OK HTTP response or an old cached picture cannot produce “已重新连接”; a new decoded picture with reliable geometry must arrive first. Authentication pause and disconnected/unknown device state still clear or block controls.
- Persistent browser transport failure explains that the Agent may need to reopen the phone screen, rather than claiming a Refresh request alone can restart a dead service.

A standalone browser cannot launch an OS process. After an OS restart or manual termination, an active MCP App read can restore the service; otherwise ask the Agent to open Android Use once. If another process permanently owns the saved port, startup falls back to a new port and the standalone page must be reopened. This fix prevents routine idle expiry and preserves addresses when available; it is not a system login service or a promise that every old, already-expired bookmark can repair itself.

## Validation

- **305 Python tests and 21 browser tests** passed locally, with Ruff and UI compilation.
- HTTP regression covers a listener whose last activity is already older than the former 600-second cutoff, plus shutdown/restart using the same private URL. This is a controlled expired-idle-state regression, not a ten-minute wall-clock soak.
- Browser tests require a fresh frame before reporting success and reject “connected” for a disconnected phone. Existing authentication-clearing and geometry guards remain covered.
- Android 16 local acceptance used the installed plugin with the hotfix applied: upgrade kept the current URL; the test then terminated only its authenticated preview daemon. An MCP App read recreated it on the same URL and received a new stream. The recovery read took **5.419 seconds**, after a separate 0.7-second fault-settling delay. No phone UI/business action was performed; capture processes were restarted.
- Actual browser-button acceptance observed **缓冲中 / 正在重连… → Live**, a decoded picture and a hidden blocking overlay. No order, text entry or App navigation was involved.
- [Sanitized acceptance metadata](benchmarks/preview-reconnect.json). This trial does not resolve the separately retained emulator motion-gap failure documented for v0.5.1.

The local cache and local marketplace source received a backed-up hotfix; package metadata still reads v0.5.1. Existing desktop MCP processes can retain the old Python methods until their next reload; the new resident preview daemon and updated browser UI were exercised immediately. No new GitHub release is implied by this local hotfix.

## 中文

这次问题出在本地预览入口：手机已经连接，但旧页面请求的服务地址已失效，按钮只重试旧地址。热修复让入口保持可用，闲置时只停止录屏；正常重启尽量沿用原地址，MCP 读图失败时自动恢复服务，收到新画面后才提示连接成功。点击、输入等手机动作不会因重连自动重放。

源码通过 305 项 Python、21 项界面测试；真机已验证服务重启后 URL 不变、MCP 读图恢复，以及实际按钮从“正在重连”恢复为 Live。本机已应用有备份的热修复，尚未发布新版本；已加载的桌面 MCP Python 方法在下次重载后更新。操作系统重启、手动结束服务或端口被占用时，浏览器本身不能启动 Python，需要 Agent 重新打开屏幕。
