# Virtuoso Design Agent (VDA)

VDA 是 [virtuoso-bridge-lite](https://github.com/Arcadia-1/virtuoso-bridge-lite) 之上的 Python 设计与验证编排工具。
AI agent 负责理解拓扑、分析和工程判断；VDA 负责明确任务、计划、有限搜索、规格判断、授权和证据；
Bridge 负责 SSH、Virtuoso/SKILL/OA、Spectre 与文件传输。VDA 不内置云端 LLM，也不要求使用特定 AI 产品。

**第一次使用：** [安装与离线入门](docs/getting-started.md) →
[换机／换服务器适配清单](docs/environment-porting.md)。
**交给 AI agent：** 从 [Agent 接手指南](docs/agent-start-here.md) 开始，不要照抄维护者的远端任务。

## 能做什么

- 单独建图、读图、改参数、仿真；也可有限调优或执行有检查点的闭环。
- 接入已有 schematic，绑定实际实例、端子、CDF 参数、激励、负载和测量。
- 根据清晰图片的审阅后描述生成可编辑原生 schematic；主要连接使用实体导线。
- 复用 OP／器件表／局部模型进行物理分析与有界候选生成，不把“试过的最好点”称作全局最优。
- 管理 ADE 人工交接，保留可打开、调整、保存和重载的工作流。
- 记录真实输入、回读、结果、恢复状态和失败边界，支持复盘与下一轮决策。

目标是给定大致拓扑后的跨电路模块设计细化（项目内部 **L5B**），不是任意电路一键自主设计。
当前已有受控执行、有限优化和部分设计能力，**完整 L5B 质量闭环尚未成立**。
范围以[当前基线](docs/current-baseline.md)、[分级与路线](docs/l5-roadmap.md)和具体验证记录为准。

## 5 分钟离线入门：不需要服务器、Bridge、PDK 或 Cadence

需要 Git、Python **3.13 或更高版本**。从源码 checkout 安装；任务示例随仓库提供。

### Windows / PowerShell

```powershell
git clone https://github.com/XuMai25/Virtuoso-Design-Agent.git
cd Virtuoso-Design-Agent
py -3.13 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m virtuoso_design_agent catalog
./.venv/Scripts/python.exe -m virtuoso_design_agent plan examples/tasks/inverter-close-loop.demo.json
```

不需要激活虚拟环境或更改 PowerShell 执行策略。也可以用同一环境的 `vda` 命令。

### Linux / macOS：离线入口

```sh
git clone https://github.com/XuMai25/Virtuoso-Design-Agent.git
cd Virtuoso-Design-Agent
python3.13 -m venv .venv
./.venv/bin/python -m pip install -e ".[dev]"
./.venv/bin/python -m virtuoso_design_agent catalog
./.venv/bin/python -m virtuoso_design_agent plan examples/tasks/inverter-close-loop.demo.json
```

以上是 Python 离线入口的对应命令；维护者的真实 EDA 路径主要在 Windows 客户端＋远端 Linux 上验证，
不据此承诺其他系统的 GUI、子进程清理或真实 EDA 已通过。

`plan` 只生成计划，不连接服务器。示例中的 library/cell 和 PDK 名称是演示字段，
不证明你拥有该环境。接着执行本地 demo 并读回记录，见[完整入门](docs/getting-started.md#运行离线-demo)。

## 两种模式不要混淆

| 模式 | 需要什么 | 能证明什么 |
| --- | --- | --- |
| 本地计划／编译／复盘 | VDA；部分命令另需你自己的历史记录 | 契约、编排、推理或记录一致性 |
| `--adapter demo` | VDA，无 Bridge／PDK | `software_inference`，不是电路性能 |
| `--adapter bridge` | 已配置的 Bridge、合法 EDA/PDK、已适配的 VDA profile 和任务 | 在本次确切输入与环境下取得的 OA／EDA 证据 |

默认不执行。真实任务必须先审阅计划，再提供 `--execute --token <本次计划token>`；
OA 写入与远端计算还分别需要任务中的显式许可。不要将 demo 的许可和目标复制到真实设计。

## 换一台电脑或服务器怎么用

**Bridge 连接层优先使用它自己的配置，不重写 SSH 或部署机制。**
仅换客户端、仍访问相同已授权环境时，通常配置自己的 Bridge、Python 路径与连接 profile 即可。

换账号、服务器或 PDK 时，还要适配 VDA 侧的工作区、PDK facts 和设计任务。
目前源码保留若干维护者环境约束（包括 `/data/xum`），不是仅改一个主机名就能迁移所有功能。
[适配清单](docs/environment-porting.md)逐项列出位置、修改方法、验证方式和停止条件，
可直接交给对方 AI agent。不要复用维护者的账号、token、OA cell 或历史性能结论。

本仓库另提供[Bridge 兼容补丁参考包](references/bridge-compat/README.md)，包含客户端及远端 daemon 改动、
上游基线、可审阅 patch、校验清单和上游许可证。它不是第二份 Bridge，也不会自动安装或部署；
先核对所用上游是否已有等价修复，再选择性迁移并验证。

## 按任务选择入口

| 需求 | 入口／说明 |
| --- | --- |
| 看能力、生成计划 | `catalog`、`plan`、`run`（不加 `--execute`） |
| 已有 schematic 首次接入 | [inspect → onboarding-draft → onboarding-resolve](docs/existing-schematic-onboarding.md) |
| 汇集设计意图／测量／历史 | [design-intake](docs/design-intake-workflow.md) |
| 参数、偏置和负载微调 | [数值 CDF 与 testbench 映射](docs/numeric-cdf-refinement.md) |
| 物理理由与 gm/ID／电流到尺寸 | [设计理由与物理细化](docs/design-reasoning-and-physical-refinement.md) |
| 从原理图图片生成原生图面 | [schematic-compile 与回读](docs/schematic-image-reproduction.md) |
| 多点候选快速筛选 | [preview → shortlist → OA 真值](docs/fast-preview-shortlist-workflow.md) |
| 复盘、下一步和跨轮次 | [design-review／next](docs/design-review-workflow.md)、[design-iterate](docs/measurement-and-iteration-workflow.md) |
| ADE／人工调整 | [基本操作](docs/virtuoso-basic-operations.md)、[ADE L](docs/ade-l-native-workflow.md) |
| 其他准确参数 | `python -m virtuoso_design_agent --help` 或 `<子命令> --help` |

这些文档中的 `artifacts/...` 历史输入并不随 GitHub 分发。使用需要历史证据的命令时，
请换成自己生成且绑定匹配的记录；不要补造文件、哈希或测量。

## 安全与证据

- 默认不覆盖已有 cellview；写权限、library 白名单、cell 前缀、状态指纹和 checkpoint 不能绕过。
- 修改配置或恢复连接后先核对实际目标；发送后断线不等于未执行，先对账，不盲目重放。
- 不公开密码、私钥、token、license 配置、PDK 文件或私人运行资料。
- 不因一次 return code 0、OA 对象存在或历史 demo 成功宣称设计完成。
- 区分 `eda_result`、`bridge_readback`、`software_inference` 和 `user_input`。
- 新环境真实执行前，列明目标 library/cell/view、写入、计算次数、远端路径、覆盖风险与恢复方式，取得授权。

## 本地验证与贡献

首次离线安装可运行不依赖私有记录的入门回归：

```powershell
./.venv/Scripts/python.exe -X utf8 -m pytest tests/test_getting_started.py --basetemp artifacts/quickstart-test-01
```

`--basetemp` 必须指向新的、本次测试专用目录，不能指向已有运行资料。
完整开发交付仍使用[双环境本地验收](docs/local-validation.md)。
本轮干净副本安装、补丁可应用性与实际测试见[共享入口验收](docs/validation/2026-10-26-sharing-onboarding-local.md)。
新 clone 没有维护者的 Bridge 或私有历史资料时，完整验收可能报告缺项／skip；
离线入门通过不等于完整软件、远端连接或设计性能验收。

源码：`src/virtuoso_design_agent/`；测试：`tests/`；
PDK facts：`src/virtuoso_design_agent/resources/pdks/`。
修改前读 `AGENTS.md`，按任务只查必要指南；本地未提交改动不应被覆盖。
本仓库不随附 PDK、Cadence、Bridge 安装或用户认证材料。

维护者原 README 的详细命令与现场历史保留在[维护者快照](docs/maintainer-workflows.md)，
仅供按需追溯，不是新用户默认配置或现成授权。
