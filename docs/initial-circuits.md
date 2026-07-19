# 起步电路与验收门

## Gate 1：CMOS 反相器

状态：Gate 1 已于 2026-07-19 通过。证据覆盖 OA/`si` 同源网表、逐候选暂存、9 点 timing/energy、收紧规格、最佳点写回、不可行/预算耗尽恢复，以及 3 次 SSH/tunnel 中断后的候选级 checkpoint/resume 和最终独立 OA 回读。

- OA：创建 `MP0`、`MN0`，连接 `IN/OUT/VDD/VSS`，创建顶层 pins。
- 参数：回读并修改 NMOS/PMOS finger width 与 length。
- 仿真：TSMC28 transient，提取 `tPHL`、`tPLH`、平均 delay、rise/fall、skew、过冲/欠冲，以及每输入周期总供电能量和平均供电功率。
- 闭环：在有限 `Wn/Wp` 网格中逐候选暂存 OA 参数、回读、自动 netlist 和仿真；应用最佳可行点，无可行点时恢复初始参数。
- 失败注入：Bridge 不在线、已有不匹配 cell、netlist 失败、OA/netlist 参数不一致、空波形、无 crossing、规格不可行、预算耗尽、可恢复中断、错 task/token checkpoint 和搜索空间外 OA 状态。
- 权衡：`supply_energy_per_cycle_fj` 与 `average_supply_power_uw` 来自 `VDD_SRC:p` 波形并标为 `eda_result`；周期能量包含泄漏。`gate_area_proxy_um2=(Wn+Wp)L` 仍是 `software_inference`，不是实际 layout area 或输入电容。
- 已知边界：Bridge 隔离分支已修复 Windows stale PID、no-tunnel warm、幂等传输退避和 payload 发送前恢复；payload 可能已发送后的中断仍由 VDA checkpoint/readback 处理，不能盲目重放。
- 后续硬门已转到 Gate 2；反相器不再阻塞共源 DC 工作，但 transport 边界仍跨 Gate 保留。

## Gate 2：单 MOS 共源与源极退化

状态：Gate 2A 电阻负载 NMOS 共源级的 DC operating point 已于 2026-07-19 通过；AC 与源极退化尚未通过。

- OA：`MN0` 与 `analogLib/RD0`，连接 `IN/OUT/VDD/VSS`，W/L/R 创建后结构化回读。
- 同源：`si` 网表中的 MN0/RD0 master、端口和 W/L/R 与 OA 一致；wrapper 只提供 VDD、VIN bias 和 DC analysis。
- 指标：Spectre `Id`、`VGS`、`VDS`、`VDSAT`、`gm`、`gds`，并核对节点/器件电压与 MN0/RD0 KCL。工作区按 `VDS >= VDSAT` 显式推导并标为 `software_inference`。
- 闭环：6 点 `W × Vbias` 搜索得到 3 个可行点和 3 个线性区点；选择 `W=0.5 µm, Vbias=0.35 V`，最终 OA 回读和独立重新 netlist 的紧规格 DC 复核均通过。真实不可行 guard 恢复原始 OA；预算 guard 在一次 transport 中断后从 checkpoint 完成且没有重复候选。
- 最终 nominal：`Id=22.291 µA`、`VGS=0.35 V`、`VDS=0.4542 V`、`VDSAT=0.1044 V`、饱和/输出摆幅余量 `0.3498 V`、`gm=321.916 µS`、`gds=29.785 µS`。
- 下一道硬门：在已验证 DC 偏置点上做共源 AC gain/bandwidth 与 gain-bandwidth trade-off；随后源极退化拓扑重新通过 DC→AC，而不是直接复用本结果。

Gate 2 完整验收仍需覆盖 AC 增益与带宽，以及器件尺寸、偏置、负载和退化电阻的受控调整。必须先满足偏置和工作区，再比较增益/带宽。

## Gate 3：差分对

至少覆盖输入共模范围、尾电流、支路平衡、差模增益、带宽、CMRR 和受控 transient。nominal 稳定后再加入有限 PVT，不在第一步盲目扩展组合。

## 升级原则

每个 Gate 都要同时通过结构创建与回读、参数写入与回读、非空仿真和指标重算、可行/不可行规格判定，以及中断恢复。
