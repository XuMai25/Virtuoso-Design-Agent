# 双环境本地软件验收

在 VDA checkout 的本地环境运行一个入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent.local_validation
```

入口依次执行主环境 tests/ 全量和 Bridge 专用 Python 的必需本地模块。Bridge 当前
清单为 tests/test_schematic_drawing_bridge.py，它使用真实已安装 builder 与假客户端，
不连接服务器、不写 OA、不运行仿真。主环境不需要另装 Bridge，原独立 worker 架构不变。

默认主 Python 是本仓库 .venv，Bridge Python 复用原 DEFAULT_BRIDGE_PYTHON 或进程中
VDA_BRIDGE_PYTHON。不会读取 .env 全文、自动安装依赖或修复网络。可显式指定路径：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent.local_validation `
  --bridge-python 'C:\Users\aknigsesl\tools\virtuoso-bridge-lite\.venv\Scripts\python.exe' `
  --output artifacts/local-validation/my-new-run
```

也有 --main-python 与 --timeout（每阶段秒数，默认180）。两个 Python 必须是不同路径；
保留配置的虚拟环境启动路径，不把其符号链接解析成底层系统 Python。
这是 checkout/可编辑安装的开发入口，需要仓库的 tests/ 与 pyproject.toml，不是服务器
部署命令或新的设计 operation。所有默认采集测试保持离线，live 验收另行授权。

## 通过规则

- 两阶段都要执行并有实际通过用例；不仅检查退出码，还解析各自 JUnit 的 failure/error/
  skip 和必需 Bridge 模块是否被实际采集。
- 主环境仅容许清单中模块的顶层“未安装 virtuoso_bridge”导入 skip，它必须由 Bridge
  阶段无 skip 地覆盖。主环境和补验计数分别保存，不混成“全部零 skip”或相加。
- Bridge 模块仍 skip、Python 缺失、报告缺失、pytest 失败、空覆盖或主环境出现未知
  skip，整体均不通过。失败/incomplete 返回非零；不会把 skip 自动改为通过。
- 新增 Bridge 必需模块时更新 local_validation.BRIDGE_TESTS。未声明的新依赖 skip 会
  阻止整体通过，提醒补清单，而不是自动运行未审查的测试或真实 EDA。
- 非 Windows 的 Job Object skip、未携带旧 artifacts 造成的历史数据 skip 也会报告
  incomplete；这表示本次完整验收尚有边界，不要求造数据或假装平台支持已验收。

## 记录与生命周期

默认写入新建的 artifacts/local-validation/<UUID>/，或 --output 指定的新目录。
**已有目录一律拒绝**，避免覆盖旧 summary 或让 pytest --basetemp 清理用户旧目录。

- summary.json：状态、两套解释器、固定测试命令、各阶段统计、skip 原因和问题。
- main/pytest.xml、main/pytest.log：主环境 JUnit/日志。
- bridge/pytest.xml、bridge/pytest.log：Bridge 环境补验 JUnit/日志。
- 各阶段 tmp/：该次独立 pytest 临时数据；不清理此前验收或用户目录。

不启动 PowerShell/shell 或常驻服务；阶段子进程同步等待，Windows 使用隐藏控制台标志，
超时/失败不自动重试。隔离的测试临时资料保留用于排查，不等于后台计算仍在运行。
调用方的 PYTEST_ADDOPTS 不得悄悄把全量缩为 -k/-m 子集，入口会清除此覆盖。

单独 pytest 仍用于快速开发；完整交付使用本入口。它只提供 software_inference 的
软件验收，不证明当前服务器、PDK、OA、网表或设计性能。实际首轮结果见
[本地验证记录](validation/2026-10-23-dual-validation-p1b-local.md)。
