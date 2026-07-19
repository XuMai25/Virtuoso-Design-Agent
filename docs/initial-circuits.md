# 起步电路与验收门

## Gate 1：CMOS 反相器

状态：Gate 1 已于 2026-07-19 通过。证据覆盖 OA/`si` 同源网表、逐候选暂存、9 点 timing/energy、收紧规格、最佳点写回、不可行/预算耗尽恢复，以及 3 次 SSH/tunnel 中断后的候选级 checkpoint/resume 和最终独立 OA 回读。

- OA：创建 `MP0`、`MN0`，连接 `IN/OUT/VDD/VSS`，创建顶层 pins。
- 参数：回读并修改 NMOS/PMOS finger width 与 length。
- 仿真：TSMC28 transient，提取 `tPHL`、`tPLH`、平均 delay、rise/fall、skew、过冲/欠冲，以及每输入周期总供电能量和平均供电功率。
- 闭环：在有限 `Wn/Wp` 网格中逐候选暂存 OA 参数、回读、自动 netlist 和仿真；应用最佳可行点，无可行点时恢复初始参数。
- 失败注入：Bridge 不在线、已有不匹配 cell、netlist 失败、OA/netlist 参数不一致、空波形、无 crossing、规格不可行、预算耗尽、可恢复中断、错 task/token checkpoint 和搜索空间外 OA 状态。
- 权衡：`supply_energy_per_cycle_fj` 与 `average_supply_power_uw` 来自 `VDD_SRC:p` 波形并标为 `eda_result`；周期能量包含泄漏。`gate_area_proxy_um2=(Wn+Wp)L` 仍是 `software_inference`，不是实际 layout area 或输入电容。
- 已知边界：VDA 能在 Bridge 外部恢复后确定性续跑，但 Bridge 0.7.0 的 Windows stale PID 和自动 tunnel 重建尚未修复；这属于 Bridge 层债务，不由 VDA 复制 SSH 逻辑规避。
- 下一道硬门：Gate 2 共源/源极退化放大器，先验证 DC operating point，再做 AC gain/bandwidth。

## Gate 2：单 MOS 共源与源极退化

至少覆盖 DC operating point（`Id`、`VGS`、`VDS`、工作区、摆幅余量）、AC 增益与带宽，以及器件尺寸、偏置、负载和退化电阻的受控调整。必须先满足偏置和工作区，再比较增益/带宽。

## Gate 3：差分对

至少覆盖输入共模范围、尾电流、支路平衡、差模增益、带宽、CMRR 和受控 transient。nominal 稳定后再加入有限 PVT，不在第一步盲目扩展组合。

## 升级原则

每个 Gate 都要同时通过结构创建与回读、参数写入与回读、非空仿真和指标重算、可行/不可行规格判定，以及中断恢复。
