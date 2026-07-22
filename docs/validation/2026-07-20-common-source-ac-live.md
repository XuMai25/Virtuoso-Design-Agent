# 2026-07-20 共源与源极退化只读同源 AC 真实验证

状态：**common-source nominal and source-degenerated read-only AC bias/load tuning verified; downstream bounded design-parameter gate completed**。

本轮在用户明确授权的只读范围内，对两个既有 OA schematic 连续执行 `Bridge readback -> si netlist -> DC OP + complex AC -> metrics -> constraints`。两次任务都允许远端计算、禁止 OA 写入、禁止覆盖已有对象；没有创建或修改 cellview，也没有修改 `virtuoso-bridge-lite`。后续专用 cell 的写入调优是独立授权、独立 run record，记录在 [共源 AC 控制变量与 W/RD/RS 真实调优](2026-07-20-common-source-ac-design-tuning-live.md)。

## 执行范围

| 项目 | nominal | source-degenerated |
| --- | --- | --- |
| target | `vb_pdk_smoke/vda_cs_gate2a_001/schematic` | `vb_pdk_smoke/vda_param_surface_001/schematic` |
| task | `common-source-ac-verify-bridge` | `common-source-source-degeneration-ac-verify` |
| bias / VDD / load | 0.35 V / 0.9 V / 2 fF | 0.35 V / 0.9 V / 2 fF |
| sweep | 1 kHz–1 THz，30 points/decade | 1 kHz–1 THz，30 points/decade |
| OA write | false | false |
| remote compute | true | true |

两次 run 都只包含 `bridge.probe`、`schematic.inspect.before` 和 `simulation.candidate.1`，没有 parameter apply、schematic transform 或其他 OA 写动作。

## 真实结果

| 指标 | nominal | source-degenerated |
| --- | ---: | ---: |
| low-frequency gain | 4.02246 V/V | 3.23782 V/V |
| low-frequency gain | 12.0898 dB | 10.2051 dB |
| −3 dB bandwidth | 5.63155 GHz | 4.17538 GHz |
| gain × bandwidth | 22.6527 GHz | 13.5191 GHz |
| unity-gain frequency | 21.9699 GHz | 12.9475 GHz |
| phase at bandwidth | 134.477° | 132.814° |
| phase at unity gain | 102.280° | 101.190° |
| drain current | 22.2913 µA | 26.5711 µA |
| saturation margin | 0.349798 V | 0.178952 V |
| KCL mismatch | 0.0000466% | 0.00483% |

两点的 DC 工作区都按保存的 `IDS/VDS/VDSAT` 判为 saturation。nominal 与退化点的器件尺寸和 RD 不相同，因此这张表只证明两种既有设计都能走通同源 AC 链路，不能把数值差异全部归因于 RS。相位沿当前复数传递函数的 unwrap 约定报告；这里没有闭环反馈网络，不能把该相位直接称为 phase margin 或稳定性结论。

## AC 波形与交点证据

- 两次 PSFASCII 都返回 271 点 `ac_freq/ac_IN/ac_OUT`，VDA 使用复数 `VOUT/VIN`，不是输出幅度的单独近似。
- nominal 低频参考窗变化 `1.14e-13 dB`；退化点为 `2.06e-13 dB`，都低于任务给定的 0.5 dB 上限。
- nominal −3 dB 交点位于 `[5.41170, 5.84341] GHz`；0 dB 交点位于 `[21.5443, 23.2631] GHz`。
- 退化点 −3 dB 交点位于 `[3.98107, 4.29866] GHz`；0 dB 交点位于 `[12.5893, 13.5936] GHz`。
- 两次均只有一个向下 −3 dB 交点和一个向下 0 dB 交点，没有 analysis issue 或 extraction warning；`analysis_complete=true`。
- gain、bandwidth、GBW、unity-gain、DC 连续量和 KCL 标为 `eda_result`；OA 结构标为 `bridge_readback`；饱和分类、交点算法和完整性判断标为 `software_inference`；task 中的 analysis、sweep、bias、VDD 和 load 标为 `user_input`。

## OA、网表和远端产物

nominal：

- OA/`si` topology：`common_source` / `common_source`。
- 参数一致性：`matched`。
- netlist：`/data/xum/virtuoso_bridge_smoke/vda_common-source-ac-verify-bridge_f469761ceea2/netlist`。
- netlist SHA-256：`b7d8d9812f06cbae544d15266828934dcbb987978bd0cf175b5b39ca39ff97b6`。
- wrapper SHA-256：`7d7a82e6a9352f91d6b56a7f25ea2addd894625a5fa6b17fc8a8e048e3cbf1a9`。
- run record：`artifacts/runs/common-source-ac-verify-bridge/live-20260720.json`。

source-degenerated：

- OA/`si` topology：`source_degenerated_common_source` / `source_degenerated_common_source`。
- 参数一致性：`matched`，同时通过 MN0/RD0 与 MN0/RS0 KCL。
- netlist：`/data/xum/virtuoso_bridge_smoke/vda_common-source-source-degeneration-ac-verify_c028c6243134/netlist`。
- netlist SHA-256：`38259648436e6379d9d925d661b52674472d21cf453b1638f3a9120ccf49735f`。
- wrapper SHA-256：`c63b872fa059af1b1bbab5dce9ffbadaecf4f2dac2bdd044228e538293322f67`。
- run record：`artifacts/runs/common-source-source-degeneration-ac-verify/live-20260720.json`。

Spectre 两次都完成为 0 error、3 warnings、2 notices。run record 保留了 hierarchy flattening 提示和三个 `SFE-1131 scalefactor` warning；当前没有观察到空波形、形状错误或交点异常，但不能把一次 nominal smoke 外推为所有 PDK/corner 均无该 warning 的影响。

## 只读 bias/load 有限搜索

在单点 smoke 之后，两个既有 cell 又分别运行 6 点 `bias_v=[0.30,0.35] V × load_ff=[1,2,5] fF`。planner 把 stage/finalize 标为 `read_only`；run record 中没有 parameter apply、schematic transform 或 create action。每个候选仍重新执行 OA 回读、`si` netlist 一致性、DC OP 和复数 AC。

| topology | bias | load | gain | bandwidth | GBW | swing margin |
| --- | ---: | ---: | ---: | ---: | ---: | ---: |
| nominal | 0.30 V | 1 fF | 3.29 V/V | 8.69 GHz | 28.54 GHz | 0.26 V |
| nominal | 0.30 V | 2 fF | 3.29 V/V | 4.80 GHz | 15.79 GHz | 0.26 V |
| nominal | 0.30 V | 5 fF | 3.29 V/V | 2.05 GHz | 6.75 GHz | 0.26 V |
| nominal | 0.35 V | 1 fF | 4.02 V/V | 10.14 GHz | 40.80 GHz | 0.35 V |
| nominal | 0.35 V | 2 fF | 4.02 V/V | 5.63 GHz | 22.65 GHz | 0.35 V |
| nominal | 0.35 V | 5 fF | 4.02 V/V | 2.41 GHz | 9.71 GHz | 0.35 V |
| source-degenerated | 0.30 V | 1 fF | 3.19 V/V | 6.06 GHz | 19.32 GHz | 0.36 V |
| source-degenerated | 0.30 V | 2 fF | 3.19 V/V | 3.82 GHz | 12.17 GHz | 0.36 V |
| source-degenerated | 0.30 V | 5 fF | 3.19 V/V | 1.81 GHz | 5.77 GHz | 0.36 V |
| source-degenerated | 0.35 V | 1 fF | 3.24 V/V | 6.56 GHz | 21.23 GHz | 0.18 V |
| source-degenerated | 0.35 V | 2 fF | 3.24 V/V | 4.18 GHz | 13.52 GHz | 0.18 V |
| source-degenerated | 0.35 V | 5 fF | 3.24 V/V | 2.00 GHz | 6.48 GHz | 0.18 V |

12/12 候选均为 `analysis_complete=true` 且满足本任务约束。两种 topology 都按 GBW 最大化选择 `bias=0.35 V, load=1 fF`。nominal 6 点一次完成；source-degenerated 在候选 4、5 前各遇到一次 `WinError 10054`，Bridge 原生重启 tunnel 后分别从 checkpoint index 4、5 恢复，没有重跑前三/前四候选。最终 checkpoint `complete=true`，独立 after-inspect 与 before semantic parameters 完全一致：

- nominal：`W=0.5 µm, L=0.03 µm, RD=20 kΩ`；
- source-degenerated：`W=1.0 µm, L=0.03 µm, RD=22 kΩ, RS=2 kΩ`。

run record：

- `artifacts/runs/common-source-ac-bias-load-tune/live-20260720.json`；
- `artifacts/runs/common-source-source-degeneration-ac-bias-load-tune/live-resume3-20260720.json`；
- 两个同目录 checkpoint 均为 complete。

## 已通过与尚未闭合

已通过：

- 既有 nominal 与退化 OA schematic 的真实 `si` 自动网表；
- OA/网表 topology、几何和 semantic 参数一致性；
- 同一 wrapper 中的 DC 饱和预检与复数 AC；
- 真实 gain、bandwidth、GBW、unity-gain、phase 和诊断提取；
- 两个已知合格点的只读 constraints 判定。
- nominal 与退化各 6 点的只读 bias/load 搜索、objective 选择和最终 OA 不变审计；
- 退化搜索经历两次 tunnel 中断后的候选级 checkpoint/resume。

尚未闭合：

- 本文两个既有点不是控制变量 A/B，不能据此得出 source degeneration 的独立因果权衡；后续专用 cell 已补同 W/L/RD/bias/load、只改变 RS 的单点 A/B，但结论仍只适用于该设计点。
- W/RD/RS 的真实 `design.tune` 已在后续专用 cell 通过；L/VDD 与全部维度的联合搜索仍无 live 证据。
- noise、corner、输入电容、真实功耗/面积、feedback stability 和 post-layout 均未验证。

原定的专用 `vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic` Gate 已完成：控制变量 A/B、W/RD/RS 8 点搜索、预算耗尽、不可行恢复、最佳 OA 写回与 transport resume 均有证据。随后同一 cell 的线性度/失真、真实功耗和 ordinary noise 单点只读 Gate 也已通过，见 `2026-07-20-common-source-quality-live.md`。下一道 Gate 转为跨 analysis 的质量约束调优、有限 corner，以及 L/VDD 或经预算约束的联合搜索；通过前不能称为 Gate 2 完整设计质量闭环。
