# Jev executor: Android emulator comparison

Latest: [faster Android queries and new paired simulator comparison](agent-jev-v3.md). This page preserves earlier measurements.
Latest: [three-repeat Agent / Jev decision / Android Use execution comparison](agent-jev-v2.md). This page preserves earlier measurements.
Date: **2026-11-04**. Experimental source feature, not a v0.3.0 release claim.

This page measures execution overhead. For the intended **Agent + Jev** workflow, see the [live host-Agent comparison](agent-jev.md): Agent planning, ordinary tool helpers, optional delegation and final Agent verification. The optional API baseline mentioned below is not required for normal hybrid use.

**Finding:** Jev completed all 12 measured fixture tasks, but was slower than known deterministic tool paths. A stepwise generative-model comparison remains unmeasured because its endpoint/model/key were not configured. There is no evidence here that Jev improves speed or cost over an LLM agent.

## Setup and measurement

- Dedicated Android API 35 ARM64 emulator, Pixel 6 profile, ARM64 macOS host, local ADB. **No physical-device results** in this report.
- Permission-free native test app, synthetic content only; native `setText`, with soft-keyboard opening disabled in the fixture. No production apps, network-backed business tasks or visual controls.
- Four fixed tasks × three repeats × two available arms = **24 runs**. Both arms start from a recreated activity. Order reverses in repeat two, then returns in repeat three.
- Deterministic arm uses existing tap/wait/batch/scroll/long-press tools and a known path. The nested-list path reads the observed container bounds before scrolling. It is an execution floor, **not an LLM baseline**.
- Jev uses `jev-1.14.0`, top probability ≥0.65, margin ≥0.1, ≤20 steps, ≤30 model calls and a 60-second local-goal budget. No hidden retry or fallback.
- End-to-end time includes device checks, HTTP, actions, observations and independent final selector verification. APK build/install, app reset and cleanup are excluded. Calls use local Runtime dispatch; a separate acceptance run checks actual stdio MCP transport.
- All failures/handoffs would count in aggregates and cost per success. They were not filtered out. P95 uses nearest rank; with only 12 observations it equals the observed maximum and is not a reliable tail-latency estimate.

## Measured results

| Arm | Success | End-to-end median | P95 / observed max | Model calls | Estimated API cost, all 12 runs | Cost per success |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| Existing deterministic tools | 12/12 | 2.698 s | 22.660 s | 0 | $0 | $0 |
| Jev executor | 12/12 | 8.600 s | 25.979 s | 42 | $0.003901 | $0.000325 |
| Stepwise generative model | Not run | — | — | — | Unknown | Unknown |

Per-task end-to-end medians; every cell has three runs:

| Task | Existing tools | Jev | Success per arm |
| --- | ---: | ---: | ---: |
| Navigate to Status, show delayed result (English goal) | 2.570 s | 6.250 s | 3/3 |
| Open Profile, enter Chinese text, apply local draft (Chinese goal) | 2.877 s | 10.258 s | 3/3 |
| Open Catalog, scroll nested list, select Item 18 (English goal) | 19.806 s | 25.238 s | 3/3 |
| Navigate and long-press confirmation (Chinese goal) | 2.550 s | 6.269 s | 3/3 |

Jev returned usage for every measured call: **92,882 input / 3,759 output tokens**. Estimate: `92,882 × $0.042 / 1,000,000 = $0.003901044`; output tokens are free under the [published Jev pricing](https://docs.typesafe.ai/models). These are token-based estimates, not billed-cost reconciliation. Pilot and separate MCP acceptance calls are excluded from that total.

Total recorded Jev request time was 10.005 s across 42 calls: **238 ms mean per call** (sum of request times divided by calls, not a per-call percentile). Local-goal device reads consumed 70.920 s and actions/input verification 43.681 s across the 12 runs. Additional time includes guards, context checks and client setup. Zero stale decisions occurred in this fixture, so real UI churn remains covered by offline failure tests rather than these task timings.

## What this supports

Keep known short paths on `batch`, action-plus-wait and `scroll_until`: they have zero decision-model API cost and were faster here. Use the optional Jev loop when the host would otherwise inspect the UI and choose an action repeatedly; it removes host model/tool round trips, but this test does not quantify that benefit.

The next meaningful comparison is a configured stepwise generative baseline using the same candidates, independent result checker and budgets. Include its planning/fallback costs before making whole-agent efficiency claims. Provider pricing must be verified or remain unknown. Current `skipped.text` explicitly records the missing configuration.

Twelve successes on four small tasks do not establish broad reliability. The fixture does not test keyboard overlays, authentication, WebViews, custom drawing, remote app latency or real external submissions. Overlay/password/pause/lock/scope changes, stale state, model errors, missing usage, uncertain mutations and budget limits have offline tests; they should not be confused with real-app acceptance.

## Development pilots

Two earlier one-repeat pilots are **not included** in the table. The first exposed indistinguishable swipe directions in model criteria and an incorrect default region in the deterministic nested-list path (each arm passed 3/4). The candidate descriptions and observed-region baseline were fixed; a second pilot passed 4/4 in both arms. The measured three-repeat run then used the frozen executor and fixture, without removing failures or adjusting thresholds between repeats.

## Reproduce / inspect

Validation on the same emulator: **105 Python tests**, **6 browser tests**, Ruff and wheel/sdist build passed. A separate real stdio MCP run passed **12 scenarios in 69.60 s**: the original 11 interaction/stop/pause regressions plus `aua_execute_goal` calling the live Jev API. The server exposed 23 tools. The Jev MCP case completed with one model call; independent UI verification passed. The fixture was uninstalled and the original activity restored. [Acceptance record →](jev-mcp-acceptance.json)

See [configuration and command](../jev.md#reproduce-the-comparison). Full sanitized records, environment notes and SHA-256 hashes of the executor/provider/models/fixture sources are in [jev-emulator.json](jev-emulator.json). No API key, device serial, private URL or raw UI is included.

中文结论：模拟器四类任务各跑三轮，两组均 12/12 成功；Jev 中位数 8.60 秒，现有工具 2.70 秒。固定路径继续用批处理；尚未配置逐步大模型基线，不能声称 Jev 比大模型更快、更省或成功率更高。
