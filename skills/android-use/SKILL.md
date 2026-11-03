---
name: android-use
description: 通过 Android Use 的 MCP 工具或 CLI 操作 ADB 已授权的 Android 真机和模拟器。支持界面树、截图、点击、滑动、中文输入、App 启动及已知路径批处理；适用于用户要求在 Android 手机上完成任务。
---

# Android Use

首次使用执行 `aua_setup(action="status")`。服务缺失时调用一次 `aua_setup(action="start")`，再调用 `aua_ready(screenshot=false)`。多台设备先 discover，按用户目标 configure(serial=...)，不随意选第一台。USB 未授权或锁屏让用户在手机上完成信任/解锁，再 READY。服务不会自动重启；启动失败按返回原因诊断，不循环 start。

READY 自动关联本聊天的实时手机屏幕侧边栏；同一聊天复用同一个面板，不反复打开新面板。用户单独要求看屏幕时调用一次 `aua_screen()`，无需为打开预览重复 READY。视频由独立的 ADB screenrecord H.264 通道提供；需要宿主安装 ffmpeg，缺失时按工具返回处理。预览没有可见 viewer 5 秒后停止手机端采集；前端读取的是内存中缓存的视频帧，不轮询手机截图或 XML。侧边栏失败不自动判定控制通道失效。

`aua_screen_frame`、`aua_screen_action` 仅供预览 App 自己使用，模型不要调用它们刷新画面或模拟用户点击。模型需要看图片仍用 aua_observe，侧边栏 Live 不替代模型观察。用户可以在预览中点击、滑动、返回、Home 或将当前视频画面复制为剪贴板 PNG 图片；这些动作会改变真实页面，下一次模型观察按实际状态继续。CLI 的 `screen` 返回本机预览 URL，可作为 Codex 的右侧浏览器面板打开。

READY 验证解锁、亮屏、服务、前台、视口和非空控件树。复用返回 observation，不再立即读一遍。常规动作默认 observe=none，下一步需要新页面时在动作传 observe=tree/screenshot/both。最后的关键结果用实际界面验收，action_executed 和 verified=false 都不证明业务完成。

优先按当前树里的 text、description、resource_id、class_name 定位。多个匹配返回 candidates，用 index 明确选择；不可凭空挑第一项。选择器失败或页面异常先看工具返回图片，再决定坐标操作。截图里的 `(px,py)` 乘 image.pixel_to_device 得到动作需要的 **Android 显示像素**，不要用 dp 或宿主窗口坐标。树不证明无遮挡、自绘信息完整或列表全量。observation_id 可选，只适用于同一 MCP Runtime，并且仅检查前台/视口上下文。

输入先点中实际可见的非密码 EditText，再调用 aua_type_text。默认 replace=true、不提交，支持 Unicode；verify=true 用原生 getText 回读完整字段。XML 树可能把 emoji 显示成点，不要拿树里的点号重写草稿；replace=false 会直接读取原生文本后追加，保留 emoji。富文本/自绘编辑器可能不支持。换行需 allow_newlines=true，部分 App 会将其当作提交；明确编辑器行为和用户意图后使用。工具不安装或切换输入法，setText 不支持时查看截图再选择可行的 UI 路径，不把输入失败当成功。

任何动作超时或 uncertain=true，都先观察真实状态；不重发整段文本、发送、订单或整个 batch。已知短路径可用 batch（最多 20 步）；它预校验全部参数，在出错、等待超时、Enter 提交或时间预算时停止。按 completed_steps / stopped_at 继续；失败步骤可能已执行，先观察。按需滑一次，direction 是手指方向，up 通常看后续内容；无进展不证明到底。

密码、PIN、验证码和生物认证由用户本人完成。先 aua_pause，再用宿主提问工具等待用户明确完成（首个选项「已完成继续」）；接管期间不读取、不截图、不操作。用户完成后 aua_resume，再重新观察；真正锁屏还需要 READY。pause 状态在该设备的 CLI 和 MCP 之间共享，不影响用户直接操作手机。

aua_apps 查询已安装的第三方 **包名**，不支持按 App 显示名查找。不要连续猜包名。aua_launch_app 使用已核实的 package，不强停或清除 App 数据。发送、下单等外部变更需用户授权，并核对目标与内容，执行一次后验收结果。

MCP 不可用时使用同一仓库 CLI：`uv run --frozen --directory <仓库路径> android-use <操作> '<JSON>'`。CLI 截图保存到 image_paths 中的本机绝对路径，使用图片查看工具实际查看。MCP 图片必须转发 image 块，不输出 base64。CLI 的每次运行是新 Runtime，不能跨进程传 observation_id。
