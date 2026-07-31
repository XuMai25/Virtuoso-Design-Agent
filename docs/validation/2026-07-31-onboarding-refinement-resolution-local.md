# Onboarding topology refinement 与 winner-only quality 编译本地 Gate

日期：2026-07-31

状态：本地契约 Gate 通过；没有运行 Bridge、远端 Spectre 或 OA 写入。

## 目的

首次接入链路此前只能把只读 draft 与用户确认的角色、CDF 权限、testbench 和有限候选编译成
`simulation.run` 或 `design.tune`。这仍要求用户在真正需要局部拓扑细化时手写完整 TaskSpec，也无法在
onboarding 层表达“所有 topology×parameter 候选先做 nominal 筛选，只对真实 winner 做昂贵质量/PVT”。

本 Gate 只补这个编译缺口。它复用已有 `design.close_loop` controller、通用 topology delta、
generic OA→`si` simulation、checkpoint 和 winner verification；没有新增电路专用模板、worker 或
远端执行路径。

## 新增契约

`ExistingSchematicOnboardingResolution` 现在允许 `design.close_loop`，并新增：

- 一个显式 `TopologyEditPolicy`；
- 最多三个 `OnboardingTopologyAlternative`；
- 可选 `ExistingSchematicWinnerVerificationSpec`。

每个 alternative 只声明 draft-bound forward delta、after-topology 的完整角色、新增实例 fixed CDF
权限/初值，以及必要的 OA→`si` binding。若端口、激励、load、transfer 和 OP 语义未改变，编译器继承
baseline `generic_simulation`，只追加新增实例 binding；真正改变这些语义时才允许提供一份完整替代
simulation，二者不能同时出现。

onboarding 的三项上限是首次接入 convenience layer 的保守预算；普通 TaskSpec 已有最多七个共同
基线 alternative 的能力没有改变，Bridge 的原始能力也没有被限制。

## 编译前证明

resolver 在产生普通 TaskSpec 之前逐项执行：

1. 草案文件 SHA-256、topology SHA-256、target/PDK 与零权限状态保持原有校验；
2. baseline role、source/load/transfer、OP instance 和 hierarchy binding 必须存在于 draft topology；
3. baseline CDF permission 与 OA→`si` binding 必须一一相等并来自完整 inventory；
4. 每个 forward delta 真实应用到 draft snapshot，声明 inverse 再真实应用到 after snapshot；
5. inverse 后 fingerprint 必须精确等于 draft fingerprint；
6. after-role、source/load/transfer、OP instance 与 hierarchy binding 在 after snapshot 上重新校验；
7. 新增 CDF permission 只能属于该 delta 新增的实例、只能使用 fixed mode，且与初值 update 和
   netlist binding 的字段集合完全相等；
8. baseline 与 alternative 的 frozen 集合分别由对应 topology 减去显式 mutable scope 派生；
9. winner analysis/metric 必须先属于 resolution 的 required/optional intent，随后继续由普通
   TaskSpec 校验 sweep、PVT source binding、candidate override 与完整 topology×candidate 预算。

resolver 输出仍固定：

```text
allow_remote_compute = false
allow_remote_write   = false
replace_existing     = false
```

因此编译成功只表示计划契约完整，不是 `eda_result`，也没有产生新的 `bridge_readback`。需要真实运行时，
必须修改生成后的普通任务安全开关并重新 plan，不能复用 resolution 阶段 token。

## 本地代表用例

测试从一个 `MN0 + RD0` 的已读回 draft 出发，以一个三操作 contract 表达最小源极退化：

```text
add NSRC
MN0.S: VSS -> NSRC
add RS0(NSRC, VSS), r=1K
```

baseline 与 source-degenerated alternative 共用两个原子候选；候选同时覆盖 `MN0.Wfg`、输入 DC 和
负载。nominal 使用 shared-netlist DC→AC，预算为 `2 topology × 2 candidate = 4`。transient linearity
与 noise 只放入 winner verification。编译结果成功进入原 planner，且所有远端开关保持关闭。

拒绝测试覆盖：

- 声明 after hash 漂移或正逆不能精确往返；
- delta 触及未授权 mutable object；
- after-role 引用未知实例；
- 新增实例 permission/update/binding 字段不一致；
- candidate 的 testbench override 在 alternative simulation 中失去目标 load。

最后一项同时修复了普通 `TaskSpec` 的一个计划期缺口：候选级 testbench override 现在会对每个
alternative simulation 逐一应用并验证，而不只验证 baseline。

## 验证结果

```text
91 passed  # tests/test_onboarding.py + tests/test_generic_simulation.py
859 passed # 全量 Python 回归
230 planned, 0 failed # 全量 examples/tasks
python -m compileall -q src tests
vda catalog
vda resources # local transient=0, cancel marker=0
git diff --check
```

## 证据边界

- 草案中的 OA topology、placement 与 CDF inventory：既有 `bridge_readback`，本 Gate 未新增；
- 本地 delta replay、hash、context 派生、预算和 planner 接受：`software_inference`；
- role、拓扑意图、新增实例 CDF 名、quality/PVT intent：`user_input`；
- 本 Gate 没有 `eda_result`，也没有远端 `system_event`。

新增实例的 CDF 名在真实 delta 写入前仍只是 `user_input`，不能包装成 Bridge 已确认。首次 live
integration 留给下一次真实用户模块：写后必须重新 inspect 新实例 CDF，再由正常 OA→`si`/ADE 路径
验证参数进入网表，并只对真实 winner 运行任务实际需要的质量分析与可选 PVT。不会为了重复证明编译器
再造一个电路或把每个候选都跑一遍。
