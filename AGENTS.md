# Virtuoso Design Agent 协作约束

## 产品边界

本项目是位于 `virtuoso-bridge-lite` 之上的设计与验证编排层，不复制 Bridge 的 SSH、SKILL、Spectre 或文件传输实现。Bridge 负责可靠执行；本项目负责任务契约、规划、受控写入、有限搜索、结果判定和证据记录。

当前阶段是 L5A：只在已知 PDK、固定电路模板和显式搜索边界内执行。不得把一次命令成功、OA 对象存在或仿真退出码为 0 单独表述成“设计完成”。

## 远端安全

- 默认只生成计划；真实执行必须同时提供 `--execute` 和计划 token。
- OA 写入还必须在任务文件中显式设置 `allow_remote_write: true`，并通过 library 白名单和 cell 前缀检查。
- 仿真会写远端 scratch；必须显式设置 `allow_remote_compute: true`。
- 默认不得覆盖已有 cellview。`replace_existing` 保持 `false`；需要覆盖时必须在任务中显式声明。
- 不读取、打印、提交 SSH 密码、API key、license 内容或 `.env` 全文。
- 远端 EDA 运行遵守 nics4304 规则：通过 `nics4304-cad1` 进入 cad 执行上下文，产物位于 `/data/xum`，不写 `/home/xum`。

## 证据规则

- 区分 `eda_result`、`bridge_readback`、`software_inference` 和 `user_input`。
- demo adapter 只能证明编排与搜索逻辑，不得作为电路性能证据。
- 原理图创建后必须结构化回读实例、网络和 pins。
- 参数写入后必须回读；仿真必须解析波形或标量指标，不能只看 return code。
- 失败和未验证边界保留在 run record 中，不得静默降级。

## 实现原则

- Python 代码放在 `src/virtuoso_design_agent/`；测试放在 `tests/`。
- 任务能力保持正交：建图、读图、应用参数、ADE 准备/人工捕获/后台运行、仿真、调优、完整闭环均可单独调用。
- 首版只实现反相器真实 adapter；共源/源极退化和差分对先作为明确的后续验收门，不写空壳执行器。
- 不引入 Web UI、数据库、多智能体框架或云端 LLM 依赖，除非真实工作流证明有必要。

## ADE 人工交接

- VDA 的自动规划、搜索和选优不得取消人工打开、调整、运行和保存 ADE setup/history 的能力。
- 自动路径与人工 ADE 路径必须通过显式 operation、目标 view 和状态/结果指纹交接；不得静默覆盖人工改动或把两个仿真状态混成同一真源。
- `ade.prepare` 只能创建明确不存在的新 Maestro view；已有 view 一律拒绝，后续修改必须使用带前置指纹和逐项回读的显式 patch。
- `ade.run` 只能消费已保存的 Maestro setup：使用后台 session，不改变 GUI 焦点、不保存 setup、不写 OA；缺少结构化 output 时不得仅凭 history/回调成功宣称仿真有效。
- ADE setup 属于 `bridge_readback`，实际 Spectre 输入/结果属于 `eda_result`；捕获成功不等于规格闭环。
- 当前只把 Bridge 已公开的 Maestro 接口称为已实现；旧 ADE L state 在完成备份、非覆盖迁移和 live smoke 前不得宣称已打通。

## 验证入口

```powershell
python -m pytest
python -m virtuoso_design_agent catalog
python -m virtuoso_design_agent plan examples/tasks/inverter-close-loop.demo.json
```

真实 Bridge smoke 必须由用户或当前任务明确授权后执行。
