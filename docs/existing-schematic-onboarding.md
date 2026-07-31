# Existing-schematic 自动接入工作流

## 目标

`vda onboarding-draft` 把已经完成的只读 `schematic.inspect` 任务与 run record 编译为一份
可审查的接入草案。它解决的是“每换一个电路都要手工抄写 topology hash、实例参数、pins 和
层级 scope”的时间成本，不负责凭名称猜出完整电路意图。

该命令完全在本地读取 JSON：

- 不启动 Bridge；
- 不访问或写入 OA；
- 不运行 `si`、Spectre 或 Maestro；
- 不修改源 inspect 记录；
- 输出固定为 `status=needs_user_intent`、`read_only=true`、
  `generic_simulation_draft.executable=false`。

## 为什么同时需要 task 和 run

run record 自身保存 task ID 和 plan token，但没有重复保存完整 target。编译器因此同时读取原始
inspect task 和 run，并核对：

1. task 必须是 `existing_schematic + schematic.inspect`；
2. `allow_remote_compute=false`、`allow_remote_write=false`；
3. run 必须来自 `virtuoso-bridge-subprocess` 且整体成功；
4. task ID 与由当前 task 重算的 plan token 必须和 run 一致；
5. run 只能包含 `bridge.probe` 和唯一一次 `schematic.inspect`；
6. inspect action 必须是成功的 `bridge_readback`；
7. canonical topology、完整实例参数表和 placement SHA-256 必须齐全。

task 与 run 的文件 SHA-256 都写入草案。这样不能把另一个 cell 的回读、demo 数据或已经改变的
task 静默拼到当前 target 上。

## Flat schematic

先准备并执行一个安全开关均为 false 的只读 inspect task。得到 run record 后执行：

```powershell
.\.venv\Scripts\vda.exe onboarding-draft `
  <inspect-task.json> `
  <inspect-run.json> `
  --id <context-id> `
  --output <onboarding-draft.json>
```

输出包含：

- exact target、PDK profile、task/run hash 和 plan token；
- canonical topology 与 topology/placement SHA-256；
- 默认冻结的全部 instance/net/pin；
- 未过滤的逐实例 CDF 字段和值；
- 根据 pin direction 和少量常见供电名称生成的输入、输出、正电源、return 候选；
- 空的 source/load/metric/parameter-binding 模板；
- 必须解决后才能运行的 decision 清单。

所有 CDF 字段都被保留，但每项默认是 `not_authorized`。`Wfg/l/r/nf/m` 等名称只得到
`geometry_width/geometry_length/resistance/multiplicity` 类型提示；提示属于
`software_inference`，不会自动变成写权限或网表映射。

## One-level hierarchy

top inspect 不能证明 child schematic 的真实结构，因此每个需要接入的一层 child 都必须另提供
自己的只读 inspect task/run：

```powershell
.\.venv\Scripts\vda.exe onboarding-draft `
  <top-inspect-task.json> `
  <top-inspect-run.json> `
  --id <context-id> `
  --child-inspection XAMP <child-inspect-task.json> <child-inspect-run.json> `
  --output <onboarding-draft.json>
```

`--child-inspection` 可以对不同 child 重复使用。编译器要求：

- top instance 存在；
- child task target 与 top instance master 的 library/cell 完全一致，view 为 schematic；
- top/child 使用同一 PDK profile；
- child pin set 与 top instance terminal set 相同；
- child 与 top 位于同一 design library；
- 该 child 没有被多个 top instance 共享。

通过后，`design_context_draft.hierarchy_parameter_scopes` 会绑定 child target、topology SHA 和
placement SHA，child 参数以 `TOP/CHILD` 路径进入完整 inventory。但编译器仍不把字典顺序当作
`si` terminal order，也不宣称 child 一定是 primitive-only。建议的 subcircuit 名、terminal set
与待确认状态保存在 `hierarchy_candidates`，而可执行模板中的 `hierarchy_bindings` 保持为空。

共享 child 被明确拒绝，是因为修改 child OA 会影响所有 top 引用，不能把这种写入描述成某个 top
instance 的私有参数。per-instance override 需要独立契约，当前不会静默降级。

## 输出中的证据边界

| 内容 | evidence source |
|---|---|
| OA topology、pins、CDF 原始字符串、placement | `bridge_readback` |
| topology hash、角色候选、参数类型提示、hierarchy candidate | `software_inference` |
| 最终角色、写权限、testbench、analysis、规格、objective、预算 | `user_input` |

`design_context_draft` 是一个可验证的安全起点：它只有结构 inventory role、冻结全部结构、不给参数
权限，也不声明 analysis/metric。`generic_simulation_draft` 则故意不是可执行 contract，不能直接传给
`vda run`。

## 从草案到执行任务

Agent 或用户需要逐项完成：

1. 确认或更正输入、输出、供电、return、bias 和器件角色；
2. 从完整 inventory 中选择允许 fixed/search 的真实 CDF 字段；
3. 声明 source、DC/AC 激励和负载值；
4. 声明 DC/OP metric、transfer、analysis 和必要 sweep；
5. 为每个可写字段声明 OA-CDF 到 `si` 参数映射；
6. 对 hierarchy 确认 subcircuit、terminal order 和 primitive boundary；
7. 最后才增加 constraints、objective、candidate domain、预算和远端安全开关。

正常 `TaskSpec`、planner、token、OA 写后回读、`si` 一致性和 checkpoint 仍是最终执行边界。
onboarding 草案不会绕过任何一项，也不会削弱无 `design_context` 的独立 `parameters.apply` 能力。

## 当前边界

- 只编译既有真实 Bridge inspect，不接受 demo 或 `software_inference` 冒充 OA 状态；
- 当前 object count 沿用 `design_context` 的 instance/net/pin 各 128 上限；
- child scope 仅支持同库、唯一引用的一层 schematic；
- 不推断深层 hierarchy、派生 CDF、per-instance override 或 `si` terminal order；
- 当前输出是 review draft，自动把已确认 resolution 编译成完整 simulation/tuning task 仍是下一项能力。
