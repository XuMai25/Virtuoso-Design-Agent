# 安装与离线入门

这是给新 clone 的教程，不依赖维护者电脑、服务器或 artifacts。
真实服务器接入见[适配清单](environment-porting.md)，AI agent 先看[接手指南](agent-start-here.md)。

## 1. 安装

在你自己选择的目录执行 README 的 clone 和安装命令。要求 Python >=3.13；
不要使用维护者的 H: 盘、用户名或虚拟环境。建议从 checkout 可编辑安装：

```powershell
py -3.13 -m venv .venv
./.venv/Scripts/python.exe -m pip install -e ".[dev]"
./.venv/Scripts/python.exe -m virtuoso_design_agent --help
```

Linux/macOS 对应解释器是 `./.venv/bin/python`。离线规划不需要另装 Bridge。
`vda` console script 与 `python -m virtuoso_design_agent` 调用相同入口；
显式解释器可避免用错环境。当前推荐 checkout 工作流：pip 包不包含仓库的全部 examples/tests/docs。

## 2. 只生成计划

```powershell
./.venv/Scripts/python.exe -m virtuoso_design_agent catalog
./.venv/Scripts/python.exe -m virtuoso_design_agent plan examples/tasks/inverter-close-loop.demo.json
./.venv/Scripts/python.exe -m virtuoso_design_agent run examples/tasks/inverter-close-loop.demo.json --adapter demo
```

应看到计划、`Plan token` 和 `No action executed`。即使换成 `--adapter bridge`，
没有 `--execute` 的 run 仍是计划，不是连接健康检查。不要凭 catalog 中的 executable 判断你的环境可用。

## 运行离线 demo

以下代码只针对仓库内原样的 demo task。先阅读前一步的计划，然后取 JSON 计划中的 token：

```powershell
$demoTask = "examples/tasks/inverter-close-loop.demo.json"
$demoPlan = ./.venv/Scripts/python.exe -m virtuoso_design_agent plan $demoTask --json | ConvertFrom-Json
$demoPlan.confirmation_token
./.venv/Scripts/python.exe -m virtuoso_design_agent run $demoTask --adapter demo --execute --token $demoPlan.confirmation_token --output artifacts/getting-started/demo-run.json
./.venv/Scripts/python.exe -m virtuoso_design_agent design-review $demoTask artifacts/getting-started/demo-run.json
```

Linux/macOS 可先人工复制 plan 打印的 token，再用：

```sh
./.venv/bin/python -m virtuoso_design_agent run examples/tasks/inverter-close-loop.demo.json --adapter demo --execute --token 'COPY_THIS_PLAN_TOKEN' --output artifacts/getting-started/demo-run.json
```

`COPY_THIS_PLAN_TOKEN` 是占位符，必须替换。不要对真实 Bridge 任务自动复制 token 形成无人审阅授权。
每次使用新的输出路径；不要把已有记录覆盖后当作新证据。

预期：执行有限候选、生成 JSON run record 及同目录 checkpoint；不启动 Bridge、SSH、Virtuoso 或 Spectre。
demo task 中虽然声明了模拟写入／计算许可，但实际由显式 demo adapter 在内存中处理。
不要把它的 target、权限、规格或参数当成真实环境模板直接执行。所有 demo 指标属于
`software_inference`，不是 TSMC 性能或 PDK 校准数据。

## 3. 从最小例子理解任务

查看 `examples/tasks/inverter-close-loop.demo.json`：

- `operation`：要做哪一件事，不强制全闭环。
- `circuit`、`target`、`pdk_profile`：电路契约与目标；计划成功不代表目标存在。
- `parameters`／`parameter_space`／原子候选：固定值与显式有限域。
- `constraints`／`objective`：规格与选优目标，必须有实际可解析的测量。
- `safety`：计算、写入、白名单、cell 前缀和非覆盖许可。
- `limits`：候选预算和 timeout。

完整 schema 在 `src/virtuoso_design_agent/models.py`；支持组合由 catalog/planner 检查。
不确定字段时查子命令 help 和同类任务，不发明 `--config`、`--pdk-file` 或 operation。

## 4. 给真实工程做准备

用户可给大致拓扑、图片或已有 OA，再声明规格、可调范围、冻结对象和预算。
先做物理分析与必要的 OP 判断；不是随意列几组尺寸后称最优。实际操作入口见 README 表格。

真实执行需要另一套材料：自己的 Bridge 连接、合法 EDA/PDK、明确工作区、PDK facts、目标和授权。
按[换机适配清单](environment-porting.md)补齐，缺什么就报告什么；不用为了入门新建一堆电路。

## 常见问题

| 现象 | 含义／处理 |
| --- | --- |
| Python 版本不符／import pydantic 失败 | 核对本次解释器与安装；不要安装到系统 Python 后继续调用别的环境 |
| Bridge Python 指向旧维护者路径 | 显式传自己的 `--bridge-python`；不要创建同名假目录 |
| `unknown PDK profile` | 目前从 package resources 加载 JSON；没有通用外部 profile 文件参数 |
| 完整 pytest 出现历史资料 skip | GitHub 不分发私有 artifacts；保留缺项，不伪造历史记录，也不改为自动通过 |
| token mismatch | 内容或计划变了；重新生成并审阅，不能固定旧 token |
| demo 运行成功 | 只证明离线软件流程；不证明 OA、网表、物理模型或真实规格 |

完整软件交付用[双环境验收](local-validation.md)。本教程只是可移植的离线入门验收。
