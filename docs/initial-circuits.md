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

状态：Gate 2A 电阻负载 NMOS 共源与源极退化的 DC、复数 AC、bias/load 条件搜索和 W/RD/RS 写入调优已真实通过。专用 cell 还完成同参数只加 RS 的控制变量比较、预算/不可行/transport 恢复、5 点 transient 线性度/真实功耗和 ordinary noise。AC+linearity+noise 固定质量组合已经覆盖 bias/load、W/RD/RS 和 L/VDD；随后同一 OA/`si` 网表又通过 TT/SS/FF 三个显式温度/供电条件，并完成可选的两候选 PVT-aware bias 调优。2026-07-23 在全新 cell 上又完成 source degeneration add→DC/AC→remove；恢复后的 placement、nominal `si` 网表和全部 DC/AC 指标与 add 前相同。当前状态是 **reversible controlled common-source topology patch verified**；真实 OA 设计变量跨 PVT 写回、任意实例参数搜索、差分对和完整 L5B 仍未闭合。

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
- 质量组合：`analysis: quality` 强制声明 AC、linearity、noise 三组 sweep；每候选复用一次 OA/`si` 网表，任一子分析不完整或共享证据不一致即拒绝。真实 4 点搜索得到 2 个可行点；两个 0.40 V 点虽有更高 GBW，但因 THD 与 DC 功耗超限被拒绝，最终选择 `0.35 V/1 fF`。预算、全不可行和首候选 transport 恢复均正确，before/after OA 参数一致。
- 质量写回：8 点 W/RD/RS 搜索全部三项完整，候选 4 transport 中断后从独立 OA 回读恢复；GBW objective 写回 `1 µm/20 kΩ/1 kΩ`。线性度 objective 随后选择 `RS=2 kΩ`，真实获得 THD/P1dB/功耗改善并接受 GBW/noise 代价。2 点全不可行任务恢复初始 OA，selection 保持为空。
- L/VDD + PVT：固定 W=1 µm、RD=20 kΩ、RS=2 kΩ、bias=0.35 V、load=1 fF 的四点 `L×VDD` 质量搜索全部可行，按 GBW 选择并回读 `L=0.03 µm/VDD=0.9 V`。随后不写 OA 的 TT/25℃/0.90V、SS/125℃/0.81V、FF/−40℃/0.99V 共九项分析共享一个网表，全部通过；SS 给出最坏 GBW 18.696 GHz、P1dB 97.56 mV peak 和输入参考噪声 1348.7 µV RMS。
- 可选 PVT 调优：`bias=[0.35,0.40] V` 两候选各跨上述三条件运行九项分析。0.40 V 最坏 GBW 更高，但 TT/SS/FF 均违反至少一项 THD、摆幅或功耗约束；0.35 V 三条件全部通过并被选择。该任务无 `parameters.*` action，OA W/L/RD/RS 前后完全相同。
- ADE 自动链已在专用 Maestro cell 真实通过 prepare/setup patch/background run/sweep/corner/result mapping。反相器的 named corner 只改变同一 `top_tt` 下的 VDD；真实 process/temperature corner 目前只在 direct common-source `si`/Spectre Gate 通过，尚未写入或人工打开 Maestro setup。人工打开/调整/重跑和 ADE L 迁移继续按延期记录处理。

跨拓扑的基础有两条。`existing_schematic` 可以不依赖固定模板读取已有 schematic，并用 `instance_parameter_updates` 人工指定实例原始 CDF 参数和值字符串；固定模板还可把它与 W/L/R semantic parameters 组合。写入必须经过 callback、立即定向 OA 回读和独立再次回读。ADE 路径保留 prepare/capture/corner/variable/setup/run 的正交能力和明确真源；当前 ADE live 证据仍不证明真实 process/temperature corner、history 名唯一或 multi-test 通用映射。direct `si`/Spectre 已证明三条件 PVT，但不会把该状态静默包装成 Maestro setup。通用 OA smoke 已枚举 MN0 的 233 个 CDF 字段；专用新 cell 上又真实闭合 `MN0.fingers=2` 和 `RD0.r=22K` 的双重回读。`MN0.m=2` 被当前 PDK callback 恢复为 `1`，因此保留为字段不可持久化边界。这些能力只证明“按名字修改并以 OA 值确认”或“准备、修改声明 setup 范围、运行当前 ADE 状态”，不证明 VDA 理解任意参数的物理作用，也不自动允许该参数参与调优。

Gate 2 已分别覆盖 bias/load、W/RD/RS 和 L/VDD 网格，能对固定 OA 设计执行有限 PVT 验证，也能在任务显式要求时让每个 testbench 候选跨相同 PVT 集合评估；该能力不默认启用，也没有把全部维度塞入一个爆炸式联合搜索。OA 设计变量跨 PVT 的写回路径已有本地测试，尚未 live。实现不要求为源极退化新建模板或复制执行器：`schematic.transform` 在同一 common-source cellview 上应用固定 add/remove delta，`source_resistance_ohm` 随后直接进入原有 `parameters.apply`/`design.tune`。remove 可绑定 add 前 placement 哈希，但保存成功后的任意后置失败仍没有通用 OA snapshot 回滚；任何拓扑都必须先满足偏置和工作区，再比较增益/带宽。

## Gate 3：差分对

至少覆盖输入共模范围、尾电流、支路平衡、差模增益、带宽、CMRR 和受控 transient。nominal 稳定后再加入有限 PVT，不在第一步盲目扩展组合。

## 升级原则

每个 Gate 都要同时通过结构创建与回读、参数写入与回读、非空仿真和指标重算、可行/不可行规格判定，以及中断恢复。凡是声明支持人工 ADE 介入，还必须证明保存后的 setup 可由人工重开、修改和重跑，VDA 能在不覆盖改动的前提下重新捕获同一个 history/网表/结果关系。
