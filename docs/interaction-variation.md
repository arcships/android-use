# Optional interaction variation

Experimental source update after v0.6.2. The published v0.6.2 ZIP does not contain these fields. Use the current source and reload MCP tools to advertise the updated schemas. Existing tool names and defaults remain compatible.

This is bounded variation for interaction testing. It does not hide the UiAutomator execution channel or promise that third-party apps cannot recognize automation. Native swipe/drag paths remain straight; this feature does not add multi-touch, arbitrary curves or synthetic per-character typing.

## Enable per call

```bash
# Replace the label with a unique target observed on the current screen.
uv run --frozen android-use tap '{"selector":{"text":"Display"},"variation":{"seed":42,"delay_max_seconds":0.15,"point_radius":4,"duration_fraction":0.15}}'
```

MCP uses the same `variation` object in `aua_tap`, `aua_long_press`, `aua_double_tap`, `aua_drag`, `aua_swipe` and `aua_scroll_until`. Omit it, or pass `null`, to keep the existing path. There is no global default change; manual live-panel controls are unaffected.

| Field | Default | Allowed values |
| --- | --- | --- |
| `seed` | unset | Integer 0–4294967295. Set for repeatable sampling; unset creates an independent generator. |
| `delay_max_seconds` | 0.15 | 0–0.5. Sample a delay before the action. Zero disables this wait. |
| `point_radius` | 4 | 0–16 Android display pixels around the original point, further constrained to the visible control/region. |
| `duration_fraction` | 0.15 | 0–0.25 fractional variation around the requested duration/interval, clamped to each operation's existing limits. |

The same seed, code version, UI and call sequence produce the same sampled parameters. Sampling does not modify Python's global random state. Operating-system scheduling and RPC latency still vary; a fixed seed does not guarantee identical wall-clock durations.

## Operation boundaries

| Operation | What varies |
| --- | --- |
| Selector tap/long press/double tap | Point within the visible control; pre-action delay; long-press duration or double-tap interval. Both clicks of a double tap use the same point. |
| Explicit `(x,y)` tap/long press/double tap | Delay and applicable duration/interval; the specified point stays exact. |
| Drag | Delay and duration; explicit start/end coordinates stay exact. |
| Directional swipe | Delay, duration and bounded endpoints within the supplied/default region; the requested direction is preserved. |
| `scroll_until` | One seeded generator advances across swipes. Stop/no-progress/timeout conditions remain in force; the result contains per-swipe samples. |
| `execute_goal` | Pass `variation` with the goal. Host and Jev decision paths apply it to tap, long press and swipe candidates and record samples in `steps`. Candidate coordinates remain exact, so goal taps receive timing variation; goal swipes can vary their endpoints. A supplied seed advances by executed trace index across host round trips. |
| Text/input macros, system buttons, App launch | No variation or text rewriting. Input macros retain their exact focus/write/readback path. |
| Visual taps | A simultaneous `visual` and `variation` is rejected before image/model work. Visual taps keep their pixel-validated path. |

For batches, include `variation` in the intended step's `args`; the batch still validates all steps before any action. Repeat a seed intentionally for repeatable steps, use distinct seeds, or omit the seed for independent sampling.

## Guards and audit

Added waits recheck takeover/lock/screen state in short slices. Before and after the wait, compare foreground/display and the UI tree; a changed page stops with `interaction_context_changed` and no write. A visible password control refuses the added wait. Existing observation tokens, postconditions, authentication and no-replay rules remain in force.

Variation spends the existing goal/scroll/batch time budget. Insufficient budget stops with `interaction_time_budget`, including a check immediately before writing. An RPC already in progress may still exceed a deadline, as in the existing executor; duration is not a hard transport timeout. This mode adds checks and latency, so it is optional and is not a throughput optimization. Conservative tree comparison can stop on harmless UI updates; observe and replan rather than automatically replaying a failed action.

Successful responses contain `variation` with the sampled target delay, applied point/endpoints and duration/interval plus `spatial_applied`. `scroll_until` returns `variations`; goals record relevant samples in their steps. These are values passed to the backend, not measured native event timings. `verified=false` still requires business verification. A timeout/uncertain result never automatically retries an action or batch.

## Validation and remaining limit

The API 35 ARM64 emulator passed the 11-scenario baseline in 35.19 seconds and the final variation run, including profiled batch gestures, in 41.88 seconds. A separate instrumented run passed in 38.62 seconds. These single runs are functional checks, not a speed or success-rate benchmark.

The first full variation attempt timed out verifying a double tap after the backend acknowledged execution. Four direct-backend event probes and both subsequent full runs recognized the double tap; there was no native event trace for the initial failure, so its cause remains unresolved. No automatic replay was added. Variation stays opt-in; this acceptance does not establish physical-device compatibility or third-party app detection behavior. Host/Jev forwarding is covered by unit tests, without live Jev requests in this acceptance. [All attempts and sampled parameters](benchmarks/interaction-variation.json).

To reproduce the functional check on an authorized test device, run `uv run --frozen python scripts/accept_device.py --serial <serial> --variation`. It installs a temporary fixture, uninstalls it in `finally`, and checks foreground restoration. Omit `--variation` for the baseline.

## 中文

这是 v0.6.2 之后的源码更新。显式传 `variation` 才启用；不传或传 `null` 保持原执行路径，实时面板手动操作不受影响。支持固定随机种子、最多 0.5 秒的动作前等待、最多 16 显示像素的控件/滑动区域内偏移，以及最多 ±25% 的耗时变化，最终受现有手势参数限制。

明确指定的点击坐标、拖拽端点和文本不改写。选择器点击可在可见控件内偏移；双击两次使用同一个点。滑动保持方向与区域，仍是直线路径。Agent/Jev 目标可传同样配置，已有候选坐标保持精确；输入宏不改变。视觉点击不接受该配置，避免在像素验证后插入等待或偏移。

新增等待会分段检查接管、锁屏、亮屏状态，并前后核对 App、显示几何和界面树。界面变化或预算不足就停止；不确定的动作不会自动重放。此功能会增加检查和时延，默认关闭，不承诺防检测或模拟真人身份。

模拟器基线 11 项通过，35.19 秒；最终启用变化参数与批处理的 11 项通过，41.88 秒。首轮曾出现双击执行确认后效果等待超时，后续原生事件探针和两轮完整复验均识别成功，但首轮没有事件日志，根因仍未确定。保留失败记录，功能标为实验性，不据此宣称真机兼容、普遍提速或更高成功率。此次没有实际调用 Jev，配置透传由单元测试覆盖。
