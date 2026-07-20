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

状态：Gate 2A 电阻负载 NMOS 共源与源极退化的 DC、复数 AC、bias/load 条件搜索和 W/RD/RS 写入调优已真实通过。专用 cell 还完成同参数只加 RS 的控制变量比较、3/8 预算耗尽、2 点不可行恢复、多次 transport checkpoint/resume、最佳 OA 写回，以及 5 点 transient 线性度/真实功耗和 211 点 ordinary noise 只读 live smoke。AC+linearity+noise 的固定质量组合已通过本地调优与失败门回归，但尚未运行真实多候选 Spectre；corner 和 L/VDD 联合搜索仍未闭合，因此尚不是完整 L5B 单模块设计代理。

- OA：`MN0` 与 `analogLib/RD0`，连接 `IN/OUT/VDD/VSS`，W/L/R 创建后结构化回读。
- 同源：`si` 网表中的 MN0/RD0/可选 RS0 master、端口和 W/L/R 与 OA 一致；DC wrapper 只提供 VDD/VIN/VSS。AC 复用同一网表和 DC OP，额外提供 unit AC input、显式 sweep 和可选 `load_ff`，不复制器件 topology。
- 指标：Spectre `Id`、`VGS`、`VDS`、`VDSAT`、`gm`、`gds`，并核对节点/器件电压与 MN0/RD0 KCL。工作区按 `VDS >= VDSAT` 显式推导并标为 `software_inference`。
- 闭环：6 点 `W × Vbias` 搜索得到 3 个可行点和 3 个线性区点；选择 `W=0.5 µm, Vbias=0.35 V`，最终 OA 回读和独立重新 netlist 的紧规格 DC 复核均通过。真实不可行 guard 恢复原始 OA；预算 guard 在一次 transport 中断后从 checkpoint 完成且没有重复候选。
- 最终 nominal：`Id=22.291 µA`、`VGS=0.35 V`、`VDS=0.4542 V`、`VDSAT=0.1044 V`、饱和/输出摆幅余量 `0.3498 V`、`gm=321.916 µS`、`gds=29.785 µS`。
- 最终源极退化 DC：原位加入 `RS0(NSRC,VSS)` 后，MN0/RD0/pins/未点名参数保持；`Wfg=1 µm, fingers=2, m=1` 与网表 `w=2 µm, nf=2, multi=1` 四量一致。6 点搜索仅 `Vbias=0.35 V, RS=2 kΩ` 可行，得到 `Id=26.571 µA`、`VDS=0.2623 V`、`VDSAT=0.08337 V`、饱和/摆幅余量 `0.17895 V`，随后 RS0=2 kΩ 独立回读。
- AC 指标契约：从复数 `VOUT/VIN` 计算低频增益和相位；以首个向下半功率交点定义 `bandwidth_3db_hz`，以 `gain × bandwidth` 定义 GBW，并把 unity-gain frequency 独立报告。参考窗不平坦、扫频不足、空/非有限波形和多交点均有显式诊断。
- 最终 nominal AC：271 点真实复数波形，`gain=4.022 V/V`、`bandwidth=5.632 GHz`、`GBW=22.653 GHz`、`unity=21.970 GHz`；DC 饱和、参考窗平坦且 OA/`si` 一致。
- 最终源极退化 AC：271 点真实复数波形，`gain=3.238 V/V`、`bandwidth=4.175 GHz`、`GBW=13.519 GHz`、`unity=12.948 GHz`；DC 饱和、MN0/RD0/RS0 KCL 与 OA/`si` 一致。该点与 nominal 的 W/RD 不同，不作为 RS 的控制变量 A/B。
- 只读条件搜索：nominal 与退化各 6 点 `bias=[0.30,0.35] V × load=[1,2,5] fF` 全部 analysis complete；都选择 `0.35 V/1 fF`。退化搜索两次从 transport 中断恢复，最终 before/after OA 参数一致。
- 控制变量与设计调优：专用 cell 在同一 W=0.5 µm、L=0.03 µm、RD=20 kΩ、bias=0.35 V、load=1 fF 下只加入 RS=2 kΩ，得到 gain −36.98%、BW −20.12%、GBW −49.66%。随后 8 点 W/RD/RS 搜索选择 W=1.0 µm、RD=20 kΩ、RS=1 kΩ，GBW=30.306 GHz，并完成预算和不可行保护。
- 设计质量 live：DC/各动态分析都保存实际 `VDD_SRC:p`；100 MHz 相干 nested sweep 的 5 个输入幅度均有完整样本，得到 `P1dB=88.32 mV peak`、150 mV 点 `THD=13.16%`；1 kHz–10 GHz ordinary noise PSF 有 211 点，输出/输入参考积分噪声为 3.304/0.983 mV RMS。P1dB 未跨越、空 sweep、幅度/PSF 形状不一致仍会显式失败或 unresolved。
- 调优边界：W/L/RD/RS 维度仍逐候选写 OA 和回读；纯 bias/load 条件搜索不写 OA。两种路径都复用原有 constraints/objective、预算和 checkpoint 语义。
- 质量组合：`analysis: quality` 强制声明 AC、linearity、noise 三组 sweep；每候选复用一次 OA/`si` 网表，任一子分析不完整或共享证据不一致即拒绝。该契约和离线搜索已通过，本条尚无真实组合 run。
- 当前最近的硬门：在现有专用 cell 上运行不写 OA 的 bias/load 质量搜索，覆盖可行、不可行、预算耗尽与 transport 恢复；随后才做少量 W/L/RD/RS/VDD 质量写回和有限 corner。不能用单点 live 数值或当前 GBW 网格代替完整放大器设计质量。

跨拓扑的参数基础：`existing_schematic` 可以不依赖固定模板读取已有 schematic，并用 `instance_parameter_updates` 人工指定实例原始 CDF 参数和值字符串；固定模板还可把它与 W/L/R semantic parameters 组合。写入必须经过 callback、立即定向 OA 回读和独立再次回读。通用只读 live smoke 已保留完整 Bridge 结构并枚举 MN0 的 233 个 CDF 字段；专用新 cell 上又真实闭合 `MN0.fingers=2` 和 `RD0.r=22K` 的双重回读。`MN0.m=2` 被当前 PDK callback 恢复为 `1`，因此保留为字段不可持久化边界。它只证明“按名字尝试修改并以 OA 值确认”，不证明 VDA 理解任意参数的物理作用，也不自动允许该参数参与调优。

Gate 2 已分别覆盖 bias/load 条件网格与 W/RD/RS 设计网格，但尚未把器件尺寸、偏置、负载、L/VDD 和退化电阻放入一个受预算约束的联合调整。实现不要求为源极退化新建模板或复制执行器：`schematic.transform` 在同一既有 common-source cellview 上应用固定最小 delta，`source_resistance_ohm` 随后直接进入原有 `parameters.apply`/`design.tune`。当前未实现自动逆变换，且保存成功后的后置审计失败尚无通用 snapshot 回滚；任何拓扑都必须先满足偏置和工作区，再比较增益/带宽。

## Gate 3：差分对

至少覆盖输入共模范围、尾电流、支路平衡、差模增益、带宽、CMRR 和受控 transient。nominal 稳定后再加入有限 PVT，不在第一步盲目扩展组合。

## 升级原则

每个 Gate 都要同时通过结构创建与回读、参数写入与回读、非空仿真和指标重算、可行/不可行规格判定，以及中断恢复。
