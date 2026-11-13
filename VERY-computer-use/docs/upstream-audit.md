# 上游契约与适配范围

本项目适配 `@agent-sh/computer-use-linux@0.7.49`，源码参考提交为 `6b288a604283a500a2254dcbe1433491746a76f5`。发行版的 tools/list 契约保存在 [upstream_contract.py](../src/semantic_cu/upstream_contract.py)，相关 MIT 声明见 [第三方声明](../THIRD_PARTY_NOTICES.md)。

上游负责 Linux 窗口信息、截图、AT-SPI 与实际输入。本项目增加 Host-facing MCP、观察 ID/动作绑定、有界采集、快速批次、字面输入保护和错误处理。不向模型开放上游 shell/setup 能力。

## 适配时需要保持的约定

- 单次观察返回独立 image 内容块与 JSON 元数据，窗口范围、截图尺度和树快照 ID 不能混用。
- 节点编号只在其快照有效期内使用；树截断或分页不足不能当作完整的焦点证据。
- 快速键盘批次固定目标后使用无重复窗口激活的普通输入，动作之间仍核对焦点；坐标来自已观察界面。
- ok 或 dispatched 只代表派发，不保证文字与业务结果正确；超时或连接错误后不自动重放。
- 上游版本变化时，重新核对输入/输出 schema、按键语法、坐标空间、窗口匹配和错误语义，而不是只改版本号。

上游源码：[computer-use-linux](https://github.com/agent-sh/computer-use-linux/tree/6b288a604283a500a2254dcbe1433491746a76f5)。本仓库只包含适配代码与契约，不附带本机完整窗口树、截图或原始桌面日志。
