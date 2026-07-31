# Existing-schematic 参数 binding 自动发现本地 Gate

日期：2026-07-31

## 目的

现有 generic OA→`si` 仿真能够验证用户事先声明的
`instance.oa_parameter -> netlist_parameter`，但不能回答陌生器件的真实 netlist 字段是什么。
仅按名称猜测会把 `Wfg/w`、`fingers/nf`、`m/multi`、派生 CDF callback 或无效字段混在一起，
也会阻塞 onboarding 后新增器件进入正常 `design.tune`。

本 Gate 新增正交 operation `parameters.binding.discover`。它只发现一个明确字段的直接字面
binding，不运行 Spectre、不搜索尺寸、不改变 Bridge，也不把派生关系包装成现有 equality binding。

## 契约

任务只对 `circuit: existing_schematic` 开放，并必须同时声明：

- exact `design_context.expected_topology_sha256`；
- 目标 `instance` 与真实 `oa_parameter`；
- 该字段的 `fixed` 权限；
- 一个与原值按 Spectre scalar 语义不同的 `probe_value`；
- 目标实例未过滤的完整 `expected_instance_parameters` CDF 表；
- scoped child 时，完整的一层 hierarchy scope 和 `si` subcircuit/terminal binding；
- `allow_remote_write`、`allow_remote_compute`、library allowlist、`vda_` cell 前缀和当前 plan token。

`replace_existing` 固定为 false。完整 CDF 表不是输出摘要，而是 compare-and-swap 前置条件：
若 callback 或人工编辑改变了任一旁路字段，任务不会只凭目标字段看似正确继续执行。

## 执行序列

worker 在一个 action 内完成：

1. 读取 OA、物理 pin/placement 和可选 child scope，重新绑定 design context；
2. 要求完整 CDF 表等于任务 baseline；若目标字段恰为声明 probe，则先按中断恢复路径写回原值，
   并要求完整表重新等于 baseline；其他状态直接拒绝；
3. 从未修改 OA 运行 `si -batch`，解析全部 top/scoped instance 的 model、node 和参数表；
4. 只写一个 CDF probe，立即定向回读，再完整读取 callback 后 CDF 表；
5. 重新 netlist，并对完整 top/scoped instance inventory 做参数与结构差分；
6. 无论第 4/5 步成功或失败，都在 `finally` 写回原字段并完整回读；
7. 成功路径第三次 netlist，要求 canonical instance/model/node/parameter signature 与 baseline 相同；
8. 三份 raw netlist 与 `si` log 复制到本地 stage 目录和 SHA-256 manifest；只有本地证据完整后，
   才按 `_generate_oa_netlist` 返回的精确 `/data/xum/.../vda_*` 目录执行并复核远端清理。

若自动恢复失败，worker 返回硬失败并要求 fresh readback；不会继续尝试另一字段，也不会把未知
OA 状态当作 baseline。成功目录被清理，失败目录沿用既有诊断保留策略，不做宽泛删除。

## 提升规则

只有同时满足以下条件，classification 才是 `direct_literal_binding` 并产出
`GenericNetlistParameterBinding`：

- OA 差分恰好只有目标 CDF 字段；
- 完整 top/scoped inventory 的 `si` 差分恰好只有目标实例的一个参数；
- baseline netlist 值与原 CDF 值等价；
- probe netlist 值与 probe CDF 值等价；
- 完整 OA CDF 表恢复；
- canonical `si` signature 恢复。

其他完整执行结果仍有诊断价值，但不进入 tuning：

| classification | 含义 |
| --- | --- |
| `inert` | CDF 改变没有进入 `si` 参数表 |
| `ambiguous_netlist_change` | 一个 probe 引起多个 netlist 参数变化 |
| `netlist_structure_changed` | probe 改变 instance/model/node 结构而不是单一参数 |
| `callback_coupled` | callback 同时改变其他 OA CDF 字段 |
| `single_netlist_parameter_nonliteral` | 只有一个 netlist 字段变化，但不是现有 literal-equality binding |

分类器显式记录 `same_name_assumption_used=false`。因此同名只能是观察结果，不能成为证据规则。
worker 返回 classification 后，父 executor 从 raw OA 表与全 netlist inventory 用同一纯函数独立复算；
二者不完全一致则 run 失败，worker 不能自报一个 mapping 直接进入后续 tuning。

## 证据分类

- probe 合同和字段权限：`user_input`；
- baseline/probe/restored OA 完整参数表：`bridge_readback`；
- 三份 raw `si` netlist、参数 inventory、log 和 manifest：`eda_result`；
- 差分、canonical signature 和 binding classification：`software_inference`；
- interrupted-probe recovery 与远端 scratch cleanup：`system_event`；
- demo adapter：所有状态仍只属于 `software_inference`，不能作为真实映射证据。

## 本地验证

新增 `tests/test_parameter_binding_discovery.py`，覆盖：

- TaskSpec 的 circuit/target/topology/permission/full-CDF/probe 约束；
- planner 同时披露临时 OA 写入和三次远端 netlisting；
- 不依赖 simulation testbench 的完整 `si` parameter inventory；
- direct、nonliteral、callback-coupled、ambiguous、inert 五类差分；
- 跨实例参数变化与 model/node/instance 结构变化不会被提升；
- worker 从声明 probe 中断态恢复后重新开始；
- probe netlisting 注入失败时 `finally` 恢复 OA；
- executor 将 raw action 与 `software_inference` classification 分开记录、独立复判，并独立 after-inspect；
- persisted netlist/log 的实际 bytes 与 manifest size/SHA-256 一致；
- `/home/xum`、非 `vda_` 路径和带 `..` 的路径在触及远端 shell 前被 cleanup guard 拒绝；
- subprocess adapter 路由到专用 worker action及独立本地 artifact root。

完整命令：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

结果：定向文件 `19 passed`；完整回归 `886 passed`。

计划入口也已验证：

```powershell
.\.venv\Scripts\vda.exe plan `
  examples\tasks\existing-schematic-parameter-binding-discovery.demo.json
```

示例只用于查看计划，compute/write 开关为 false；其中 topology hash 与 CDF 表不得用于真实 cell。

## 当前边界与下一 Gate

本轮没有连接服务器、没有 OA 写入、没有远端计算，也没有修改
`C:\Users\aknigsesl\tools\virtuoso-bridge-lite`。因此只称为本地恢复/证据契约已验证。

下一道真实 Gate 应只挑一个已保留、非关键的 `vda_` cellview 和一个已知能直接进入 `si` 的字段，
先由 fresh inspect 生成完整 CDF CAS，再执行一次三网表 probe。它要证明：真实 Bridge CDF callback、
`si` inventory、artifact manifest、远端清理、任务外独立 OA 回读和进程归零均成立。该 Gate 仍不需要
Spectre，也不需要遍历每个字段；只验证机制一次。派生 CDF transformation、多字段 callback、深层
hierarchy 和并发人工 editor 保持后续独立能力，不因本地 direct-binding 测试而宣称闭合。
