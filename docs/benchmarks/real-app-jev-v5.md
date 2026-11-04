# HEYTEA checkout acceptance · 2026-11-04

[Sanitized attempts](real-app-jev-v5.json) · [Jev guide](../jev.md) · [中文](#中文)

This is iterative acceptance on an authorized physical Android phone, using live `jev-1.14.0`, the source CLI and a separate **standard stdio MCP** server. It resumes known stores from the earlier comparison. It is **not** a full search, an exhaustive cheapest-drink result, or a matched Agent-only / Agent + Jev speed benchmark. Code, evidence layout and question wording changed during the run; failed attempts remain in the JSON.

## Observed behavior

- A Meituan page with 132 semantic nodes / 63 candidates reached Jev instead of the old local context handoff. One request selected both the operation and its target, opened the intended drink specifications, and met the supplied completion selector: **2.175 s** local goal time, **0.318 s** model time, **14,143 bytes** model-facing state/questions. This was before the later duplicate-wrapper pruning.
- Jev selected the requested sugar option and subsequently added one drink. Later ambiguous/low-probability targets handed back to the Agent. The Agent closed the specifications and opened checkout. A wait for `提交订单` timed out because this profile displayed `极速支付`; the acknowledged navigation was not replayed.
- The first Meituan field request incorrectly selected the old delivery fee **5.4** with high probability. Restoring visual text rows, restricting the evidence to the bill region, adding precise current-price instructions and a code-side sum check produced **19 + 1.5 + 2.4 = 22.9**. Five questions used one request: **1.415 s** local field time, **0.308 s** model time, **6,062 bytes**. The coupon-pack advertisement was classified as requiring purchase.
- Taobao's miniapp exposed empty containers rather than usable product controls. Jev returned `model_blocked` with no action; the Agent used observed screenshot coordinates. An initial OCR judgment left delivery/discount unresolved and failed the requested sum check. After grouping the same OCR into visual rows and clarifying the roles, six fields passed through a separate stdio MCP call: **24 + 1.5 + 2.3 − 6 = 21.8**, **1.206 s** field time, **0.307 s** model time, **5,231 bytes**. The source is explicitly `caller_provided_text`, **not** verified phone text.
- Full-tree geometry changes initially discarded read-only field judgments. Field validation now checks unchanged context and the selected region's text rows; the action executor retains its stricter full-tree guard. Lock, password and user-pause checks still cover the entire snapshot.
- Device-busy and foreground-change interruptions also occurred. They are retained, not counted as successful model decisions. Older failed field outputs lack provider telemetry; their calls/cost cannot be assumed zero.

The Agent independently inspected both checkout screenshots. No order, payment or new coupon/card purchase was made. Both temporary drinks were removed and both empty carts were visually checked. The two observed orders differ in drink and configuration; these numbers are not a universal lowest-price claim.

## Reproduce safely

1. Update a source checkout and enable Jev through local settings or an ignored `.env`. Confirm setup/READY and select the intended device.
2. Resume a known store or have the Agent search from the actual screen. Supply a narrow goal, exact texts and a completion selector matching the current profile.
3. At checkout call `read_fields` with the current package, requested field roles, a bill `region`, and a `sum_check` covering all charges/discounts. Copy printed signs exactly and include only one currency in a check.
4. For AX-poor pages, the Agent must capture/inspect a current screenshot and obtain/group OCR externally before passing `evidence_text`. The built-in tool does **not** perform OCR, vision grounding or automatic coordinate clicks.
5. Inspect `accepted`, `checks`, source provenance and the actual checkout. Remove only the temporary items added for the test. Never submit or pay during acceptance.

## 中文

这轮验证的是已知喜茶门店的规格选择与结算核对，不是完整搜索、全量最低价证明，也没有同条件 Agent-only 对照。最终美团为 **22.9 元**、淘宝为 **21.8 元**；饮品和规格不同。

保留了配送原价误判、低确定性交回、页面变化、等待条件不符、OCR 字段缺失和设备占用等失败。修正依赖于视觉行分组、限定账单区域、明确字段角色与代码金额校验，不能把高概率直接当作正确率。

淘宝动作由 Agent 看图完成，字段使用本机 Vision 生成并按视觉行组合的 OCR，明确标为 Agent 提供的文本。OCR 工具是本次验收辅助手段，未内置到产品。两边均未下单、付款或购买券包，临时商品已清理并目视确认。
