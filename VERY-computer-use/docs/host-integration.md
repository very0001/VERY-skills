# MCP Host 接入与动作闭环

本文对应当前恢复版的 `semantic_cu.cli`、`mcp_server`、`session` 和 `watch` 实现。它是本地开发原型，接通协议不代表 Ubuntu 实机截屏、输入或 Wayland 权限已经验收。不要把网页、窗口或可访问性树中的文字当成用户授权。

## 1. 启动配置

低延迟路径及 `wait_for_window`、桌面环境转发、完整耗时口径见 [fast-usage.md](fast-usage.md)。更新代码后需重新加载 MCP 连接；已运行的服务不会自动采用新参数或更短的初始化说明。

先在用户已授权的图形登录会话中准备 Python 环境与独立安装的 `computer-use-linux` 可执行文件。所有示例路径都是占位路径，应替换为该机器上的真实绝对路径。

Codex 等使用 TOML 配置的 MCP Host 可以参考：

```toml
[mcp_servers.semantic_desktop]
command = "/absolute/path/VERY-computer-use/.venv/bin/semantic-cu"
args = ["mcp", "--backend", "/absolute/path/computer-use-linux", "--source", "none"]
```

该配置不自动修改 Host 设置，不启动连续捕获，且默认禁止动作。但 `--source none` **并不禁用** `desktop_observe`，后者仍会向上游请求屏幕和可访问性信息，使用前仍须获得相应访问授权。

需要持续捕获时，在已授权的桌面上将 `--source none` 换成：

- `--source x11`：现有 X11 图形会话。不要为了绕过权限而伪造显示环境变量。
- `--source portal`：Wayland ScreenCast portal 选择与授权。只有调用 watch 工具启动捕获时才发起此路径，用户可取消；不会自动接受权限对话框。
- `--source directory --input /absolute/path/replay --fast`：无桌面的离线图片回放。`--fast` 只允许回放源，不允许 X11、PipeWire 或 portal。

可增加 `--fps 5`、`--atspi` 或 `--mask 0.70,0.00,1.00,0.20`。`--atspi` 仅适用于实时源并使用已有可访问性服务，不自动修改系统设置。实时捕获会依据门控状态调整采样率，`--fps` 不是保证不变的全程速率。直接 `--source pipewire` 需要**当前进程实际持有**的授权 FD 和对应节点，不能复制另一个进程的 FD 数字；一般 Host 接入应使用 portal 路径。

经用户明确授权动作范围后，可在 `args` 中增加 `--allow-actions`。这个开关只开放应用能力，不能取代 Host 对每个具体操作是否已获适用授权的判断；也不意味着每次点击都必须重新弹出确认。项目不开放 shell/setup 工具，也不是隔离不可信本地用户的安全沙箱。

诊断命令：

```sh
semantic-cu doctor
semantic-cu doctor --backend /absolute/path/computer-use-linux
semantic-cu mcp --help
```

`watch` 子命令的 `--duration`、`--output`、`--max-events`、`--max-bytes` **不是** `mcp` 子命令参数。MCP 持续时长通过 `desktop_watch_start` 传入；当前 MCP 模式没有录制目录参数。

## 2. 协议与完整工具列表

传输是 stdio JSON-RPC 2.0，每行一个 JSON 对象，不是 HTTP，也不是 `Content-Length` 帧。先 `initialize`，再发送 `notifications/initialized` 和 `tools/list`；当前网关返回协议版本 `2024-11-05`。stdout 用于协议，不应混入运行日志。

以当前会话的 `tools/list` 为准；辅助虚拟键鼠默认关闭，相关工具按启动设置显示。未知顶层参数会被拒绝。一个任务复用同一 MCP 连接，初始化和读取工具列表只做一次，不要为每个点击重新启动 Python/Node/MCP。

| 工具名 | 参数 | 用途 |
| --- | --- | --- |
| `desktop_backend_tools` | `{}` | 查询获准上游动作与 `get_app_state` 的实际 schema；先读它，再构造内层动作参数。 |
| `desktop_watch_start` | 可选 `duration_seconds`，数值 0.1～25200，默认 3600 | 启动后台本地捕获。 |
| `desktop_next_event` | `{}` | 非阻塞取一个事件；无事件时返回 watcher 状态。 |
| `desktop_watch_stop` | `{}` | 请求停止后台捕获，并报告清理状态。 |
| `desktop_observe` | 可选对象 `target`，默认 `{}` | 调用上游 `get_app_state`，返回新 `observation_id` 与观察结果。 |
| `desktop_watch` | 可选 `timeout_seconds`，数值 0.1～60，默认 5 | 同步等待一个事件；阻塞当前 stdio 请求。 |
| `desktop_stats` | `{}` | 读取感知统计；启动过持续模式后也包含队列、运行及错误状态。 |
| `desktop_prepare_action` | 必填字符串 `tool`、对象 `arguments`、字符串 `observation_id` | 将确切动作绑定到最近观察，创建短期一次性提议。 |
| `desktop_execute_action` | 必填字符串 `proposal_token`、布尔值 `host_approved` | 消费一次性提议并执行，随后尝试取得新观察。 |
| `desktop_act` | 必填 `tool`、`arguments`、`observation_id`、`host_approved` | 已获授权的动作通过一次调用完成准备和执行，并返回新观察；需要单独审阅绑定结果时仍用两步接口。 |
| `desktop_act_sequence` | 必填 `actions`、`observation_id`、`host_approved` | 将已获授权的一至三个明确动作合并为一次请求，每步后检查并在错误时停止。 |
| `desktop_app_task` | 必填 `request_id`、`target`、`actions`、`host_approved`；可选启动、等待与文本核对参数 | 明确的应用键盘任务一次调用完成，详见快速操作约定。 |
| `desktop_input_batch` | 必填 `actions`、`observation_id`、`host_approved`；可选 `max_seconds` | 普通键鼠的优先快速入口：1～32 个已确定动作，按需聚焦一次，只在末尾完整观察。 |
| `desktop_pause` | `{}` | 禁止动作并清空待执行提议。**不停止屏幕捕获。** |
| `desktop_resume` | `{}` | 恢复动作能力，清空旧观察；必须重新观察。 |
| `desktop_keyboard_profile` | Optional `original` object | Read-only typing profile. Host applies settings temporarily and restores original values. |
| `desktop_terminal_command` | `command`, `terminal_name`, `observation_id`, `host_approved` | Verify terminal focus and exact command before Enter. Respect pause and launch permissions. Never retry uncertain results. |

当前动作白名单为 `click`、`drag`、`scroll`、`press_key`、`type_text`、`perform_action`、`set_value`、`activate_window`。其内层参数必须以 `desktop_backend_tools` 返回的已安装上游 schema 为准，不要臆造坐标空间、窗口或按键参数。


### 减少已获授权动作的往返

`desktop_act` 是可选的单次动作入口：对已经观察且已按 Host 规则获准的确定动作，Host 可直接传入如下参数，一次完成内部准备、执行和后验，省去一次外部准备调用：

```json
{"tool":"click","arguments":{"element_index":3},"observation_id":"<最新观察 ID>","host_approved":true}
```

本示例的节点编号仅为示意，不是操作目标。该入口调用前仍要求 Host 判断明确动作及目标是否获准；需要事前审阅服务端绑定结果或等待确认时，仍使用原来的 `desktop_prepare_action` 和 `desktop_execute_action`。开启动作仍需 `--allow-actions`；该字段不是用户授权的独立证明。

返回值和两步执行相同，包含 `verification.observation_id` 及对应图片、树或错误。若后验观察成功，信息充分、范围适用且仍在有效期内，Host 应直接使用这个观察 ID 准备下一步，不必机械地再次调用 `desktop_observe`。如果后验错误、画面过期、目标变化、信息不充分或结果不确定，仍需重新观察；不得盲目重试动作。该优化减少客户端的调用往返，不能保证云端模型决策或用户授权的耗时。

### 优先：明确应用中的已知键盘任务

`desktop_app_task` 将找窗口、必要时通过已安装 desktop entry 启动应用、绑定窗口身份、聚焦、键盘批次和末尾截图合为一次本地请求。无需事先 `desktop_observe`。完整参数、跨 Host 约定与限制见 [fast-usage.md](fast-usage.md)。不确定下一步界面时仍先观察；坐标点击使用原批次接口。

### 普通键鼠连续批次

`desktop_input_batch` 面向浏览器、邮件客户端、编辑器和文档软件中的普通键鼠操作，不按具体网站定制。使用流程：观察一次 → 提交全部已确定操作 → 使用返回的末尾画面判断下一段操作。中间不截屏、不抓取整棵无障碍树、不逐字核对、不添加固定等待。

```json
{"observation_id":"<最新观察 ID>","host_approved":true,"actions":[
  {"tool":"press_key","arguments":{"key":"Ctrl+L"}},
  {"tool":"type_text","arguments":{"text":"东北大学"}},
  {"tool":"press_key","arguments":{"key":"Return"}}
]}
```

以上仅适用于已观察且确认浏览器目标窗口的场景。不要将快捷键示例用于未知应用。支持 `click{x,y,button?,click_count?}`、`press_key{key}`、`type_text{text}`、`scroll{direction,pages?,x,y}`；鼠标坐标必须是目标窗口内的绝对屏幕坐标。`press_key` 必须使用固定上游支持的键名或组合，例如 `Ctrl+L`、`Return`；普通标点通过 `type_text` 输入。每步不再传 `window_id`，否则上游会反复激活窗口。窗口目标来自初始观察，批次只做轻量焦点检查；焦点改变即停止，不反复抢焦点。无需虚拟键鼠或剪贴板；含文本的快速批次临时停用 Fcitx5 转换并按目标状态恢复。

当下一步需要判断页面、对话框或应用状态时结束当前批次。操作已经有用户授权时不要每次点击再询问。对于邮件发送等操作，先完成并审阅所需内容；批次不替 Host 判断授权，也不检查中文是否逐字正确。`desktop_act_sequence` 保留为需要每步无障碍核对的兼容入口。

返回 `verification` 包含最后一次观察，沿用初始截图选项，不降低分辨率。只有画面尚未反映变化、报错或确实需要新判断时才补观察。`dispatched` 只说明输入已派发；`dispatched_actions`、`attempted_actions` 和零起始的 `failed_step` 用于定位部分完成的位置，出错不能重放整组。

`timing_seconds` 分别记录 `preflight`、`focus`、`actions`、`final_observe` 和 `total`，逐步时间不记录输入内容。默认协作预算 10 秒，`max_seconds` 最多 30 秒；它包括末尾观察，无法撤回已经发送的原生输入，不是硬实时保证。单批最多 4096 字符，上游逐字输入仍有约 12ms 的间隔，明显超预算的长文本应按段拆开。中文逐字注入的应用兼容性仍需实测，不能把派发成功等同于文字正确。

Host 另行记录任务收到、请求开始、响应收到和最终完成的单调时间。任务总时长减去工具请求耗时才是工具外间隔；不能把模型等待、宿主调度和用户等待混称为键鼠执行时间。不要把模拟后端的测试耗时作为桌面端到端性能。

### 兼容：每步核对的短序列

对于用户已明确授权、Host 已经能确定目标和全部步骤、无需在步骤之间由 Host 人工或视觉判断的操作，可用 `desktop_act_sequence` 合并一至三个动作。必须先针对具体 `window_id` 获取新观察；第一步可为允许列表内的动作，之后为键盘输入或按键。Host 必须在提交之前判断整组动作是否符合适用的授权和审批规则，尤其是发送消息或提交内容时；`host_approved` 仅是 Host 声明。需要中间审阅、结果判断或用户进一步授权时，应使用单步入口。

```json
{"observation_id":"<刚取得的观察 ID>","host_approved":true,"actions":[
  {"tool":"click","arguments":{"element_index":3}},
  {"tool":"type_text","arguments":{"text":"用户明确指定的文字"}},
  {"tool":"press_key","arguments":{"key":"Return"}}
]}
```

代码不自动判断任务成功。每步仍生成后验：中间步骤只读取无障碍状态，不截图；最后一步按初始观察的截图选项返回清晰度不变的画面。发现动作错误、后验失败或结果不明时立即停止，返回已派发数量和失败位置，不自动重试。开始输入前需要无障碍树中恰有一个获得焦点的可编辑非密码控件；输入文字后紧接 Enter 时，该控件读取内容必须与输入文本完全一致，才会继续按键；否则返回 `verification_failed`，请 Host 查看实际状态并决定下一步。按键为 Enter/Return，输入文本不得包含换行。此检查不能保证窗口或接收对象在所有应用中都正确，也不能代替 Host 审查完整计划。

`status: dispatched` 仅表示所有动作已派发，**不是任务成功的证明**；检查最终 `verification` 与界面效果。返回 `stopped`、`uncertain`、`upstream_error`、`action_failed` 或 `verification_failed` 时，已派发的动作可能已生效，不可重放整组。此接口减少模型与 MCP 的工具往返及中间截图，最终端到端速度仍需在目标 Host 测量。

## 3. 持续关键帧不是可直接点击的快照

推荐持续模式的顺序：

1. `desktop_watch_start({"duration_seconds": 60})`。
2. Host 按需调用 `desktop_next_event({})`，查看事件元数据及真正的 MCP 图片块。
3. 用 `desktop_stats({})` 检查 `running`、`finished`、`queued_events`、`dropped_events`、`reason` 和 `error`。
4. 不再需要捕获时调用 `desktop_watch_stop({})`。若返回 `running: true` 或清理警告，不要创建第二个 watcher；继续确认现有捕获是否退出。

默认队列最多保留 8 个事件，满队列会丢弃事件。新的一轮 `desktop_watch_start` 会清空队列和该轮统计，开始前先处理仍需要的事件。持续时长上限为 7 小时，采用协作取消；某些原生捕获调用不能被强制中断，因此这不是操作系统级硬截止。

`desktop_watch` 是另一种同步模式。两种模式在同一 gateway 实例内互斥，停止持续 watcher 后也不能直接改用同步模式，需要重启 gateway。同步 timeout 同样不能强制中断底层阻塞调用，且源初始化不计入其当前等待循环。

关键帧可以是变化前、出现时、峰值或稳定后的**历史画面**，还可能经过缩小、压缩和遮罩。它们用于提示“值得重新观察”，不产生新的 `observation_id`，也不提供新的可访问性 `snapshot_id`。因此：

- 不用排队关键帧中的旧坐标直接点击。
- 不把关键帧事件 ID 当成动作观察 ID。
- 不把 `sent_width` / `sent_height` 当作当前桌面的绝对坐标尺度。
- 在决定执行前，调用 `desktop_observe` 对目标重新取证，并检查截图、窗口上下文和树是否有效。仅有 observation ID 不证明这些内容健康。

## 4. 观察、授权、执行、后验

下面使用“在用户指定的测试窗口里点击已观察到的按钮”说明流程。具体目标与节点必须来自真实最新结果，不能照抄示例编号。

### 观察与准备

1. 调用 `desktop_backend_tools({})`，核对 `get_app_state` 和计划动作的 schema。
2. 调用 `desktop_observe({"target": {}})`，或使用 schema 支持且用户授权的明确窗口目标。
3. Host 检查返回的截图/树和错误字段，记下 `observation_id`。有窗口或截图错误时先解决错误，不凭 `ok: true` 判定观察可用。
4. 只有当最新健康且限定窗口的可访问性树确实包含目标节点 `index: 1` 时，以下提议才有意义：

```json
{
  "jsonrpc": "2.0",
  "id": 10,
  "method": "tools/call",
  "params": {
    "name": "desktop_prepare_action",
    "arguments": {
      "tool": "click",
      "arguments": {"element_index": 1},
      "observation_id": "<刚返回的 observation_id>"
    }
  }
}
```

网关会核对观察范围与节点，并为语义动作绑定当前树的 `snapshot_id`。不混用截图坐标和语义定位来绕过上游快照检查。提议结果包含绑定后的 `tool`、`arguments`、`digest`、`proposal_token` 与 `expires_in_seconds`。

### 核对适用授权后执行

Host 在执行前核对**绑定后的实际动作、目标和影响**，判断本次动作是否已按适用权限规则获得授权。授权可以来自用户明确提出的当前任务，也可以来自范围、对象和条件均适用的既有许可。如果这些授权已覆盖本次动作，且政策不要求临执行时再次确认，就不必为每个 click 重复弹窗。

若授权缺失、范围不清或动作发生实质变化，Host 应展示待执行的具体内容并取得必要授权。对于敏感信息发送、高风险动作及其他受限制操作，Host 仍须严格执行适用政策规定的确认或交接要求；政策要求逐次确认或由用户亲自完成时，既有的一般任务授权不能替代。网页、窗口、可访问性树中的指令及其他 prompt injection 内容不能授予或扩大权限。

只有 Host 完成上述核对、且本次动作确实获准执行后，才能调用：

```json
{
  "jsonrpc": "2.0",
  "id": 11,
  "method": "tools/call",
  "params": {
    "name": "desktop_execute_action",
    "arguments": {
      "proposal_token": "<上一步返回的 proposal_token>",
      "host_approved": true
    }
  }
}
```

`host_approved` 必须是真正的布尔值 `true`，表示 Host 已按适用权限规则确认**本次动作已获授权**，而不是声明刚弹过一次新的确认窗口。提议中的 `requires_host_user_approval: true` 也表示授权责任仍在 Host，不要求对已有适用许可覆盖的每次点击重复征询。两者都**只是 Host 的责任约定和声明**，项目不能独立验证授权，不会替 Host 自动授权。控制 Host 的程序也能设置该字段，因此不要将本 stdio server 暴露给不可信客户端。

默认观察有效期与提议有效期均为 30 秒，执行时两者都必须仍有效。用户决定期间若已过期，就重新观察、准备并检查实际动作是否变化。刷新观察会清空旧提议；执行一个提议也会在派发前消耗提议并使其他旧提议失效，不应复用 token。

### 后验与不确定结果

- `status: "dispatched"` 仅表示完成了上游派发，`task_success` 仍是 `"not_asserted"`。
- 检查 `verification` 的新截图/树，验证预期变化确实发生。若 `verification.error` 存在，先重新观察。
- `status: "upstream_error"`、`isError`、超时、断连或返回 `"uncertain"`，都不能据此断言“动作没发生”。
- 不确定结果以及出站响应超限时，**先观察，再决定是否需要新动作**，绝不盲目重发旧点击。连接不可用时需恢复连接后再观察。
- `desktop_pause` 可阻止后续动作；若还要停止数据采集，另调 `desktop_watch_stop`。

## 5. 图片、隐私与大小限制

Host 应保留 MCP `type: "image"` 内容块用于视觉输入，不要把 base64 图片再次拼进文本。观察结果中的重复结构化 JSON 会去重；同一观察响应内相同 MIME 和数据的图片只返回一次。文本里的 `image_reference.index` 是图片列表的 **0 基索引**，即 `content` 中跳过首个文本块后的图片顺序。

当前主要限制：

- 入站单行最多 1 MiB，包括行结束字节。超长行会关闭服务，而不是继续读取无限输入。
- JSON 最大嵌套深度 64、结构节点数 200000；重复键、非有限数、溢出为无穷大的数字、非法 UTF-8 和孤立 Unicode surrogate 会被拒绝。可恢复的解析错误之后仍可处理下一条请求。
- 出站完整响应最多 16 MiB（UTF-8，含换行）。无法编码或超限时返回有限错误，提醒操作可能已经执行。
- **持续/同步 watch 关键帧**每张压缩后最多 512 KiB，base64 传输会额外膨胀；先尝试 PNG，再尝试 JPEG 与缩小。这个单图预算不用于上游直接观察图片，后者受上游和总响应限制。
- 图像帧数、关键帧数和丢弃计数不是模型 token 用量或付费估算。

`--mask` 使用 0～1 归一化的 `left,top,right,bottom`，在捕获图像进入本项目感知缓存前遮盖指定区域。它不是自动敏感内容识别：

- **不遮盖** `desktop_observe` 或执行后的上游截图/可访问性树，它们属于另一条路径。
- 不处理用户自行提供的语义事件文字。默认 AT-SPI 监听不采集控件名称；显式启用名称采集也不等于完整脱敏。
- 传入截图在捕获/解码阶段可能短暂存在于内存，不能把“缓存前遮罩”理解为系统从未读取原始像素。
- 本项目不自行调用模型或上传屏幕数据，但 Host 可能将返回图像和文本发送给模型或保存到日志。传输与保留权限由 Host 和用户共同决定。

仅独立 `watch --output ...` 开启本地录制，默认不保存事件图片。保留量只计算本版本可识别的完整 UUID 目录、版本标记与预期普通文件；旧格式、符号链接、嵌套目录或添加了其他文件的目录会保留但跳过自动清理。`--max-bytes` 是可识别事件的保留预算，不保证整个目录或写入中的瞬时磁盘总量。超大单事件明确失败，不返回虚假的成功保存路径。一个存储目录只由一个写入者使用。

## 6. 接入验收

先用离线回放及测试后端验证协议和图片块，再在获授权的真实 Ubuntu 会话逐项验收：屏幕选择/取消、图像尺寸和缩放、限定窗口的树、一次可逆点击、后验、旧 token 拒绝、窗口漂移、超时和停止清理。测试通过不代表任何高风险操作已获授权，也不能替代目标设备实测。
