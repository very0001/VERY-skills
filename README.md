# VERY-tools

持续整理和维护的 **AI Skills、MCP 工具与工程工作流集合**，覆盖机器人软件开发、学术文档制作和本地桌面协作。

仓库原名为 `VERY-skills`。随着可运行工具的加入，现统一使用 `VERY-tools`：既保留指导智能体完成任务的技能，也收纳可以安装、连接和验证的工具代码。

**`main` 是总览与导航，各类项目放在对应分支。** 浏览或下载时请先选择需要的分支。

## 内容导航

| 分支 | 内容 | 入口 |
| --- | --- | --- |
| `mcp` | 本地桌面 MCP、快速动作批次、窗口与输入状态处理 | [VERY Computer Use](https://github.com/very0001/VERY-tools/tree/mcp/VERY-computer-use) |
| `ros2` | ROS 2 项目结构、模块契约、部署与验收规范 | [ros2-project-development](https://github.com/very0001/VERY-tools/tree/ros2/ros2-project-development) |
| `academic` | 学术报告、答辩 PPT 与 Word 文档整理 | [学术技能目录](https://github.com/very0001/VERY-tools/tree/academic) |

### MCP：本地桌面工具

[VERY Computer Use](https://github.com/very0001/VERY-tools/tree/mcp/VERY-computer-use) 面向 Ubuntu 桌面，通过 MCP 向支持本地工具的 AI Host 提供截图观察、普通共享键鼠、连续动作批次和应用任务。

当前重点包括：减少重复观察和调用往返、在本地等待目标窗口就绪、保护 Rime 环境下的字面输入，以及控制持续截图缓存数量。默认不启用虚拟键鼠，不录制截图。运行依赖、权限、版本核对和实际验证边界见该项目 README；本地动作耗时不等于完整 AI 任务耗时。

### ROS 2：工程开发规范

[ros2-project-development](https://github.com/very0001/VERY-tools/tree/ros2/ros2-project-development) 用于规划、开发、整理和交付 ROS 2 工作空间与功能包。覆盖职责拆分、接口与参数、资源安装、系统组合、独立运行、部署说明和分层验收，适用于 Python、C++ 与混合项目。

### Academic：学术内容与文档

| 技能 | 用途 |
| --- | --- |
| [academic-ppt](https://github.com/very0001/VERY-tools/tree/academic/academic-ppt) | 中文工科开题、中期与答辩 PPT；组织高信息密度的图、公式、方法与证据 |
| [word-academic-reprot](https://github.com/very0001/VERY-tools/tree/academic/word-academic-reprot) | 中文 Word 学术报告、开题、文献综述与研究方案的写作、修订和审查 |
| [word-organize](https://github.com/very0001/VERY-tools/tree/academic/word-organize) | 整理已有 Word/WPS 文档的样式、标题、图表、编号和分页 |

## Skills 和 MCP 有什么区别

- **Skill** 主要由 `SKILL.md`、参考资料和辅助脚本组成，告诉智能体如何组织与检查任务。使用前阅读技能说明，并把完整目录安装到相应 Host 支持的技能位置。
- **MCP 工具** 是实际运行的程序，通过标准协议提供操作能力。需要先安装运行依赖，再在 Host 中配置连接；模型看到工具不代表目标机器上的功能已经验收。

两者可以一起使用，但不能把复制一个 Skill 文件夹当成安装了完整 MCP 服务，也不能把协议握手成功当成真实桌面操作成功。

## 获取需要的内容

```bash
# 桌面 MCP 工具
git clone --branch mcp --single-branch https://github.com/very0001/VERY-tools.git VERY-tools-mcp

# ROS 2 技能
git clone --branch ros2 --single-branch https://github.com/very0001/VERY-tools.git VERY-tools-ros2

# 学术技能
git clone --branch academic --single-branch https://github.com/very0001/VERY-tools.git VERY-tools-academic
```

进入对应项目后，以它自己的 README 或 `SKILL.md` 为安装和使用入口。不同技能可能依赖文档编辑、渲染或特定应用工具；不同 MCP 也有各自的平台要求。

## 维护方式

- 按主题分支组织内容，具体项目在独立目录中维护。
- 保留可执行入口、依赖、参数、测试和适用边界，让新环境或不同 AI 能独立使用。
- 区分模拟测试、真实应用操作和完整任务验收，不把派发成功或局部耗时当成最终结果。
- 不在仓库保存账户凭据、用户桌面截图、私有文档、虚拟环境或运行缓存。

发现问题时，可在 [Issues](https://github.com/very0001/VERY-tools/issues) 中说明分支、项目、系统与版本、复现步骤和实际错误；提交前请移除敏感信息。

## 许可与第三方内容

各目录的许可和第三方声明以对应文件为准；公开可见不等于自动授予整个仓库统一的开源许可。MCP 项目的声明见 [THIRD_PARTY_NOTICES.md](https://github.com/very0001/VERY-tools/blob/mcp/VERY-computer-use/THIRD_PARTY_NOTICES.md)。
