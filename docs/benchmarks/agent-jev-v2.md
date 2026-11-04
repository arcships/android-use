# Agent decisions vs Agent + Jev decisions

Latest: [faster Android queries and new paired simulator comparison](agent-jev-v3.md). This page preserves earlier measurements.
2026-11-04 · Android API 35 ARM64 **emulator** · three paired repeats · experimental source feature.

**Division of responsibility:** the host Agent plans and routes a task; Jev is a next-action **decision model**; Android Use builds candidates, validates the selected ID and executes the action. The Agent verifies outcomes and recovers from a handoff.

## Design

Five task types × three repeats × two arms = **30 completed trials**, with **15/15 verified successes per arm**. Every run recreates the permission-free fixture. Order alternates by task/repeat. The host uses the same chat and Android Use tools; there is no scripted action selector. Both arms keep `batch`, `scroll_until`, action-plus-wait and native text verification. A separate UI selector and host reply verify the final result. Failed trials would remain in the output; none were removed or rerun in this measured set.

Four familiar tasks cover navigation/wait, Chinese draft input, nested scrolling/selection and long press. The Agent knows these fixture paths from earlier development and legitimately batches them. Both arms used the same batches; the hybrid arm made **zero Jev requests** on them.

The fifth task opens Workflow and follows four successive **Next color** instructions. The next instruction is observed after each action; wrong selection produces `WORKFLOW_FAILED`. Repeat seeds are 41, 1038 and 2035, identical for both arms of each pair. The host followed visible instructions; it did not precompute the sequence from the seed. The hybrid Agent delegated this short goal, Jev chose five successive action IDs (open + four colors), and Android Use executed them. Both arms verified `WORKFLOW_DONE`.

The host wall budget is 180 s; each delegated goal is limited to 60 s. Setup/build/install/reset/cleanup are outside measured time. The source hashes and sanitized records are in [agent-jev-v2.json](agent-jev-v2.json).

## Results

Medians below include Agent/CLI interaction and final verification. Each task cell has three observations:

| Task | Agent-only end-to-end | Agent + Jev end-to-end | Verified success per arm | Host replies, total: only → hybrid |
| --- | ---: | ---: | ---: | ---: |
| Known navigation / delayed result | 17.31 s | 22.89 s | 3/3 | 6 → 6 |
| Known Chinese local draft | 16.59 s | 17.41 s | 3/3 | 6 → 6 |
| Known nested scroll / selection | 43.88 s | 37.40 s | 3/3 | 6 → 6 |
| Known navigation / long press | 18.49 s | 21.00 s | 3/3 | 6 → 6 |
| UI-dependent color workflow | **41.27 s** | **29.58 s** | 3/3 | **18 → 6** |
| All 15 runs per arm | **19.63 s** | **23.30 s** | **15/15** | **42 → 30** |

In the UI-dependent task, the observed end-to-end median was 28.3% lower, with 66.7% fewer host decision replies. Across the full task mix the hybrid median was **higher**, despite 28.6% fewer host replies. These observations support selective delegation, not a general speedup claim.

## Where time went

The driver separately measures time from emitting a host request to receiving its JSON reply. This **host wait** includes host inference, commentary/deliberation, CLI bridge and polling. Subtracting it gives non-host wall time, including device checks/actions, Jev HTTP, serialization and verification. Neither component is a direct measurement of pure host-model latency.

| Task subset | Agent-only host-wait median | Hybrid host-wait median | Agent-only non-host median | Hybrid non-host median |
| --- | ---: | ---: | ---: | ---: |
| Dynamic color workflow | 33.86 s | 16.42 s | 7.42 s | 13.37 s |
| Known navigation | 13.70 s | 19.26 s | 3.61 s | 3.63 s |
| Known draft | 12.52 s | 13.27 s | 4.06 s | 4.14 s |
| Known scroll | 24.00 s | 17.94 s | 19.12 s | 19.24 s |
| Known long press | 14.73 s | 16.86 s | 3.93 s | 4.02 s |

The same known-path device work stayed close between arms; host wait explains most of its wall-time variation. Dynamic delegation added executor/inference work but avoided four host replies per trial. The live bridge has nonuniform delays, so the 28.3% observation is not a stable API/MCP latency guarantee. No provider or threshold tuning occurred between these three measured repeats.

## Cost and success

- Jev: **15 requests**, **29,370 input / 1,014 output tokens**, all reported as `jev-1.14.0`; zero stale decisions and zero unpriced requests.
- At [the verified TypeSafe price](https://docs.typesafe.ai/models), input is $0.042/M tokens and output is free: **$0.00123354 total**, or **$0.00041118 per delegated workflow**. Known paths incurred no Jev API charge. These are usage-based estimates, not invoice reconciliation; separate acceptance calls are excluded.
- Host usage and per-task billing are **not exposed by this chat**. Host cost and total cost remain `null` for both arms. A smaller number of replies does not prove a particular token or dollar saving. UTF-8 protocol byte counts are recorded for inspection only; the two interfaces present UI differently, and bytes are not billed tokens.
- The dynamic task saved 12 host replies for $0.00123354 of added Jev cost. A hypothetical cost break-even is $0.000102795 per avoided reply **if all other host costs were equal**. That assumption was not measured and is not a claim of savings.
- Both arms succeeded 15/15, including 3/3 dynamic sequences. This small synthetic fixture does not establish real-app success rates, visual reasoning, authentication, cross-app planning or difficult recovery performance.

## Implementation verified

Jev receives state/candidate descriptions and returns an ID/probabilities. It receives no backend/Runtime object and cannot supply executable code or device commands. Android Use owns the bounded loop, semantic freshness checks, allowed-action validation, input verification, pause/lock guards and once-only mutation ledger.

The optimized fingerprint ignores global XML `id`/`parent` offsets while preserving visible app-node semantics, geometry, enabled/focused flags, context and ordering. This avoids false staleness when unrelated System UI nodes shift traversal indices. Real target changes still discard a decision. Safe completed/handoff results can reuse the last checked tree; authentication/transport/scope errors do not expose an old tree. Results separate `roles`, decision audit, action ledger and structured handoff.

The paired trials had no handoffs. A separate actual stdio MCP acceptance exercises a one-step goal budget, then resumes only the remaining draft actions through ordinary tools; see [acceptance record](jev-mcp-acceptance-v2.json). That recovery test is outside timing/cost aggregates.

Validation: **111 Python tests**, Ruff and **13 actual stdio MCP scenarios (73.06 s)** passed, including the new bounded handoff/recovery case. The six earlier screen-UI browser tests remain applicable; no UI source changes were made here.

## Reproduce

Use an authorized unlocked emulator/device, JDK, Android SDK and running Android Use service. Set the local TypeSafe opt-in/key as described in [setup](../jev.md). No `TEXT_MODEL_*` is needed for the live host benchmark.

```bash
uv run --env-file .env --frozen python scripts/benchmark_agent.py \
  --serial emulator-5554 --repeats 3
```

The host reads `choose`/`delegate`/`verify` events and sends one JSON reply. It may choose ordinary tools in either arm and delegate only in the hybrid arm. Output defaults to ignored `test-results/agent-jev-v2.json`. This requires a live Agent, not a script choosing actions for itself. Temporary app installation is refused if the fixture already exists; it is uninstalled and the original activity restored on exit.

[Earlier single-repeat pilot](agent-jev.md) · [Historical deterministic execution-floor comparison](jev-emulator.md)
