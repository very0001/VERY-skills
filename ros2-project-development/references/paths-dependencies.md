# 资源、路径与依赖

## 源码与安装资源

由功能包拥有其配置、模型清单及小型资源，按项目约定放在包内 `config/` 或 `include/`。明确 CMake `install()` 或 ament_python `data_files` 将运行所需的非代码资源安装到 `share/<package>/`，不要以为 colcon 会自动复制所有文件。维护相对层级，测试实际安装结果。大文件、权重和受许可约束的模型可放在外部资源目录：通过参数指定位置或通过可配置下载准备，记录来源、版本、许可证、校验值和空间要求；默认不将模型权重提交 Git，用户另有要求时另行确认许可和体积。

运行代码通过 ROS 包索引找已安装资源而非依赖当前目录。例如 Python：

```python
from pathlib import Path
from ament_index_python.packages import get_package_share_directory

config_path = Path(get_package_share_directory('input_module')) / 'config' / 'default.yaml'
```

C++ 使用 `ament_index_cpp` 的包 share 查询；Python 包声明 `ament_index_python` 依赖，Python 代码须安装到可导入位置，不是仅复制到 share；launch 可使用 `FindPackageShare` 与 `PathJoinSubstitution`。包名及文件须与真实项目一致。也可提供参数允许用户覆盖资源路径。默认值以包 share 或明确的可配置资源根目录为基础，不把开发者机器的 `/home/...`、工作目录或 `/tmp/...` 硬编码进源码、配置与 launch。示例命令中的路径仅为示例时明确说明并允许替换。

配置和模型不应只存在于 `install/`，否则清理或换板后无法复现。用户提供的模型和数据不是可随意删除的编译产物。测试普通安装及重新构建后的查找，若使用 `--symlink-install`，还要说明符号链接与源码的依赖，不能将该目录视为可独立搬运的发布包。

## 依赖声明与安装

- 每包 `package.xml` 按构建、运行、测试用途声明 ROS 包及可用 rosdep key；选择正确构建工具，避免依赖遗漏或依赖环。验证目标发行版下可解析的 rosdep key。
- Python 第三方库用标准 `requirements.txt`：每行可被 pip 解析的包及版本约束，注释可说明来源，不把中文部署说明混入依赖行。版本按真实验证记录固定或给出合理范围；明确平台或架构限制、不可用轮子时的处理。不要把 `pip freeze` 的全部无关环境和本机路径当作项目依赖。
- 在 README 记录 ROS 发行版、操作系统、架构、Python 版本、系统库、ROS 依赖、pip 安装、模型获取和设备配置。`rosdep` 无法安装的项说明来源、版本及安装顺序。依赖如果是可选组件，写明启用条件。
- 如使用 venv，明确先决条件和创建步骤，确定 ROS 提供的 Python 绑定、pip 依赖和实际节点解释器兼容；必要时设置 `--system-site-packages` 或采用其他已验证方案。`source` 环境脚本本身不等于已安装全部依赖。检查实际 `ros2 run` 入口使用的解释器（包括已安装脚本的 shebang）和导入路径，避免用 pip 覆盖 apt 管理的 ROS 依赖；不要将现成虚拟环境直接复制到其他目录或板子；可按依赖清单重新创建。不要把虚拟环境随 colcon 当作普通包资源复制。
- 不把硬件驱动、设备权限、运行服务和模型下载写成 pip 安装能够自动完成。编译通常也不会自动安装缺失的模型和库；如果提供安装或下载脚本，写清会执行什么，允许用户选择，并验证失败处理。
- 从未准备的独立环境按文档复现比在现有机器上复用已安装包更能检验完整性；做不到时准确报告限制。

对于 C++，按需安装节点可执行文件到 `lib/<包名>` 供 `ros2 run` 使用；公共库、头文件及其依赖按 ament 导出规则安装，供其他包查找。用非 symlink 安装以及实际消费者包检查可执行文件、头文件和库的发现。

## 发布前检查

检查源码是否携带了模型大文件、令牌、私人数据、build/install/log、缓存及平台相关二进制；按项目要求决定忽略和发布清单。部署文件或模型需要加入仓库时记录用户意图与许可。保持外部设备和底盘配置为项目按需依赖，不擅自作为所有 ROS 项目的通用必需项。

参考：[ROS 2 包清单依赖规范](https://docs.ros.org/independent/api/rep/html/rep-0140.html)、[ROS 2 Python launch 文件安装](https://docs.ros.org/en/humble/How-To-Guides/Launch-file-different-formats.html)。
