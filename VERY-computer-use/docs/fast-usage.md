# 所有 MCP Host 通用的快速操作约定

本约定同时写入服务的初始化说明、工具描述和参数 schema。它不依赖某个聊天的记忆、某个模型、私有脚本或推理技巧。更换 AI 后，连接同一 MCP 服务并读取工具列表即可使用。

## 选择最少调用的入口

| 任务状态 | 入口 | Host 所需往返 |
| --- | --- | --- |
| 已知应用和全部键盘步骤 | `desktop_app_task` | 一次，包含最终图片 |
| 已观察界面，需要点击/滚动/输入 | `desktop_input_batch` | 一次，复用观察 ID |
| 需要识别界面、判断下一步 | `desktop_observe`，随后上述动作工具 | 仅在判断边界观察 |
| 需要每步无障碍文本核对 | `desktop_act_sequence` | 兼容严格路径 |

一个 MCP 连接复用整个任务，初始化及 tools/list 只做一次。已获任务授权时不为每个按键重复询问。不要加入任意固定等待，启动窗口的等待由工具本地处理；不要重新截取已经在 verification 中返回的有效画面。

## 窗口变化在本地等待，不再多一轮观察

`desktop_act`、`desktop_input_batch`、`desktop_app_task` 均支持 `wait_for_window`。若动作将打开应用、对话框，或让文件管理器导航到已知位置，提交明确的目标条件。工具完成动作后仅查询窗口列表，目标唯一、已聚焦、可见且尺寸有效时才截图一次；不重复点击、不抢回焦点。最终截图上下文还会再次核对，避免在查询与截图之间切换窗口却误报就绪。

```json
"wait_for_window": {
  "wm_class": "gnome.Nautilus.Org.gnome.Nautilus",
  "title_contains": "回收站",
  "timeout_seconds": 3
}
```

至少指定 `window_id`、`app_id` 或 `wm_class`；可叠加 `pid` 和 `title_contains`。默认等待上限 3 秒、最高 10 秒，批次仍受总预算约束。这只能确认窗口状态，不能证明网页内容或文件列表已全部加载。普通单击、键入等无窗口变化的操作无需此参数。

失败返回 `timeout`、`ambiguous_target`、`window_changed_after_wait` 等原因，已发出的动作不能重放。不要在失败后无条件再点一次。

## 图形环境与时间口径

Host 应转发当前授权桌面的 `DISPLAY`/`WAYLAND_DISPLAY`、`DBUS_SESSION_BUS_ADDRESS`、`XDG_RUNTIME_DIR`、`XAUTHORITY`、`XDG_SESSION_TYPE`。请在支持 env_vars 的 Host 中按变量名转发，不硬编码显示号。MCP 启动时若变量缺失，只从唯一、同 UID、身份稳定的 GNOME Shell 进程恢复白名单变量；冲突、多会话或不可读会明确报错。未解析环境时保留只读协议诊断，禁止动作，避免跳过共享输入锁。不会更改系统环境或 Host 审批策略。

区分三个时长：服务端内部执行、Host 工具调用生命周期、用户任务整轮。脚本内第一次 `Date.now()` 晚于用户发消息，因此不能作为完整任务起点；没有整轮时标时不要编造“用户总耗时”。优先报告宿主 UI 的整轮时长。缩短说明和减少调用旨在降低调用前/调用间负担，不意味着已测得模型推理减少同样比例。

## 示例：打开文本编辑器并新建文档

调用 `desktop_app_task`：

```json
{
  "request_id": "editor-pi-001",
  "target": {"wm_class": "Gedit"},
  "launch_desktop_id": "org.gnome.gedit.desktop",
  "actions": [
    {"tool": "press_key", "arguments": {"key": "Ctrl+T"}},
    {"tool": "type_text", "arguments": {"text": "3.14159"}}
  ],
  "host_approved": true
}
```

Gedit 的 Ctrl+T 新建同一窗口内的标签页；Ctrl+N 会创建另一个窗口，当前批次会因为目标改变而停止。VS Code 新文档使用 Ctrl+N，目标 `wm_class: Code`，desktop ID 为 `code.desktop`。这些例子不是对未知应用的通用快捷键猜测。

`target` 支持 `window_id`、`pid`、`app_id`、`wm_class`，所有给出的字段都必须匹配。多个匹配窗口中只有一个获得焦点时选择它，否则返回 `ambiguous_target`，由 Host 指定具体窗口。仅当没有匹配窗口且显式给出已安装的 `launch_desktop_id` 才启动一次。该参数不是命令字符串，也不接受路径或自定义 shell。

app task 不要求先看到图片，适用于明确的快捷键与文本计划。它先在本地绑定当前具体窗口和 PID，再按批次执行普通共享键盘。坐标依赖画面，因此此入口不接受点击；需先观察再使用 `desktop_input_batch`。

## 内容判断、重复调用和同时操作

- `status: dispatched` 表示派发完成，`task_success` 仍为 `not_asserted`。Host 查看返回图像判断结果。
- 可选 `expected_text` 对最终可访问性快照中唯一聚焦编辑框做精确比对。`matched` 表示匹配；`mismatch` 将整体标记为 `verification_failed`；树信息不足时为 `unavailable`，直接检查已返回图像，不自动重输或强制补图。
- 每个独立任务使用新的 `request_id`。同一服务进程内，相同 ID/参数再次到达只返回 `already_processed` 和之前状态，不聚焦、不输入、不截图。相同 ID 配不同参数会拒绝。该保护不跨服务重启；Host 不应把旧请求当新请求重放。
- 内存台账只存摘要和执行计数，最多 256 项，达到上限拒绝新任务，不自动逐出旧 ID。它不保存文字内容或截图。
- 每个键盘动作前检查窗口焦点和 PID。用户切换窗口时停止，不中途抢回焦点，不重放已派发部分。关闭窗口、截图失败、超时都明确返回部分完成/不确定状态。
- 这不是操作系统级原子事务。用户在单个文本动作内部切换、输入或切回，仍可能造成部分文字落入不同位置；窗口检测不能排除所有竞态。最后画面和可选文本核对仍有意义。
- 普通共享鼠标键盘、现有截图尺寸与质量、默认不录制策略保持不变；不启用 MPX，不修改输入法配置或剪贴板。字面输入会临时停用 Fcitx5 转换并在原目标仍聚焦时恢复。

## 耗时和边界

`timing_seconds` 的 `resolve_window`、`launch`、`initial_state`、`focus`、`actions`、`final_observe` 与 `task_total` 描述本地调用。`total` 是内部输入批次总时长，已包含在 `task_total` 中，不能再次相加。

客户端从发出 MCP 请求到收到包括图片在内的完整响应计时，才是 MCP 请求总耗时；用户任务总时长还包含模型判断、Host 调度、调用间隔与报告。不得用本地亚秒动作时间替代用户等待时间。

默认协作预算 10 秒，最高 30 秒，包括找窗口、启动、输入和末尾观察。长文本仍受上游逐字输入速度及布局兼容性限制。同步调用期间，同连接的后续 pause 不能即时中断当前原生输入；超时也不能撤回输入。

服务代码更新后重新加载 MCP 连接以获得新工具与初始化提示。仅修改文档或给模型口头建议不会让已经加载的旧服务自动升级。


## 字面输入与运行版本核对

`desktop_input_batch` 和 `desktop_app_task` 在第一次 `type_text` 之前，检测目标窗口对应的 Fcitx5 状态。活动的 Rime 等转换引擎会被临时停用，随后字面字符、空格、回车直接传给应用；完成整组输入后在 finally 中恢复。初始已停用或没有 Fcitx5 服务时不启停服务。不改全局配置、不创建虚拟键盘、不使用剪贴板。

若用户中途切换焦点或输入法，引擎恢复不会抢回窗口或覆盖新上下文，返回 `input_method.restore.status=restoration_pending`；Host 应明确报告。停用失败会阻止文字继续发送。`timing_seconds.input_method` 单独记录开销。

这只作用于含 `type_text` 的快速批次，不改变用户显式切换输入法的纯按键任务。需要稳定字面输入时优先使用这两个入口；旧的逐步 `desktop_act` 仍是兼容路径。

用 `desktop_stats` 查看实际服务的 `runtime.build`、PID 与能力。修复版标识为 `20261113-literal-input-v2`。磁盘代码或工具列表存在并不证明当前连接已更新；必须从实际调用返回值核实。新建宿主会话会加载新服务，已有父会话的缓存不能假称已刷新。
