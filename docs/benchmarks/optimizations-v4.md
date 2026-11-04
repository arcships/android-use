# Phase-scoped sampling and verified input macros

2026-11-04 · Android API 35 ARM64 emulator · existing MCP tools preserved · unreleased source.

**Agent plans and verifies; Jev selects candidate IDs; Android Use checks and executes.** Completion time and successful outcomes take priority over token cost.

## What changed

1. Within one validation phase, screen-on and viewport checks reuse one `deviceInfo` response. Context checks still bracket every tree; no sample is cached across inference, actions or waits. Safe final observations reuse their checked context.
2. An optional `focus_type_text` candidate combines focus, exact allowlisted input and native readback into one Jev decision. Each native mutation has its own guard and ledger entry. Stable unique field identity is required; layout changes before writing, overlays, passwords, lock and pause cause a handoff. A post-write resize from font metrics is allowed for the same visible, focused field, with exact native text verification. No submit, newline, IME switch or replay is added. `input_macros=false` retains separate decisions.

`max_steps` counts candidates; an input macro contains at most two native mutations. `substeps` distinguishes executed focus, uncertain input and verified readback. Original tool parameters and batch semantics are unchanged. [Configuration and full boundaries](../jev.md#fewer-reads-and-input-decisions).

## Controlled source comparison

**Two tasks × three matched seeds × three arms = 18 trials. All 18 passed.** Each trial resets a permission-free native fixture; order rotates and seeds 41, 1038 and 2035 match across arms. Every decision uses the real pinned Jev model; the task supplied by the host Agent and independent final selector are identical. This isolates executor changes; it is not a replacement for the live Agent comparison below.

- Baseline: exported source at `2b930af652c7dd267233e6c26bb19e7d6cc5c740`.
- Sampling only: new source with `input_macros=false`.
- Sampling + macro: new source with `input_macros=true`.

Each worker uses the same interpreter/dependencies and TypeSafe credentials, with its source selected through `PYTHONPATH`. Records contain source/fixture hashes, metrics, candidate IDs, mutation ledgers and independently verified outcomes. No raw UI or credentials are saved. Counts below are actual `deviceInfo` property reads inside each goal call, including guards and final observation; they are not all ADB/RPC calls.

| Task / median | Baseline | Sampling only | Sampling + macro |
| --- | ---: | ---: | ---: |
| Chinese draft: local goal loop | 7.38 s | **5.36 s** | 5.98 s |
| Chinese draft: deviceInfo reads | 62 | 34 | **31** |
| Chinese draft: Jev decisions | 4 | 4 | **3** |
| Chinese draft: native actions | 4 | 4 | 4 |
| Dynamic four-color workflow: local loop | 11.08 s | **8.42 s** | 10.87 s |
| Dynamic workflow: deviceInfo reads | 74 | **40** | **40** |
| Dynamic workflow: Jev decisions / native actions | 5 / 5 | 5 / 5 | 5 / 5 |
| Independently completed | 6/6 | 6/6 | 6/6 |

The sampling-only medians were **27.3% / 24.0% shorter** than baseline, and deviceInfo reads fell **45.2% / 45.9%**. Adding the macro reduced draft decisions by **25%**, with the same four native actions, but **did not improve wall time over sampling-only in this sample**. The macro retains extra verification; this small sample cannot separate its overhead from timing variability. The macro-enabled draft still completed 19.0% faster than baseline.

The dynamic task contains no editable field: the two optimized arms execute the same path, yet their medians differ. This exposes substantial timing variability and the limits of three samples. Do not infer a general speedup, a reliable macro slowdown, or model accuracy from these timings. Keep batch for known paths; use `input_macros=false` when separate decisions perform better in your environment.

Local-loop timing includes Jev requests, checks, actions, observations and the returned checked tree. Separate wall timing includes worker startup and independent verification. Neither includes installation, reset or cleanup. Jev usage remains diagnostic; no cost-based routing or total-cost claim is made.

### Failures retained

The [initial interrupted comparison](optimizations-v4-initial.json) is retained separately. One input macro wrote the correct Chinese text, then handed off as uncertain because the same EditText grew from 118 to 124 pixels. The original post-write geometry check was too strict. The fix allows that resize after writing while retaining unique identity, visible focus, context checks and exact native readback; geometry/layout changes before writing still stop input. A dedicated regression covers this. Completed initial trials are not pooled into the fixed implementation's 18 trials.

Two earlier benchmark bootstrap attempts failed before any goal action because the metering harness tried to assign a read-only backend property. The harness was corrected to wrap the underlying device. An initial full MCP run stopped at the existing offscreen-scroll assertion before reaching Jev; the emulator had also shown a System UI ANR after boot. This is retained as an acceptance attempt, not counted as a passed scenario or a model failure.

## Live Agent comparison and validation

**Two tasks × three matched repeats × two arms = 12 live trials. Agent-only and Agent + Jev each passed 6/6.** The same current host Agent made the decisions through the interactive bridge; no scripted next-action selector or extra baseline API was substituted. Both arms used the same batch for the familiar draft. Hybrid delegated the dynamic workflow with `tap`/`wait`, then the Agent independently verified the visible result. Agent-only does not use the internal Jev macro; the three-arm comparison above tests that macro separately.

| Live task / median | Agent-only | Agent + Jev |
| --- | ---: | ---: |
| Familiar Chinese draft, end-to-end | 27.15 s | 30.94 s |
| Draft, excluding measured host wait | 3.83 s | 6.47 s |
| Dynamic workflow, end-to-end | 47.44 s | 27.20 s |
| Dynamic workflow, excluding measured host wait | 7.47 s | 10.48 s |
| Dynamic workflow, host wait | 38.82 s | 16.72 s |
| Dynamic workflow, total host replies over three repeats | 18 | 6 |

The dynamic end-to-end median was 42.7% shorter **in this chat/CLI experiment**. That contrast includes inference, prompt delivery, tool bridge and polling. The hybrid's non-host work was longer because it also runs Jev and its freshness checks. The known batch path did not improve. These results support fewer host interactions on dynamic tasks, not universal faster device actions or model accuracy. All 12 trials and measured waits are in [the live records](agent-jev-v4.json).

The host-interaction budget was explicitly set to 600 seconds, with the dedicated emulator kept awake; local goal calls still used a 60-second limit. There were no timeouts in this run. Historical [v3 timeout and follow-up records](agent-jev-v3.md) remain unchanged and are not pooled into this result.

Final validation:

- **151 Python tests**, Ruff and wheel/sdist build passed, including per-phase query counts, rotation/pause, stable/ambiguous/offscreen bindings, Unicode readback, pre-write layout changes, post-write resize, partial/uncertain ledgers and no replay.
- **15 actual stdio MCP scenarios passed in 86.98 seconds**, with 23 tools. Includes original gestures/waits/scroll/pause, Jev decisions, handoff/resume, the Unicode input macro and real portrait/landscape dimensions matching legacy queries and screenshots. [Acceptance record](jev-mcp-acceptance-v4.json).
- The installed fixture was removed and original foreground restored after each harness. The new measurements use the emulator; no new physical-device performance claim is made.
- All **six browser regressions** passed, including PNG clipboard copy and native MCP App integration. An isolated plugin ZIP includes the macro and benchmark and excludes credentials/unrelated artifacts. The v0.3.0 release assets are unchanged.

## Reproduce

Start the normal control service once if absent, then verify READY on an explicitly selected, unlocked emulator. Requires a TypeSafe key, JDK and stable Android SDK platform/build tools. No separate text-model API is needed.

```bash
uv run --env-file .env --frozen python scripts/benchmark_optimizations.py \
  --serial emulator-5554 --repeats 3 --output test-results/optimizations-v4.json
uv run --env-file .env --frozen python scripts/benchmark_agent.py \
  --serial emulator-5554 --repeats 3 --tasks draft_zh dynamic_workflow_en \
  --host-budget 600 --output test-results/agent-jev-v4.json
uv run --env-file .env --frozen python scripts/accept_device.py --serial emulator-5554 --jev
```

The live comparison requires a host Agent replying to the JSONL prompts. All scripts refuse to replace an existing fixture and remove their temporary installation on exit, restoring the original foreground when possible. The fixture suppresses keyboard opening; third-party apps, exposed IMEs, visual controls and physical-device performance are outside this new comparison.

[18-trial raw comparison](optimizations-v4.json) · [Initial interrupted comparison](optimizations-v4-initial.json).
