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

状态：Gate 2A 电阻负载 NMOS 共源与源极退化的 DC、复数 AC、bias/load 条件搜索和 W/RD/RS 写入调优已真实通过。专用 cell 还完成同参数只加 RS 的控制变量比较、预算/不可行/transport 恢复、5 点 transient 线性度/真实功耗和 ordinary noise。AC+linearity+noise 固定质量组合已经覆盖 bias/load、W/RD/RS 和 L/VDD；随后同一 OA/`si` 网表又通过 TT/SS/FF 三个显式温度/供电条件，并完成可选的两候选 PVT-aware bias 调优。2026-07-23 在全新 cell 上又完成 source degeneration add→DC/AC→remove；恢复后的 placement、nominal `si` 网表和全部 DC/AC 指标与 add 前相同。同一 cell 随后完成 `MN0.fingers=["1","2"]` 原始 CDF 两点搜索、同源网表证明、最佳写回和首候选 transport checkpoint 恢复。当前状态是 **bounded common-source topology and explicit-instance-parameter tuning verified**；真实 OA 设计变量跨 PVT 写回、复杂 callback 组合、差分对和完整 L5B 仍未闭合。

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
- 原始实例参数调优：`instance_parameter_space` 显式声明已有实例、实际 CDF 字段和有限字符串值；固定 raw 字段、semantic space 与 raw sweep 共享同一候选预算。live 固定 Wfg=1 µm/L=0.03 µm/RD=10 kΩ/bias=0.35 V/VDD=0.9 V/CL=2 fF，搜索 `MN0.fingers=1/2`。OA 与 `si` 的 nf/总宽度分别为 1/1 µm、2/2 µm；GBW 为 39.582/58.375 GHz，最终写回并独立回读 fingers=2。首轮 `si -batch` transport reset 没有生成候选，从恢复后的 index 1 重试。该能力不自动枚举 233 个 MOS 字段，也不把 callback 耦合解释成独立设计变量。
- ADE 自动链已在专用 Maestro cell 真实通过 prepare/setup patch/background run/sweep/corner/result mapping。反相器的 named corner 只改变同一 `top_tt` 下的 VDD；真实 process/temperature corner 目前只在 direct common-source `si`/Spectre Gate 通过，尚未写入或人工打开 Maestro setup。人工打开/调整/重跑和 ADE L 迁移继续按延期记录处理。

跨拓扑的基础有两条。`existing_schematic` 可以不依赖固定模板读取已有 schematic，并用 `instance_parameter_updates` 人工指定实例原始 CDF 参数和值字符串；固定模板还可把它与 W/L/R semantic parameters 组合。写入必须经过 callback、立即定向 OA 回读和独立再次回读。已有真实仿真 adapter 的固定模板可进一步用 `instance_parameter_space` 显式选择少量实际 CDF 字段参与有限调优，但不会自动枚举或猜别名。ADE 路径保留 prepare/capture/corner/variable/setup/run 的正交能力和明确真源；当前 ADE live 证据仍不证明真实 process/temperature corner、history 名唯一或 multi-test 通用映射。direct `si`/Spectre 已证明三条件 PVT，但不会把该状态静默包装成 Maestro setup。通用 OA smoke 已枚举 MN0 的 233 个 CDF 字段；专用新 cell 上又真实闭合 `MN0.fingers=2` 和 `RD0.r=22K` 的双重回读。`MN0.m=2` 被当前 PDK callback 恢复为 `1`，因此保留为字段不可持久化边界。这些能力只证明“按名字修改并以 OA 值确认”“对显式有限字段执行有证据搜索”或“准备、修改声明 setup 范围、运行当前 ADE 状态”，不证明 VDA 理解任意参数的物理作用。

Gate 2 已分别覆盖 bias/load、W/RD/RS 和 L/VDD 网格，能对固定 OA 设计执行有限 PVT 验证，也能在任务显式要求时让每个 testbench 候选跨相同 PVT 集合评估；该能力不默认启用，也没有把全部维度塞入一个爆炸式联合搜索。OA 设计变量跨 PVT 的写回路径已有本地测试，尚未 live。实现不要求为源极退化新建模板或复制执行器：`schematic.transform` 在同一 common-source cellview 上应用固定 add/remove delta，`source_resistance_ohm` 随后直接进入原有 `parameters.apply`/`design.tune`。remove 可绑定 add 前 placement 哈希，但保存成功后的任意后置失败仍没有通用 OA snapshot 回滚；任何拓扑都必须先满足偏置和工作区，再比较增益/带宽。

## Gate 3：差分对

状态：2026-07-23 已在新建且不覆盖的 `vb_pdk_smoke/vda_diffpair_gate3_001/schematic` 上完成固定电阻负载 NMOS 差分对的 **nominal same-source DC/AC/CMRR/linearity live Gate**。OA 创建/回读、设计参数有限搜索与最佳写回、不可行恢复、预算截断、transport checkpoint、只读 testbench 搜索和动态指标均有真实 TSMC N28 证据；这仍不是带真实尾管、noise、PVT、mismatch 或 ADE 人工交接的完整差分放大器。

- OA DUT 固定为 `MN0/MN1/RD0/RD1`；连接为 `MN0(OUTP,INP,TAIL,VSS)`、`MN1(OUTN,INN,TAIL,VSS)`、`RD0(VDD,OUTP)`、`RD1(VDD,OUTN)`，顶层 pins 为 `INP/INN/OUTP/OUTN/TAIL/VDD/VSS`。
- 尾电流源和匹配共模输入源属于外部 testbench，不写进 DUT OA。这样 VDA 自动 wrapper 与后续人工 ADE 都能使用同一 cellview。
- OA semantic 参数为两管共同 `input_width_um/length_um` 和两负载共同 `load_resistance_ohm`；testbench 参数为 `tail_current_ua/common_mode_v/vdd_v`、可选有限 `tail_output_resistance_ohm` 和每端对称 `load_ff`。创建或参数应用会拒绝把 testbench 参数持久化。
- `si` parser 要求两管、两负载的 topology/master 完全匹配，并核对两管单指宽、指数量、multiplicity、总宽、L 和两只 R 的对称性；OA 与网表任一差异停止。
- nominal DC 指标包括两支路 Id、支路失配、尾源/电源/两负载 KCL、VGS/VDS/VDSAT、双管饱和余量、输出偏移/共模、上下摆幅余量、gm/gds、最小 intrinsic gain 和真实 VDD 功耗。三类 KCL 任一超过 1% 是证据错误，不是普通不可行候选。
- `schematic.create`、`schematic.inspect`、`parameters.apply`、`simulation.run`、`design.tune` 和 `design.close_loop` 共用原有 executor/checkpoint，不复制 Bridge。18 点 W/RD/尾电流搜索经历两次 transport 恢复后写回 `W=2 µm/L=30 nm/RD=8 kΩ`；预算任务只提交已评估前缀的最佳点，全不可行任务恢复精确初值。
- 该最终 OA 在无额外负载的平衡差模 AC 下得到低频增益 `2.992 V/V`、`29.18 GHz` 带宽、`87.25 GHz` GBW 和 `86.80 GHz` unity。加入每端 0.5/2 fF 后的四点尾电流×负载只读搜索均完整；网格内最佳为 `50 µA/0.5 fF`，GBW `55.71 GHz`。所有这些点的 OA `si` 网表 SHA 都是 `45282f...cd588`。
- CMRR Gate 在外部理想 DC 尾源并联显式 `1 MΩ` 小信号输出电阻；差模与同相共模分别运行、共享同一 `si` 网表，并要求两次 DC OP 一致。低频 CMRR 为 `58.59 dB`，首次下降 3 dB 的 CMRR 带宽为 `306.38 MHz`。共模增益随频率上升，故不再错误要求“共模自身的低通 -3 dB 带宽”；第一次使用该错误判据的 `partial` 记录被保留。
- 输入共模 0.20–0.90 V 的 12 个采样点表明：0.25 V 因尾节点低于 0 V 失败，0.30 V 通过；0.875 V 的最小余量为 62.29 mV 并通过，0.90 V 虽仍在饱和区但余量 40.44 mV，低于 50 mV 门限。故当前只称采样通过区间 `0.30–0.875 V`，低/高边界分别夹在 `0.25–0.30 V` 与 `0.875–0.90 V`，不外推为连续解析 ICMR。
- 100 MHz、每端 1 fF 的 7 点平衡差分 transient sweep 得到小信号增益 `2.991 V/V`、输入 P1dB `110.9 mV peak`、输出 P1dB `293.5 mV peak`；最大 250 mV 输入时 THD `16.54%`、HD3 `-15.75 dBc`，HD2 接近数值底噪，平均 VDD 功耗约 `45.0 µW`。每点都验证实际 `VINP-INN` 基波与声明幅度一致。

Gate 3 的理想尾源路径继续保留为独立、低成本的局部能力；它不会被 Gate 4 删除或静默替换。

## Gate 4：差分对真实尾管

状态：2026-07-23 已在全新且不覆盖的 `vb_pdk_smoke/vda_diffpair_tail_gate4_001/schematic` 上完成 **real-tail same-source multi-analysis live Gate**。这证明固定 `MNTAIL/BIAS` 小变更及其 DC/AC/CMRR/ICMR/transient/noise 可以无缝复用 Gate 3 流程；仍不等于任意拓扑综合、完整差分放大器或 L5B。

- `schematic.create` 先建立原四器件 core；`schematic.transform/add_tail_device` 只新增 `MNTAIL(TAIL,BIAS,VSS,VSS)` 与 `BIAS` pin。前后独立 inspect 证明 core 实例、连接、pins 和 placement 保持，`replace_existing=false`。
- 尾管 W/L 是 OA semantic 参数并进入定向回读与 `si` 网表；BIAS 电压是 testbench 条件。真实尾管模式拒绝理想尾源的 `tail_current_ua/tail_output_resistance_ohm`，因此不会把两种状态混成同一真源。
- 5 点 `tail_bias_v` 只读搜索选择 `0.40 V`；随后 `MNTAIL.W=0.8/1.0/1.2 µm` 三点全部完成 OA 暂存→回读→自动网表→DC，按最小功耗写回并独立回读 `0.8 µm`。名义点实际尾电流 `49.368 µA`、DC 功耗 `44.432 µW`、尾管饱和余量 `131.6 mV`。
- 每端 `1 fF` 的成对 AC 使用同一 `si` 网表，得到差模低频增益 `2.928 V/V`、−3 dB 带宽 `13.806 GHz`、GBW `40.418 GHz`、unity `38.453 GHz`；低频 CMRR `21.135 dB`、CMRR 带宽 `19.069 GHz`。这里的有限 CMRR 来自 OA MNTAIL 的真实小信号行为，不是 wrapper 的人为尾源电阻。
- 10 点 VCM 扫描全部分析完整。0.35 V 因 MNTAIL 非饱和而规格失败，0.40–0.80 V 离散点通过；0.65 V 的摆幅余量最大，为 `222.2 mV`。0.80 V 仍通过，因此只报告已采样通过范围和低边界夹逼，不声称解析上边界。
- 100 MHz 六点 transient 得到小信号增益 `2.927 V/V`、输入 P1dB `116.3 mV peak`；0.2 V peak 点 THD `10.17%`、最大平均功耗 `45.63 µW`。1 kHz–10 GHz 的 141 点 ordinary noise PSF 得到输入参考积分噪声 `921.8 µV RMS`、差分输出积分噪声 `2.584 mV RMS`。
- 首轮 ICMR 曾把“尾管非饱和”误归为分析不完整；修正后工作区只由 metric/constraint 判可行性，完整性只描述数据与解析。首轮 noise 的 1 TΩ 共模偏置真实漂移到 0.4395 V；改为唯一差分 `iprobe` 加 `+0.5/-0.5` VCVS 后，DC 共模和 PSF 均通过。两次失败记录都保留。

Gate 4 的真实尾管能力继续保留；Gate 5 不替换它，而是在其上增加可逆的小变更。

## Gate 5：差分对对称源极退化

状态：2026-07-23 已在全新且不覆盖的 `vb_pdk_smoke/vda_diffpair_deg_gate5_001/schematic` 上完成 **reversible symmetric source-degeneration full-analysis live Gate**。它证明两支对称 RS 可以作为正式 agent 能力加入、调参、写回和移除，且 Gate 4 的 DC/AC/CMRR/ICMR/transient/noise 流程无需另建仿真器或一次性脚本。

- add 只执行 `MN0.S/MN1.S: TAIL -> NSP/NSN` 并加入 `RS0(NSP,TAIL)`、`RS1(NSN,TAIL)`；原 `MN0/MN1/MNTAIL/RD0/RD1` 与所有 pins 保持。OA readback 和 `si` 都要求两只 R 数值相同、节点正确。
- 500 Ω DC 的两支路电流均为 `24.2402 µA`；由电阻压降重算均为 `24.2400 µA`，最大差异 `0.00117%`。三管工作区、负载/尾管/两只源电阻 KCL、摆幅和功耗约束全部通过。
- 500 Ω AC 得到差模增益 `2.404 V/V`、带宽 `13.077 GHz`、GBW `31.436 GHz`、unity `28.970 GHz`、低频 CMRR `19.466 dB`。10 点 ICMR 全部 analysis complete，离散通过点为 `0.40–0.80 V`。
- 100 MHz 六点 transient 得到输入 P1dB `150.30 mV peak`、200 mV 点 THD `7.108%`；相对同参数无退化基线，P1dB 提升 `29.19%`、THD 降低 `30.11%`，同时 GBW 降低 `22.22%`。输入参考积分噪声为 `1086.05 µV RMS`，高于基线 `17.82%`。
- `source_resistance_ohm=[250,500]` 的两点搜索都完成真实 OA 暂存→回读→自动网表→transient；按 P1dB 选择 500 Ω并独立回读 `RS0=RS1=500 Ω`。这证明 RS 是可重复使用的有限调参维度，而不只是本次固定值 smoke。
- remove 绑定 add 前 placement SHA，只删除 VDA 创建的两只 R 和四条 terminal stub/label，恢复两管源极到 TAIL。完整 add/remove/restore 序列重复两次；两次恢复 placement 相同，两份恢复网表 SHA 相同，semantic 参数和全部所选 DC metrics 也逐项相同。最终 cell 保持 Gate 4 真实尾管状态，不残留 RS0/RS1/NSP/NSN。

## Gate 6：PMOS 电流镜有源负载差分对

状态：2026-07-23 已在全新且不覆盖的 `vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic` 上完成 **current-mirror-load same-source bounded closure live Gate**。固定拓扑从 Gate 4 未退化真实尾管版本出发，只把 `RD0/RD1` 替换为 `MP0(OUTP,OUTP,VDD,VDD)` 与 `MP1(OUTN,OUTP,VDD,VDD)`；反向 action 可按显式电阻值恢复 RD0/RD1，并可绑定恢复 placement SHA。该路径不与 Gate 5 的 RS 同时启用，避免一次引入两种拓扑变量。

- `pmos_load_width_um/pmos_load_length_um` 已是匹配 MP0/MP1 的 OA semantic 参数，可用于直接 apply 或有限搜索；显式原始实例参数面仍保留。
- OA 与 `si` 双边都会拒绝 PM 单边缺失、W/L 不同、错误 master/node、RD/PM 混合，以及 active load 与 source degeneration 混合。
- DC 已定义 MP0/MP1 IDS/VGS/VDS/VDSAT/GM/GDS、两支路负载 KCL、镜像误差、PMOS 饱和余量和上下管联合输出摆幅；KCL 超过 1% 是证据失败。
- 动态输出为差分输入、OUTN 单端输出：AC/CMRR 用 `OUTN/(INP-INN)`，transient 从 OUTN 提取 THD/P1dB，noise 用 `noise (OUTN 0)`，`load_ff` 只加在 OUTN。指标证据显式保存 `output_mode=single_ended_outn`。
- live 先暴露 `PM0/PM1` 会被 Spectre 解释为 port primitive；失败日志被保留，RD 基线精确恢复，固定模板改用 `MP0/MP1` 后再继续，没有把执行错误包装成电路不可行。
- 同一 OA/自动网表完成 DC、AC/CMRR、10 点 ICMR、transient 和 noise。最终 `Wn=Wp(load)=1.5 µm`、`L=30 nm` 得到增益 `3.7421 V/V`、带宽 `2.9756 GHz`、GBW `11.1351 GHz`、CMRR `34.8451 dB`、50 mVpeak THD `1.5089%` 和输入参考积分噪声 `699.24 µVrms`。
- 6 点 bias/load 与 6 点 Wn/Wp 搜索、预算耗尽、两点全不可行、两次真实 transport timeout checkpoint/resume 均按既有状态机执行；最佳几何写回并独立回读。恢复 RD 后 placement SHA 精确等于基线，再重建最佳 active load 并以新 DC 复核最终状态。
- PSRR+/PSRR− 已加入为独立 `analysis: psrr`：每个候选复用一份 OA/`si` 网表，依次运行差模、VDD 注入和 VSS 注入三次 AC，核对 DC 与频率网格后计算 supply gain、低频 PSRR、扫频最差值、声明频带最差值和首次下降 3 dB 频点；三份根 `ac.ac` 各自记录大小与 SHA-256。只改 `tail_bias_v/load_ff` 时不会写 OA。2026-07-24 nominal 单点低频 PSRR+=`11.5156 dB`、PSRR−=`13.4535 dB`；后续四点只读搜索的 `1 kHz–100 MHz` 最差值为 `11.4275–11.5125 dB`，全部通过其他物理护栏却未通过临时 `20 dB` 门。三种沟道 L 的 `0.03/0.06 µm` 八点 OA 搜索又把最优观测值提高到 `19.7438 dB`，但仍无可行点，因此恢复初始 L；一次写后回读 transport reset 经 checkpoint 从候选 3 续跑，最终 8/8 完整且独立 OA inspect 与基线一致。

下一步不再细扫 `tail_bias_v/load_ff`，也不再把尾管 L 当主 PSRR 旋钮：CL 只改变高频带宽，BIAS 的带内改善约 `0.085 dB` 且增加功耗；尾管 L 从 30 nm 增到 60 nm 的八点平均 PSRR 反而降低约 `0.19 dB`，平均带宽从 `2.033 GHz` 降到 `0.581 GHz`。下一道 Gate 固定尾管 30 nm，仅在显式小网格内细化输入对/PMOS L；若仍不能在增益、带宽、功耗、摆幅和工作区护栏下过门，再判断是否改变 BIAS 对 VSS 的参考或增加供电隔离结构。真实写入继续使用逐候选回读、checkpoint、无可行恢复和最终独立 OA 回读；任何过门点还必须复跑 CMRR、linearity 和 noise。产品 PSRR 数值与频带仍由目标应用定义，临时 `20 dB` 不得外推。之后再处理 slew/settling、输出驱动/摆幅边界，或按任务显式启用差分对 PVT；PVT 不默认附加。若继续拓扑能力，则把 `RS0/RS1 + MP0/MP1` 定义成独立组合 Gate。当前 P1dB 未在 5–50 mVpeak 范围内被包围；差分对 ADE/Maestro setup、人工打开/调整/重跑、mismatch/Monte Carlo 和多 test/multi-analysis 仍未闭合。

为避免上述几何小网格退化成随意试值，Gate 6 已增加本地 theory-first 尺寸入口。它对声明的输入 NMOS/PMOS 负载/尾管 gm/Id 表域逐组合解析求解最小支路电流与 W，而不是把人工 W 列表重新排序；增益、BW、GBW、功耗、面积代理和三管 KVL 余量都保留公式、裕量与证据来源。只有完整穷尽声明表域时才报告 `best_in_declared_discrete_domain`，从不报告连续或全局最优。六点真实 Wn×Wp 同源数据现已把固定 `top_tt`/30 nm L/单偏置下的一阶增益与带宽模型校准到最大留一误差小于 `0.46%`，并由一次新鲜只读同点 Spectre 复跑确认执行重复性；这只证明 topology-local 表内模型。独立 TSMC N28 MOS gm/Id 表、L/VDS/BIAS/VCM/CL/PVT 范围和未参与拟合的电路点仍未闭合，因此 synthetic 推荐与该局部校准都不得直接写回 OA。

该固定拓扑尺寸器之下现已增加通用 MOS/R/C 小信号矩阵核心。它不识别 Gate 2/3/4/5/6 名称，同一套器件点和节点 stamping 已覆盖共源、源退化共源、NMOS 差分对与 PMOS 共源本地解析测试。后续新电路不应复制一套 AC 方程；应先由 OA/`si` 图绑定通用网络，再由少量 topology-aware 层定义设计意图、约束和允许的结构变换。对于尚未进入正式 JSON 契约的特殊受控源、激励或辅助方程，电路脚本可复用公开的 `ComplexNodalSystem` 系数/RHS 接口或 raw complex MNA solver；重复出现并通过 Spectre 对照后才提升为正式 element/metric。Gate 7A 已补上 nominal TSMC N28 独立器件表，Gate 7B/7C 又分别完成 exact-signature nominal 与源极退化共源 OA/`si` 图、真实 DC 偏置和 held-out AC 对照；多 MOS 差分对尚未取得同等级 live 证据，所以这些通过不能外推到所有电路 Gate。

## Gate 7A：独立 TSMC N28 MOS 表征

状态：2026-07-24 已完成 **standalone TSMC N28 MOS characterization live Gate at nominal top_tt**。这是跨电路的器件数据 Gate，不新建 OA cellview，也不替换 Gate 1–6 的同源仿真。

- 正式 operation 为 `device.characterize`，circuit 为 `mos_device`；任务不接受 OA target、remote write、ADE 或 design-search 字段，只允许显式远端计算。
- Bridge 默认连接负责 SSH、Cadence 环境、Spectre 和下载；VDA PDK profile 独立绑定 `nch_lvt_mac/pch_lvt_mac`、model include 和 `top_tt`。没有修改第三方 Bridge。
- 一个 DC deck 完成 W=1 µm、L=30/60 nm、四个 VGS、五个 VDS、三个 VSB、双 polarity 共 240 个训练点，另跑 4 个真实 VGS 留出点。
- 每点保存 signed Id/VGS/VDS/VBS/VDSAT/gm/gds/gmb 和 Cgs/Cgd/Cgb/Cdb/Csb。原始量为 `eda_result`；width-normalized `Id/W`、gm/Id、gds/Id、gmb/Id、电容密度和插值审计为 `software_inference`。
- 最终 Spectre `21.1.0.612.isr15` 记录包含 244 个 OP、六项文件 manifest、deck 和聚合 SHA-256；OA access/write 均为 false。四个留出点全部通过 25% 门，最坏为 PMOS Id/W 的 13.70%。
- 45 nm 留出几何被当前 PDK 拒绝，96 点粗网格又有 3/4 留出失败；两类失败都保留，最终使用已验证合法 L、加密 VDS/VSB 采样并为近零电容记录 mixed normalization floor，没有抬高 25% 门。
- artifact 已直接通过通用 small-signal schema，但简单节点图推导的 gain/BW 仍是理论量，不是完整 OA 电路的 Spectre 证据。

## Gate 7B：exact-geometry 共源小信号验证

状态：2026-07-24 已完成 **nominal common-source same-geometry small-signal validation verified**。目标是既有 `vb_pdk_smoke/vda_cs_gate2a_001/schematic`，只读 OA、自动 `si`，并运行同一网表的 DC/AC；没有 OA 写入。

- `vda small-signal-validate` 只接受 real Bridge 的器件表和电路 run record，核对 target、topology、PVT、OA/`si` semantic 参数、DC node/device/KCL、netlist/wrapper/raw AC SHA-256 及 Spectre 版本。
- binder 从 `si` 的 MN0/RD0 和 testbench CL 构图，以 EDA DC OP 定位 VGS/VDS/VSB；只在 exact L 平面内做有权重记录的 rectilinear interpolation，拒绝所有外推。
- W=1 µm 表被 exact-width 门拒绝；W=0.5 µm 但没有 OA 扩散/LDE 参数的表按固定门限得到 partial。没有用继续密扫电压或抬高门限掩盖差异。
- `device.characterize` 现可显式携带逐 polarity 的安全 numeric Spectre 实例参数签名，`w/l/nf/m/multi` 仍由独立字段控制。最终表复现 `si` 的 31 项扩散/LDE 参数；60 个训练点和 1 个留出点通过，留出最坏误差 7.42%。
- held-out circuit 的 Id/gm/gds/VDSAT 相对误差为 7.57%/9.78%/11.04%/0.78%；低频增益绝对误差 0.464 dB，BW/GBW 相对误差 10.38%/15.05%，低频与带宽处相位均通过 5° 门。
- raw OP/AC 与网表文件属于 `eda_result`，OA 结构属于 `bridge_readback`；归一化、插值、矩阵预测、签名比较和误差门属于 `software_inference`。该点不构成任意偏置、几何、拓扑或 PVT 的保证。

## Gate 7C：源极退化共源迁移

状态：2026-07-24 已完成 **source-degenerated common-source same-source
small-signal migration verified at nominal top_tt**。目标为既有
`vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic`，全过程只读 OA；没有新建或修改
cellview。

- 第一次 run 用旧的 W=0.5 µm 声明访问当前 W=1 µm OA，参数门在 Spectre 前拒绝，证明旧 run record 不会代替新鲜回读。
- `vda characterization-task-from-run` 从刷新后的 real `si` MN0 自动提取 model、W/L 和 31 项参数，绑定 source run/netlist/signature hash，再与显式 N28 bias-grid template 生成普通 `device.characterize` 任务；当前只自动支持 nf=1/m=1。
- 新 standalone 表在 W=1 µm、L=30 nm、VGS=0.3–0.6 V、VDS=0.15–0.75 V、VSB=0–0.15 V 上完成 60+1 点，留出最坏误差 15.61% < 25%。
- binder 从实例图而非拓扑公式识别 `MN0.S=NSRC` 和 `RS0(NSRC,VSS)=2 kΩ`，要求 EDA DC source-current consistency、node/device 和 KCL 均 matched。
- 实际 VGS/VDS/VSB 为 0.34391/0.28300/0.05609 V；Id/gm/gds/VDSAT 误差 17.59%/17.69%/21.36%/2.82%，gain 误差 0.374 dB，BW/GBW 误差 13.52%/17.16%，相位误差也通过。Gate 7B 的固定门限没有改变。
- OA 为 `bridge_readback`；`si`/OP/raw AC 与表征 raw OP 为 `eda_result`；任务推导、归一化、插值、矩阵预测和误差门为 `software_inference`。

下一道理论 Gate 扩到含多个 MOS、不同 polarity/角色的差分对。每个实例必须匹配自己的
model/W/L/signature 与偏置域；PVT 是可选扩展，不作为迁移前置条件。任何理论 seed 仍需
Spectre 验证后才可能进入受控 OA 写回。

## Gate 7D：三表差分对小信号迁移

状态：2026-07-25 已完成 **differential-pair exact three-plane same-source
small-signal validation verified at nominal top_tt**。目标仍是既有
`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`；只读 OA、自动 `si` 并运行
Spectre，没有新建、修改或覆盖 cellview。

- 输入 NMOS、PMOS 电流镜负载和 NMOS 尾管分别使用一张 standalone 表；每张表都匹配目标实例的 exact W/L、31 项 `si` 参数签名、来源实例、同一个电路 run SHA-256 和 netlist SHA-256。
- 器件表现保存完整 signed 4×4 `dQi/dVj` 本征电荷导数矩阵，方向性不对称项不取绝对值；drain/source 结耗尽电容 `cjd/cjs` 分开保存和 stamp。
- 只加入 `dQi/dVj` 时，BW/GBW 仍约错 43%；从同一电路 OP 提取实际 `cxx` 后，三表最坏只差 0.74%，排除了 bias 插值/表绑定主因。补齐 `cjd/cjs` 后才闭合，不是靠放宽 policy。
- 最终预测/实际 gain 为 11.5254/11.4623 dB，BW 为 3.0987/2.9756 GHz，GBW 为 11.6800/11.1351 GHz；误差为 0.063 dB、3.97% 和 4.67%，相位与五管 DC/电容门也全部通过。
- 原始 `si`、OP、AC 和器件表 Spectre 结果是 `eda_result`；OA 结构是 `bridge_readback`；归一化、插值、矩阵预测和误差判定是 `software_inference`；固定 policy 是 `user_input`。

这条 Gate 只证明 nominal `top_tt`、`nf=m=1`、当前五管电流镜负载和单端 `OUTN`
观察点。PVT 是可选扩展；多指/多重器件、mismatch、noise 和其他输出表达式仍需单独
证据。下一自动 Gate 使用该器件表/矩阵能力产生 theory seed，再以有限 Spectre 搜索复核，
不会把理论预测直接写回 OA。

## Gate 8：理论候选到同源 EDA 选优

状态：2026-07-25 已完成 **real-PDK theory shortlist to bounded same-source EDA
selection verified at nominal top_tt**。目标是既有
`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`；没有新建或覆盖 cellview，
但按本 Gate 明确授权逐点修改三组器件尺寸并在最终写回 EDA 选中的可行点。

- Gate 7D 的 passed validation、三张 characterization run、实际 DC 偏置和 PDK/PVT
  全部由 SHA-256 绑定后，才派生 64 组合 theory request；12 个一阶可行点中选出 6 个
  原子 tuple，而不是手列六个 W 或展开 Wn×Wp×Wtail。
- 第一次连续宽度 `1.316019 µm` 被 OA 量化成 `1.315 µm` 时在 Spectre 前失败并恢复
  基线；正式 task 因而在 plan 前显式按 `0.005 µm` half-up 网格量化，且把量化规则
  纳入 provenance/token。
- 六点均完成 OA 写入/回读、自动 `si`、DC、差模 AC、共模 AC；4/6 满足饱和、失配、
  摆幅、功耗、增益、BW、GBW、peaking 和 CMRR 约束。功耗 objective 选择理论第二名
  `Wn/Wp/Wtail=1.315/1.180/0.605 µm`：功耗 `10.981 µW`、增益 `3.7277 V/V`、
  BW `2.6895 GHz`、GBW `10.0258 GHz`、CMRR `34.815 dB`。
- 候选 6 前发生 `WinError 10054`；VDA 恢复基线、保存 5/6 checkpoint、独立回读后只
  执行未完成点。最终最佳写回和独立 OA readback 一致，transport 没有被算作不可行。
- follow-up 只读完成 PSRR、noise、5/20/50 mV transient 和 0.35–0.80 V ICMR。
  PSRR+ 仅 `11.4828 dB`，P1dB 未包围；ICMR 只能报告声明网格上 0.35–0.75 V 可行。
  PVT 按授权未执行。
- theory validation 将 shortlist utility 与 prediction accuracy 分开：真实可行比例
  `4/6` 使候选生成 Gate 通过，但理论首选与 EDA 选择不同，24 个功耗/增益/BW/GBW
  对照有 8 个超过 25%，所以精度 Gate 保留为 `partial`。

下一理论 Gate 先对候选做第一遍真实 DC OP 重线性化，再用未参与校正的 held-out tuple
验证；不能在这六点上拟合后回测同一数据。Spectre 指标继续拥有最终约束和写回决定权。
完整记录见
[`2026-07-25-differential-pair-theory-seeded-gate8-live.md`](validation/2026-07-25-differential-pair-theory-seeded-gate8-live.md)。

## 升级原则

每个 Gate 都要同时通过结构创建与回读、参数写入与回读、非空仿真和指标重算、可行/不可行规格判定，以及中断恢复。凡是声明支持人工 ADE 介入，还必须证明保存后的 setup 可由人工重开、修改和重跑，VDA 能在不覆盖改动的前提下重新捕获同一个 history/网表/结果关系。
