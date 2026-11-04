# Agent-only vs Agent + Jev: live workflow pilot

Latest: [faster Android queries and new paired simulator comparison](agent-jev-v3.md). This page preserves earlier measurements.
Latest: [three-repeat Agent / Jev decision / Android Use execution comparison](agent-jev-v2.md). This page preserves earlier measurements.
2026-11-04 · Android API 35 ARM64 **emulator** · experimental, unreleased.

The host Agent remains responsible for understanding, planning, exact input text, recovery and final verification. Jev handles repeated UI decisions within a short delegated goal. Normal use requires the existing Agent and TypeSafe key; **no separate text-model API configuration is required**.

## What was actually tested

The current Codex Agent answered live JSONL requests from `scripts/benchmark_agent.py`. There was no scripted choice of action IDs. Both arms used the same synthetic app/tasks and an independent final selector check, followed by the host Agent's verification.

- **Agent-only:** the Agent observed each newly opened page and chose ordinary Android Use operations. `batch`, action-plus-wait and `scroll_until` remained available and were actually used. The baseline was not forced to decide on every swipe or keystroke.
- **Agent + Jev:** the same Agent delegated each local goal using `aua_execute_goal`, supplied exact text/completion criteria, then verified the outcome. A handoff would return to the same Agent with current UI for recovery. No handoffs occurred in these four completed trials, so live recovery is not established by this pilot.
- Four task pairs, one repeat: **8 completed trials**. Each arm got a recreated activity. Order alternated by task. The host wall budget was 180 s; each Jev goal remained bounded at 60 s. The temporary app was removed and the original activity restored.

## Observations

| Measure | Agent-only | Agent + Jev |
| --- | ---: | ---: |
| Verified fixture tasks | 4/4 | 4/4 |
| Host decision replies, including final verification | 13 | 8 |
| Jev API requests | 0 | 14 |
| Estimated Jev API cost for all four tasks | $0 | $0.001307922 |
| Host token usage / cost | Unavailable | Unavailable |
| Total model cost | Unknown | Unknown |

The protocol needed **5 fewer host decision replies (38.5%)**. These are counted responses to planning/action/verification requests, **not** billed model API calls, all internal inference turns, token totals or a measured cost reduction.

Exploratory wall times include chat/CLI bridge waits, polling, commentary and Agent deliberation, as well as device work and final verification. These are full recorded wall times, **not a controlled estimate of model or MCP latency**:

| Task | Agent-only wall time | Agent + Jev wall time | Host replies: only → hybrid |
| --- | ---: | ---: | ---: |
| Navigate / delayed result | 53.41 s | 22.62 s | 3 → 2 |
| Chinese local draft | 25.49 s | 27.89 s | 3 → 2 |
| Nested scroll / select | 48.89 s | 41.19 s | 4 → 2 |
| Navigate / long press | 23.72 s | 26.39 s | 3 → 2 |
| Median of four observed wall times | 37.19 s | 27.14 s | — |

Two tasks were slower in the hybrid arm. The navigation baseline also includes substantial host/bridge deliberation; the median should **not** be advertised as a stable percentage speedup. The useful result here is that the intended delegation/return/verification workflow works and reduces requested host decisions in this particular page-by-page policy.

## Practical routing

Use `batch`, action-plus-wait or `scroll_until` for known paths. A competent Agent may combine more known steps than this pilot's page-by-page policy, further reducing its own decision count. Jev is useful to evaluate for unfamiliar local navigation or repeated choices that would otherwise send the host back to the model on every step. Return to the Agent for ambiguity, missing coverage, authentication, cross-app planning, generated text or final business verification.

This pilot does not prove lower total cost, general task accuracy, independent fresh-session performance, live recovery quality or production-app speed. All tasks share one familiar fixture and one chat context. A controlled repeated API-based planner comparison can still be run with the optional `TEXT_MODEL_*` settings, but those settings are not a prerequisite for using Agent + Jev.

## Reproduce / inspect

The updated Python suite passes **108 tests**, including normal host batch support, final-verification timeout recording and preserving unknown host costs. Ruff passes. The earlier six browser regressions remain applicable; this change does not modify the screen UI.

After normal service setup and READY:

```bash
uv run --env-file .env --frozen python scripts/benchmark_agent.py \
  --serial emulator-5554 --repeats 1
```

The host Agent reads `choose`, `delegate` and `verify` events and writes one JSON response. `choose` accepts a candidate ID or an ordinary tool/args call, so batch and scrolling helpers stay available. A hybrid handoff invokes the same host interface for recovery. This is an interactive host-Agent benchmark, not a headless script that can answer its own prompts.

The initial protocol preflight used the Jev low-level loop for the host baseline, which did not represent ordinary Agent tool usage. It completed three trials, then timed out during scrolling; its final-verification timeout also exposed a record-handling bug. That preflight was excluded from the completed pilot. The driver was corrected to keep the host's normal helpers, use an outer host budget, and treat final verification timeouts as failure. A post-pilot defensive validation fix does not change the successful measured paths.

[Sanitized completed-trial data](agent-jev.json) · [Earlier execution-floor measurements](jev-emulator.md) · [Agent + Jev setup](../jev.md#agent--jev)

中文：正确分工是 Agent 规划、Jev 执行局部步骤、Agent 验收/恢复。这轮两组均 4/4 成功，宿主决策回复 13→8；其中中文草稿、长按两项混合组更慢。聊天桥接等待会影响耗时，不能据此宣传稳定提速，也没有宿主账单来证明总费用降低。固定路径继续优先批处理。
