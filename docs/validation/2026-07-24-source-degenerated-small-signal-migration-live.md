# 2026-07-24 Gate 7C 源极退化共源小信号迁移

## 结论

VDA 已把 Gate 7B 的同一个 OA/`si` binder 和通用 MOS/R/C 矩阵核心迁移到既有
源极退化共源级，没有增加源极退化专用 gain/BW 方程。当前状态是：

**source-degenerated common-source same-source small-signal migration verified at
nominal top_tt**

固定门限沿用 Gate 7B，未因结果移动。Id/gm/gds/VDSAT、gain、phase、bandwidth 和
GBW 全部通过。本 Gate 只覆盖一个 single-MOS、`nf=1/m=1`、nominal `top_tt` 点；不是
多 MOS、多指、任意几何、任意偏置或 PVT 的误差保证，也不授权理论结果写回 OA。

## 授权与副作用

- OA target：`vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic`；
- OA 行为：结构化只读和 `si -batch`；`oa_access_performed=true`，
  `oa_write_performed=false`；
- 远端计算：一次目标 OA 的 `si` + Spectre DC/AC，以及一个 standalone 61 点
  MOS characterization；
- 条件：`nics4304_tsmc28`、`top_tt`、27 ℃、VDD=0.9 V；
- 实际电路参数：W=1 µm、L=0.03 µm、RD=20 kΩ、RS=2 kΩ、VBIAS=0.4 V、CL=3 fF；
- 远端根均位于 `/data/xum/virtuoso_bridge_smoke/`，使用 nonce 并做不存在 preflight；
- 没有新建、修改或覆盖 OA cellview，没有修改 `virtuoso-bridge-lite`。

## 先拒绝过期声明

最初任务沿用 2026-07-20 的 W=0.5 µm 记录，但当前 OA 已为 W=1 µm。执行在 Spectre
前停止：

```text
parameter mismatch for device_width_um: requested candidate=0.5, OA readback=1
```

失败记录为
`artifacts/runs/common-source-small-signal-degenerated-heldout/live-20260724.json`。
它保留 `bridge.probe` 与新鲜 OA readback，simulation action 为
`failed/system_event`；没有远端仿真和 OA 写入。旧 run record 没有被当作当前真源。

## 可复用表征任务生成

新增纯本地命令：

```text
vda characterization-task-from-run GRID_TEMPLATE CIRCUIT_RUN
  --id TASK_ID --instance INSTANCE --polarity nmos|pmos
  [--operating-condition NAME] --output TASK_JSON
```

它只接受成功的 real Bridge、只读 OA + remote-compute run。选定的 simulation action
必须是 `eda_result`，analysis 完整，`si` 参数一致，且目标 MOS 的模型参数 token 全部
可结构化。VDA 从实例提取 model、exact W/L 和参数集合，用户审查的 grid template
只定义 VGS/VDS/VSB、留出点、温度、误差门和 timeout。生成任务保留：

- source run/task/action/instance；
- source `si` netlist SHA-256；
- PDK/corner/temperature/topology；
- model/W/L；
- 参数数量和 canonical signature SHA-256。

来源实例与 `si` 参数是 `eda_result`，生成动作是 `software_inference`。当前自动路径明确
拒绝 `nf != 1` 或 `multiplicity != 1`；它没有假设多指器件可按总宽度等价成单管。

本 Gate 使用：

- grid：`examples/characterization/nics4304-tsmc28-nmos-nominal-grid.json`；
- source circuit run：
  `artifacts/runs/common-source-small-signal-degenerated-heldout/live-signature-20260724.json`；
- generated task：
  `examples/tasks/mos-device-characterize-cs-degenerated-mn0-top-tt.bridge.json`；
- source signature：31 项，
  `9efdaa2cd4f40504a92aafc155b7a74dda3d04ddf001c82707c04680a22a690b`。

## standalone 表征

生成任务在 W=1 µm、L=30 nm 上运行：

```text
VGS: 0.30, 0.40, 0.50, 0.60 V
VDS: 0.15, 0.30, 0.45, 0.60, 0.75 V
VSB: 0, 0.075, 0.15 V
training: 60
holdout: VGS=0.35 V, VDS=0.375 V, VSB=0.075 V
```

run record：
`artifacts/runs/mos-device-characterization-gate7c-cs-degenerated-mn0/live-20260724.json`。
留出点最坏归一化误差为 `15.6103% < 25%`；raw action 与 normalization action 均
`succeeded`。Spectre 为 `21.1.0.612.isr15`，OA access/write 为 false/false。
manifest SHA-256 为
`e4078836e6c40b3c76ae054fcaaa722f71df4b1417defe2dd5408f9faae710e2`。

## 图绑定与固定 policy

policy：`examples/theory/common-source-gate7c-degenerated-validation-policy.json`。
门限与 Gate 7B 完全相同：器件 DC 相对误差 ≤25%，gain 绝对误差 ≤0.5 dB，低频与
−3 dB phase wrapped error ≤5°，BW/GBW 相对误差 ≤25%。此外要求 exact width、exact
model-parameter signature 和 characterization source-instance binding。

binder 从结构化 `si` 得到：

```text
MN0 (OUT IN NSRC VSS) nch_lvt_mac
RD0 (VDD OUT) resistor 20k
RS0 (NSRC VSS) resistor 2k
CL0 (OUT 0) 3fF
```

当 MOS source 不是固定 `VSS` 时，binder 必须在图中找到唯一的 source-node→`VSS`
电阻；policy 还要求真实 DC 的 `source_degeneration_consistency=matched`。RS 接错、缺失
RS、NSRC 电位与器件 VGS/VDS 不一致、source-current 不匹配都会在预测前拒绝。

## 最终结果

实际 DC bias 为 VGS=0.3439089 V、VDS=0.2829975 V、VSB=0.0560911 V；插值使用
exact 30 nm 平面的八个 VGS/VDS/VSB 角点。

| 量 | 理论预测 | Spectre | 误差 | 门限 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| Id | 34.0309 µA | 28.0462 µA | 17.59% | 25% | pass |
| gm | 551.038 µS | 453.582 µS | 17.69% | 25% | pass |
| gds | 63.2810 µS | 49.7661 µS | 21.36% | 25% | pass |
| VDSAT | 0.104321 V | 0.101382 V | 2.82% | 25% | pass |
| low-frequency gain | 9.83439 dB | 9.46026 dB | 0.37413 dB | 0.5 dB | pass |
| low-frequency phase | 179.999982° | 179.999980° | 0.000003° | 5° | pass |
| phase at bandwidth | 133.5186° | 133.9526° | 0.4340° | 5° | pass |
| −3 dB bandwidth | 3.88894 GHz | 3.36329 GHz | 13.52% | 25% | pass |
| GBW | 12.0656 GHz | 9.99487 GHz | 17.16% | 25% | pass |

validation：
`artifacts/runs/common-source-small-signal-degenerated-heldout/gate7c-validation-20260724.json`。
最终 `status=succeeded`、`gate_passed=true`。

| 证据 | SHA-256 |
| --- | --- |
| policy | `9280f7d05eba67a89d13ac2eba3fde1aa2b93c73e187c70ebaf545560d47a0d5` |
| characterization run | `f42072dbae8a6ff6d05e15e316d7bb9ae89c5e95981006f1407515ae2b49bcd9` |
| circuit run | `b20be4b3636b813cd320722bfe280c875ca1606df983e319f997c5fe10139f16` |
| characterization manifest | `e4078836e6c40b3c76ae054fcaaa722f71df4b1417defe2dd5408f9faae710e2` |
| `si` netlist | `cdddb1f631d4baaec731c6c4567077ea2c330be5efdbc9156e6f02289e8d91f4` |
| testbench wrapper | `25537f848acb268e2a919ccd4d8c165a9a626d315146d2f51ac51520c55647c9` |
| raw AC PSF | `3a6f2d7f005c21268e02ba1daf2d24dfbb04502181d7d539b4463b720903c6c7` |

OA 结构为 `bridge_readback`；`si`、OP、raw AC、Spectre metrics 和 source instance 为
`eda_result`；任务推导、归一化、插值、图绑定、矩阵预测和误差 Gate 为
`software_inference`。return code 没有单独用于闭合。

## 测试与下一 Gate

新增测试覆盖自动任务生成、demo/write/incomplete/unparsed/nf/m 拒绝、binding 贯穿 raw
与 artifact、signature/PVT 漂移、RS/NSRC 错接、DC consistency 缺失和固定门失败仍为
partial。完整回归为 `552 passed`。

下一道理论 Gate 是多 MOS 差分对。需要逐实例绑定 NMOS/PMOS 的 model/W/L/signature
和真实 VGS/VDS/VSB，并扩展多器件 OP 映射与差分输入/输出表达式；不能把本 Gate 的
single-MOS 通过直接外推。PVT 仍是可选扩展，不默认附加。
