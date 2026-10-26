# 2026-07-31 existing-schematic winner-only PVT、多拓扑与一层 hierarchy Gate

状态：**winner-only staged PVT same-source OA writeback live verified; independent multi-alternative controller locally verified here and subsequently live verified; explicit one-level hierarchy locally verified; L5B closure pending**。

> 同日 follow-up 已完成三个 topology alternative 的真实 OA round-trip、同源 DC/AC、三次 transport
> checkpoint 恢复和 cascode winner 写回，见
> [existing-schematic 多 topology alternative 真实闭环 Gate](2026-07-31-existing-schematic-multi-alternative-live.md)。

本轮没有修改 `virtuoso-bridge-lite`，没有覆盖或新建远端 cellview，也没有访问 `/home/xum`。真实执行只修改既有
`vb_pdk_smoke/vda_l5b_staged_gate_001/schematic` 中任务明确声明的 `MN0.Wfg` 与 `RD0.r`，远端计算产物只写入
`/data/xum/virtuoso_bridge_smoke/vda_existing-schematic-winner-verification-pvt-live_*`。

## 本轮实现

### Winner-only 昂贵验证

`existing_schematic design.tune/design.close_loop` 可声明独立 `winner_verification`：

- nominal candidate search 与 winner Gate 使用各自的 ordered analysis stages、constraints 和 sweep；
- nominal search 先按真实 EDA 结果产生 provisional winner；
- 只有该候选被暂存并进入 winner Gate，runner-up 不会消耗 transient/noise/PVT；
- 可选 operating conditions 最多五项，每项显式给出 process corner、temperature 和 VDD；
- VDD 必须通过 `generic_simulation.operating_condition_supply_source` 绑定到已声明 source；
- 每个 condition 都必须 analysis complete 且满足全部声明约束；跨 condition 最坏值仅作为
  `software_inference` 聚合，原始指标继续保留为 `eda_result`；
- Gate 失败或不完整时恢复搜索前 OA，清空最终 selection，并在 search audit 记录 recommendation withheld；
  不会把未经同一 Gate 验证的 runner-up 自动升级为 winner。

### 多个独立 topology alternatives

`topology_refinement` 现可声明最多七个 independent alternatives，同时兼容旧的单 alternative task：

- 所有 delta 必须锚定同一 baseline topology SHA-256；
- 每个 after topology fingerprint 必须不同；
- 每个 alternative 自带 design context、generic simulation 和新增实例的固定参数；
- executor 按 `(baseline + alternatives) × parameter candidates` 展平候选域；
- 每个 alternative 从 exact common baseline 开始，进入下一 alternative 前先执行当前 inverse；
- baseline、最后 alternative 或更早 alternative 都可成为最终 winner；
- checkpoint 保存扁平 candidate index 与 exact topology identity；未知或部分结构拒绝自动写入。

本地故障测试覆盖三拓扑完整顺序、最后变体胜出、从最后变体返回并提交更早变体、共享基线拒绝、
alternative 中断恢复、全不可行与 initial OA 恢复。fixture 中的模拟指标仅为 `software_inference`，不是电路性能证据。

### 显式一层 hierarchy

`GenericOaSimulationSpec.hierarchy_bindings` 可逐个声明 top-level instance、child library/cell/schematic、
`si` subcircuit 名与 terminal order。worker 在 Spectre 前完成：

1. 顶层 OA instance/master/terminal 与 `si` subckt call 精确匹配；
2. 通过 Bridge 独立回读 child schematic；
3. child pin set 与声明 terminal order 相容；
4. child primitive instance/model/node graph 与 subckt body 相同；
5. child topology SHA-256 与 `bridge_readback` 来源进入 netlist evidence；
6. 顶层 subckt call 上声明的 CDF→netlist 参数仍逐项核对。

未绑定的非 primitive master、nested subcircuit、端子顺序或节点漂移均拒绝。当前范围明确记录为
`explicit_one_level_primitive_children`；更深 hierarchy 不会静默 flatten。本轮完成 parser/readback fixture、
child node mismatch 和 unbound hierarchy 负向测试，但尚未创建真实层次化 OA cell，因此不能称为 live verified。

## 真实 winner-only PVT Gate

任务：`examples/tasks/existing-schematic-winner-verification-pvt.bridge.json`

计划 token：`249f24d21b641d45`

名义搜索只运行 DC→AC，共两个显式原子候选：

| candidate | MN0.Wfg / RD0.r | output DC | gain | BW | GBW | nominal |
|---|---:|---:|---:|---:|---:|---|
| compact-load | 1u / 5K | 0.639971 V | 2.76710 V/V | 16.5187 GHz | 45.7088 GHz | feasible |
| gain-load | 1.1u / 18.5K | 0.264448 V | 4.58293 V/V | 7.22436 GHz | 33.1087 GHz | feasible |

声明离散域 2/2 完整执行，GBW objective 选择 `compact-load`。search audit 为
`best_in_declared_discrete_domain`，但 `continuous_optimum_claim=false`、`global_optimum_claim=false`。

只有 `compact-load` 运行 winner-only DC→AC→transient→noise：

| condition | output DC | gain | BW | GBW | max THD | max average supply power | input-referred noise |
|---|---:|---:|---:|---:|---:|---:|---:|
| TT / 27 ℃ / 0.90 V | 0.639971 V | 2.76710 V/V | 16.5187 GHz | 45.7088 GHz | 1.08374% | 46.8591 µW | 690.668 µV RMS |
| SS / 125 ℃ / 0.81 V | 0.551188 V | 2.32419 V/V | 15.5007 GHz | 36.0266 GHz | 0.954840% | 41.9634 µW | 738.273 µV RMS |

两条件均满足本任务的 output DC、gain、bandwidth 和 THD 约束；noise stage 要求结果完整但本任务没有为噪声设置
质量门。三个 transient 幅度没有包围 1 dB compression，因此 `input_1db_compression_v_peak` 保留 unresolved warning；
不能把 THD 通过包装成 P1dB 已闭合。

winner worker 只回读一次 OA、生成一次 `si` netlist，并在同一路径复用四类 analysis：

- topology SHA-256：`a0bb019a7c81a0d4422e90dca0b5ade814d265cd41544ef245104cb3c7856a41`
- netlist：`/data/xum/virtuoso_bridge_smoke/vda_existing-schematic-winner-verification-pvt-live_cd1e4ddedc55/netlist`
- netlist SHA-256：`3fbb25de33a6117d5b74bd5ef3ca9b18542173c916ddf0c384c72371da3d3680`
- parameter consistency：`matched`
- reuse contract：`one_worker_one_oa_readback_one_si_netlist`

## 中断、恢复和最终回读

首次 candidate 1 在下载生成的 `si.env` 时遇到本机 DNS/SCP 无法解析 `nics4304-cad1`。该动作记录为
`system_event`，不是 EDA 不可行；executor 将 OA 恢复并回读为 `1u/5K`，checkpoint 保持
`next_candidate_index=1`。连接恢复后使用同一 checkpoint，从 candidate 1 重新执行，没有伪造半批结果。

成功 run 保留失败 action、恢复 readback、两个 nominal candidate、winner-only Gate 和最终 inspect。
对应 simulation action 时间为：candidate 1 `86.23 s`、candidate 2 `87.63 s`、winner Gate `150.13 s`。
恢复命令自身约 `334.8 s`；run record 从首次开始到最终完成的时间跨度为 `387.15 s`，包含中断与恢复边界，
不能与纯 Spectre compute time 混用。

任务内 `schematic.inspect.after` 与随后新的任务外 Bridge inspect 都确认：

```text
MN0.Wfg = 1u
RD0.r   = 5K
```

证据文件：

- `artifacts/runs/existing-schematic-winner-verification-pvt/live-20260731.json`
- `artifacts/runs/existing-schematic-winner-verification-pvt/live-20260731.checkpoint.json`
- `artifacts/runs/existing-schematic-winner-verification-pvt/live-resume1-20260731.json`
- `artifacts/runs/existing-schematic-winner-verification-pvt/inspect-after-transport-20260731.json`
- `artifacts/runs/existing-schematic-winner-verification-pvt/inspect-final-target-20260731.json`

## 资源边界

只读资源盘点从 567 个远端 evidence directory、310,779,921 bytes 变为 571 个、311,013,370 bytes；
本 Gate 新增四个精确目录、233,449 bytes。postflight 得到 `spectre=0`、`si=0`、VDA-managed Maestro session=0；
两个远端 Virtuoso 进程为 preflight 已存在对象，本轮未终止。未执行任何删除。Bridge tunnel 最终显式停止，
本地未见 `ssh/scp/spectre/si/vda/virtuoso-bridge` executor 残留。

## 验证与边界

本轮完成后全量 Python 回归：

```text
797 passed
```

已真实验证：winner-only PVT 调度、OA 参数暂存/写回、同一 OA→`si` 网表跨两条件四分析、transport resume、
最终独立 OA 回读和资源归零。

本页当时尚未真实验证的多 alternative OA round-trip 已由上述 follow-up 闭合。仍未真实验证：一层 hierarchy OA→`si`、派生 CDF、深层 hierarchy、并发人工 editor、
mismatch/Monte Carlo，以及更完整 foundry corner set。当前状态仍是 L5B 通用 controller 的重要纵切，不是可重复的完整
L5B 单模块设计质量闭环。

下一道有价值的 Gate 不是继续给本两点域增加随机点，而是在用户首次提供的真实单模块拓扑上，按规格只声明必要的
局部 alternatives 和 analysis；若该设计含一个 subcell，则同时完成一层 hierarchy live 绑定、winner-only 质量 Gate、
失败恢复和人工可复核 OA/ADE 交接。
