# Faster Android queries: Agent vs Agent + Jev

2026-11-04 · dedicated Android API 35 ARM64 emulator · speed and successful completion first.

## Implementation change

The performance probe identified repeated ADB queries, not the XML tree read, as the main local overhead. Android Use now reads viewport dimensions from one UiAutomator `deviceInfo` RPC instead of separate size/rotation shell queries. Foreground lookup first parses a single `dumpsys window displays` result; the existing client path remains the fallback for missing, ambiguous or unsupported focus information and multiple displays.

No lock, pause, foreground, freshness, input verification or once-only mutation checks were removed. The model division is unchanged: **Agent plans, Jev decides candidate IDs, Android Use validates and acts**. Both Agent-only and hybrid paths benefit from the faster backend. Cost is diagnostic telemetry, not a routing constraint.

## Query probe

Seven warm samples of each method on the same running emulator, using the actual legacy client methods and new methods:

| Query | Legacy median | Optimized median | Observed reduction |
| --- | ---: | ---: | ---: |
| Current viewport | 78.61 ms | 19.33 ms | 75.4% |
| Foreground package/activity | 124.14 ms | 29.01 ms | 76.6% |

Outputs matched in the probe. The launcher ignored a requested landscape orientation, so those initial requests are **not** presented as landscape validation. The separate acceptance app did rotate: both portrait and landscape dimensions were asserted against the legacy query, actual screenshot dimensions and MCP observation. See [query records](backend-profile-v3.json) and [14-scenario MCP acceptance](jev-mcp-acceptance-v3.json).

The source basis is the installed pinned client and the [upstream DeviceInfo implementation](https://github.com/openatx/android-uiautomator-server/blob/master/app/src/androidTest/java/com/github/uiautomator/stub/DeviceInfo.java), which reports UiDevice display width/height. Emulator timings are not guarantees for other devices; unsupported focus formats retain the legacy path.

## New paired simulator run

The focused comparison repeats **two task types × three paired repeats = 12 trials**. The familiar Chinese draft uses the same batch in both arms; the dynamic Workflow requires observing each new color instruction. Seeds 41, 1038 and 2035 match within each pair. The same live Agent replies over the JSONL bridge, and a fresh independent selector plus host reply verifies each completed result. Installation/reset/cleanup are excluded. Host waiting is measured separately; all failures remain in the records.

| Primary run | Agent-only | Agent + Jev |
| --- | ---: | ---: |
| Completed successfully | **5/6** | **6/6** |
| Known draft | 3/3 | 3/3 |
| Dynamic workflow | 2/3 | 3/3 |
| Host replies received | 18 | 12 |
| Jev decision requests | 0 | 15 |

The third Agent-only dynamic trial received **no host reply within 180 seconds**, before its first device action. It remains a failed `host_timeout` trial. This is a chat/CLI delivery-timeout observation, **not evidence that the host selected a wrong action or Android execution failed**. Do not interpret 5/6 vs 6/6 as a model accuracy comparison.

Raw end-to-end medians, including host waiting and the timeout:

| Task | Agent-only | Agent + Jev |
| --- | ---: | ---: |
| Known Chinese draft | 22.05 s | 14.61 s |
| Dynamic workflow | 61.60 s | 20.56 s |

The raw dynamic contrast is inflated by host delivery variation and the timeout; it is not a defensible general speedup percentage. On the known path both arms performed the same batch, with non-host medians of **2.69 s / 2.77 s**. Their different wall times primarily reflect host waiting.

## Device-side improvement

All three primary hybrid dynamic trials completed with five Jev decisions each and no stale decisions. The bounded local loop itself (including the returned checked observation, but excluding host planning and host verification wait) measured:

| Same dynamic workflow | Previous implementation, three runs | Optimized implementation, three runs |
| --- | ---: | ---: |
| Local goal-loop median | **12.45 s** | **7.38 s** |
| Device-read median within loop | 7.77 s | 3.78 s |
| Device-action median within loop | 1.35 s | 1.10 s |
| Jev HTTP median total per loop | 1.08 s | 1.03 s |

The observed local-loop reduction is **40.8%**. Previous values come from [the v2 records](agent-jev-v2.json); the task, seeds, Jev model and action policy are the same. This is a cross-run comparison, not randomized old/new backend A/B testing. The separate same-emulator legacy/new query probe supports the source of the reduction. It does not measure model inference speed or guarantee overall Agent latency.

## Separate timeout follow-up

Seed **2035** was rerun once per arm as a separate follow-up, with a 600-second host-interaction budget and the dedicated emulator kept awake. The production local-goal limit stayed at 60 seconds. Both arms passed: Agent-only **41.88 s**, hybrid **21.88 s**. The Agent followed all four visible instructions; no hardcoded action sequence was substituted. These two records are **not merged into or substituted for the primary 12 trials**.

The CLI gained explicit `--seed-base` and `--host-budget` flags to reproduce an individual case. Default seed/budget retain the primary-run behavior. The original driver hash and the follow-up driver hash are retained in their respective records.

## Validation and records

- **125 Python tests** and Ruff passed, including viewport field validation, portrait/landscape dimensions, unsupported focus fallback, permissions popup ownership and ambiguous/multidisplay fallback.
- **14 actual stdio MCP emulator scenarios passed in 61.92 s**, including the original interactions, no replay, pause, Jev decisions, bounded handoff/recovery, and portrait/landscape matching the legacy query and screenshot.
- The earlier six browser regressions apply to the unchanged live-screen UI. Wheel/sdist and isolated plugin-package checks are also required before pushing. No existing v0.3.0 release asset is replaced.
- [Primary 12 trials, including timeout](agent-jev-v3.json) · [Separate follow-up pair](agent-jev-v3-followup.json) · [Query probe](backend-profile-v3.json) · [MCP acceptance](jev-mcp-acceptance-v3.json).

Jev usage remains recorded as auxiliary data. Primary hybrid trials used 15 calls / 29,370 input tokens. Host billing is not available and is not used to choose routes. No monetary saving is claimed.

## Reproduce

```bash
uv run --frozen python scripts/profile_backend.py --serial emulator-5554 --samples 7
uv run --env-file .env --frozen python scripts/benchmark_agent.py \
  --serial emulator-5554 --repeats 3 --tasks draft_zh dynamic_workflow_en \
  --output test-results/agent-jev-v3.json
uv run --env-file .env --frozen python scripts/benchmark_agent.py \
  --serial emulator-5554 --repeats 1 --tasks dynamic_workflow_en \
  --seed-base 2035 --host-budget 600 --output test-results/agent-jev-v3-followup.json
uv run --env-file .env --frozen python scripts/accept_device.py --serial emulator-5554 --jev
```

Start and verify the normal control service first. The interactive comparison requires a live host Agent responding to prompts. Use an authorized unlocked device; the recorded runs used a dedicated emulator. Fixture replacement is refused, and temporary installation is removed on exit.
