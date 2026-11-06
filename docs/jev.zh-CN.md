# Agent + Jev 决策集成（实验性，v0.4.0 起提供）

[English](jev.md) · **简体中文** · [项目首页](../README.zh-CN.md)

新增标准 MCP 工具 `aua_execute_goal`，在本地执行有边界的短目标。全部原有工具及参数保留，无云端密钥也可继续使用。Jev 默认关闭，**v0.4.0 ZIP 已包含此功能**；v0.3.0 用户需更新。

参考 [browser-use/jev-ultrafast](https://github.com/browser-use/jev-ultrafast) 独立实现 Android 版本：读取可访问性树 → 本地生成动作候选 → Jev 选 ID → 执行一次 → 再观察。不需要安装浏览器项目，不依赖文本生成模型。

## 无 Key：宿主决策模式

`decider` 默认 `auto`：已启用且配置 Key 时用 Jev，否则用 `host`。也可显式传 `"decider": "jev"` 或 `"decider": "host"`。宿主模式下不向外发送任何数据：

1. `aua_execute_goal` 返回 `status=decision_required`，附带精简的 `state`、`options`（候选 ID 以及 `DONE` / `BLOCKED`）和一次性 `token`。
2. Agent 调用 `aua_goal_decide`，传入 `{"token": ..., "choice": "a3"}`。
3. Android Use 重新读屏。界面变化则丢弃该选择，返回新的决策请求并标记 `previous_choice_stale=true`；否则执行该候选一次、检查效果，再返回下一步决策、完成或交回结果。

令牌只能用一次，空闲 5 分钟过期，保存在当前 MCP 服务进程内存中，一次性 CLI 进程无法续接。无效的选项 ID 不会消耗令牌。`timeout_seconds` 只计设备时间，不计 Agent 思考时间。宿主模式用更多往返换来零配置；长决策循环仍是 Jev 更快。

## Agent + Jev 的分工

路由按端到端完成速度、成功率和减少宿主大模型往返优先。Jev token 费用只作辅助记录，不作为路由约束。连续的局部决策交给同一次调用内的 Jev 循环处理，安全返回的观察用于后续规划，Agent 保留规划、恢复和业务验收职责。

现有 Agent 理解用户需求、规划子目标、编写准确输入内容、打开目标 App，并验收业务结果。需要反复读树选动作的**短局部目标**交给 `aua_execute_goal`：Android Use 读屏并生成候选，Jev 只返回候选 ID 与概率，Android Use 校验后执行一次动作。成功或不确定时返回同一个 Agent，由 Agent 查看已执行步骤和当前位置，决定继续、恢复或转向。长任务按子目标分段，不把完整任务全部交给 Jev。

已知路径继续用 batch / scroll_until。预期收益来自减少 Agent 的高频决策与工具往返，正确对比是 **Agent 逐步操作 vs 同一个 Agent + Jev**，包含规划、交回恢复和最终验收。此前确定性工具对比只反映执行开销。日常使用只需要现有 Agent 和 TypeSafe 密钥，**无需配置 `TEXT_MODEL_*`**；那组变量仅用于另一个 API 基线测试脚本。

[最新性能对比与原始记录 →](benchmarks/optimizations-v4.md)。`scripts/benchmark_agent.py` 用实时 JSONL 让当前 Agent 选择普通工具或委托 Jev 决策，并负责恢复、最终验收；驱动单独记录宿主等待，宿主没有提供逐任务 token 账单时，Agent 费用保持未知。

<a id="结构化决策与字段判断源码更新尚未发布"></a>

## 结构化决策与字段判断（v0.5.0）

新版源码不再取前 120 个原始节点直接发送，而是构造精简语义表、保留候选目标信息，并逐级压缩非目标内容。重复的无标签父容器不再与其可操作子控件竞争；精确输入文本不截断。完整界面树仍留在本机校验动作。每轮一次 Jev 请求，并行判断操作和各操作的目标，只消费选中操作对应的目标头，按所需头中最低的概率与 margin 检查。

新增只读标准 MCP 工具 **`aua_read_fields`**，CLI 对应 `read_fields`。最多 12 个 `money/text/choice` 字段在一次请求中判断：代码先找原文候选，Jev 选择角色，代码复制原文并用 Decimal 解析金额；缺失、含糊时选择 NONE，不生成数字。

```json
{
  "package": "com.example.shop",
  "currency": "CNY",
  "source": "auto",
  "expected_labels": ["合计", "打包费"],
  "fields": [
    {"name": "items", "instructions": "选择实际购买商品的优惠前小计。"},
    {"name": "packing", "instructions": "选择本单打包费。"},
    {"name": "delivery", "instructions": "选择当前实际配送费，不选划线原价。"},
    {"name": "total", "instructions": "选择结算实际待支付合计。"}
  ],
  "sum_check": {"total": "total", "add": ["items", "packing", "delivery"]}
}
```

按实际前台填写包名、费用和优惠。`sum_check` 用代码验证 **sum(add) − sum(subtract) = total**，仅适合同一币种、费用完整的等式。原文负优惠可放 add，正的优惠金额可放 subtract，不能重复抵扣。缺字段或等式不符时，参与字段均标为不接受，保留原文供 Agent 复核。

可传 `region={x,y,width,height}` 限定账单区域，单位为 Android 显示像素，按文本节点中心取值。先按视觉行组合文字，再判断字段；推理后检查同一前台/Activity/视口和区域文本是否变化。仅只读判断忽略空容器动画，动作仍保留严格的新鲜度与几何校验；密码、锁屏、接管检查始终覆盖整页。

树缺失时，默认可自动截取当前 ROI 并在本机 OCR；也保留 Agent 提供 `evidence_text` 的途径。后者始终标为 `caller_provided_text`、`source_verified_on_device=false`、`freshness=unchecked`，由 Agent 确认其新鲜度与正确性。自动视觉定位及点击尚未实现。

检查 `fields.*.accepted`、`checks` 和来源。`status=judged`、字段全部接受或数字相符均不证明业务成功，`verified` 始终为 false，最终由 Agent 对照结算画面验收。缺失/低概率值返回 null；金额冲突保留已选原文并标记 `review_reason=sum_check_failed`。推理后出错时仍在 `error.metrics` 保留用量与耗时。

[最新自动 OCR 验收及失败](benchmarks/real-app-ocr-v6.md) · [此前 Agent 提供 OCR 的历史记录](benchmarks/real-app-jev-v5.md)。开发验收最终为 22.9 / 21.8 元，饮品不同；不是最低价或整体性能对照。上述更新已包含在 v0.5.0 ZIP 中。

<a id="自动只读-ocr源码更新尚未发布"></a>

## 自动只读 OCR

`aua_read_fields` / CLI `read_fields` 的 `source` 默认 `auto`：所选区域有文本、包含全部 `expected_labels`，且 money 问题有金额候选时走 AX；否则在首次 Jev 请求前切换本机 OCR。这只是确定性的充分性筛选，不证明目标信息完整。`source=tree` 禁用 OCR；`source=screen_ocr` 强制 OCR。`expected_labels` 最多 12 个，按原文子串匹配。可先用宽泛字段判断，但比价应明确费用角色，并根据当前画面指定账单 `region`。

内置提供器目前仅支持 **macOS Vision + Xcode Command Line Tools**。缺少工具时按系统方式安装 `xcode-select --install`；本项目不会自动安装。Linux / Windows 上 AX 和现有工具仍可用，OCR 返回 `ocr_unavailable`，由 Agent 恢复或提供已核对的文本；尚未实现这些平台的 OCR。

截图在本机裁剪，OCR 输出文字、块边界框和分数，再按视觉行生成字段候选。图像不发送到 TypeSafe，**识别到的文字和字段问题会发送**。ROI 和可选 `ocr_masks` 均为 Android 显示像素；mask 必须完整位于 ROI，最多 8 个，面积之和不超过 ROI 的 20%，在 OCR 与哈希前涂白，不能当作证据。不要屏蔽价格、弹窗或业务结果；优先通过 ROI 排除地址等无关隐私。

OCR 推理后再次读取同一 ROI，比较精确像素哈希；变化就丢弃全部字段。仅显式 mask 排除的区域可变化，不使用未经校准的模糊相似度。前台、Activity、视口、暂停、锁屏与可检测认证状态均重新检查；截图不能证明所有自绘遮挡或认证都已被识别。输出 `source=device_screen_ocr`、`freshness=roi_pixels_checked`，并保留 `frame` 的 ROI、像素摘要、时间、mask 与独立 ID。这个 ID **不是**可供点击使用的 `observation_id`。`source_verified_on_device=true` 只说明证据来源于手机，不代表 OCR 或业务正确；`verified` 始终为 false。

`min_ocr_confidence` 默认 0，只保留提供器分数；它不是正确率。money/text 字段可按已验证样本设置阈值，低于阈值不接受；choice 分类没有单个金额候选的 OCR 分数。Jev 概率/margin、OCR 分数、金额等式和 Agent 验收互不替代。OCR 当前不识别划线样式，输出 `style=unknown`；不能仅靠左右位置保证现价角色。

全局 `timeout_seconds` 默认 20、最大 60 秒；冷编译最多使用剩余 15 秒，本机识别最多剩余 5 秒，Jev 最多剩余 8 秒。期限耗尽不返回有效判断；设备 RPC 受现有超时约束，进行中的调用可能超出剩余预算。返回分项耗时，失败时保留模型调用指标。

**兼容性变化：** `sum_check` 要求每项币种明确且一致。`¥` / `$` 有歧义，应传 `currency`；支持 CNY、JPY、USD、HKD、EUR、GBP、CAD、AUD。保留原文，规范化负号和千位逗号，用 Decimal 解析；仅支持逗号千位、点号小数（含全角等价字符），不支持所有金额地区格式。负优惠放 `add`；`subtract` 只接受正的抵扣幅度，负数会报 `negative_subtrahend`。传 `evidence_text` 时不能同时指定设备 `source`、`region` 或 `ocr_masks`，避免虚假的来源或区域声明。

`aua_execute_goal` 另新增 `effect_timeout_seconds`（默认 1，范围 0.1–3 秒）。点击/输入确认后只观察，等待非空语义变化或完成条件；无效果交回 `uncertain_effect`，同一调用回到已消费的状态与动作则交回 `duplicate_action`。几何动画和过渡空树不算效果。ACK 仍记为 executed，不代表业务成功；`wait` 与有界列表滑动保留原有无进展限制。该机制不是跨调用去重，Agent 仍需根据返回记录恢复，不能重新提交整个目标。

## Jev Box 视觉定位（截图需单独开启）

有些目标在控件树里没有可用节点：Canvas/游戏界面、WebView 图片、无标签图标。`aua_locate` 让 Jev Box（需 `jev-1.14` 及以上）判断描述的元素在哪；`aua_tap` / `aua_long_press` / `aua_double_tap` 也接受同样参数的 `visual`，代替 `selector` 或 `x`/`y`。

这是唯一会**上传截图**的功能，因此有独立开关，和启用 Jev 分开，**默认关闭**。在 Jev 设置中勾选「允许发送截图（视觉定位）」，或为服务进程设置 `ANDROID_USE_JEV_IMAGES=1`；本机保存的选择优先于环境变量。未开启时两个调用都在读取手机前返回 `jev_images_disabled`。

```json
{"package": "com.example.shop", "description": "右上角的购物车图标",
 "region": {"x": 0, "y": 192, "width": 1080, "height": 2016}}
```

- 每次请求发送一张 `region`（默认全屏）的 JPEG，等比缩放到每边 280–1568 px，同时问三个问题：Noul 是否存在、none/one/several 的 Choice 匹配数量、Box 位置。约 3,900 输入 token（≈$0.00016），模型耗时约 0.4 秒。若首轮在缩小后的图上不确定（存在概率在 0.3 与门槛之间，或置信度偏低），会在首个框周围裁出至少 560 px 的原分辨率区域再问一次；`refined` 返回该窗口，`model_calls` 变为 2。
- 仅当存在概率 ≥ `min_presence`（默认 0.7）、恰好一个匹配、框置信度 ≥ `min_confidence`（默认 0.5）时 `status=located`；否则为 `not_found`、`ambiguous`、`low_confidence` 或 `masked`（框中心落在遮罩内）。`box=[x,y,宽,高]`、`tap=[x,y]` 为显示像素；只有 `located` 才给出 `tap`。
- 最多 8 个 `masks` 在上传前涂白。不需要状态栏时用 `region` 排除通知内容。
- 设备暂停、可见密码框或树中出现验证码/PIN 提示时不截图。安全窗口（全黑截图）直接返回 `screen_capture_blocked`，不调用模型。只画在像素里的自定义认证无法识别，应先 pause。
- 视觉点击依次执行：定位，重新读取上下文并再次截图，比较框外扩区域像素；变化像素超过 1% 返回 `stale_visual_target`。通过后在框中心**只点一次**，支持 `wait_for`/`observe`。未确认的目标返回 `visual_target_not_found`、`ambiguous_target` 或 `visual_low_confidence` 并附定位结果，不会点击。`batch` 中不允许视觉点击。
- 适用于无标签图标、图片和 Canvas 内容。价格、标签、列表项中的某一行等小字不可靠：在一个真实电商 App 上，Box 会高置信度地框住整行文字（8 个目标漏了 3 个，置信度 0.74–0.85）。文字请用控件树选择器。
- 定位框会与控件树交叉检查：若框不对应任何单个有标签或可点击的节点，却包含两个及以上更小的有标签节点（`reason=box_spans_text_nodes`），或只包含一个但框中心落在标签之间（`reason=tap_between_labels`），状态改为 `ambiguous`，`tree_candidates` 列出这些节点（标签和 rect）供改用选择器。检查只能看到树暴露的内容：Flutter 等把卡片内文字合并成一个节点、或没有控件树的应用不在覆盖范围内。
- 定位框是模型判断，不是验收。能用控件树选择器时优先使用，结果以设备实际界面为准。

## 配置

### 推荐：在屏幕中配置

打开实时手机画面，点击 **Jev** 按钮，粘贴 TypeSafe API Key，再点**验证并启用**。服务端使用固定内容发起一次连接测试，不读取手机 UI；测试通过后保存并启用。也可测试已有连接、停用，或移除本机保存的 Key。保存成功及关闭设置时，输入框会清空。

本机浏览器、原生 MCP App，以及后续 CLI/MCP 调用共享设置，**新任务无需重启服务即可生效**。正在执行的有界目标继续使用启动时的配置。设置原子写入 `~/.local/share/android-use/jev.json`（可由 `ANDROID_USE_STATE_DIR` 指定），POSIX 权限为 `0600`，不打入安装包，也不回传界面。

本机明确设置的启用/停用优先于 `ANDROID_USE_JEV_ENABLED`；本机保存的 Key 优先于 `TYPESAFE_API_KEY`。移除本机 Key 后仍保持停用，即使环境中还有 Key。模型和服务地址的高级覆盖仍由环境变量提供，设置页会展示当前值。

### 高级：环境变量方式

使用界面无需手动编辑 `.env`；已有环境变量配置仍兼容。

复制 `.env.example` 为 `.env`，设置 `ANDROID_USE_JEV_ENABLED=1` 和 `TYPESAFE_API_KEY`。文件已被 Git 忽略，不要在聊天或工具参数里发送密钥。先启动正常 Android 服务、确认 READY、打开目标 App 并保持解锁；执行层不会自动启动服务。

插件和 MCP 宿主**不会自动加载**仓库 `.env`。将以下配置合并到客户端现有设置，替换绝对路径后重连，也可使用宿主密钥管理器注入环境：

```json
{
  "mcpServers": {
    "android-use": {
      "command": "uv",
      "args": ["run", "--env-file", "/absolute/path/android-use/.env", "--frozen", "--directory", "/absolute/path/android-use", "android-use-mcp"]
    }
  }
}
```

默认固定 `jev-1.14.0`，使用 `https://api.typesafe.ai/v1/systemone`。可用 `TYPESAFE_MODEL` / `TYPESAFE_BASE_URL` 修改；自定义地址会收到密钥，仅使用可信服务。要求 HTTPS（本机测试允许 loopback HTTP），不跟随重定向、不自动重试收费请求、不偷偷切换模型。

## 使用

调用 `aua_execute_goal`，包名和选择器来自实际观察：

```json
{
  "goal": "打开 Profile，将 Name 设为 安卓助手，然后点击 Apply draft 保存本地草稿。",
  "package": "your.verified.app.package",
  "texts": ["安卓助手"],
  "completion": {"text": "DRAFT=安卓助手"},
  "max_steps": 8,
  "max_model_calls": 16,
  "timeout_seconds": 20.0
}
```

CLI 使用同样 JSON：`uv run --env-file .env --frozen android-use --serial <设备> execute_goal '<JSON>'`。

`texts` 是允许输入的精确值列表，Jev 只选择，不编写新内容。默认允许 `tap`、`swipe`、`type_text`、`wait`；需要长按或返回时，在 `actions` 显式加入 `long_press` / `back`。双击、任意拖拽、启动 App、自绘控件及恢复仍使用原有工具。不能放入 `batch`。

## 减少读屏开销与输入决策

同一校验阶段内，亮屏与视口检查复用一次 `deviceInfo` 响应，不跨模型推理、动作或等待缓存。读树前后仍各自检查最新上下文；推理返回后和执行前仍会校验。推理后那次读屏的收尾检查就是动作前的最后一次检查；每轮循环直接沿用刚完成读屏的检查结果，不再重复查询锁屏/亮屏（Android 16 真机上每步约省 170 毫秒）。

每次 Jev 请求还会在同一观察上并行问一个 `goal` 问题（`NOT_YET` / `ACHIEVED`）。输出免费，几乎不增加费用和延迟。没有 `completion` 选择器时，若 `ACHIEVED` 达到同样的 `min_probability` / `min_margin` 阈值，就在下一次动作前停止，返回 `stop_reason=model_goal_achieved`、`handoff.next=verify_outcome`。这是判断，不是验证。有选择器时只看选择器，判断仅记录在 `decisions[].goal`。目标问题答案缺失或无效会被忽略：它只能让执行停下，不能触发动作。

`input_macros` 默认 `true`。同时允许 `tap`、`type_text` 时，有唯一 resource ID 或 content description 的可见、未聚焦 EditText 可提供 `focus_type_text` 候选：**聚焦 → 设置一个精确允许值 → 原生 getText 回读**。Jev 只决策一次，Android Use 最多执行两个原生修改动作。标识必须在包含屏外节点的整棵树中唯一；不支持换行、密码或隐式提交。没有稳定标识时继续使用原有单步候选。

写入前要求焦点、标识、几何和布局仍符合检查；意外弹窗、覆盖层、切页、锁屏或暂停都会停止。写入后允许同一输入框因文字和字体改变尺寸，仍须满足标识唯一、可见、聚焦及原生精确回读，此时不再执行坐标动作。暴露在树中的输入法仍会触发交回；合成验收应用关闭了输入法弹出，测试不代表兼容所有键盘布局。

`steps[].substeps` 分别记录聚焦、写入的确认和验证。聚焦后中止可返回 `partial`；写入或回读异常可返回 `uncertain`，不要自动重放。`handoff.completed_actions` 统计已执行的原生子动作；`max_steps` 统计候选次数，一个输入宏可包含两个原生动作。设置 `"input_macros": false` 可恢复分开的聚焦、输入决策，用于兼容或对照。普通 MCP 工具和 batch 行为不变。

[旧版本 / 仅复用采样 / 采样加输入宏的配对实测 →](benchmarks/optimizations-v4.md)

## 结果与边界

| 返回 | 含义 |
| --- | --- |
| `status=completed`、`verified=true` | 当前非空 App 树满足调用者给出的完成选择器；条件是否足以证明业务成功由调用者判断。 |
| `status=handoff` | 交回宿主 Agent，`ok=true` 也不等于业务成功。 |
| 模型返回 DONE | 没有完成选择器只算未验证交回；选择器未满足同样不能算成功。 |
| `steps` | 已尝试候选及 executed/partial/uncertain 状态；宏的 substeps 保留每次原生修改和输入验证，不自动重放。 |
| `roles` / `decisions` | 规划者、决策者、执行者，以及过期/已消费等决策审计。 |
| `handoff` | 停止原因、已确认动作数、不确定步骤及认证或重新观察规划的交回建议。 |
| `observation` | 默认 `observe=tree` 在安全时复用最后校验过的树；`observe=none` 关闭。认证、传输和范围错误不返回旧树。 |
| `metrics` | 调用数、实际模型、token、模型/设备耗时、过期决策数及费用估算。 |

默认选项概率 ≥0.65、与第二选项差距 ≥0.1，否则交回。它们是实验门槛，不是正确率保证，也不能把 TypeSafe confidence 当成任务正确率。

推理后重读前台、Activity、尺寸和可见 App 树语义指纹（排除全局 XML 序号及父节点序号，保留节点语义、几何、状态、顺序与前台上下文），变化则丢弃决策，计入调用预算。离开目标包、其他包交互覆盖层（包含暴露在树中的输入法）、密码框、锁屏、熄屏、暂停、三步无变化、次数/时间上限都会停止。进行中的 ADB 请求可能超时超额；读树和输入不是原子操作，也不能证明无遮挡，宿主必须验收最终业务结果。

仅从当前 App 可见控件生成动作，最多 200 个候选；模型上下文按预算逐级压缩。保守本地预算为每个状态/问题 28,000 UTF-8 字节、整份状态/所有问题 56,000 字节，并为服务端 token 限制留余量；字节不是 token。不可再压缩的精确输入或目标仍交回。不提供可能触发提交的换行输入。密码页面在推理前停止。不启用实验性图像接口。

启用后，目标、可见 UI 文本、候选描述、允许输入值及短动作历史会发送到 TypeSafe，避免敏感输入。常规返回轨迹不包含原始 UI 和输入内容。原有本地 MCP 工具独立可用。

已知版本按 [官方价格](https://docs.typesafe.ai/models) 估算：输入每百万 token $0.042，输出免费，不是账单对账。未知模型、缺失 usage、失败请求返回 `estimated_cost_usd=null`，另有费用下界及 `unpriced_calls`，不把未知费用当零。

## 复现对比

需要已授权、解锁的模拟器/真机、JDK、Android SDK（API 28+ 稳定平台和 Build Tools）及正常控制服务。脚本只安装无权限的合成内容临时 APK，拒绝覆盖已存在同名包，结束卸载并恢复原 Activity。

```bash
uv run --frozen android-use --serial emulator-5554 setup '{"action":"status"}'
# 仅在服务缺失时启动一次，再确认 READY：
uv run --frozen android-use --serial emulator-5554 setup '{"action":"start"}'
uv run --frozen android-use --serial emulator-5554 ready
uv run --env-file .env --frozen python scripts/benchmark_executor.py \
  --serial emulator-5554 --repeats 3
```

三组：现有确定性工具/批处理（执行开销下限）、Jev、可选 OpenAI-compatible 逐步文本模型。模型组共享目标、状态、候选与预算；文本模型无 Jev 选项概率，只校验 ID 并共享其他执行边界。配置 `TEXT_MODEL_BASE_URL`（按服务需要含 `/v1`）、`TEXT_MODEL`、`TEXT_MODEL_API_KEY`；可填写已核实的 `TEXT_MODEL_INPUT_USD_PER_MILLION` / `TEXT_MODEL_OUTPUT_USD_PER_MILLION` 估算费用。未配置明确记录跳过，不编造大模型对比。

四项任务覆盖导航与延迟结果、中文草稿、嵌套滚动选择、长按。每次重置 App、轮换方案顺序；端到端包含推理、动作、观察和独立最终验收，不含构建/安装/重置/清理。失败和交回也计入耗时与每成功任务费用。测试框不弹软键盘，隔离原生 setText；不能外推到生产 App、视觉推理或所有 Android 任务。

默认输出到已忽略的 `test-results/jev-benchmark.json`。[实测与限制 →](benchmarks/jev-emulator.md)

真实 stdio MCP 与旧手势回归：启用后运行 `uv run --env-file .env --frozen python scripts/accept_device.py --serial <设备> --jev`，通过独立 MCP 服务进程验收新工具，同样安装并清理临时 APK。
