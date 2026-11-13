# VERY Computer Use

面向本地 AI 智能体的 **Ubuntu 桌面 MCP 工具**。通过普通共享鼠标和键盘操作桌面，提供屏幕观察、连续动作批次、应用任务、窗口就绪等待，以及有界的持续观察队列。

它负责本机执行与状态反馈；看图理解和任务规划由接入的 AI Host 完成。不内置模型，不需要模型 API Key，不监听 HTTP 端口。

当前运行版本标识：`20261113-literal-input-v2`。优先验证环境为 **Ubuntu 22.04 / GNOME / 原生 X11 / Python 3.11 / Node.js 22**。

## 这版解决什么

- **减少模型往返**：明确的应用键盘任务可在一次 `desktop_app_task` 调用中完成找窗口、必要时启动应用、聚焦、输入和最终截图。
- **连续操作不重复抓图**：`desktop_input_batch` 连续执行已确定的动作，中间仅检查窗口焦点，末尾观察一次。
- **等待真正的目标窗口**：`wait_for_window` 在本地等待目标窗口可见并聚焦，避免刚点击就返回旧画面，再让模型补一轮截图。
- **保护字面输入**：含文本的快速批次会临时停用活动的 Fcitx5 转换，避免 Rime 用空格和回车提交预编辑；结束后按焦点与引擎状态恢复。
- **重复请求不重复输入**：应用任务使用请求 ID 台账，返回部分完成位置，不自动重放不确定操作。
- **默认不录制截图**：持续采集在内存中使用有界缓存；只有显式启用录制才写入项目管理的存储目录。

**默认不启用虚拟键鼠或 MPX。** 辅助设备相关代码保留为实验功能，须单独显式开启；普通任务不需要它。

## 快速安装

### 1. 获取代码并准备 Python

```bash
git clone --branch mcp --single-branch https://github.com/very0001/VERY-tools.git
cd VERY-tools/VERY-computer-use

# 使用已有的 Python 3.11 或更新版本
python3.11 -m venv .venv
. .venv/bin/activate
python -m pip install -e .
very-cu --help
```

也可使用 Conda：

```bash
conda create -n Computer_Use python=3.11 nodejs=22 -c conda-forge
conda activate Computer_Use
python -m pip install -e .
```

Ubuntu 22.04 默认的 Python 3.10 不满足本项目要求。无需也不建议替换系统 Python。

### 2. 桌面依赖与上游后端

原生 X11 需要可用的 `xdotool`、`wmctrl`、X11 库、当前用户的显示授权；窗口语义信息依赖 AT-SPI 和应用支持。Fcitx5 输入保护使用 `gdbus`，不会自动安装或启动 Fcitx5。

如系统尚未安装这些工具，可由管理员按需安装 Ubuntu 软件包：

```bash
sudo apt install xdotool wmctrl libglib2.0-bin libx11-6 libxtst6
```

桌面控制后端固定适配 `@agent-sh/computer-use-linux@0.7.49`。准备 Node.js 22 后，在项目目录执行：

```bash
npm install --prefix ./upstream-runtime --save-exact @agent-sh/computer-use-linux@0.7.49
very-cu doctor --backend "$PWD/bin/upstream-x11"
```

`bin/upstream-x11` 选择上游 X11 截图路径，依次寻找显式 `VERY_CU_UPSTREAM_BIN`、项目内 `upstream-runtime`、当前 Python 环境的 `opt/upstream-runtime`、PATH。它不安装依赖、不改系统设置，也不适用于 Wayland。

可选功能：`python -m pip install -e '.[video,portal]'`。Wayland 的 Portal/PipeWire 路径另见 [Wayland 文档](docs/wayland.md)，不能把 Xwayland 当作原生 X11。

## 接入任意 MCP Host

以获得授权的桌面用户启动，使用**绝对路径**。不要使用 sudo，也不要复制其他用户的显示授权。

```json
{
  "mcpServers": {
    "very_computer_use": {
      "command": "/absolute/path/VERY-computer-use/.venv/bin/python",
      "args": ["-I", "-m", "semantic_cu.cli", "mcp", "--backend", "/absolute/path/VERY-computer-use/bin/upstream-x11", "--source", "x11", "--allow-actions"],
      "env": {"SEMANTIC_CU_ENABLE_AUX": "0"}
    }
  }
}
```

只读使用时去掉 `--allow-actions`。Host 应继承当前图形会话的 DISPLAY/Wayland、D-Bus、runtime 和 X 授权环境。变量缺失时，工具仅尝试从**唯一、同 UID 的 GNOME Shell** 恢复白名单字段；不猜显示号、不覆盖冲突、不跨用户取环境。无法解析时保留只读诊断并拒绝输入动作。

Codex TOML 配置示例：

```toml
[mcp_servers.very_computer_use]
command = "/absolute/path/VERY-computer-use/.venv/bin/python"
args = ["-I", "-m", "semantic_cu.cli", "mcp", "--backend", "/absolute/path/VERY-computer-use/bin/upstream-x11", "--source", "x11", "--allow-actions"]
env_vars = ["DISPLAY", "WAYLAND_DISPLAY", "DBUS_SESSION_BUS_ADDRESS", "XDG_RUNTIME_DIR", "XAUTHORITY", "XDG_SESSION_TYPE", "XDG_CURRENT_DESKTOP"]

[mcp_servers.very_computer_use.env]
SEMANTIC_CU_ENABLE_AUX = "0"
```

配置保存后需要重新加载 MCP 连接。**磁盘代码更新不代表旧进程已经更新**：调用 `desktop_stats`，核对 `runtime.build`、服务 PID 和能力字段。当前版本应返回 `20261113-literal-input-v2`。不同 Host 的刷新机制各异，不要用临时测试客户端的成功代替真实宿主连接验证。

## 最短操作路径

| 场景 | 推荐接口 | 行为 |
| --- | --- | --- |
| 应用及全部键盘步骤已明确 | `desktop_app_task` | 一次请求完成找窗口、可选启动、输入及最终图片 |
| 已看过界面，动作已确定 | `desktop_input_batch` | 合并点击、按键、文本、滚动，末尾观察一次 |
| 单个已授权动作 | `desktop_act` | 准备与执行合并，返回后验 |
| 需要看图判断下一步 | `desktop_observe` | 获取新鲜图片、树与观察 ID |
| 需要分开审核动作 | `desktop_prepare_action` / `desktop_execute_action` | 保留两阶段接口 |
| 持续观察变化 | `desktop_watch_start` / `desktop_next_event` / `desktop_watch_stop` | 本地有界队列，Host 主动取事件 |
| 状态与暂停 | `desktop_stats` / `desktop_pause` / `desktop_resume` | 暂停输入不等于停止采集 |

完整参数以实际 `tools/list` 为准。原 `desktop_act_sequence` 保留逐步无障碍核对，是兼容接口；无需中间核对时优先使用快速路径。

### 示例：打开文本编辑器并写入数字

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

这是已知 Gedit 快捷键的示例：Ctrl+T 新建同窗口标签页，Ctrl+N 会创建另一窗口。VS Code 新文档使用 Ctrl+N。不要把示例按键、窗口名或坐标直接套到未知应用。

一个任务复用一个连接。直接使用返回的 `verification`，只在画面不足、目标变化或下一步需要判断时重新观察。`host_approved` 是 Host 对本次适用授权的声明，不代表网页内容能授予权限，也不要求每次点击重复询问。

### 窗口变化与输入法

三个快速入口均可指定 `wait_for_window`，例如应用身份加目标标题子串。它确认窗口状态，不证明网页或文档内容全部加载。等待失败只报告状态，不重复输入。

输入结果中的 `input_method.start` / `restore` 用于核对临时停用和恢复。用户切换窗口、输入上下文消失或控制服务不可用时，可能出现 `restoration_pending` / `restoration_failed`；Host 必须如实报告，不能假称已恢复，也不能抢回用户焦点。

应用任务最多 32 步，默认协作预算 10 秒、最高 30 秒；请求 ID 台账在同一服务进程内最多 256 项，不跨重启去重。普通观察与动作提议默认有效期 30 秒。更多例子与边界见 [快速操作约定](docs/fast-usage.md) 和 [Host 接入](docs/host-integration.md)。

## 截图、持续观察和存储

单次观察与动作后验沿用上游图像参数；本版提速主要减少往返与重复观察，不以降低图像分辨率作为默认方案。图像通过 MCP image 内容块返回。

持续观察是另一条采集链：本地差分/局部变化/稳定性规则挑选关键帧，不包含视觉理解模型。默认缓存 50 帧、事件队列 8 项、每事件至多 4 个关键帧。满队列会丢弃事件，历史关键帧不能当作新鲜可点击快照。

默认不录制截图。显式 `watch --output` 才启用有界录制；它只清理项目识别的录制目录，不管理 Host 自身的聊天记录、图片缓存或日志。截图与树仍可能被 Host 发给模型，使用前需有适用授权。

## 验证与性能口径

已在本机真实桌面验证过浏览器打开与搜索、文本输入、VS Code 创建并保存 Python 文件、在集成终端打印 1～100 并核对完整输出。Rime 活动状态下的字面输入及恢复在这些批次中通过验证；后续也出现过输入上下文消失而恢复未确认的情况，仍需按返回状态处理。

**不承诺完整任务三秒完成。** 本地请求曾达到约 1～3 秒；完整任务还包含模型推理、Host 调度、授权、网络和最终回复。VS Code 完整验收曾用 134 秒，修复版的小范围复测曾用 68.8 秒，其中输入并运行命令的一次原生调用为 5.8 秒。这些单次记录不构成跨机器性能保证。

共享桌面上的焦点检查不是原子事务。用户在单次较长输入内切换或编辑，仍可能造成部分输入；`dispatched` 只表示派发，必须检查最终图像、文本核对和运行结果。不要盲目重放部分完成的操作。

### 运行随包测试

```bash
python -m pip install -e '.[dev]'
python -m pytest -q
python -m pip wheel --no-deps --no-build-isolation --wheel-dir /tmp/very-cu-wheel .
```

随包测试使用模拟后端并隔离输入法调用，不会操作真实桌面。它们验证协议、参数、窗口状态、输入法恢复及回归，不能代替目标 Host 的实际 GUI 验收。

## 项目结构

```text
VERY-computer-use/
├── README.md
├── pyproject.toml
├── bin/upstream-x11
├── src/semantic_cu/
│   ├── mcp_server.py        # stdio MCP、工具描述、输入锁
│   ├── app_task.py          # 单次应用任务与重复请求台账
│   ├── fast_input.py        # 普通共享输入批次
│   ├── literal_input.py     # Fcitx5 字面输入保护
│   ├── window_ready.py      # 本地窗口就绪条件
│   ├── desktop_env.py       # 图形会话环境解析
│   ├── session.py          # 观察、绑定与单步动作
│   └── capture/watch/perception/storage 等模块
├── docs/
├── tests/
└── LICENSES/与 THIRD_PARTY_NOTICES.md
```

辅助 MPX 和离屏会话代码是非默认实验模块，完整能力不支持 Windows/macOS。Wayland 授权采集与原生 X11 控制具有不同边界。

## 第三方声明

项目原创部分尚未指定开源许可证，不应因依赖采用 MIT 就推断整个项目也是 MIT。上游契约及相关 MIT 声明已保留；详见 [THIRD_PARTY_NOTICES.md](THIRD_PARTY_NOTICES.md) 与 [LICENSES](LICENSES/)。本仓库不打包 Node/Python 环境、上游二进制、截图或用户桌面数据。
