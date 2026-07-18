# 起步电路与验收门

## Gate 1：CMOS 反相器

状态：L5A 执行闭环已于 2026-07-19 通过首轮 live smoke；同源 OA netlisting、有效约束和功耗/面积权衡仍未闭合。

- OA：创建 `MP0`、`MN0`，连接 `IN/OUT/VDD/VSS`，创建顶层 pins。
- 参数：回读并修改 NMOS/PMOS finger width 与 length。
- 仿真：TSMC28 transient，提取 `tPHL`、`tPLH`、平均 delay、rise/fall 和 skew。
- 闭环：在有限 `Wn/Wp` 网格中找满足规格的候选，应用回 schematic，再回读。
- 失败注入：Bridge 不在线、已有不匹配 cell、无 crossing、规格不可行、预算耗尽。
- 下一道硬门：用 OA schematic 自动 netlist，而不是长期维持“schematic 和手写 deck 共享参数”的并行路线。

## Gate 2：单 MOS 共源与源极退化

至少覆盖 DC operating point（`Id`、`VGS`、`VDS`、工作区、摆幅余量）、AC 增益与带宽，以及器件尺寸、偏置、负载和退化电阻的受控调整。必须先满足偏置和工作区，再比较增益/带宽。

## Gate 3：差分对

至少覆盖输入共模范围、尾电流、支路平衡、差模增益、带宽、CMRR 和受控 transient。nominal 稳定后再加入有限 PVT，不在第一步盲目扩展组合。

## 升级原则

每个 Gate 都要同时通过结构创建与回读、参数写入与回读、非空仿真和指标重算、可行/不可行规格判定，以及中断恢复。
