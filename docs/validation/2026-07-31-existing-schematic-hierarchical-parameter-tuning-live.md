# 2026-07-31 existing-schematic 一层 child 参数调优真实 Gate

状态：**explicit one-level hierarchical child parameter addressing, OA write/readback,
si binding, Spectre DC/AC selection and winner writeback live verified at nominal TSMC
N28; L5B design-quality closure pending**。

本 Gate 没有修改 `virtuoso-bridge-lite`，没有访问或写入 `/home/xum`，也没有新建、替换
或覆盖 cellview。它复用上一道 Gate 保留的一层 hierarchy 夹具，写入范围只包含 child
`vb_pdk_smoke/vda_l5b_hier_child_001/schematic` 内的 `MN0.Wfg` 和 `RD0.r`；top
`vb_pdk_smoke/vda_l5b_hier_top_001/schematic` 的结构保持冻结。所有远端仿真产物位于
`/data/xum/virtuoso_bridge_smoke/`。

## 新增契约

`existing_schematic` 的实例字段现在可以使用严格的一层路径 `TOP/CHILD`。本次任务用
`XAMP/MN0.Wfg` 和 `XAMP/RD0.r` 表示 top 中 `XAMP` 所引用 child schematic 的两个 primitive
CDF 字段。该语法不是任意层级字符串：

- top-level OA 实例名可保留 Cadence 合法字符，例如 `I0<3>`；
- 只接受恰好一个 `/`，`TOP/MID/LEAF`、空段和反斜杠均在 plan 前拒绝；
- `design_context.hierarchy_parameter_scopes` 必须把 top instance、child library/cell/view、
  child topology SHA-256 和 placement SHA-256 全部固定；
- scoped permission、candidate update 与 `generic_simulation.netlist_parameter_bindings` 必须指向
  同一个 scope；
- 当前只允许同 design library 的 primitive-only child，递归指回 top、跨库 child 和 nested
  hierarchy 均拒绝；
- 同一个可写 child 若被 top 中多个实例复用会拒绝，因为修改 child OA 不是某个 top instance
  私有的 override，不能把共享写入包装成 per-instance tuning。

worker 在每个候选前独立回读 top 和 child，确认 scope 的 master、完整 child topology 与 placement
未漂移，再把 scoped update 解析为 child library/cell/local instance 的定向 CDF 写入。写后定向回读
也直接来自 child；generic `si` parser 则把 subckt body 内的 primitive 重新映射为
`XAMP/MN0`、`XAMP/RD0`，证明 OA `Wfg -> Spectre w` 和 OA `r -> Spectre r`。`si` 完成后还会
再次核对 child topology/placement，避免 netlisting 期间结构被并发改变。

## 任务与授权边界

可复现任务为
[`examples/tasks/hierarchy-child-parameter-tune.bridge.json`](../../examples/tasks/hierarchy-child-parameter-tune.bridge.json)，
plan token 为 `bfb4c38328394998`。任务显式设置 `allow_remote_compute=true`、
`allow_remote_write=true`、`allowed_library=vb_pdk_smoke`、`required_cell_prefix=vda_`，并保持
`replace_existing=false`。

固定上下文指纹：

| 对象 | topology SHA-256 | placement SHA-256 |
|---|---|---|
| top `vda_l5b_hier_top_001` | `d558b41830cfe50cb7c344bde426346c5236e3a89c5af3a77ed0a863d9d5fd6a` | `8899f1d68e1badb505c46aa15c1a6b78ff436d93389948e199c257afe14a6b52` |
| child `vda_l5b_hier_child_001` | `a0bb019a7c81a0d4422e90dca0b5ade814d265cd41544ef245104cb3c7856a41` | `b59661b2047674c4a7da81b56fe745a76bac56082e08bdd92d0e7d2f6433ea35` |

候选域由用户声明的三个原子 tuple 构成。每点只启动一个 worker、回读一次 OA、生成一份 `si`
网表，再在同一份网表上运行 DC 和 AC。DC 要求 `0.1 <= output_dc_v <= 0.8 V`；AC 要求 gain
不低于 `4 V/V`、带宽不低于 `1 GHz`；可行点中最大化 GBW。`max_iterations=3` 正好等于候选数，
controller 不随机造点。

## 三点真实结果

| candidate | `XAMP/MN0.Wfg` / `XAMP/RD0.r` | output DC | gain | bandwidth | GBW | 结果 |
|---|---:|---:|---:|---:|---:|---|
| `compact-load` | `1u / 5K` | `0.639971 V` | `2.76710 V/V` | `16.5187 GHz` | `45.7088 GHz` | 不可行：gain |
| `reference-load` | `1u / 20K` | `0.268755 V` | `4.58848 V/V` | `6.76211 GHz` | `31.0278 GHz` | 可行 |
| `gbw-load` | `1.1u / 18.5K` | `0.264448 V` | `4.58293 V/V` | `7.22436 GHz` | `33.1087 GHz` | 可行且胜出 |

第一点的 GBW 数值最高，但违反 gain 规格，因此没有被 objective 直接选中。这证明 executor 先做
完整规格判定、再在可行集合中排序，不是从手列数值中任选一个“最大值”。最终 winner 的 gain 为
`13.222858899995062 dB`，unity-gain frequency 为 `32.41207461170176 GHz`。

search audit 记录三点全部声明、尝试和完成，`domain_exhausted=true`，结论为
`best_in_declared_discrete_domain`；`continuous_optimum_claim=false`、
`global_optimum_claim=false`。本 Gate 只证明该显式小域内的规格驱动选择，不声称连续或全局最优。

## OA、si 与 Spectre 同源证据

三份 Cadence `si` raw netlist 均同时满足 `topology_consistency=matched` 和
`parameter_consistency=matched`：

| candidate | raw netlist | raw SHA-256 | Spectre envelope SHA-256 |
|---|---|---|---|
| `compact-load` | `/data/xum/virtuoso_bridge_smoke/vda_hierarchy-child-parameter-tune-live_7c119aa46a67/netlist` | `ab26262417a595f29e82b82fed112264f39423a863bcc62252ca2e6c6d0411f3` | `cf523f2ba8e0c0e5906102d6580f1d9f9761352f04cf40d5c96c0f82c732b2da` |
| `reference-load` | `/data/xum/virtuoso_bridge_smoke/vda_hierarchy-child-parameter-tune-live_0a58eb5f0348/netlist` | `78c5874ce82c6d98b6afd640d9a829259036268b91181975a6fb8858436638b0` | `0cffdcb5b01661ff68f7fc7fdbc7249e304c7f45ab96e4f08ec67892d028a35c` |
| `gbw-load` | `/data/xum/virtuoso_bridge_smoke/vda_hierarchy-child-parameter-tune-live_568535936c45/netlist` | `d0dd80931e7d2613d03172a85e0bde187c61e702c1ca5fa17dc757dd6ca91593` | `1d59aa93503f933c536d5b697b9d4aa8d05c8f11ba313caeb11be36cdced00ee` |

每份证据都包含：top `XAMP` call 与 terminal order、child OA primitive graph、完整 child
topology/placement，以及该候选的 `XAMP/MN0.Wfg -> w`、`XAMP/RD0.r -> r` 数值一致性。
raw `si` netlist、Spectre DC/AC 波形和标量属于 `eda_result`；top/child OA 结构、CDF 写后回读属于
`bridge_readback`；路径解析、hash 比较、stage gate、规格与 objective 排序属于
`software_inference`；候选、约束、objective、预算与远端授权属于 `user_input`。

主 run record 位于
`artifacts/runs/hierarchy-child-parameter-live/run.json`，SHA-256 为
`9816F435C7B14BE33458128F32B2C47E7AA1D1696A7F29FEAE80931AE85C3024`。checkpoint SHA-256
为 `B584FFA3DA65F175BD519C11B5D58347E9C3198DE5D3D3242B8EC4AC9F1D3B77`，最终
`next_candidate_index=4`、`pending_candidate=null`。记录时间跨度约 `342.906 s`，外层命令约
`344.7 s`；它包含 Bridge/OA/`si`/Spectre/传输和 VDA 判定，不应冒充纯 Spectre compute time。

## Winner 独立回读

任务完成后使用新的只读 operation 独立回读 child，确认：

```text
MN0.Wfg = 1.1u
RD0.r   = 18.5K
topology SHA-256  = a0bb019a...6a41
placement SHA-256 = b59661b2...ea35
```

child inspect record SHA-256 为
`FA4A0EF709A6DC03F72C413F63268943C176A7EBD948EC7EF87B3F7B3070B002`。另一次独立 top
inspect 确认 top 仍只有 `XAMP`，master、四个 terminal、topology 与 placement 均未变化；其 record
SHA-256 为 `7560FCCF615B926FBC73976C7DB8C62B76635CF0F9C1A6245BFC742EB2B76682`。

## 故障、恢复与资源

本地故障注入覆盖两类必须保留的控制器语义：

- 三点全部不可行时恢复搜索前 child `1u/20K`，不提交“最接近”候选；
- child OA 已写、仿真前中断时，checkpoint 记录 exact pending state；恢复先重新读回 child，完成
  原子候选后只从未完成 index 继续，最后提交 winner。

本次 live 三点运行本身没有发生中断，因此不能把本地故障注入包装成 live transport recovery。
完成后的首次资源审计发现共享 Bridge tunnel 已意外退出；该事件记录为 `system_event`，没有改变已完成
run 或 OA winner。仅为只读审计静默重启 tunnel 后，最终 inventory 为远端 `spectre=0`、`si=0`、
VDA-managed Maestro session=`0`；两个 Virtuoso 进程是运行前已存在的服务。Bridge 随后显式停止，
本地没有残留 VDA/Virtuoso 相关 Python、SSH、SCP、Spectre 或 `si` 进程。最终资源记录
`artifacts/runs/hierarchy-child-parameter-live/resource-audit-after.json` 的 SHA-256 为
`3E931EF3B4E86E253D7E50CB1A46E4FC6D8A20C5B09C2A59E0F002940512D010`，
`deletion_performed=false`；远端证据目录有意保留，没有执行广泛清理。

## 本地验证与剩余边界

新增回归覆盖路径语法、scope/context 审计、child topology/placement/source 漂移、跨库与递归拒绝、
共享 child alias 拒绝、scoped OA 写入/定向回读、`si` 参数绑定、可行选择、全不可行恢复，以及
post-write checkpoint/resume。全量 Python 回归为：

```text
822 passed
```

本 Gate 尚未证明：

- `TOP/MID/LEAF` 两层以上 hierarchy；
- 一个 shared child 对不同 top instance 的独立 override；
- 派生 CDF、`nf/m` 或 multi-finger 总宽度语义；
- 多个不同 child scope 在同一真实模块中的联合优化；
- 并发人工 schematic editor 的 merge；
- transient、noise、PVT、mismatch/Monte Carlo 或 ADE 人工 handoff；
- 用户实际单模块的完整多 analysis 质量规格闭环。

因此下一步不应继续在该三点域增加随机候选。更有价值的 Gate 是在用户首次给出的非夹具单模块上
复用这套路径，只针对真实需要补齐多个唯一 child scope、必要的 output/OP metric 映射和最小 analysis
集合；只有届时确实需要 per-instance override、深层 hierarchy 或派生 CDF 时，才扩展相应契约。
