# 2026-07-21 ADE 双向人工交接本地实现

## 目标

把“VDA 给出可人工继续操作的 ADE 入口，人工调整后再把真实结果交回 VDA”提升为正式 operation，而不是依赖口头约定。首个纵向切片可以为已有 design 新建一个明确不存在的 ADE Explorer/Assembler Maestro view/test，也可以接收由用户保存、运行并聚焦的 view；不修改任何已有 Maestro 状态，不在本 Gate 自动配置 analysis/sweep 或运行仿真。

## 已实现范围

- 新增正交 operation `ade.prepare` 与 `ade.capture`，均可用于 `existing_schematic`、反相器和共源任务。
- `ade.prepare` 要求 design view 已存在、目标 `maestro` 不存在；创建一个持久化 Spectre test，保存/关闭后重新打开并回读 test 名称。
- `ade.prepare` 在模型、planner 和 worker 三层拒绝覆盖已有 Maestro view，不设置 analysis、stimulus、sweep 或 output，也不改 schematic。
- 任务必须显式使用 `target.view: "maestro"` 和 `ade_capture.backend: "maestro"`。
- 可固定 `ade_capture.history`；省略时由 Bridge 按最新可用 history 规则选择。
- 默认要求 setup 已保存并存在非空 EDA result artifacts；可进一步要求 ADE Detail 表提供结构化 output/spec。
- 先用轻量 snapshot 核对当前聚焦窗口的 library/cell/view/session；无焦点、目标不符、捕获中切换 session 或默认模式下有未保存改动都会失败。
- 复用 Bridge 公共 `snapshot` 与 `read_results`，捕获 Maestro setup、Spectre netlist、PSF、日志和逐 sweep point 的变量、output、spec/pass-fail。
- 本地 manifest 保存每个文件的相对路径、字节数、SHA-256、类别和证据来源；setup 与 simulation artifacts 另有独立聚合指纹。
- executor 对 `prepare` 只记录新 Maestro view 写入和持久化 test 回读；对 `capture` 明确 `automated_simulation_performed=false`、`oa_write_performed=false`。
- demo adapter 明确拒绝伪造 ADE 会话或 EDA 结果。

## 证据分类

- 任务显式指定的 history：`user_input`
- 人工窗口、保存状态和 Maestro setup：`bridge_readback`
- 实际 simulator input、PSF/log 和 ADE output/spec：`eda_result`
- 未指定 history 时的最新 history 选择：`software_inference`
- 焦点、session、结果缺失等失败：`system_event`

`ade.prepare` 的成功只代表存在一个可由人工继续编辑的持久化 Spectre test；`ade.capture` 的成功只代表人工 setup/history 和已有结果被捕获。两者都不代表结果满足 VDA constraints，也不代表 VDA 已经打通 ADE 原生 sweep/corner 执行。

## 本地验证

测试覆盖：

- `prepare` 任务拒绝缺少设置、非 Maestro target、参数/搜索混入、`replace_existing=true` 和设置泄漏；`capture` 保留原有相应拒绝。
- worker 模拟验证 design 存在 + Maestro 不存在时只创建一次 Spectre test、保存、关闭并独立回读；已有 Maestro view 时不会打开或修改。
- planner 披露 `prepare` 的 Maestro OA 写入、无 remote compute，以及不配置 analysis/sweep；安全层仍要求 library 白名单和 cell 前缀。
- planner 披露“不会打开、保存、关闭或运行 ADE”，且只有远端读取与本地证据写入，没有 remote compute/OA write。
- executor 保留人工交接语义，不生成 candidate，不调用 simulation 或参数写入。
- Bridge payload 不伪造默认 `analysis`，显式保留 history 与字段来源。
- artifact manifest 区分 setup、simulator input、EDA result 与 log，并生成 SHA-256。
- 无焦点、目标不匹配和 session 中途变化会失败。
- 模拟的完整 focused Maestro capture 同时得到 setup 指纹、Spectre input/result 指纹和结构化逐点输出。

仓库根目录执行 `.\.venv\Scripts\python.exe -m pytest`：`168 passed in 0.57s`。

全部 `examples/tasks/*.json` 重新生成计划：`48/48 example plans passed`。`ade.prepare` 示例计划明确披露 Maestro OA 写入、既有 view 拒绝、无 remote compute；`ade.capture` 示例则保持远端只读与本地 artifact 写入。

## 第三方边界

本次没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`。VDA 只调用 Bridge 已存在的 Maestro 公共 API；没有复制其 SKILL、SSH、文件传输、snapshot 或 output-view parser。Bridge 已存在 ADE L state 到 Maestro 的迁移原语，但本次没有调用、更改或包装它。

## 尚未验证

- nics4304/Virtuoso 6.1.8 上真实非覆盖 prepare 和 focused Maestro capture。
- 人工修改变量、analysis、sweep 或 output 后，前后 setup 指纹能否稳定反映变化。
- ADE Detail 表在当前环境的全部 sweep subpoint、表达式和 pass/fail 读取。
- 旧 ADE L state 的备份后非破坏迁移及人工重开。
- VDA 对现有 Maestro setup 的全局变量 CAS patch 已有本地契约但仍待 live；test/corner scoped 变量、analysis/output patch 和原生 corner 尚未实现。background parametric sweep run/result 回收已有本地契约，仍待 live。
- ADE PSF 指标与当前 VDA `si` wrapper 指标的数值交叉核对。
- `save_setup` 已落盘而随后 close/readback/transport 失败时可能留下一个新但未确认的 Maestro view；重试会因“已存在”而停止，必须先人工检查，当前没有删除式自动回滚。

## 后续 Gate（人工部分已延期）

人工 prepare/edit/capture、旧 ADE L 迁移和数值交叉检查已按用户决定延期，详见 [`../deferred-manual-gates.md`](../deferred-manual-gates.md)。全局变量逐项旧值 CAS 已由 [`ade.variables.apply`](2026-07-21-ade-variable-patch-local.md) 完成本地契约；当前自动化 Gate 转为 test/corner scoped 变量的等价回读、analysis/output patch，并让 [`ade.run`](2026-07-21-ade-background-run-local.md) 在 background 原生 sweep 后保留 netlist/PSF 证据。延期项目完成前不升级人工兼容状态。
