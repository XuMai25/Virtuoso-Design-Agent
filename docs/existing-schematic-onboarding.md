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

## 从草案到普通 TaskSpec

Agent 或用户先在 resolution 文件中逐项完成：

1. 确认或更正输入、输出、供电、return、bias 和器件角色；
2. 从完整 inventory 中选择允许 fixed/search 的真实 CDF 字段；
3. 声明 source、DC/AC 激励和负载值；
4. 声明 DC/OP metric、transfer、analysis 和必要 sweep；
5. 为每个可写字段声明 OA-CDF 到 `si` 参数映射；
6. 对 hierarchy 确认 subcircuit、terminal order 和 primitive boundary；
7. 最后才增加 constraints、objective、candidate domain 和预算；需要联合微调偏置或负载时，
   把 typed testbench override 与 OA CDF 更新放进同一个原子 candidate。

然后用草案文件和 resolution 编译普通任务：

```powershell
.\.venv\Scripts\vda.exe onboarding-resolve `
  <onboarding-draft.json> `
  <resolution.json> `
  --output <task.json>

.\.venv\Scripts\vda.exe plan <task.json>
```

`onboarding-resolve` 不接受 target、PDK、topology hash、冻结对象或安全开关的 override。它从草案
继承这些字段，并逐项检查：

- resolution 自带的 draft SHA-256 必须与输入文件完全一致；
- 最终 role 必须标为 `user_input`，且 instance/net/pin/terminal 必须存在于 top OA graph；
- 每个授权 CDF 字段必须存在于完整 inventory，并且恰有一个 OA→`si` binding；
- source、load、transfer 和 voltage metric 只能引用 top net 或 ground `0`；
- operating-point metric 只能引用已见 top instance；
- 每个已 inspect 的 child 都必须有 exact library/cell/view 和完整 terminal-order binding；
- `design.close_loop` 的每个 delta 必须以 draft topology 为输入并由声明 inverse 精确恢复；
- baseline 与 alternative role/source/load/OP 对象必须分别存在于对应的本地重算 topology；
- 新增实例 CDF 只能作为 fixed update，permission、update 与 OA→`si` binding 必须精确相等；
- winner-only analysis/metric 必须已经进入 resolution 的 required/optional intent；
- 最终对象必须能通过现有 `TaskSpec` 与 planner 的全部交叉验证。

resolution 开放 `simulation.run`、`design.tune` 与 `design.close_loop`。输出任务固定
`allow_remote_compute=false`、`allow_remote_write=false`、`replace_existing=false`，并把
`allowed_library` 固定为草案 target library；它可以直接 plan，但不能直接真实执行。需要远端动作时，
必须在生成后的普通任务上显式修改安全开关并重新取得 plan token。
`simulation.run` 还拒绝任何参数 update/search、objective 或 `max_iterations>1`，避免声明一个 planner
不会执行的静默写入；需要改变参数时必须明确使用 `design.tune` 或独立 `parameters.apply`。

正常 `TaskSpec`、planner、token、OA 写后回读、`si` 一致性和 checkpoint 仍是最终执行边界。
onboarding 草案不会绕过任何一项，也不会削弱无 `design_context` 的独立 `parameters.apply` 能力。

## 局部拓扑与 winner-only quality

`design.close_loop` resolution 只需在普通参数/analysis intent 之外增加紧凑字段：

```json
{
  "operation": "design.close_loop",
  "topology_edits": {
    "allowed_operations": ["add_instance", "remove_instance", "reconnect_terminal"],
    "mutable_instances": ["MN0", "RS0"]
  },
  "topology_alternatives": [
    {
      "id": "source-degenerated",
      "context_id": "module-source-degenerated",
      "topology_delta": {"direction": "forward", "contract": "<compiled contract>"},
      "roles": ["<roles bound to the after topology>"],
      "added_instance_parameter_permissions": [
        {"instance": "RS0", "parameters": ["r"], "modes": ["fixed"]}
      ],
      "added_netlist_parameter_bindings": [
        {"instance": "RS0", "oa_parameter": "r", "netlist_parameter": "r"}
      ],
      "instance_parameter_updates": [
        {"instance": "RS0", "parameters": {"r": "1K"}}
      ]
    }
  ],
  "winner_verification": {
    "analysis_stages": ["<transient/noise or required quality stages>"],
    "constraints": ["<winner-only constraints>"]
  }
}
```

示例中的字符串占位只说明字段形状，不是可执行 JSON。实际 contract 必须由
`vda topology-compile` 绑定 draft snapshot；roles 仍是完整 typed objects。如果 alternative 没有改变
测试端口、激励、load、transfer 或 OP metric，它自动继承 baseline `generic_simulation`，这里只追加
新增实例 binding。若这些语义确实变化，则改为提供完整 alternative `generic_simulation`，不能同时
使用追加 binding。onboarding layer 最多接受三个 alternative；直接手写普通 TaskSpec 的原七项上限
和 Bridge 低层能力保持不变。

所有 topology×candidate 点只执行 resolution 声明的 nominal stages。`winner_verification` 消费
nominal 真实 winner，才运行 linearity/noise 或显式 operating conditions；失败时沿用现有恢复与
withhold 语义。编译动作本身不运行 EDA，也不自动打开 PVT。

## 写后 CDF promotion

首次 delta 之前，新增实例还不存在于 OA，因此 resolution 只允许它携带 fixed 初值。第一阶段真实
`design.close_loop` 选定并写回 topology winner 后，必须再运行一次独立、只读
`schematic.inspect`。然后用 `onboarding-promote` 把真实 CDF inventory 提升为第二阶段调优权限：

```powershell
.\.venv\Scripts\vda.exe onboarding-promote `
  <stage1-close-loop-task.json> `
  <stage1-close-loop-run.json> `
  <winner-inspect-task.json> `
  <winner-inspect-run.json> `
  <promotion-intent.json> `
  --draft-output <post-readback-draft.json> `
  --task-output <stage2-design-tune.json> `
  --record-output <promotion-record.json>
```

若保留一层 hierarchy，像 `onboarding-draft` 一样重复提供
`--child-inspection TOP_INSTANCE TASK RUN`。promotion intent 必须用最终 stage-1 task 的文件
SHA-256 绑定，可同时预声明 baseline 与最多三个 alternative 分支。每个分支给出：

- variant ID，以及新 draft/task/context ID；
- fresh readback 后允许的完整 CDF permission 集合；
- 与权限集合一一相等的完整 OA→`si` binding；
- 可选 fixed update、完整原子 candidate set 和 winner-only verification；
- 第二阶段预算。

编译器不根据实例类型猜参数，也不会把 `RS0.r`、MOS `Wfg` 或其他名字写死。实际 winner 决定使用
哪个分支；每个授权字段都必须出现在 winner inspect 的未过滤 CDF inventory 中。这样人工指定任意
真实 CDF 字段仍可进入调优，但仿真型调优还必须给出精确 `si` binding。若 onboarding inventory
已经证明 OA 字段存在、但陌生 device/PDK 的 netlist 字段未知，可先把 fresh inspect 的完整目标实例
CDF 表和 exact topology 编译为独立 `parameters.binding.discover` 任务；它只在可逆三网表差分得到
`direct_literal_binding`，或得到“唯一 literal 主字段 + 同实例逐名逐值镜像 derived callback”的
`direct_literal_binding_with_derived_callbacks` 时提供可回填的 mapping。跨实例、结构变化、多个主
候选、未镜像派生或只有 OA callback 的结果不会自动提升；所有副作用仍留在 discovery evidence。
只想直接改一个实例字段时，原有独立 `parameters.apply` 仍可使用，不经过 discovery 或 promotion。

这里的“编译”已有正式入口 `vda binding-discovery-task`，输入同一对 inspect task/run 和一个只含
task/context ID、instance、CDF 字段、probe 值及可选一层 hierarchy binding 的 intent。编译器复用
本章 read-only source 审计，保留完整 CDF inventory 并输出 hash handoff；生成 TaskSpec 的
`allow_remote_compute`/`allow_remote_write` 均为 false。它不自动修改 onboarding resolution，也不把
发现结果直接写入另一个 task；mapping 的采用仍是后续显式 intent/promotion 边界。
分类规则变化时可用 `vda binding-reclassify` 重新校验已保存的 real-Bridge 三阶段 artifact 和恢复
签名；它不连接远端、不写 OA，也不改写原 run record。

stage-1 run 还必须满足：真实 Bridge adapter、成功状态、完整且连续的 candidate record、穷尽声明
topology×parameter 域、唯一 selected candidate，以及与 selected hash 一致的最终 Bridge topology
回读。独立 inspect 必须晚于 stage 1，target/PDK/hash 必须相同。输出 task 冻结所有读回结构并固定
关闭 compute/write/replace；要真实运行第二阶段必须重新授权、重新 plan。若任一步中断，先用原
checkpoint 恢复该阶段；promotion 本身不保存远端状态，相同输入会确定性重建完全相同的三份 artifact。

## 候选级 testbench 微调

`existing_schematic design.tune` 的原子 `candidate_set` 可以同时携带
`instance_parameter_updates` 和 typed `testbench_overrides`。source override 只允许
`dc_value`、`ac_magnitude`、`ac_phase_deg`；load override 只允许已声明 R/C 的正有限 value。
名称必须存在于 resolution 确认的 `generic_simulation`，source/load 类型、节点连接、transfer、
metric 和 OA topology 均保持冻结。每个候选必须包含完全相同的 OA/testbench 字段集合；executor
将完整 tuple 写入 candidate record、checkpoint 和 selection，恢复时不会只凭 OA 参数误认候选。

这类值属于本次仿真条件，不会写入 OA 或自动保存为 ADE/Maestro variable。2026-07-31 的首次 live
路径在新 PMOS 有源负载共源级上用同一候选联合改变 `MP0.Wfg` 与 `VBP_SRC.dc_value`，固定
`VIN_SRC.dc_value` 和 `CL0.value`，并完成 OA→`si`→Spectre DC/AC、transport resume 和 winner
W/L 写回。任务与完整边界见
[`../examples/tasks/pmos-loaded-common-source-onboarding-tune.bridge.json`](../examples/tasks/pmos-loaded-common-source-onboarding-tune.bridge.json)
和
[`validation/2026-07-31-pmos-loaded-common-source-onboarding-live.md`](validation/2026-07-31-pmos-loaded-common-source-onboarding-live.md)。

## 当前边界

- 只编译既有真实 Bridge inspect，不接受 demo 或 `software_inference` 冒充 OA 状态；
- 当前 object count 沿用 `design_context` 的 instance/net/pin 各 128 上限；
- child scope 仅支持同库、唯一引用的一层 schematic；
- 不推断深层 hierarchy、派生 CDF、per-instance override 或 `si` terminal order；
- topology refinement 不能删除或替换已绑定的一层 child；新增 hierarchy 与 child-scope 改写尚未开放；
- 新增实例 CDF 名在本地来自 `user_input`，必须在真实 delta 写后由 Bridge readback 再确认；
- promotion 已能在真实写后 readback 上把新增或人工指定 CDF 提升为第二阶段 search，并将 quality/PVT 只交给该阶段 winner；当前只完成本地编译 Gate，首次端到端 live integration 留给真实用户模块，而不是再造一个测试电路；
- promotion 不推断派生 CDF 语义或未知 `si` 参数名；这些仍须 profile/人工 intent 与真实 netlist 一致性证明。
