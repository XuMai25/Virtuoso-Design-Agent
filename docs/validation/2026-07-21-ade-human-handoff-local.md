# 2026-07-21 ADE 人工交接捕获本地实现

## 目标

把“人工仍可在 ADE 中调整并把真实结果交回 VDA”提升为正式 operation，而不是依赖口头约定。首个纵向切片只接收已经存在、由用户保存并聚焦的 ADE Explorer/Assembler Maestro view；不在本 Gate 创建 setup、修改变量、运行仿真或写 OA。

## 已实现范围

- 新增正交 operation `ade.capture`，可用于 `existing_schematic`、反相器和共源任务。
- 任务必须显式使用 `target.view: "maestro"` 和 `ade_capture.backend: "maestro"`。
- 可固定 `ade_capture.history`；省略时由 Bridge 按最新可用 history 规则选择。
- 默认要求 setup 已保存并存在非空 EDA result artifacts；可进一步要求 ADE Detail 表提供结构化 output/spec。
- 先用轻量 snapshot 核对当前聚焦窗口的 library/cell/view/session；无焦点、目标不符、捕获中切换 session 或默认模式下有未保存改动都会失败。
- 复用 Bridge 公共 `snapshot` 与 `read_results`，捕获 Maestro setup、Spectre netlist、PSF、日志和逐 sweep point 的变量、output、spec/pass-fail。
- 本地 manifest 保存每个文件的相对路径、字节数、SHA-256、类别和证据来源；setup 与 simulation artifacts 另有独立聚合指纹。
- executor 只记录 `bridge.probe` 和 `ade.capture`，并明确 `automated_simulation_performed=false`、`oa_write_performed=false`。
- demo adapter 明确拒绝伪造 ADE 会话或 EDA 结果。

## 证据分类

- 任务显式指定的 history：`user_input`
- 人工窗口、保存状态和 Maestro setup：`bridge_readback`
- 实际 simulator input、PSF/log 和 ADE output/spec：`eda_result`
- 未指定 history 时的最新 history 选择：`software_inference`
- 焦点、session、结果缺失等失败：`system_event`

`ade.capture` 的成功只代表人工 setup/history 和已有结果被捕获，不代表结果满足 VDA constraints，也不代表 VDA 已经打通 ADE 原生 sweep/corner 执行。

## 本地验证

测试覆盖：

- 任务拒绝缺少 `ade_capture`、非 Maestro target、参数/搜索/constraints 混入和设置泄漏到其他 operation。
- planner 披露“不会打开、保存、关闭或运行 ADE”，且只有远端读取与本地证据写入，没有 remote compute/OA write。
- executor 保留人工交接语义，不生成 candidate，不调用 simulation 或参数写入。
- Bridge payload 不伪造默认 `analysis`，显式保留 history 与字段来源。
- artifact manifest 区分 setup、simulator input、EDA result 与 log，并生成 SHA-256。
- 无焦点、目标不匹配和 session 中途变化会失败。
- 模拟的完整 focused Maestro capture 同时得到 setup 指纹、Spectre input/result 指纹和结构化逐点输出。

仓库根目录执行 `.\.venv\Scripts\python.exe -m pytest`：`156 passed in 0.52s`。

全部 `examples/tasks/*.json` 重新生成计划：`47/47 example plans passed`；新增示例 token 正常包含 target、history 要求和本地 artifact 副作用。

## 第三方边界

本次没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`。VDA 只调用 Bridge 已存在的 Maestro 公共 API；没有复制其 SKILL、SSH、文件传输、snapshot 或 output-view parser。Bridge 已存在 ADE L state 到 Maestro 的迁移原语，但本次没有调用、更改或包装它。

## 尚未验证

- nics4304/Virtuoso 6.1.8 上真实 focused Maestro view 的只读捕获。
- 人工修改变量、analysis、sweep 或 output 后，前后 setup 指纹能否稳定反映变化。
- ADE Detail 表在当前环境的全部 sweep subpoint、表达式和 pass/fail 读取。
- 旧 ADE L state 的备份后非破坏迁移及人工重开。
- VDA 对现有 Maestro setup 的非覆盖式变量 patch、原生 parametric sweep/corner run 和结果回收。
- ADE PSF 指标与当前 VDA `si` wrapper 指标的数值交叉核对。

## 下一道 Gate

在 `vb_pdk_smoke` 下使用一个新建、不会覆盖已有对象的专用 testbench/Maestro view：人工打开并运行一个小型 Spectre AC sweep，保存 setup；VDA 只读捕获指定 history，核对 setup、design variables、每个 sweep point、input.scs、PSF 和结构化 output/spec。通过后再实现带 setup 指纹前置条件的非覆盖式变量 patch 和 ADE 原生批量 sweep。
