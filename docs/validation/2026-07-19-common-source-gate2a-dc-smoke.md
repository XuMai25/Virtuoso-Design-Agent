# 2026-07-19 共源放大器 Gate 2A DC smoke

## 结论

状态：**Gate 2A common-source nominal DC same-source bounded loop verified; AC closure pending**。

在 `nics4304_tsmc28` profile 上，新建了 `vb_pdk_smoke/vda_cs_gate2a_001/schematic`。目标 OA 中的 `MN0 + analogLib/RD0` 经结构化回读、`si -batch` 自动网表、Spectre DC operating point、6 点有限搜索、最佳 W 写回和最终独立重新 netlist 复核后通过。结果不能外推为 AC gain/bandwidth、源极退化、noise、corner 或完整 L5B 闭环。

本轮没有修改第三方 Bridge。使用的 Bridge checkout 仍是干净的隔离分支 `codex/vda-transport-recovery@e74379a`；补丁和上游兼容说明见 `docs/third-party/virtuoso-bridge-local-patch.md`。

## 授权和副作用

- 用户明确同意继续 Gate 2 工作并在目标目录中新建 cellview。
- OA 目标：`vb_pdk_smoke/vda_cs_gate2a_001/schematic`。
- 创建时 `replace_existing=false`；只读预检返回 analogLib resistor master 存在、TSMC28 NMOS master 存在、目标 cellview 不存在。
- OA 写入：创建 MN0/RD0，候选搜索逐点暂存 W/L/R，最后写回最佳 W。
- 远端计算：`si` 与 Spectre DC OP。
- 远端 scratch 根：`/data/xum/virtuoso_bridge_smoke/vda_<task-id>_<nonce>/`，不写 `/home/xum`。

## 电路与参数边界

OA 设计拓扑：

```text
VDD -- RD0 -- OUT
              |
             MN0
              |
             VSS

MN0.G = IN, MN0.B = VSS
```

- `MN0`：`tsmcN28/nch_lvt_mac`。
- `RD0`：`analogLib/res`。
- OA canonical parameters：`device_width_um`、`length_um`、`load_resistance_ohm`。
- testbench conditions：`bias_v`、`vdd_v`；它们不伪装成 OA 属性。
- 固定：`L=0.03 µm`、`RD=20 kΩ`、`VDD=0.9 V`。
- 搜索：`W={0.5,1.0} µm × Vbias={0.35,0.40,0.45} V`，预算 6 点。

## 同源证据链

每个已完成候选执行：

1. 写入候选 OA W/L/R 并结构化回读，证据源 `bridge_readback`。
2. `simInitEnvWithArgs` 生成 `si.env`，`si -batch` 从目标 OA 导出网表。
3. 解析并核对：
   - `MN0 (OUT IN VSS VSS) nch_lvt_mac`；
   - `RD0 (VDD OUT) resistor`；
   - OA 与 netlist 的 W/L/R 一致。
4. wrapper 只加入 VDD/VIN/VSS source、DC analysis 和显式 operating-point save，不重复 MN0/RD0 拓扑。
5. Spectre PSFASCII 返回节点电压和 `MN0:ids/vgs/vds/vdsat/gm/gds`。
6. VDA 核对节点 VGS/VDS 与器件 VGS/VDS、MN0 Id 与 RD0 电流，然后计算规格。

最终独立复核的 `si` 网表：

- 远端路径：`/data/xum/virtuoso_bridge_smoke/vda_common-source-dc-verify-bridge_919eaa141529/netlist`。
- SHA-256：`b7d8d9812f06cbae544d15266828934dcbb987978bd0cf175b5b39ca39ff97b6`。
- wrapper SHA-256：`e2e5c3b4f0657b5596f566b7d146c773ad925953ada34a46e6931d164519d0e9`。
- OA/netlist semantic parameters：`W=0.5 µm`、`L=0.03 µm`、`RD=20 kΩ`，一致性 `matched`。

## 调查中暴露并修正的问题

第一次 DC run 的 Spectre 分析本身完成，节点电压和 `dcOpInfo_MN0` 聚合对象存在，但任务失败，因为 VDA 要求的六个器件标量未被 Bridge 通用 parser 展平。失败保留在：

- `artifacts/runs/common-source-dc-op-bridge/gate2a-dc-op-20260719.json`。

处理方式不是修改第三方 parser，也不是把聚合对象当成证据，而是在 VDA 的受控 wrapper 中显式加入：

```text
save MN0:ids MN0:vgs MN0:vds MN0:vdsat MN0:gm MN0:gds
```

重试后得到完整标量，但初始点 `W=1 µm, Vbias=0.45 V` 被正确判为线性区：`Id=42.278 µA`、`VDS=54.43 mV`、`VDSAT=152.07 mV`、饱和余量 `-97.64 mV`。该 partial 记录保留在：

- `artifacts/runs/common-source-dc-op-bridge/gate2a-dc-op-retry1-20260719.json`。

这证明“仿真成功”没有被直接提升为“偏置闭合”。

## 6 点真实搜索

| index | Vbias (V) | W (µm) | Id (µA) | 饱和/摆幅余量 (mV) | 可行 |
| ---: | ---: | ---: | ---: | ---: | :---: |
| 1 | 0.35 | 0.50 | 22.291 | 349.798 | 是 |
| 2 | 0.35 | 1.00 | 31.562 | 163.479 | 是 |
| 3 | 0.40 | 0.50 | 32.219 | 130.553 | 是 |
| 4 | 0.40 | 1.00 | 40.067 | -27.527 | 否 |
| 5 | 0.45 | 0.50 | 38.948 | -27.760 | 否 |
| 6 | 0.45 | 1.00 | 42.278 | -97.636 | 否 |

约束为 `Id>=5 µA`、`VDS-VDSAT>=0`、输出摆幅余量 `>=50 mV`、KCL mismatch `<=1%`；objective 为最大化输出摆幅余量。选中候选 1，随后写回 `W=0.5 µm`。最终 OA 独立回读为：

- `MN0 W/L=0.5/0.03 µm`；
- `RD0=20 kΩ`；
- MN0 terminals：`D=OUT, G=IN, S/B=VSS`；
- RD0 terminals：`PLUS=VDD, MINUS=OUT`；
- pins/nets：`IN/OUT/VDD/VSS`。

run record 与 checkpoint：

- `artifacts/runs/common-source-dc-tune-bridge/gate2a-dc-tune-20260719.json`；
- `artifacts/runs/common-source-dc-tune-bridge/gate2a-dc-tune-20260719.checkpoint.json`；
- checkpoint：`complete=true`、`next_candidate_index=7`、候选索引严格为 1–6。

### 真实不可行与预算 guard

不可行 guard 只声明已知线性区点 `W=1 µm, Vbias=0.45 V`，约束要求饱和/摆幅余量 `>=0.3 V`。任务得到 `-97.64 mV`，返回 `partial`，执行一次 `parameters.restore`，最终独立 OA 回读恢复到 `W=0.5 µm`；checkpoint 为 `complete=true`。记录：

- `artifacts/runs/common-source-dc-infeasible-guard-bridge/gate2a-infeasible-20260719.json`；
- `artifacts/runs/common-source-dc-infeasible-guard-bridge/gate2a-infeasible-20260719.checkpoint.json`。

预算 guard 声明 4 点但 `max_iterations=1`。唯一允许点可行，VDA 仍写出 `search budget exhausted after 1 of 4 declared candidates`，不称为全空间最优。第一次 run 在最终参数应用回读时遇到 WinError 10054；自动恢复原始 OA 后保留 incomplete checkpoint。随后 `--resume` 独立 probe/readback，跳过已完成候选，没有重复 Spectre，只重试 finalize 与最终回读，结果为预期 `partial`、checkpoint `complete=true`、最终 `W=0.5 µm`。记录：

- `artifacts/runs/common-source-dc-budget-bridge/gate2a-budget-20260719.json`；
- `artifacts/runs/common-source-dc-budget-bridge/gate2a-budget-resume1-20260719.json`；
- `artifacts/runs/common-source-dc-budget-bridge/gate2a-budget-20260719.checkpoint.json`。

恢复记录保留原始 transport 错误。该次历史记录产生了两条相同预算注记；随后执行器已将预算 note 改为幂等并补回归测试，未改写历史证据文件。

## 最终独立紧规格复核

最终 OA 写回后，`simulation.run` 在 `allow_remote_write=false` 下重新执行 OA readback → si → Spectre。结果：

| 指标 | 数值 | 来源 |
| --- | ---: | --- |
| `drain_current_ua` | 22.291343 | `eda_result` |
| `vgs_v` | 0.350000 | `eda_result` |
| `vds_v` | 0.454173 | `eda_result` |
| `vdsat_v` | 0.104375 | `eda_result` |
| `saturation_margin_v` | 0.349798 | `eda_result` |
| `output_swing_margin_v` | 0.349798 | `eda_result` |
| `gm_us` | 321.916073 | `eda_result` |
| `gds_us` | 29.784565 | `eda_result` |
| `intrinsic_gain_v_per_v` | 10.808151 | `eda_result` |
| `current_mismatch_percent` | 0.0000466% | `eda_result` |
| `saturation_region` | 1 | `software_inference` |

紧规格 `Id=22.3±1.0 µA`、`VDS>=0.4 V`、饱和余量和摆幅余量 `>=0.3 V`、KCL mismatch `<=0.1%` 全部通过。记录：

- `artifacts/runs/common-source-dc-verify-bridge/gate2a-dc-verify-20260719.json`。

`saturation_region` 不是 PDK 模型直接输出的枚举；它按 `|VDS|>=|VDSAT| && |IDS|>0` 推导，因此标为 `software_inference`。连续器件量和节点量来自 Spectre，OA 结构/参数来自 `bridge_readback`，显式 Vbias/VDD 来自 `user_input`。

`output_swing_margin_v` 的当前 DC 定义是 `min(VDD-VOUT, VDS-VDSAT)`；它是 nominal bias 周围到上电源或饱和边界的较小电压余量，不是 transient 大信号摆幅测量。

## 本地回归

实现后执行：

```text
58 passed
python -m compileall -q src tests
vda catalog
vda plan examples/tasks/common-source-create.bridge.json
vda plan examples/tasks/common-source-dc-tune.bridge.json
vda plan examples/tasks/common-source-dc-verify.bridge.json
git diff --check
```

新增测试覆盖共源网表拓扑和参数解析、DC 标量缺失、节点/器件/KCL 一致性、指标计算、正交 operation 计划、可行选择、不可行恢复、预算耗尽和候选前缀 checkpoint/resume。

## 未验证边界和下一道 Gate

- 未做 AC small-signal gain、-3 dB bandwidth 或 gain-bandwidth trade-off。
- 未做源极退化拓扑；其 DC/AC 不能继承本 cell 的通过状态。
- 未做 noise、PVT/corner、Monte Carlo、输入电容或版图面积。
- 当前是电阻负载 NMOS 共源级，不覆盖有源负载或电流源偏置。
- 当前 nominal 只在 `nics4304_tsmc28/top_tt`、`VDD=0.9 V`、单一温度默认条件下验证。
- Bridge payload 可能已发送后的 transport 中断仍不能自动重放。

下一道硬门：以最终 `W/L/R` 和 `Vbias` 为 nominal DC 基线，由同一 OA schematic 自动 netlist，加入 AC 激励，提取低频增益、-3 dB bandwidth 和 GBW，并在有限、显式参数边界内验证 gain-bandwidth trade-off。通过后再进入源极退化 DC→AC。
