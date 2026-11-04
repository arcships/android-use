# Read-only automatic OCR acceptance · 2026-11-04

[Sanitized attempts](real-app-ocr-v6.json) · [Parameters and limits](../jev.md#automatic-read-only-ocr) · [Page-routing plan — 中文](../visual-routing.zh-CN.md)

This is iterative acceptance on an authorized physical Android phone, using source CLI / a separate **standard stdio MCP** process, local macOS Vision and live Jev 1.14.0. It is not an Agent-only comparison, a measured production success rate, or an exhaustive cheapest-drink search. Code, ROI and confidence handling changed during development. Every recorded field/goal attempt, including diagnostic failures, remains in the JSON; navigation actions are outside this field report.

## Observed results

| App / path | Checked bill (CNY) | Jev calls | Jev time | Local OCR | Field call elapsed |
| --- | --- | --- | --- | --- | --- |
| Meituan / forced screen OCR | 19 + 1.5 + 2.4 = **22.9** | 1 | 0.315 s | 1.048 s | 4.417 s |
| Meituan / automatic AX | Same amounts | 1 | 0.376 s | None | 1.394 s |
| Taobao / automatic OCR | 24 + 1.5 + 2.3 − 6 = **21.8** | 1 | 0.325 s | 1.355 s | 4.054 s |

Taobao's checkout supplied no useful AX text (`ax_rows=0`), so `source=auto` selected device-owned OCR **before the first Jev request**. No `evidence_text` was supplied. Its output was `source=device_screen_ocr`, `freshness=roi_pixels_checked`, six accepted fields, and a matching signed-amount sum. Jev distinguished the currently charged delivery fee from the adjacent old fee and classified the already-applied coupon separately from the unchecked card upsell.

Meituan's native text stayed on AX with no screenshot/OCR work. Forced OCR reached the same bill and classified the coupon-pack advertisement as requiring purchase. Its final ROI excluded the address; explicit masks removed an animated tooltip arrow and unrelated payment details before OCR and pixel hashing. These are caller-specified exclusions, not automatic detection of irrelevant animation.

The Agent independently inspected both actual checkout screens, including the unselected paid-card upsell, then removed only the temporary drink in each app and visually confirmed empty carts. No order submission, payment or new card/coupon purchase occurred. The drinks/configurations differ, so the two totals cannot establish a cheapest-price ranking.

## Retained failures and revisions

- One acknowledged Jev navigation hit a transient empty tree and handed off with `goal_empty_tree`. It did not repeat the tap. The final executor now permits bounded post-action observation through transient empty trees, while keeping pre-action tree and full auth/scope guards. Delayed rendering, geometry-only animation, cycling back to a consumed action, and guard interruptions have offline regression tests.
- The first OCR request rejected changing ROI pixels (`stale_field_evidence`). Investigation located an animated arrow; an explicit small mask excluded it. Arbitrary changes remain rejected rather than accepted through a similarity threshold.
- Apple Vision correctly read the red total but reported confidence about **0.3**. An initial hardcoded 0.5 floor rejected it; changing block attribution alone did not solve this. The final default treats the provider score as uncalibrated telemetry (`min_ocr_confidence=0`); callers may set a validated floor for money/text candidates. This does not prove accuracy: Jev gates, exact sums, unchanged pixels and Agent business verification remain separate checks. Choice classifications do not have a candidate-level OCR score.
- Reusing an ROI after checkout scrolling omitted fee rows in both apps; native and OCR attempts returned missing fields and failed sums. The Agent observed the actual page, revealed the bill and supplied a suitable ROI. The tool did not scroll or infer missing charges.
- The final OCR frame ID is diagnostic provenance, not an action `observation_id`. No automatic OCR-based tap was performed or implemented.

Later refinements to transient-empty-tree handling and authentication-prompt recognition passed offline tests only. Phone testing stopped at the user's request; they were not revalidated on the phone. A separate full video-rollover check was also interrupted and is not claimed as passed here.

## 中文

本轮将自动 OCR 接入只读字段工具：淘宝空语义树自动走本机 OCR，无需 Agent 传入识别文本；美团优先用树，也验证了强制 OCR。费用原文、负号和确定性等式均保留，最终由 Agent 独立对照画面验收。两边临时商品已清空，没有下单、付款或购买券包。

记录保留了动画导致过期、低 OCR 分数拒绝、滚动后 ROI 失效、字段缺失和点击后过渡空树等失败。ROI 与显式 mask 仍由 Agent 根据当前页面提供；OCR 不提供划线样式证明，也不会自动找图点击。本轮不构成整体提速、成功率或最低价结论。用户要求停止手机操作后，仅进行了离线测试。
