# 2026-07-27 候选域到 netlist preview 的确定性编译本地 Gate

## 结论

状态为 **local candidate-to-structured-preview compilation verified; follow-up remote
candidate execution and reference ranking audit completed**。

本 Gate 把现有原子候选域与轻量 standalone Spectre 预评估接起来，但没有连接 Bridge、
没有运行远端 Spectre、没有访问或写入 OA。它证明的是任务生成、来源绑定和拒绝边界，
不是候选电路性能，也不是设计闭环。

## 新增能力

新增 CLI：

```powershell
.\.venv\Scripts\vda.exe preview-task-from-candidates `
  <compile-policy.json> `
  <candidate-source.json> `
  <preview-task-template.json> `
  --output <compiled-preview-task.json>
```

编译器复用现有 `AtomicCandidateSet` 和 `TheorySeedCandidateSet`，没有增加第三套候选格式。
policy 必须显式声明：

- candidate generator 和 source ID；
- 可选 PDK profile；
- candidate ID 的完整选择与顺序；
- 要克隆的结构占位 variant 和输出 ID 前缀；
- 不随候选改变的固定参数；
- semantic parameter 到 MOS、电压源、电阻或电容 typed field 的映射与可选单位 scale。

所有候选参数必须被映射或固定。一个参数可驱动多个不同字段，但一个目标字段不能被
重复写入。raw `instance_parameter_updates`、未映射参数、固定值漂移、缺失 element、重复
目标、generator/source/PDK 漂移和引用占位 variant 的含糊单一 objective 都会拒绝。
编译器不读取 `predicted_metrics` 排名、不综合拓扑、不暗中截断候选；结构模板和候选 ID
仍由上层 Agent 或用户显式提供。结构化 preview 上限从 8 提高到 16 个 variant，以容纳
一个固定基线加当前 9 点物理 seed，同时继续保持远端工作量有界。

固定基线 variant 保持原位置；占位 variant 按声明候选顺序替换。引用占位 variant 的
constraints 会逐 candidate 展开。输出任务保存 candidate source、compile policy 和 task
template 三份文件的 SHA-256，并保存 `variant_source_ids`。这些 hash 计算和映射均标为
`software_inference`；只有后续真实 Spectre OP/AC 才能标为 `eda_result`。

## 真实候选产物的本地编译

使用已保留的 2026-07-26 共栅 seed：

- 输入：`artifacts/theory/common-source-cascode-seed-20260726.json`
- generator：`vda.cascode-seed`
- source ID：`common-source-cascode-op-seed-20260726-live`
- PDK：`nics4304_tsmc28`
- 候选：`cascode-seed-001` 至 `cascode-seed-009`

输出 `artifacts/theory/common-source-cascode-preview-task-20260727.json` 通过 `TaskSpec` 和
planner，包含 10 个 variant：一个未改变的 `common_source`，随后是
`cascode_candidate_001..009`。九个映射分别保持对应的
`cascode-seed-001..009` 身份；没有重新排序。三份输入绑定为：

- candidate source：`cac79804395d001f4cd2fcdfa0acc4498f5bd10c099888476df6546b7f7e71c4`
- compile policy：`727a7b324f9807ed59b3fa896da59f3c7a4b024eabade1eeb06630d06b4b038b`
- task template：`95122e3289d4549db2d97d8366f31399f7f26dc9a86e3f6a9e9ef4b8fcea017b`

最终计划 token 为 `37cbd9002824f337`。计划披露 10 份 standalone Spectre AC deck 和
remote compute；任务没有 OA target，`allow_remote_write=false`。本地编译 Gate 本身未
使用该 token；随后用户单独批准的远端 Gate 已原样使用该 task/token 完成 10/10 执行，
详见 [`2026-07-27-preview-candidate-selection-live.md`](2026-07-27-preview-candidate-selection-live.md)。

## 测试

- `python -m pytest tests/test_preview_compile.py tests/test_netlist_preview.py`：`27 passed`
- `python -m pytest`：`701 passed`
- `examples/tasks/*.json` 逐一 `plan --json`：`196/196`
- `python -m compileall -q src tests`：通过
- `python -m virtuoso_design_agent catalog --json`：通过
- `git diff --check`：通过

测试覆盖确定性顺序、typed 字段写入、constraint 展开、三份 hash、variant 来源映射、
raw atomic 与 theory-seed 两种输入，以及 generator/source/PDK 漂移、参数遗漏、固定值
漂移、重复目标、缺失 element、raw CDF 更新、含糊 objective、未知/重复 variant source
ID 等拒绝路径。

## 尚未验证

- 这 10 个编译后 variant 已在 follow-up 远端 Gate 运行；本记录仍只陈述编译器本地证据，
  新 `eda_result`、排序和资源审计以独立 live 记录为准。
- 当前 compiler 不做拓扑综合，也不从任意网表反推结构；新拓扑仍需提供受校验 graph
  template。
- preview 的绝对指标与 OA→`si` 已知可有超过 20% 的差异，只能作早期筛选。
- 本 Gate 当时没有定义跨 candidate 的自动最终选优；follow-up 已新增 hash-bound
  `preview-select`，可做粗约束、top-k 和 OA 参考排序审计。它仍只输出 shortlist，不把
  preview winner 当作最终设计；胜出结构仍必须进入 OA 同源或人工 ADE 验证。
