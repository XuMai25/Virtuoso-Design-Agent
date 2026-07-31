# Virtuoso Design Agent

Virtuoso Design Agent 是 `virtuoso-bridge-lite` 之上的受控设计编排层。它把“建原理图、读回、应用参数、跑仿真、判定规格、有限调优”组织成可单独执行、可组合、可审计的任务，而不是再造一套 Bridge。

当前版本从 **L5A** 起步：在已知 PDK、固定电路模板、显式规格和有限搜索空间内完成闭环。TSMC28 反相器 Gate 1，以及电阻负载 NMOS 共源/源极退化的 DC、复数 AC、W/L/RD/RS、bias/load、相干 transient 线性度、真实 VDD 功耗、ordinary noise 和固定三分析 `quality` 均有真实 OA/`si`/Spectre 证据。2026-07-22 又完成共源 L/VDD 质量搜索、最佳 OA 写回和固定设计 TT/SS/FF 三条件验证；PVT 作为显式可选项接入 testbench 调优，但不会默认附加。2026-07-23 依次闭合理想尾源差分对 Gate 3、真实 `MNTAIL/BIAS` Gate 4、可逆对称 `RS0/RS1` Gate 5，以及 PMOS 电流镜有源负载 Gate 6。Gate 6 在全新 `vda_diffpair_active_gate6_001` 上完成 RD→`MP0/MP1` exact-delta、OA→`si` 参数一致性、DC 镜像/KCL/工作区、差模与共模 AC/CMRR、10 点 ICMR、相干 transient、ordinary noise、bias/load 与 Wn/Wp 有限搜索、预算耗尽、全不可行恢复、checkpoint/resume、最佳 OA 写回、精确恢复和最终有源负载重建。最终同源网表得到增益 `3.7421 V/V`、带宽 `2.9756 GHz`、GBW `11.1351 GHz`、CMRR `34.8451 dB`；这些只是 nominal `top_tt` 和声明网格内的结果。2026-07-24 先完成 PSRR+/PSRR− 三次同网表 AC 单点，随后完成 `tail_bias_v × load_ff` 四点只读搜索、`1 kHz–100 MHz` 带限指标、12 份根 AC 文件哈希和两次 transport 中断恢复。四点均通过饱和/摆幅/增益/带宽/功耗护栏，但带内最差 PSRR 只有 `11.4275–11.5125 dB`，未达到明确标为临时证伪门的 `20 dB`；因此 bias/load-only 改善已被当前网格证伪。随后获授权的 `input/PMOS-load/tail L = 0.03/0.06 µm` 八点 OA 搜索完成 8 份不同同源网表和 24 次 AC；输入对与 PMOS L 同为 `0.06 µm` 时达到 `19.712–19.744 dB`，但仍无点通过临时门。一次候选写后回读 transport reset 经基线恢复和 checkpoint 续跑闭合，零可行点时最终 OA 精确恢复到三组 `L=0.03 µm`，没有提交“最接近”点。可选差分对 PVT、mismatch/Monte Carlo、PSRR 最终规格与完整多分析复核、slew/settling、ADE multi-test/multi-analysis、人工打开/修改/重跑和旧 ADE L 非覆盖迁移仍未闭合，因此不能称为完整 L5B 设计质量闭环。

反相器 Maestro 路径已 add-only 保存 delay、rise/fall skew 和每周期总供电能量表达式，并从 exact-history RDB 把 CL 三点、VDD×CL 六点和 test-scope CL×environmental-corner 六格结果映射到 VDA constraints/objective；最小能量点为 `0.8 V/1 fF`，delay `3.254 ps`、skew `1.425 ps`、周期供电能量 `1.309 fJ`。该 ADE corner 仍共享 nominal `top_tt`，不能冒充 process corner；真实 TT/SS/FF 证据来自上述共源 direct `si`/Spectre PVT Gate，而不是 Maestro setup。两条仿真状态继续显式分开。

PDK 默认面向晶圆厂 CMOS 设计。当前缺省 profile 为 `nics4304_tsmc28`，对应 TSMC N28/`tsmcN28` 的 LVT master；显式 `nics4304_tsmc28_svt` profile 继承同一工艺/model/corner，只把器件 master 改为 `nch_mac/pch_mac`。NMOS `nch_lvt_mac -> nch_mac -> nch_lvt_mac` 已在一个新共源 cell 上完成 OA/CDF、`si` model、Spectre DC/AC 和 exact inverse round-trip；PMOS 与其他 flavor 仍需各自 Gate。后续 TSMC、SMIC 等工艺使用独立 profile 和各自验证证据。TSV、hybrid-bonding 等封装/3D PDK 只有任务显式选择时才使用，不会成为自动 fallback，也不会改变普通晶体管级模板的默认假设。详见[决策 0002](docs/decisions/0002-foundry-cmos-pdk-default.md)。

反相器的新建缺省尺寸现为 `Wn=0.6 µm、Wp/Wn=1.20`。2026-07-25 在 TSMC N28 nominal `top_tt`、0.9 V、2 fF 和固定 5 ps input edge 下完整实测 `1.20/1.25/1.30/1.35`；四点都可行，以 rise/fall skew 为 objective 时 1.20 得到 `0.0394 ps`，优于 1.25 的 `0.2119 ps` 和经验值 1.30 的 `0.3639 ps`，且能量和面积代理更低。1.25 的网表 hash 与指标和前一天粗扫完全相同。1.20 只作为简单、可覆盖的 nominal 初始化值；多个负载、input slew 和可选 PVT 尚未证明，显式尺寸和已有 OA 尺寸始终优先，也不会限制 `parameters.apply`、原始 CDF 或 Bridge 原有能力。见[粗网格记录](docs/validation/2026-07-24-inverter-drive-ratio-calibration-live.md)和[细化 live Gate](docs/validation/2026-07-25-inverter-ratio-refinement-live.md)。

2026-07-24 又完成 Gate 7A 独立器件表征：正式 `device.characterize` 在没有 OA target、没有 OA 写权限的情况下，复用 Bridge 默认连接和 TSMC N28 `top_tt` 模型，以一个 Spectre DC deck 生成 240 点 NMOS/PMOS 训练表和 4 个真实留出点。原始 signed OP、deck/PSF/log manifest 与 SHA-256 属于 `eda_result`，width-normalized `Id/W、gm/Id、gds/Id、gmb/Id`、五类电容密度和插值审计属于 `software_inference`。最终最坏留出归一化误差为 `13.70% < 25%`；没有打开或写入 OA。

同日 Gate 7B 已把一个既有共源 cell 的只读 OA→`si` 图、真实 DC 偏置和原始 AC 频率网格自动绑定到通用 small-signal core。前两张只按 W/L 建立的表分别因宽度和扩散/LDE 参数不一致而保留为 partial；没有抬高固定门限。新增的表征契约显式保存除 `w/l/nf/m/multi` 外的 31 项 `si` 模型参数签名，并用完全相同的 W=0.5 µm、L=30 nm 参数面重跑 60+1 点。最终 held-out 共源的 Id/gm/gds/VDSAT 误差为 `7.57%/9.78%/11.04%/0.78%`，增益误差 `0.464 dB`，BW/GBW 误差 `10.38%/15.05%`，均通过运行前固定门限；全过程不写 OA。当前状态是 **nominal common-source same-geometry small-signal validation verified**，不是任意拓扑、任意几何或 PVT 误差保证。

Gate 7C 随后把同一个 binder/矩阵核心迁移到既有 `vda_cs_ac_tradeoff_001` 源极退化共源级，没有新增拓扑公式。第一次执行因旧任务声明 W=0.5 µm、当前 OA 已为 W=1 µm 而在 Spectre 前拒绝；随后 `vda characterization-task-from-run` 从刷新后的 real `si` 实例自动生成带 run/netlist/signature 哈希的 1 µm、31 参数 standalone 表征任务，避免人工复制 LDE 参数。60+1 点表的留出最坏误差为 `15.61% < 25%`；验证器从图中绑定 `MN0.S=NSRC` 与 `RS0(NSRC,VSS)=2 kΩ`，并要求 EDA DC 源电阻一致性。最终 Id/gm/gds/VDSAT 误差为 `17.59%/17.69%/21.36%/2.82%`，增益误差 `0.374 dB`，BW/GBW 误差 `13.52%/17.16%`，均通过未改变的 Gate 7B 门限。当前状态升级为 **source-degenerated common-source same-source small-signal migration verified at nominal top_tt**。

Gate 7D 已把同一核心迁移到既有 `vda_diffpair_active_gate6_001` 电流镜负载差分对。三张真实 TSMC N28 表分别绑定输入 NMOS、PMOS 负载和尾 NMOS 的 exact W/L、31 项 `si` 参数签名及同一份 held-out 电路 run/netlist 哈希。调试没有放宽 25%/0.5 dB/5° 门限：只加入完整 signed 4×4 `dQi/dVj` 电荷导数时，BW/GBW 仍约错 43%；独立回读证明三张表与电路内实际 `cxx` 最坏只差 0.74%，随后确认遗漏的是不包含在本征 `cxx` 中的结耗尽电容 `cjd/cjs`。把两者分开表征和 stamp 后，增益误差为 `0.063 dB`、BW/GBW 误差为 `3.97%/4.67%`，相位和五个器件的 DC/电容门全部通过。全过程只读 OA、没有修改 Bridge。当前状态是 **differential-pair exact three-plane same-source small-signal validation verified at nominal top_tt**；`nf/m != 1`、PVT、mismatch 和任意新器件签名仍需各自证据。

Gate 8 已把 Gate 7D 的真实器件表和 validation hash 编译成六个不可拆分的 theory seed，并在既有 active-load OA 上完成逐点写入/回读、自动 `si`、Spectre DC/差模 AC/共模 AC、transport checkpoint/resume 和最佳写回。理论连续宽度在 plan 前按声明的 5 nm 网格量化；4/6 候选真实可行，功耗 objective 选择理论第二名 `Wn/Wp/Wtail=1.315/1.180/0.605 µm`，得到 `10.981 µW`、增益 `3.7277 V/V`、BW `2.6895 GHz`、GBW `10.0258 GHz`、CMRR `34.815 dB`。只读 follow-up 覆盖 PSRR/noise/transient/10 点 ICMR，但 PSRR+ 只有 `11.4828 dB`、P1dB 未包围。候选生成门通过，逐点预测精度门因 24 项中 8 项超过 25% 保留为 partial；所以当前状态是 **real-PDK theory shortlist to bounded same-source EDA selection verified**，不是已校准预测器或连续/全局最优。详见[Gate 8 live 记录](docs/validation/2026-07-25-differential-pair-theory-seeded-gate8-live.md)。

下一层现已加入通用原子 `candidate_set` 和真实工作点局部重线性化。一个候选可同时携带 OA semantic、testbench 与 raw CDF 字段，不再被拆成笛卡尔积；预测来源、hash 和候选 ID 单独保存，最终选优仍只服从本次仿真指标。`vda op-relinearize` 强制把真实 run record 分为 training/heldout，训练与留出误差逐指标都过门才生成 task。既有共源记录的 6-train/2-heldout、10 指标最坏留出误差为 `11.450%`；差分对记录的 4-train/2-heldout、15 指标最坏留出误差为 `8.262%`。2026-07-26 共源 6 点已进一步完成 OA→`si`→AC/transient/noise、transport resume 和最佳写回：6/6 全规格可行，局部模型与真实 EDA 都选择 `W/RD/RS=1.1 µm/19 kΩ/0.75 kΩ`，GBW 为 `34.3965 GHz`，比 anchor 高 `13.561%`。新增 `vda op-relinearization-validate` 证明 GBW 最大预测误差 `4.505%`，但候选 5 的输出摆幅误差 `22.479% > 20%`，所以状态是 **common-source atomic local-response shortlist to same-source EDA selection verified; live pointwise model accuracy partial**，不是完全校准预测器或连续/全局最优。详见[本地生成记录](docs/validation/2026-07-25-atomic-candidate-op-relinearization-local.md)和[共源 live Gate](docs/validation/2026-07-26-common-source-op-relinearization-live.md)。

同日的下一轮刷新没有直接把 6 个新点重新回归后宣称闭合。新增 Gate 要求每个声明的可调参数至少被一个 heldout 点实际扰动；因此 W/RD/RS 三维刷新因留出集没有 RS 变化而明确拒绝。把 RS 固定在真实最佳值 `750 Ω` 后，W/RD 二维模型以该最佳点重定 anchor，两个 heldout 点覆盖两维，10 个指标最坏历史留出误差为 `0.568%`。随后 `W=1.05/1.10 µm、RD=18.5/19/19.5 kΩ` 六点已全部完成 OA→`si`→AC/transient/noise：6/6 可行，预测与 EDA 都选 `1.1 µm/18.5 kΩ/750 Ω`，GBW=`34.6553 GHz`；60/60 逐点预测比较通过，最坏新点误差仅 `0.354%`。两次 DNS/SCP/`WinError 10054` 均按 `system_event` 恢复并从原子 checkpoint 续跑。当前状态是 **held-out-covered common-source W/RD local response to same-source EDA selection verified at nominal top_tt**；RS 与 PVT 仍不能外推。详见[二维本地刷新](docs/validation/2026-07-26-common-source-op-refresh-2d-local.md)和[二维 live Gate](docs/validation/2026-07-26-common-source-op-refresh-2d-live.md)。

差分对的 6 个 `Wn/Wp/Wtail` 局部候选也已在既有 `vda_diffpair_active_gate6_001` 完成 OA→`si`→双支路 DC/差模 AC/共模 AC/CMRR。三次 DNS/SCP/`WinError 10054` 都在恢复并独立回读 anchor 后从未完成候选续跑；最终 6/6 可行，预测与 EDA 同选最小功耗点 `1.215/1.080/0.555 µm`，功耗 `10.2971 µW`、gain `3.71568 V/V`、BW `2.68269 GHz`、GBW `9.96800 GHz`、CMRR `34.8249 dB`，并独立回读写回值。exact validator 的 90/90 比较全过，最坏新点误差 `2.5693%`。旧 result 未序列化误差 floor 时现在必须传入 canonical-hash-bound 原 policy，禁止用 schema 默认值猜测。当前状态是 **differential-pair held-out local-response shortlist to same-source EDA selection verified at nominal top_tt**；新最佳点的 PSRR/noise/linearity/ICMR、PVT 与 mismatch 仍需独立复核。详见[差分对 live Gate](docs/validation/2026-07-26-differential-pair-op-relinearization-live.md)。

随后在全新 `vda_diffpair_active_deg_generic_001` 上真实验证“PMOS 电流镜负载 + 对称源极退化”的通用 delta 组合。forward 把五实例 active-load 结构增量变成七实例拓扑，`RS0=RS1=500 Ω` 经 OA 回读并进入同一 `si` 网表；PMOS/RS/尾管 KCL、DC、差模 AC/CMRR、noise、transient/THD、10 点 ICMR 和 nominal PSRR 均复用原 worker。组合结果为 gain `3.5523 V/V`、BW `2.0576 GHz`、GBW `7.3093 GHz`、CMRR `34.446 dB`、输入参考积分噪声 `817.84 µV RMS`；`50 mV_peak` 时 THD `1.411%`，但 P1dB 未包围，PSRR+/- 也只有约 `11.06/13.08 dB`。exact inverse 与独立 generic readback 恢复原 SHA，恢复态 DC 再次成功。当前状态是 **active-load plus symmetric source-degeneration generic-delta and nominal multi-analysis migration live verified**，不是设计质量 closure。详见[组合拓扑 live Gate](docs/validation/2026-07-26-differential-pair-active-source-degeneration-live.md)。

同日又在新 `vda_cs_cascode_gate_001` 上闭合共源→共栅原位微调。通用 delta 只增加 `MNCAS/NCAS/VCAS/VCAS pin` 并重连 `MN0.D`；真实 pin/placement 指纹、post-save 故障自动 inverse、部分恢复后的端子重建、OA CDF `iPar("simM")` 严格解析都经过独立回读。由普通共源真实 OP 生成的同一组 9 个 `(Wcas,Lcas,VCAS)` tuple 先跑 DC、再逐点跑 AC，对应网表 9/9 匹配且两轮都 9/9 可行。AC 的声明离散域最佳点为 `0.75 µm/0.03 µm/0.545 V`，gain `6.4412 V/V`、BW `3.6127 GHz`、GBW `23.2705 GHz`；exact inverse 后普通共源 netlist hash、17 个 DC 指标及两次 28 项 AC/DC 指标完全一致。当前状态是 **recoverable common-source-to-cascode bounded same-source DC/AC selection live verified at nominal top_tt**，仍不是连续最优或完整质量闭环。详见[共栅与恢复 live Gate](docs/validation/2026-07-26-common-source-cascode-and-topology-recovery-live.md)。

2026-07-28 又在未参与旧 preview 校准的差分对八点域完成首个 prospective shortlist Gate。`vda preview-shortlist` 在读取任何新 OA 真值前固定 policy、完整 reference task hash 和 top-3=`op-local-001/002/003`；`vda preview-shortlist-audit` 只接受冻结之后开始、同任务 token 且完整穷尽的 OA→`si` reference run。八点 preview 与真实功耗排序 1–8 完全一致，Spearman ρ、可行性 agreement 和真值可行点 recall 均为 `1.0`，真实 winner `op-local-001` 被保留并最终写回 OA。中途 candidate 6 下载超时/SSH reset 被记为 `system_event`，独立 OA 回读后从 checkpoint 继续到 8/8。该 Gate 证明的是 **prospectively frozen standalone preview ranking and winner retention for one nominal TSMC N28 differential-pair local domain**；BW/GBW/power 最大绝对误差仍为 `24.10%/27.71%/30.35%`，所以 preview 仍只负责筛选，不能替代 OA 真值。详见[prospective live Gate](docs/validation/2026-07-28-differential-pair-preview-prospective-live.md)。

2026-07-31 又闭合首个真实一层 hierarchy Gate。VDA 从全新 child schematic 的精确 topology/pin 指纹非覆盖生成 sibling symbol，独立重开核对 terminal direction/width 与非空 bBox，并恢复 Cadence session 的 `ssgSortPins`。随后把另一个全新 top 从同构 flat 共源级增量替换为同库 child instance；自动 `si` 网表、child OA primitive graph、terminal order、完整 child topology/placement 指纹逐项一致。flat 与 hierarchical 两条 OA→`si`→Spectre DC/AC 的 output DC、供电电流、gain、BW、GBW 和 unity 最坏相对差为 `4.09e-15`。这证明一层 primitive-child 执行链，不证明深层 hierarchy、跨层参数写回或设计质量闭环。详见[一层 hierarchy live Gate](docs/validation/2026-07-31-existing-schematic-one-level-hierarchy-live.md)。

同一保留夹具随后完成了一层 child 参数真实调优。`XAMP/MN0.Wfg`、`XAMP/RD0.r` 由显式 hierarchy scope 绑定到 child 的 exact topology/placement，逐候选定向写回与回读，并在 `si` subckt body 中证明 `Wfg→w`、`r→r`。三点 shared-netlist DC/AC 域完整执行：`1u/5K` 虽有最高 GBW，却因 gain 不足被拒绝；两个可行点中选择并独立回读 `1.1u/18.5K`，gain `4.58293 V/V`、BW `7.22436 GHz`、GBW `33.1087 GHz`。top 结构和 child 两类指纹均未漂移。该能力严格限于同库 primitive-only 一层路径；共享 child alias、深层 hierarchy、派生 CDF 和 per-instance override 仍拒绝。详见[一层 child 参数调优 live Gate](docs/validation/2026-07-31-existing-schematic-hierarchical-parameter-tuning-live.md)。

## 当前能做什么

- 将任务编译为带副作用标记的稳定执行计划。
- 单独规划或执行：`device.characterize`、`schematic.create`、`schematic.inspect`、`schematic.symbol.generate`、`schematic.transform`、`parameters.apply`、`parameters.binding.discover`、`ade.prepare`、`ade.capture`、`ade.corners.apply`、`ade.variables.apply`、`ade.setup.apply`、`ade.run`、`simulation.run`、`design.tune`、`design.close_loop`。`device.characterize` 只有远端 scratch/compute，不接受 OA target；`parameters.binding.discover` 用一个可恢复 CDF probe 和三份 `si` 网表发现陌生器件的直接 OA→netlist 字段映射，不运行 Spectre；`schematic.symbol.generate` 只为精确绑定且尚无 symbol 的 existing schematic 非覆盖生成 sibling symbol，并独立回读；当前 `schematic.transform` 开放共源级源极退化的受控 add/remove、反相器 core→ADE source/load testbench、差分对 core→`MNTAIL/BIAS`、真实尾管差分对的对称源极退化 add/remove，以及无源退化真实尾管差分对的 `RD0/RD1 ↔ MP0/MP1` 电流镜负载可逆变换。预声明通用 delta 还可把既有共源级原位增量变成共栅级，不需要重建整个 cellview；该变体的 OA pin/placement、`si`、DC/AC 和 inverse 已有 live 证据。
- 每次专用 `schematic.transform` 通过原有模板语义断言后，还会把完整结构回读规范化为通用 topology snapshot，推导只含实例增删、端子重连、master 替换、net/pin 增删的 allowlisted delta，计算前后 SHA-256，并证明自动生成的 inverse patch 精确恢复原结构。参数不混入拓扑指纹，继续由独立 CDF/semantic 回读负责。`existing_schematic` 已真实执行 instance add/remove、terminal reconnect、net add/remove，以及 instance-scoped `replace_master + CDF`：NMOS LVT/SVT round-trip 的 OA master、233 项 CDF、`si` model/W/L、271 点 Spectre AC 和恢复态均已同源验证。逻辑 pin 现与实际 pin-symbol master/坐标/方向绑定，完整实例、pin、label、wire 几何进入独立 placement SHA；`add_pin/remove_pin` 删除完整 terminal/pin figure 层级。保存后审计失败时，worker 只在新鲜回读精确等于预期 topology 时自动 inverse，并已通过真实 PDK callback 故障 Gate；状态未知或存在额外结构漂移时仍拒绝二次写。`vda topology-compile` 可从 operation 文件一并绑定 `master_parameter_migrations`。这仍是受控 contract writer，不是任意远端 OA editor。
- 用确定性 demo adapter 离线验证闭环、规格判定和参数选择；结果明确标为 `software_inference`。
- 用 `circuit: netlist_preview` 做不经过 OA/`si`/Maestro 的轻量拓扑预评估。任务只接受受校验的 MOS/R/C/独立电压源结构、共享激励/供电/负载和 DC 或 AC 设置；worker 直接生成 standalone foundry-model Spectre deck，提取每个变体的 DC 工作点、功耗、工作区、gain、−3 dB bandwidth、GBW 和 unity，并给出同条件 A/B 差值。`vda preview-task-from-candidates` 现可把既有原子 `candidate_set` 或 `theory_seed` 按显式候选顺序和 typed field mapping 编译进一个结构占位 variant；未映射参数、固定值漂移、来源/PDK 不匹配、原始 CDF patch 和重复目标都会在 plan 前拒绝，不做隐式排名或截断。`vda preview-select` 再把 policy、编译任务、preview run 和已穷尽的 OA→`si` 参考 run 全部按 SHA-256 绑定，逐 variant 重渲染 deck、核对 manifest/非空 AC/进程 guard/候选身份和证据来源，然后按显式粗约束与 objective 生成 top-k；证据损坏会硬拒绝，排序或 winner-retention 不够只返回 `partial`。原始 Spectre 量是 `eda_result`，跨变体比较、候选映射和筛选判定是 `software_inference`。2026-07-27 九点 live 校准得到 preview top-3=`009/007/003`、OA→`si` top-3=`009/003/007`、Spearman ρ=`0.9333`，两边最优均为 `009`，因此当前同拓扑可把九个 OA 候选压到三个再复核；但 gain/BW/GBW/power 的最大绝对误差仍为 `4.39%/11.04%/10.13%/19.45%`，而且这是已知候选域上的事后校准，所以不创建 cellview、不替代最终 OA→`si`→Spectre/ADE，也不宣称跨拓扑泛化。
- 对未见候选域使用两阶段 prospective 契约：`vda preview-shortlist` 只读 preview task/run 和预先写好的完整 OA reference task，在没有 reference run 的情况下冻结 policy hash、reference task hash、时间戳和 top-k；该冻结结果可立即由 `vda oa-task-from-preview-shortlist` 编译成普通 OA 任务，但仍需重新 plan 和单独授权。完整真值域运行结束后，`vda preview-shortlist-audit` 才核对冻结内容、执行先后、reference task/token、完整域、winner retention、可行性和排序。冻结后改 shortlist、换 reference task、使用冻结前已开始的真值 run 或未穷尽域都会拒绝。2026-07-28 的差分对八点 live Gate 得到 ρ=`1.0` 并保留真实 winner；绝对值仍有最高 `30.35%` 系统偏差，因此能力边界是 prospective 排名/预筛，不是数值替代或跨 PVT/任意拓扑保证。
- 上述能力已经收敛为 [默认快速 preview shortlist 工作流](docs/fast-preview-shortlist-workflow.md)：4 个及以上候选先估算是否值得 preview，默认一次批量 preview、冻结 top-3、一次 checkpointed OA shortlist，昂贵质量分析只对真实 winner 运行。完整 OA 域只做代码/PDK 变化、失效/近边界或周期性审计，不再为了证明流程而立即换拓扑重跑。
- 用独立本地命令 `vda theory` 对 Gate 6 电流镜负载差分对做理论先导尺寸估算。它不接收一份任意手列的 W 候选，而是遍历声明且有来源绑定的有限 gm/Id 表域，对每个输入管/PMOS 负载/尾管工作点组合用 KCL、小信号和一阶极点方程反解满足 BW/GBW 的最小支路电流与三组 W，再检查增益、余量、功耗、面积和宽度边界。输出包括约束裕量、主导电流下界、寄生渐近上限和局部对数敏感性；只称为 `best_in_declared_discrete_characterization_domain`，`continuous_optimum_claim` 与 `global_optimum_claim` 永远为 false。`vda theory-calibrate` 又能从绑定的真实 Bridge run records 拟合并留一验证 topology-local 增益修正和等效输出电容模型；首个 TSMC N28 六点 Gate 的 gain/BW/GBW 最大留一误差为 `0.083%/0.373%/0.457%`，新鲜只读同点复跑误差为 `0.069%/0.320%/0.390%`。Gate 7B/7C/7D 已分别把 nominal 共源、源极退化共源和五管差分对的 OA/`si` 图及实际 DC 偏置绑定到独立表；任何不同器件签名、几何或 PVT 的推荐仍不能直接写 OA。
- 用 `vda theory-request-from-validation` 从 passed held-out validation 和 exact characterization run 集派生真实 PDK theory request；用 `vda theory-seed-task` 将理论结果编译为带 hash、量化规则和最优性边界的原子候选；再由正常 `design.tune` executor 用 `eda_result` 判规格和选优。`vda theory-seed-validate` 分开报告 shortlist 可行比例与逐点预测误差，防止“候选里有好点”被包装成“理论数值已准确”。Gate 8 已验证这条交接和中断恢复，但预测精度仍为 partial。
- 共栅级微调使用更小的 `vda cascode-seed` 分析器：它从 hash-bound 的真实共源 DC OP 估计当前器件阈值/过驱动，按声明的下管饱和余量、共栅管宽比和偏置 offset 生成有限原子 `(Wcas,Lcas,VCAS)` tuple；同一 tuple 集随后原样编译进 DC 和 AC 两个普通任务。首个 live Gate 的 DC/AC 9 点顺序和对应 `si` 网表 9/9 匹配，最终选择仍来自 Spectre。seed 是 `software_inference`，只负责缩小候选域，不会宣称连续或全局最优。
- 用通用 `candidate_set` 表达人工或任意本地优化器产生的完整候选 tuple；semantic/testbench/raw CDF 可以成组出现，固定字段深合并，不会与逐维搜索交叉展开。`vda op-relinearize` 可从 hash-bound real-Bridge run 的 `eda_result` 拟合 anchor-local 一阶响应并执行独立 heldout Gate；每个建模参数还必须在至少一个 heldout 点发生变化，避免一个从未被留出验证的方向伪装成已校准。`vda candidate-task-from-relinearization` 只把 passed 结果编译成普通调优任务；`vda op-relinearization-validate` 再把 exact result/task/real run 绑定起来，分开报告候选执行、推荐一致性和逐点误差。新 result 直接保存每项误差归一化 floor；旧 result 缺少该字段时必须用 `--policy` 提供 result 已绑定 canonical SHA 的原始 policy，任何 hash、来源、metric、scale 或门限漂移都会拒绝。局部筛选可只覆盖适合线性化的连续指标，而最终 task 继续保留 saturation、THD、CMRR 等完整 EDA constraints。
- 用 `vda small-signal` 对 characterization-bound MOS/R/C 实例图做不依赖拓扑名称的复数矩阵分析。器件点按 model/polarity/L/VGS/VDS/VSB 绑定 `Id/W、gm/Id、gds/Id、gmb/Id`、完整 signed 4×4 `dQi/dVj` 本征电荷导数矩阵和分开的 `cjd/cjs` 结耗尽电容；旧 artifact 仍走显式 legacy 五电容兼容路径。实例 model、L 和偏置不匹配即拒绝。求解器统一组装 `Y(f)`，支持固定 AC 边界、差分输入/输出线性表达式、低频增益、相位、−3 dB 带宽和 GBW；同一核心已用 NMOS/PMOS 共源、源极退化共源和差分对解析值测试。数值层另公开 `ComplexNodalSystem` 的系数/RHS stamping 接口和 `solve_complex_linear_system`，电路专属脚本可增加局部受控源、独立电流探针，或自行组装带辅助未知量的 MNA 方程，而无需复制求解器。`vda small-signal-validate` 又能从 real Bridge run records 自动绑定结构化 `si` 图、EDA DC 偏置、exact W/L/模型参数签名和原始 AC 网格，拒绝长度插值与所有偏置外推，再按固定 policy 对比 DC、每项电荷/结电容、gain、phase、BW 和 GBW。多 MOS 图可用重复 `--additional-characterization-run` 提供多张真实表，每个实例按 model/polarity/W/L/签名选择唯一 artifact，缺失、歧义和未使用表都拒绝。`vda characterization-task-from-run` 可把一个成功、只读的 real-si MOS 实例与用户审查的安全偏置网格合成为 standalone 表征任务，并保留来源哈希；当前自动生成只允许 `nf=1、m=1`。Gate 7D 已用三张真实表通过 nominal held-out 差分对；该层仍不求非线性 DC，也不能替代最终 Spectre。
- 通过独立 worker 调用本机 `virtuoso-bridge-lite` 环境。反相器支持 `OA -> si -> Spectre transient` 的 timing、过冲/欠冲和周期供电能量；共源级支持同一 `OA -> si` 网表上的 DC OP、复数 AC、相干正弦 transient 幅度 sweep 和普通 noise sweep。可提取 `Id/VGS/VDS/VDSAT/gm/gds`、真实 VDD 功耗与 KCL、低频增益、首个 −3 dB 带宽、GBW、unity、HD2/HD3、THD、P1dB，以及频带积分的输出/输入参考噪声；单项执行与提取均有 live 证据。`analysis: "quality"` 已在一次 OA/`si` 核对后依次运行 AC、linearity、noise，并完成 bias/load、W/RD/RS、L/VDD 搜索、固定设计 TT/SS/FF 验证和显式启用的 PVT-aware bias 调优；每个 PVT 条件保留原始 `eda_result`，跨条件约束和最坏值聚合标为 `software_inference`。
- Gate 3/4/5/6 差分对复用同一 worker 与 executor，不复制 Bridge。Gate 3 保留外部理想尾源能力；Gate 4 只新增 `MNTAIL(TAIL,BIAS,VSS,VSS)` 与 `BIAS` pin；Gate 5 再把 `MN0.S/MN1.S` 从 `TAIL` 分离到 `NSP/NSN`，只新增对称 `RS0(NSP,TAIL)`、`RS1(NSN,TAIL)`。Gate 6 从未退化的 Gate 4 拓扑删除 `RD0/RD1` 并加入 `MP0(OUTP,OUTP,VDD,VDD)`、`MP1(OUTN,OUTP,VDD,VDD)`，反向操作可按声明电阻值恢复原负载和可选 placement 指纹。通用 topology-delta 已在新 cellview 把 Gate 6 active-load 与 Gate 5 对称源退化组合，并真实通过 OA/`si`、PMOS/RS 双层 KCL、AC/CMRR/noise/transient/PSRR/ICMR 和 exact inverse。`tail_width_um/tail_length_um/source_resistance_ohm/pmos_load_width_um/pmos_load_length_um` 属于 OA semantic 参数，`tail_bias_v` 只属于 wrapper；真实尾管路径拒绝理想 `tail_current_ua/tail_output_resistance_ohm`。Gate 3–6 的 OA→`si` 证据链均已有 live 结果；Gate 6 还真实覆盖 ICMR、多种有限搜索、预算、不可行和 transport checkpoint/resume。新增 `analysis: "psrr"` 在同一自动 `si` 网表上分别运行平衡差模、VDD 注入和 VSS 注入，并核对三次 DC 与频率网格；`evaluation_stop_hz` 提供声明频带内最差 PSRR，三份下载根 AC 文件各自绑定大小与 SHA-256。nominal 单点、四点 bias/load 只读搜索和三种沟道长度的八点 OA 搜索已经 live。后者把带内最差 PSRR 从 `11.5125 dB` 提高到 `19.7438 dB`，但临时 `20 dB` 门仍不可行，故自动恢复基线；下一步应固定对 PSRR 几乎无益且严重损失带宽的尾管 `L=0.03 µm`，再在显式小网格内验证输入对/PMOS L，并对任何可行点补做 CMRR、线性度和噪声复核。不得把“最大值”包装成规格闭合。可选 PVT、mismatch、更多质量指标和 ADE handoff 仍是边界。
- 源极退化不新建第二套模板或仿真器：add 在同一 common-source cellview 中把 `MN0.S: VSS -> NSRC`，只新增 `RS0(NSRC,VSS)`；remove 只删除 VDA 创建的 RS0 两条端子 stub/标签、恢复 `MN0.S: NSRC -> VSS`。同一 inspect、参数应用、`si` 网表解析、DC/AC 指标和有限搜索路径动态识别两种变体。
- `existing_schematic` 提供不依赖固定电路模板的 Bridge 能力面：`schematic.inspect` 保留 Bridge 的完整结构结果和所有可回读 CDF 参数；`parameters.apply` 可按实例透传 Bridge 接受的参数字符串，写入后用定向 CDF 读取再次核对。若实际 CDF 字段已知但 `si` 参数名未知，`parameters.binding.discover` 以 exact topology、完整目标实例 CDF 表和字段权限为 CAS，执行 baseline→单字段 probe→restore 三次 netlisting。只有 OA 仅改变目标字段、网表仅改变一个参数、两端值均按工程单位等价且 canonical netlist 完整恢复时才输出可执行 binding；callback 联动、派生比例、多字段或零变化只报告诊断。反相器/共源模板仍可在同一任务中组合 semantic parameters 与原始实例参数。固定模板与通用既有 schematic 都可用 `instance_parameter_space` 或原子 `candidate_set` 声明有限的 `instance.parameter -> raw strings` 域；搜索字段名必须来自未过滤 OA inspect 的实际 CDF 名，不猜 Bridge 别名。这不收窄独立 `parameters.apply` 的原有 Bridge 能力。
- 通用 `existing_schematic` 的原子候选现在还能把 OA CDF 更新与 typed `testbench_overrides` 放在同一个完整 tuple 中。override 只可改已声明 source 的 `dc_value/ac_magnitude/ac_phase_deg` 或已声明 R/C load 的正有限 value；source 类型、连接、transfer、metric 和 OA topology 保持冻结。所有候选必须覆盖相同字段集合，checkpoint/resume 和最终 selection 都保存完整 OA+testbench identity。VBP、输入偏置和负载因此可以参与有限联合微调，但不会伪装成已写入 schematic 或 ADE setup 的参数。
- 可选 `design_context` 开始把上述低层能力收敛为“用户给大致拓扑、VDA 做局部细化”的 L5B 路径。上下文以 SHA-256 绑定 instance/net/pin/terminal 角色、冻结对象、允许固定或搜索的 semantic/CDF 字段、analysis/metric 意图，以及 topology-delta 可用 operation、mutable object 和数量上限。planner 在写入/仿真前加入 `design.context.bind`；executor 对 canonical OA graph、端子连接和真实 CDF 字段做只读审计，OA 原始状态是 `bridge_readback`，绑定判断是 `software_inference`。未携带上下文的人工 `parameters.apply` 保持原 Bridge 能力。`existing_schematic simulation.run/design.tune` 现已接入 typed `generic_simulation`：可声明独立电压/电流源、R/C 负载、单端或差分 transfer、DC/source-current/MOS OP metric 和 OA-CDF→`si` 参数绑定；它复用 Bridge 的 `si`/Spectre/进程 guard/manifest，不接受 raw deck 文本，也没有增加电路专用 executor。调优只允许实际实例字段，而且每个 fixed/searched raw 字段都必须有 context 权限和 OA→`si` 绑定；现有 candidate/checkpoint 状态机负责逐点暂存、定向回读、规格判定、预算语义、最佳提交、全不可行恢复和 transport resume。首个真实只读 Gate 已在共栅级联 OA 上让通用 worker 的 DC OP、gain、BW、GBW 和 unity 与旧专用路径逐项一致，最坏相对数值差 `4.1e-16`；随后真实 OA-write Gate 完成单字段三点搜索和 transport resume。`existing_schematic design.close_loop` 再把同一能力用于 hash-bound 基线与预声明可逆局部 delta，强制 objective 和完整拓扑×参数预算，controller 不随机造点。2026-07-31 的新 cell live Gate 在普通共源和固定 `RS0=750 ohm` 的源退化变体上跑完同一两个 `(MN0.Wfg,RD0.r)` tuple；四份 OA→`si`→Spectre AC 证据完整，GBW objective 选择 baseline 的 `1.1u/18.5K`，exact inverse 与任务外回读一致。真实 `WinError 10054` 后从 checkpoint index 3 续跑且未重复前缀。随后 staged generic contract 把 DC/AC/transient/noise 作为有序 Gate：完整前级 EDA 失败才跳过昂贵分析，缺指标保持 incomplete。显式 `shared_netlist` 让每个候选只启动一次 worker、回读一次 OA、生成一次 `si`，executor 独立复算 gate；真实三点任务的 9 个指标集合与隔离模式逐项相同，simulation action 从 739.195 s 降到 309.555 s，winner `1u/5K` 已独立 OA 回读。
- `vda onboarding-draft` 把首次接入从手写 JSON 收窄为可审计编译步骤：输入必须是一对成功的 real-Bridge `existing_schematic + schematic.inspect` 任务/run，且 task ID、重算 plan token、target、PDK、task/run SHA-256、topology/placement SHA-256 和完整未过滤 CDF 表全部一致。输出默认冻结所有已见对象、保留所有 CDF 字段但授予零参数权限，只给出需确认的 pin/net 角色候选，并把 `generic_simulation` 固定为不可执行。显式 child inspect 可建立唯一一层 `TOP/CHILD` inventory；它不会猜 `si` terminal order。保留的真实 flat 与一层 hierarchy 证据已分别本地重放 235 个 CDF 字段；这只是 `bridge_readback` 到待确认契约的 `software_inference`，不是新的 live OA Gate，也不是自动设计意图识别。
- `vda onboarding-resolve` 把 hash-bound 草案与显式 `user_input` resolution 编译成现有 planner 可接受的普通 `existing_schematic simulation.run/design.tune/design.close_loop` TaskSpec。target、PDK、topology hash 和 child scope 只能从草案继承；role 必须精确引用对应 topology，每个既有 CDF 字段必须来自 inventory 且拥有一对一 OA→`si` binding。`design.close_loop` 最多接收三个局部 alternative，逐个把 forward delta 应用到 draft topology、再用声明 inverse 精确恢复；mutable scope、新增实例 fixed CDF、alternative nodes/roles/bindings 和完整 topology×candidate 预算均在 plan 前核对。alternative 默认继承 baseline testbench，只需声明新增 binding；端口或 transfer 变化时才提供完整替代 testbench。`winner_verification` 只运行真实 nominal winner 的声明 analysis/可选 PVT。所有编译输出的 compute/write 开关仍固定为 false，也没有新增 executor。
- `vda onboarding-promote` 补齐新增实例从 fixed 初值到真实可调参数的第二阶段。它只接受已完整耗尽声明 topology×parameter 域、成功写回 winner 且带最终 Bridge topology 回读的真实 `design.close_loop` task/run；随后再绑定一个时间上晚于该 run 的独立只读 winner inspect。promotion intent 可为 baseline 和每个 alternative 预声明不同的完整 CDF permission/binding、原子候选与 winner-only quality/PVT，编译器按实际 `selected_topology_variant_id` 自动选择分支，并要求所有可调字段已经存在于 fresh CDF inventory。输出是新的 readback draft、普通固定拓扑 `design.tune` TaskSpec 和六项 SHA-256 handoff record；三份 UTF-8/LF artifact 可确定性重建，第二阶段继续使用原 checkpoint/executor。输出安全开关仍关闭，Bridge 原始 `parameters.apply` 能力不受限制。本批只完成本地 contract Gate，没有新增 EDA 结果。
- 2026-07-31 又在用户没有现成原理图的情形完成首次全新拓扑 onboarding live Gate：非覆盖创建最小共源基线，用通用 topology-delta 将电阻负载替换为 `MP0` PMOS 电流源负载，随后依次执行 inspect→draft→resolution→普通 shared-netlist `design.tune`，没有新增专用 executor。理论与 standalone preview 先把六点压到冻结 top-3；真实 OA→`si`→Spectre DC/AC 三点均可行，按 GBW 选择 `MN0=0.5 µm/30 nm、MP0=1.9 µm/30 nm、VBP=0.56 V、CL=2 fF`，得到 `3.23223 V/V`、`5.72128 GHz` 和 `18.4925 GHz`。只有 W/L 写回 OA，VBP/CL 仍是 testbench；一次 `WinError 10054` 经 checkpoint 和独立 OA 回读恢复。该结论只覆盖 nominal TSMC N28 与声明的三点离散域，未证明完整六点 winner retention、连续最优或完整设计质量闭环。
- staged controller 现可先用便宜 nominal stage 选 provisional winner，再只对该点运行独立 `winner_verification`。昂贵 Gate 可声明自己的 DC/AC/transient/noise stage、约束、sweep 与最多五个 PVT 条件；供电条件必须绑定到明确 source。Gate 失败或不完整时恢复搜索前 OA、清空推荐，不会静默升级 runner-up。真实两点 TSMC N28 Gate 只对 nominal GBW winner `1u/5K` 运行 TT/27 ℃/0.90 V 与 SS/125 ℃/0.81 V 的四分析，并由任务外回读确认写回。拓扑 refinement 也已从一个 delta 泛化为最多七个共同基线、输出指纹各异的 independent alternatives；每个 alternative 必须先回到共同基线再 forward，checkpoint 用扁平 topology×parameter index 恢复，未知/部分结构拒绝写入。2026-07-31 的新 cell live Gate 又让 common-source、固定 `RS0=750 ohm` 的 source-degenerated 与 cascode 三个 variant 共用两个参数 tuple，完整执行 6/6 份 OA→`si` DC/AC；三次 SSH/Bridge 中断均经任务外 OA 回读后从 index 2/5/6 恢复且不重跑前缀，最终按 gain/BW 门和 GBW objective 写回 cascode `1.1u/18.5K`。一层 hierarchy 通过显式 top instance、child library/cell、`si` subckt 和 terminal order 绑定；worker 独立回读 child OA，并证明 child primitive graph、完整 topology/placement 与 subckt body 一致，未绑定 subcell、嵌套 subckt或端子/节点漂移均拒绝。该路径已用非覆盖 child symbol 和 top cell 完成真实 OA→`si`→Spectre DC/AC，flat/hierarchical 六项核心指标最坏相对差 `4.09e-15`。现在 `TOP/CHILD` 参数路径还能在 exact child scope 下进入原有 candidate/checkpoint/winner 状态机；真实三点 `W/RD` Gate 证明 child CDF 写入、定向回读、subckt 参数绑定、规格优先选择和最终 winner 写回。全不可行恢复与写后中断续跑已由本地故障注入覆盖。深层 hierarchy、共享 child 的 per-instance override、派生 CDF、并发人工 editor、mismatch/Monte Carlo 仍未闭合，因此仍不能称为 L5B closure。
- `ade.prepare` 与 `ade.capture` 保留显式人工介入边界。`prepare` 只在目标 Maestro view 不存在时新建持久化 Spectre test，可显式指向另一个既有 design schematic；已有 view 一律拒绝，也不预设 analysis/stimulus/sweep/output。`capture` 核对人工聚焦的目标，捕获 setup、history、真实 Spectre netlist/PSF/log 哈希和逐点 output/spec。自动分支中，`ade.corners.apply` 只在 exact tests 与旧 corner 有序列表匹配时 add-only 新增 corner；`ade.variables.apply` 只有在 expected tests、可选 enabled corners、全部声明 scope 旧值和目标 global-selection 状态匹配时才更新变量或 selection；`ade.setup.apply` 对声明 analysis 做旧状态 CAS，并只新增不存在的命名 net/point output 与可选 spec。三个 setup 写 operation 都只保存一次并独立重开回读，已有已配置 session 时拒绝。`ade.run` 为每个 test 临时把 background session 的 project/results dir 定向到唯一 `/data/xum` scratch，运行或按显式 history/scratch 恢复后还原原值；它读取逐点 output/spec，并对 exact-history companion 与唯一 runtime input 根生成大小/SHA-256 清单。任务可显式要求把哈希绑定的 `input.scs` 或 `input.scs`+sibling `netlist` 输入束的 design header、实例、节点和已知 primitive raw 参数映射与 Maestro/OA 回读核对；原生 sweep 又可严格绑定 setup、global-variable selections、共享符号输入束、RDB point/corner 和 completion log。corner 模式通过 Bridge 公开 `include_raw=True` 取得原始 Detail CSV，在 VDA 层保留 Bridge 0.7.0 尚未结构化的正交 corner 列；不会修改 Bridge。若 Bridge completion wait 超时，只有运行前后恰好新增一个名称且其 log 已 completed 时才继续，多个新 history、同名覆盖或未完成日志均拒绝。配置/OA 回读属于 `bridge_readback`，运行输入与结果属于 `eda_result`，兼容性归一化、history 选择和一致性判断属于 `software_inference`。可选 `result_mapping` 再固定 exact scalar output expression、单位 scale 和 VDA constraints/objective。若人工旧 output 在声明点必然产生 calculator `eval err`，`expected_output_evaluation_errors` 只能按 exact test/output/point selector 声明未映射项；worker 与 executor 都要求 RDB 单元格和 log error 数完全相等、未解释错误为零，不能作为通用忽略开关。未声明时保持普通 Bridge-preserving run。它不能证明 history 名称此前不存在。旧 ADE L state 的非破坏迁移尚未纳入已验证 VDA operation。
- 对远端计算和 OA 写入分别授权；真实执行还需要计划 token，避免一句模糊指令直接改库。
- 将动作、候选点、指标、约束判定、最终选择和证据来源写入本地 JSON run record；调优任务还会结构化记录声明/尝试/完整候选数、是否穷尽声明离散域和 `best_evaluated`/`best_in_declared_discrete_domain` 选择范围，连续与全局最优声明固定为 false。候选边界原子保存 checkpoint，并可在独立 OA 回读后续跑。

2026-07-19 已在 nics4304 完成首轮真实远端 smoke：Bridge doctor、反相器单点 Spectre、OA 建图与结构回读、局部参数写入与前后回读、9 点有限搜索和最佳参数写回，以及不可行规格下的禁止写回均通过。该轮 live smoke 使用的仍是手写 Spectre deck。

随后代码已改为从目标 OA schematic 调用 `si -batch` 生成结构网表，核对 OA 回读与网表中的实例、端口和 `W/L`，再用只含激励、负载、model 和 analysis 的 wrapper 运行 Spectre。第二轮只读 live smoke 验证了 OA/网表一致性和非空波形；第三轮又保存 `VDD_SRC:p`，得到 9 点真实 timing/energy 数据，在收紧规格下选择并回读 `Wn=0.6 µm, Wp=0.8 µm`，并通过不可行 + 预算耗尽恢复。第四轮加入候选级 checkpoint/resume；真实 9 点任务经历 3 次 SSH/tunnel 中断后，从候选 2、4 和最终写回边界继续，未重复已完成前缀，最终 run、checkpoint 和独立 OA 回读均成功。因此当前状态是 **inverter L5A same-source bounded closure and explicit checkpoint/resume recovery verified**。随后在 Bridge 的备份隔离分支上修复 Windows stale PID 和 no-tunnel 分支遗漏 `warm()`，强制终止精确 tunnel PID 后的只读 inspect 已自动恢复；运行中传输的随机 reset/timeout 仍未证明消失。补丁来源和升级办法见 `docs/third-party/virtuoso-bridge-local-patch.md`。

Bridge 隔离分支随后又增加幂等 SSH 传输的 1 秒/3 秒有界退避，以及只在 SKILL payload 发送前 `connect()` 被拒绝时 warm 一次 tunnel。压力任务仍真实遇到过一次候选 8 建连拒绝，但 checkpoint 成功恢复并完成 9/9；同-client 强制断链 smoke 已验证 pre-send 自动恢复。payload 发送后的中断不会自动重放，仍保留为显式 checkpoint/resume 边界。

同日 Gate 2A 在新建的 `vb_pdk_smoke/vda_cs_gate2a_001/schematic` 上完成 nominal 共源 DC。2026-07-20 又在已有 `vda_param_surface_001` 上由正式 `schematic.transform` 原位加入 RS0/NSRC，完成真实 DC、6 点 `Vbias×RS` 搜索和 checkpoint 恢复。随后 nominal/退化只读 AC 与各 6 点 bias/load 搜索通过。专用 `vda_cs_ac_tradeoff_001` 又在完全相同 W/L/RD/bias/load 下只加入 RS=2 kΩ，真实测得 gain −36.98%、BW −20.12%、GBW −49.66%；随后 W/RD/RS 8 点搜索全部可行，按 GBW 选择并写回 `W=1.0 µm, RD=20 kΩ, RS=1 kΩ`，得到 `gain=3.701 V/V, BW=8.190 GHz, GBW=30.306 GHz`。预算耗尽和人为不可行任务分别正确标为 partial，并完成最佳前缀写回或初始 OA 恢复。

## 快速开始

推荐 Python 3.13：

```powershell
cd "H:\Virtuoso Design Agent"
py -3.13 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
```

需要显式启动真实 Bridge 时，使用 VDA 的生命周期入口，不再从外层
PowerShell 直接运行 `virtuoso-bridge.exe start`：

```powershell
.\.venv\Scripts\vda.exe bridge start
.\.venv\Scripts\vda.exe bridge status
# 工作结束且确认没有其他任务复用 tunnel 时：
.\.venv\Scripts\vda.exe bridge stop
```

Windows 子进程使用隐藏窗口和 Job Object；进度、Bridge warm 耗时、daemon/Spectre
可用性和最终返回码只显示在当前终端。默认不回显原始 `[cmd]` SSH 诊断；排障时可加
`--verbose`。`-p/--profile` 是 Bridge connection profile，不是 VDA PDK profile；也可用
`--env <PATH>` 显式指定 Bridge 环境文件，但 VDA 不读取或打印其正文。这个入口只代理
Bridge 的公开 CLI，不复制 SSH、daemon 或 state 逻辑。正常启动后共享 tunnel 按 Bridge
语义继续存在；中断时只回收本次仍受控的启动进程树。`status` 会探测 daemon 并查询
Spectre 版本，但不会访问 OA、运行设计仿真或形成 `eda_result`。

首次接入用户已有 schematic 时，可以把一次成功的只读 inspect 任务及其 run record 编译成
待确认草案，而不手抄实例、网络、pin 和 CDF 字段：

```powershell
.\.venv\Scripts\vda.exe onboarding-draft `
  examples\tasks\existing-schematic-multi-alternative-inspect.bridge.json `
  artifacts\runs\existing-schematic-multi-alternative\inspect-after-create-20260731.json `
  --id my-module-onboarding `
  --output artifacts\runs\onboarding\my-module-draft.json
```

草案固定为只读、零参数权限且不可执行；用户仍需确认电路角色、可调字段、testbench、analysis、
metric 和 OA→`si` 映射。任务/run hash、plan token、target、PDK、topology/placement 指纹和完整
CDF 表都被绑定，不能拿旧回读悄悄生成新任务。一层 child 可用重复的 `--child-inspection`
显式加入；共享 child alias 会拒绝。完整流程见
[已有 schematic 自动接入](docs/existing-schematic-onboarding.md)。

确认 resolution 后，可编译为普通且默认不可远端执行的任务，再走原有 planner：

```powershell
.\.venv\Scripts\vda.exe onboarding-resolve `
  artifacts\runs\onboarding\flat-live-draft.json `
  examples\onboarding\existing-schematic-flat-ac-resolution.json `
  --output artifacts\runs\onboarding\flat-resolved-task.json

.\.venv\Scripts\vda.exe plan `
  artifacts\runs\onboarding\flat-resolved-task.json
```

用户已经给出 schematic 时，先根据 OA→`si` binding 是否已知选择入口：

如果实例 CDF 字段已由 fresh inspect 证实存在、但对应 `si` 参数名未知，先检查单字段可逆
discovery 计划：

```powershell
.\.venv\Scripts\vda.exe plan `
  examples\tasks\existing-schematic-parameter-binding-discovery.demo.json
```

这个文件只演示契约，安全开关关闭，里面的 topology hash 和完整 CDF 表不能复用于真实 cell。
真实任务必须从同一目标的 fresh readback 重建它们；discovery 只返回可回填的 mapping，不保留
probe 值，也不运行 Spectre。

已有 binding 后，再检查通用有限参数闭环的计划，不需要增加电路模板：

```powershell
.\.venv\Scripts\vda.exe plan `
  examples\tasks\existing-schematic-generic-ac-tune.demo.json
```

该示例把 `MN0.w` 的三个原始 CDF 字符串值限定在 `design_context` 权限内，并要求每个
被调字段都有显式 OA→`si` 参数绑定。计划依次执行 inspect/context bind、逐候选写入与
回读、同源 AC、规格/目标选优、最佳点提交或初值恢复和最终独立回读；不创建或替换
cellview。示例安全开关故意为 false，只能生成计划；真实执行仍需本次 token、远端计算与
OA 写入授权。

如果用户还允许一项已审计的局部拓扑修改，可以检查拓扑与参数联合闭环计划：

```powershell
.\.venv\Scripts\vda.exe plan `
  examples\tasks\existing-schematic-topology-parameter-close-loop.demo.json
```

该模板把两个完整 `(MN0.w, MN0.l)` tuple 原样用于普通级和一份 source-degeneration
delta，变体新增 `RS0.r` 是独立固定字段，因此总域严格为 4 点。只有全部四点完成才按
显式 GBW objective 选择并原子提交 topology+parameters；同分保留基线，不可行或可恢复
中断回到执行前基线。模板中的 topology hash 和 raw CDF 名只对应示例结构，实际 target
必须先 inspect/compile 生成自己的 context/delta，不能复制占位值后执行。安全开关同样故意
为 false。新 TSMC N28 cell 上的四点 OA→`si`→Spectre objective/writeback/resume 实测见
[topology + parameter live Gate](docs/validation/2026-07-31-existing-schematic-topology-parameter-close-loop-live.md)。

先做完全本地、无 Bridge/OA 副作用的理论尺寸估算：

```powershell
.\.venv\Scripts\vda.exe theory examples\theory\differential-pair-gmid.synthetic.json
```

该示例故意标为 `synthetic_example`，只验证方程、设计域穷尽和最优性边界。真实 PDK 表必须绑定 characterization artifact 的标识与 SHA-256；即使输入来自 PDK，推导指标仍是 `software_inference`，推荐尺寸还要进入同源 Spectre 验证。

理论已经给出候选、但还不值得创建或修改 OA 时，可先计划轻量 standalone Spectre
A/B。下面的例子让普通共源与共栅级联共享 `VIN/VDD/RLOAD/CLOAD`，只在变体里声明
MOS 连接和共栅偏置；它不接受任意 raw deck：

```powershell
.\.venv\Scripts\vda.exe plan `
  examples\tasks\common-source-cascode-netlist-preview.bridge.json
```

真实运行仍需 `--adapter bridge --execute --token <PLAN_TOKEN>` 和任务中的
`allow_remote_compute: true`。该路径只启动 standalone Spectre，不启动 Virtuoso、`si`
或 Maestro，也不写 OA；两个变体各用唯一 `/data/xum/.../vda_netlist_preview_*` scratch
并保留 deck/PSF/log 哈希。首个真实 smoke 的两份 241 点 AC 均完整并包围 bandwidth，
运行后 Spectre/si/Maestro 进程归零；结果与 OA→`si` 的主要 A/B 方向一致，但不是绝对值
复现。它适合初筛，胜出结构仍要进入 OA 同源或人工 ADE Gate。详见
[live Gate](docs/validation/2026-07-27-standalone-netlist-preview-live.md)。

已经存在有限候选域时，不需要手工复制九份结构。下面的纯本地编译把声明的 9 个
cascode seed 按原顺序替换一个 `cascode_template`，同时保留普通共源基线；输出是 10 个
variant 的普通 `netlist_preview` task：

```powershell
.\.venv\Scripts\vda.exe preview-task-from-candidates `
  examples\theory\common-source-cascode-preview-compile-policy.json `
  artifacts\theory\common-source-cascode-seed-20260726.json `
  examples\theory\common-source-cascode-preview-task-template.json `
  --output artifacts\theory\common-source-cascode-preview-task-20260727.json
.\.venv\Scripts\vda.exe plan `
  artifacts\theory\common-source-cascode-preview-task-20260727.json
```

compile policy 会核对 candidate generator/source ID、可选 PDK、显式 candidate IDs、固定
参数和每个 semantic parameter 到 MOS/电源/电阻/电容字段的映射。source、policy、template
三份文件 hash 以及 `variant -> candidate ID` 都进入任务。编译本身不连接 Bridge，也不
运行仿真；示例 policy 绑定的是已保留的 `...-live` seed，换用新 seed 时必须显式更新该
source ID，不能自动接受来源漂移。

preview 运行后可用独立 validator 生成 top-k，并与一个已穷尽的 OA→`si` 参考域校准：

```powershell
.\.venv\Scripts\vda.exe preview-select `
  examples\theory\common-source-cascode-preview-selection-policy.json `
  artifacts\theory\common-source-cascode-preview-task-20260727.json `
  artifacts\runs\common-source-cascode-candidate-preview\run-20260727T-candidate-preview-live.json `
  artifacts\runs\common-source-cascode-ac-seeded\run-20260726T-live-real-network.json `
  --output artifacts\theory\common-source-cascode-preview-selection-20260727.json
```

该命令只读输入文件并写本地结果，不重跑 EDA。示例 policy 是已知九点域上的
`retrospective_calibration`；用于新拓扑时应预先固定 policy 并标成
`prospective_validation`。完整九点结果见
[selection live Gate](docs/validation/2026-07-27-preview-candidate-selection-live.md)。

未见候选域不能先读取 OA reference run 再生成 shortlist。两阶段命令先冻结名单，之后
才允许用完整真值域审计：

```powershell
.\.venv\Scripts\vda.exe preview-shortlist `
  examples\theory\differential-pair-preview-prospective-policy.json `
  artifacts\theory\differential-pair-preview-prospective-task.json `
  artifacts\runs\differential-pair-preview-prospective\preview-live-20260728.json `
  artifacts\relinearization\differential-pair-preview-prospective-oa-task.json `
  --output artifacts\theory\differential-pair-preview-prospective-shortlist-20260728.json

.\.venv\Scripts\vda.exe preview-shortlist-audit `
  examples\theory\differential-pair-preview-prospective-policy.json `
  artifacts\theory\differential-pair-preview-prospective-shortlist-20260728.json `
  artifacts\theory\differential-pair-preview-prospective-task.json `
  artifacts\runs\differential-pair-preview-prospective\preview-live-20260728.json `
  artifacts\relinearization\differential-pair-preview-prospective-oa-task.json `
  artifacts\runs\differential-pair-preview-prospective-oa-reference\reference-live-20260728-resume1.json `
  --output artifacts\theory\differential-pair-preview-prospective-audit-20260728.json
```

第一条命令要求 policy 已绑定未来完整 OA task 的 SHA-256，但参数中不存在 reference run，
所以无法在看见真值后修改 top-k。第二条命令要求 reference run 的 `started_at` 晚于
`frozen_at`，并且 task hash、plan token、候选域和执行完成度都匹配。两条命令都只读
证据并写本地 JSON，不连接 Bridge、不写 OA；真正 preview 和 OA reference 的执行仍分别
需要各自 plan token 与授权。差分对实测见
[prospective live Gate](docs/validation/2026-07-28-differential-pair-preview-prospective-live.md)。

筛选 Gate 通过后，可把 retrospective selection 或已冻结的 prospective shortlist 按原
排名确定性编译回普通 OA `candidate_set` 任务：

```powershell
.\.venv\Scripts\vda.exe oa-task-from-preview-shortlist `
  artifacts\theory\common-source-cascode-preview-selection-20260727.json `
  artifacts\theory\common-source-cascode-ac-task-20260726.json `
  --id common-source-cascode-ac-preview-shortlist `
  --output artifacts\theory\common-source-cascode-ac-preview-shortlist-task.json
.\.venv\Scripts\vda.exe plan `
  artifacts\theory\common-source-cascode-ac-preview-shortlist-task.json
```

编译器逐字节绑定 selection 和完整 OA task，保留 target、约束、objective、安全策略及
候选 tuple，只把域缩成 shortlist 并把预算改为 shortlist 大小。输出仍需重新 plan，新的
token 与远端授权不能从 preview 继承。若候选语义要求共栅级联，任务还会携带
`expected_target_topology_variant=cascode_common_source`；executor 在任何候选写入或仿真
前先做 OA 结构回读，拓扑不符即失败。已知九点域的 3 点 live handoff 保留了真实 winner
009，3 点 OA 用时较原 9 点减少 71.667%；详见
[OA shortlist handoff live Gate](docs/validation/2026-07-27-preview-shortlist-oa-handoff-live.md)。

共栅级微调先对变换前的共源 OA 做一次只读 DC，再把该 real-Bridge run 的
SHA-256 写入 policy（模板中的全零 hash 只是故意不可执行的占位符）。以下命令完全
本地，只生成一个 theory result，并把同一组原子候选分别编译为 DC/AC 任务；它们不会
连接 Bridge、运行 Spectre 或写 OA：

```powershell
.\.venv\Scripts\vda.exe cascode-seed `
  examples\theory\common-source-cascode-seed-policy.template.json `
  artifacts\runs\common-source-cascode-seed-op-bridge\<RUN_RECORD>.json `
  --output artifacts\theory\common-source-cascode-seed.json
.\.venv\Scripts\vda.exe candidate-task-from-cascode-seed `
  artifacts\theory\common-source-cascode-seed.json `
  examples\theory\common-source-cascode-dc-task-template.json `
  --output artifacts\theory\common-source-cascode-dc-task.json
.\.venv\Scripts\vda.exe candidate-task-from-cascode-seed `
  artifacts\theory\common-source-cascode-seed.json `
  examples\theory\common-source-cascode-ac-task-template.json `
  --output artifacts\theory\common-source-cascode-ac-task.json
```

生成后的 DC/AC task 都要重新 `plan` 并单独满足远端授权。DC Gate 先核对下管、共栅管、
负载的 KCL、两管工作区和 stack headroom；只有完成它以后才运行相同候选域的 AC
gain、首个 −3 dB bandwidth、GBW 与 unity-gain 提取。

已有真实 run record 时，可在本地重建 Gate 6 的有界校准产物；该命令不执行 Bridge、
不写 OA：

```powershell
.\.venv\Scripts\vda.exe theory-calibrate `
  artifacts\runs\differential-pair-current-mirror-gate6\19-geometry-tune-20260723.json `
  --validation-run artifacts\runs\theory-calibration\fresh-ac-20260724.json `
  --output artifacts\theory\gate6-one-pole-top-tt-20260724.json
```

输入必须来自 real Bridge adapter，且逐点具有 OA `bridge_readback`、匹配的自动
`si` 网表、Spectre OP/AC `eda_result` 和文件哈希；条件漂移、证据降级、训练网格
缺点或验证点外推都会拒绝。详见[真实校准记录](docs/validation/2026-07-24-theory-calibration-live.md)。

同一个本地小信号核心可以直接消费不同实例/节点图，不需要声明拓扑名称：

```powershell
.\.venv\Scripts\vda.exe small-signal `
  examples\theory\common-source-small-signal.synthetic.json
```

该样例的器件值是 `user_input` synthetic 数据，网络结果是 `software_inference`。
真实 PDK characterization 必须绑定 raw `eda_result` artifact SHA-256；归一化点值
和网络推导分别保持 `software_inference`。详见
[通用小信号本地 Gate](docs/validation/2026-07-24-generic-small-signal-local.md)。

已有成功的 OA→`si`→Spectre DC/AC run record 时，还可以直接把同一次 action 的
结构化 `si` 图与精确 OP 导数送入该矩阵核心，而不再为每个候选盲跑 AC：

```powershell
.\.venv\Scripts\vda.exe small-signal-from-run `
  examples\theory\common-source-cascode-op-small-signal-policy.json `
  artifacts\runs\common-source-cascode-ac-seeded\run-20260726T-live-real-network.json `
  --output artifacts\theory\common-source-cascode-op-small-signal-20260727.json
```

policy 只声明 MOS polarity、AC 固定边界、输入/输出表达式和频率，不声明专用拓扑
方程；拓扑来自 run record 中的 `si` 实例/节点。原始 OP 是 `eda_result`，归一化和
矩阵解是 `software_inference`。旧共源/共栅 run 只保存了 `gm/gds`，因此当前产物只
覆盖低频电导模型：普通共源与共栅预测相对已有 AC 的增益误差分别为 `0.158%` 和
`0.646%`；缺失的 `gmb`、电荷导数和结电容被显式列出，不能据此声称 BW/GBW/noise
已闭合。后续 common-source wrapper 已请求保存 `gmb + signed dQi/dVj + cjd/cjs`，
但该新增保存面仍需一次只读 live capture 才能升级为真实动态预测证据。详见
[共源/共栅 OP 理论线性化本地 Gate](docs/validation/2026-07-27-common-source-cascode-op-linearization-local.md)。

生成独立 MOS 表征计划（真实运行仍需 `--execute`、本次 plan token 和
`allow_remote_compute=true`）：

```powershell
.\.venv\Scripts\vda.exe plan `
  examples\tasks\mos-device-characterize-top-tt.bridge.json
```

该任务没有 OA target，最终 `MosCharacterizationArtifact` 位于 run record 的
`device.characterize.validate` action 下；完整 live 证据见
[TSMC N28 独立 MOS characterization](docs/validation/2026-07-24-tsmc28-mos-characterization-live.md)。

已有独立 characterization 和只读 OA/`si`/Spectre run record 后，可用固定 policy
在本地执行 held-out 电路误差 Gate；该命令不会连接 Bridge 或写 OA：

```powershell
.\.venv\Scripts\vda.exe small-signal-validate `
  examples\theory\common-source-gate7b-validation-policy.json `
  artifacts\runs\mos-device-characterization-gate7b-cs-mn0\live-retry1-20260724.json `
  artifacts\runs\common-source-small-signal-heldout\live-signature-20260724.json `
  --output artifacts\runs\common-source-small-signal-heldout\gate7b-validation-signature-20260724.json
```

它要求 real Bridge adapter、只读 OA side-effect 记录、匹配 target/PVT、完整 raw hash、
exact W 与模型参数签名；结果中的理论值和误差门属于 `software_inference`。详见
[Gate 7B 共源小信号真实验证](docs/validation/2026-07-24-common-source-small-signal-validation-live.md)。

Gate 7D 的差分对 validation 和三张 exact characterization run 可继续生成 Gate 8 的
真实 PDK theory request、原子任务和事后误差审计；前三条命令完全本地，真正运行任务
仍需通常的 plan token、`--execute` 和 OA/compute 授权：

```powershell
.\.venv\Scripts\vda.exe theory-request-from-validation `
  examples\theory\differential-pair-gate8-derivation-policy.json `
  artifacts\runs\differential-pair-current-mirror-characterization-heldout-bridge\gate7d-final-validation-20260725.json `
  artifacts\runs\mos-device-characterize-diffpair-input-mn0-top-tt\run-20260724T204823Z.json `
  --additional-characterization-run artifacts\runs\mos-device-characterize-diffpair-load-mp0-top-tt\run-20260724T204820Z.json `
  --additional-characterization-run artifacts\runs\mos-device-characterize-diffpair-tail-mntail-top-tt\run-20260724T204818Z.json `
  --output artifacts\theory\differential-pair-gate8-tsmc28-request.json
.\.venv\Scripts\vda.exe theory artifacts\theory\differential-pair-gate8-tsmc28-request.json `
  --output artifacts\theory\differential-pair-gate8-tsmc28-result.json
.\.venv\Scripts\vda.exe theory-seed-task `
  examples\theory\differential-pair-gate8-seed-policy.json `
  artifacts\theory\differential-pair-gate8-tsmc28-result.json `
  examples\theory\differential-pair-gate8-task-template.json `
  --output examples\tasks\differential-pair-current-mirror-theory-seed-gate8.bridge.json
.\.venv\Scripts\vda.exe theory-seed-validate `
  examples\theory\differential-pair-gate8-validation-policy.json `
  examples\tasks\differential-pair-current-mirror-theory-seed-gate8.bridge.json `
  artifacts\runs\differential-pair-current-mirror-theory-seed-gate8-bridge\run-20260725-grid-resumed.json `
  --output artifacts\theory\differential-pair-gate8-seed-validation.json
```

最后一个命令在候选生成可用但逐点预测门失败时会输出完整 `partial` 结果并返回非零，
不会为了让 CI 变绿而隐藏误差。完整解释见上述 Gate 8 live 记录。

已有成功的 real-Bridge tuning record 后，可完全本地生成并验证下一轮局部候选。以下两条
命令不连接 Bridge、不运行 Spectre、不写 OA；失败的 training/heldout Gate 会令第一条
返回非零且不产生 `candidate_set`：

```powershell
.\.venv\Scripts\vda.exe op-relinearize `
  examples\theory\common-source-op-relinearization-policy.json `
  artifacts\runs\common-source-quality-design-tune\live-resume1-20260721.json `
  --output artifacts\relinearization\common-source-op-relinearization.json
.\.venv\Scripts\vda.exe candidate-task-from-relinearization `
  artifacts\relinearization\common-source-op-relinearization.json `
  examples\theory\common-source-op-relinearization-task-template.json `
  --output artifacts\relinearization\common-source-op-relinearized-task.json
```

差分对使用同目录下的 `differential-pair-op-relinearization-*.json` policy/template 和
Gate 8 run record。每份新生成 task 仍需要重新 `plan` 和单独远端授权；本地预测不会
自动触发 OA 写回。共源任务已完成一次真实六点运行，事后可执行：

```powershell
.\.venv\Scripts\vda.exe op-relinearization-validate `
  artifacts\relinearization\common-source-op-relinearization.json `
  artifacts\relinearization\common-source-op-relinearized-task.json `
  artifacts\runs\common-source-quality-op-relinearized-next\live-resume1-20260726.json `
  --policy examples\theory\common-source-op-relinearization-policy.json `
  --output artifacts\relinearization\common-source-op-relinearization-live-validation.json
```

该命令只读 hash-bound JSON，不连接 Bridge；任一新点超过原 held-out error limit 时
输出完整 `partial` 并返回 1。当前共源执行/推荐 Gate 通过，但 output swing 精度 Gate
保留为 partial。差分对六点也已执行；其旧 result 同样必须绑定原 policy，当前 exact
validator 返回 0：

```powershell
.\.venv\Scripts\vda.exe op-relinearization-validate `
  artifacts\relinearization\differential-pair-op-relinearization.json `
  artifacts\relinearization\differential-pair-op-relinearized-task.json `
  artifacts\runs\differential-pair-op-relinearized-next\live-resume3-20260726.json `
  --policy examples\theory\differential-pair-op-relinearization-policy.json `
  --output artifacts\relinearization\differential-pair-op-relinearization-live-validation-bound-policy.json
```

可继续使用该真实六点 record 在新最佳点重定 anchor。当前合法刷新只建模 W/RD、固定
RS；若把 RS 也声明为可调但 heldout 没有 RS 扰动，第一条命令会明确失败：

```powershell
.\.venv\Scripts\vda.exe op-relinearize `
  examples\theory\common-source-op-refresh-2d-policy.json `
  artifacts\runs\common-source-quality-op-relinearized-next\live-resume1-20260726.json `
  --output artifacts\relinearization\common-source-op-refresh-2d.json
.\.venv\Scripts\vda.exe candidate-task-from-relinearization `
  artifacts\relinearization\common-source-op-refresh-2d.json `
  examples\theory\common-source-op-refresh-2d-task-template.json `
  --output artifacts\relinearization\common-source-op-refresh-2d-task.json
```

该刷新任务已完成真实六点运行，以下 exact validator 返回 0；它与上一个三维 task 的
`partial` 历史结论并不冲突，因为新模型明确固定了没有 heldout 覆盖的 RS 方向：

```powershell
.\.venv\Scripts\vda.exe op-relinearization-validate `
  artifacts\relinearization\common-source-op-refresh-2d.json `
  artifacts\relinearization\common-source-op-refresh-2d-task.json `
  artifacts\runs\common-source-quality-op-refresh-2d-next\live-resume2-20260726.json `
  --output artifacts\relinearization\common-source-op-refresh-2d-live-validation.json
```

当目标 `si` 实例的几何或 LDE signature 不同，不再手工复制几十个参数。先用用户审查的
偏置网格模板从只读 circuit run 生成新任务，再正常 plan/授权执行：

```powershell
.\.venv\Scripts\vda.exe characterization-task-from-run `
  examples\characterization\nics4304-tsmc28-nmos-nominal-grid.json `
  artifacts\runs\common-source-small-signal-degenerated-heldout\live-signature-20260724.json `
  --id mos-device-characterize-cs-degenerated-mn0-top-tt `
  --instance MN0 --polarity nmos `
  --operating-condition top_tt_27c_0p90v `
  --output examples\tasks\mos-device-characterize-cs-degenerated-mn0-top-tt.bridge.json
```

生成任务绑定来源 run、`si` netlist、model/W/L 和参数 signature SHA-256；原始实例是
`eda_result`，任务推导是 `software_inference`。Gate 7C 的完整迁移证据见
[源极退化共源小信号迁移](docs/validation/2026-07-24-source-degenerated-small-signal-migration-live.md)。

查看能力目录并生成计划：

```powershell
.\.venv\Scripts\vda.exe catalog
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-close-loop.demo.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-dc-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-create-parameter-surface.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-apply-instance-parameters.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-add-source-degeneration.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-source-degeneration-dc-op.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-source-degeneration-dc-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-source-degeneration-inspect.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-ac-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-source-degeneration-ac-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-ac-bias-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-source-degeneration-ac-bias-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-ac-design-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-ac-tune.demo.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-linearity-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-noise-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-tune.demo.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-bias-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-bias-load-budget.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-bias-load-infeasible.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-design-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-design-tune-infeasible.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-linearity-priority.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-length-vdd-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-pvt-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-quality-pvt-bias-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\common-source-raw-fingers-ac-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-create.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-apply.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-apply-instance-parameters.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-bias-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-design-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-design-budget.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-design-infeasible.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-dc-close-loop.demo.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-ac-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-cmrr-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-common-mode-range.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-common-mode-upper-edge.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-linearity-verify.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-ac-tail-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-create.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-transform.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-dc-bias-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-width-checkpoint.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-ac-cmrr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-icmr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-linearity.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-tail-noise.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-create.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-tail-transform.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-add.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-dc.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-ac-cmrr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-icmr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-linearity.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-noise.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-resistance-linearity-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-remove.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-degenerated-restored-dc.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-create.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-tail.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-transform.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-inspect.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-dc.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-ac.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-icmr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-linearity.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-noise.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-psrr.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-psrr-bias-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-ac-bias-load-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-ac-geometry-budget.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-ac-geometry-tune.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-ac-geometry-infeasible.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\differential-pair-current-mirror-restore.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-prepare.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-capture.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-variables-apply.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-scoped-variables-apply.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-setup-apply.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-run.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-sweep-create.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-sweep-run.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-quality-outputs.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-quality-run.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-corners.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-scoped-variables.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-use-test-cl.bridge.json
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-ade-corner-quality-run.bridge.json
```

计划会打印确认 token。复制该 token 后运行离线闭环：

```powershell
.\.venv\Scripts\vda.exe run examples\tasks\inverter-close-loop.demo.json `
  --adapter demo --execute --token <PLAN_TOKEN>
```

离线结果写入 `artifacts/runs/`，不会连接远端，也不会修改 Virtuoso。

## 接入真实 Bridge

已知本机 Bridge Python 默认路径：

```text
C:\Users\aknigsesl\tools\virtuoso-bridge-lite\.venv\Scripts\python.exe
```

当前本地补丁能在没有可用 tunnel 时自动 warm；仍建议先核对 Bridge 分支/提交和状态，再做只读探测：

```powershell
C:\Users\aknigsesl\tools\virtuoso-bridge-lite\.venv\Scripts\virtuoso-bridge.exe status
.\.venv\Scripts\vda.exe doctor --adapter bridge
```

VDA 的 Bridge adapter 不会为每次请求启动 PowerShell。主进程直接启动 Bridge 虚拟环境中的 `python.exe -m virtuoso_design_agent.adapters.bridge_worker`，worker 再复用 Bridge 的原生 `ssh.exe`/`scp.exe`/`tar.exe` 路径。Windows worker 以隐藏窗口运行，并绑定请求专属 Job Object；正常返回先让 worker 完成 Bridge client/session `close()` 和结构化结果，再清空该 Job 中仍存活的 helper descendant，失败、超时、用户中断或调用方消失也会先用 cancel marker/父进程 watchdog 展开清理，再以 Job Object 收敛本地 worker 子树。由 `vda bridge start` 建立的一条共享 SSH tunnel/jump chain 位于独立生命周期边界，会有意跨请求保留到显式 `vda bridge stop`，用固定进程数换取连接复用；它不是每次请求新增的 PowerShell 或无限增长的 session。2026-07-31 真实复核中，资源盘点前后共享 SSH PID 不变，`bridge stop` 后 SSH 与 VDA/Bridge Python 进程均为 0。

direct `si`/Spectre 路径还会在每个唯一 `/data/xum/.../vda_<task>_<nonce>/` 根下安装并回读 SHA-256 匹配的 `vda_spectre_guard.sh`。远端 Spectre 受任务 timeout 和二级 TERM/KILL 限制，即使本地 SSH/worker 断开也不会无限运行；Bridge 等待预算比远端仿真预算多 15 秒，给远端清理留出窗口。Spectre 子运行目录被约束在该 VDA root 内，成功下载后由 Bridge 清理；`si` 网表、wrapper、guard 与失败诊断仍作为持久证据保留，不会被当作临时垃圾自动删除。ADE/Maestro 后台 session 现在也经过真实 timeout 故障注入：cancel marker/父进程 watchdog 先让 Python `finally` 恢复 runtime path 并关闭 session，30 秒后仍未退出才由 Job Object/进程组强制清理本地树。

2026-07-25 的真实资源审计覆盖重复只读 Bridge 调用、真实后代进程超时、用户中断、调用方消失、SSH 超时后的远端孤儿探针、最小 guarded Spectre、现有差分对单点 DC 和既有 Maestro view 的 hard-timeout session 清理。`vda resources [--remote]` 只读报告本地 temp、持久 evidence、远端 EDA 进程/Maestro session 与 age/size review candidate；`--pin-manifest` 只接受精确路径并只阻止列入 review，不授权删除。示例见 `examples/resource-retention-pins.example.json`。详情见[首轮 direct 生命周期审计](docs/validation/2026-07-25-process-resource-lifecycle.md)和[资源取消/retention follow-up](docs/validation/2026-07-25-resource-cancellation-retention.md)。

真实任务仍必须先 `plan`，再使用同一个 token 执行。任务文件还要显式允许远端计算或写入。默认 profile `nics4304_tsmc28` 的反相器 transient 与共源 DC OP 均已有 OA/`si` 单点和有限搜索 live 证据，包括逐候选暂存、最佳参数提交、不可行/预算耗尽恢复与 checkpoint；共源 nominal/退化 AC、相干 transient 线性度和 ordinary noise PSF 也已有只读 live 证据，专用 cell 的 W/RD/RS `design.tune` 已真实执行。调优默认在 run record 旁生成 `*.checkpoint.json`；Bridge 外部恢复后用 `--resume <checkpoint>` 续跑。恢复会重新核对 task、plan token、adapter 和当前 OA 参数，已完成 checkpoint 或不属于基线/已确认写入/待确认写入/声明候选的 OA 状态都会被拒绝。换 library、cell 模板、PDK、analysis 或服务器也必须重新验证，不能从既有 smoke 外推。

```powershell
.\.venv\Scripts\vda.exe run examples\tasks\inverter-tune-energy.bridge.json `
  --adapter bridge --execute --token <PLAN_TOKEN>

# Bridge 按自身流程恢复后，使用上次打印的 checkpoint：
.\.venv\Scripts\vda.exe run examples\tasks\inverter-tune-energy.bridge.json `
  --adapter bridge --execute --token <SAME_PLAN_TOKEN> `
  --resume artifacts\runs\...\run-....checkpoint.json
```

`simulation.run` 不写 OA：省略器件尺寸时直接采用目标 OA 回读值；如果任务显式给出尺寸，则必须与 OA 一致，否则停止，不会用请求值覆盖 schematic。包含 OA 设计参数的 `design.tune` 和 `design.close_loop` 会在已授权写入的前提下逐点暂存参数并回读；无可行候选或可恢复中断时恢复搜索前参数。该暂存行为会明确出现在计划和 run record 中。

人工 ADE 交接先用可选的 `ade.prepare`，再用 `ade.capture`。两者的 `target.view` 都必须为 `maestro`。`prepare` 需要 OA 写授权、library 白名单和 cell 前缀，只创建一个新 Maestro view/test；若目标已经存在则失败，因此不会覆盖人工状态。`capture` 默认要求 setup 已保存且存在非空 EDA result artifacts；可用 `ade_capture.history` 固定某个 `Interactive.N` 等 history，并用 `require_structured_outputs: true` 要求 ADE Detail output/spec 表可读。捕获前由用户自己打开、调整、运行、保存并聚焦目标窗口；VDA 不会抢焦点或修改它。setup 标为 `bridge_readback`，网表、PSF、Spectre log 和结构化 output/spec 标为 `eda_result`，自动选择最新 history 标为 `software_inference`，显式 history 标为 `user_input`。

不需要人工窗口的已保存 setup 可用 `ade.run`。它要求 `target.view: "maestro"`、`ade_run` 设置和 `allow_remote_compute: true`，但不要求 `allow_remote_write`；worker 新开 background session，回读 tests，运行 setup，并用本次调用返回的 history 读取逐点 output/spec。默认同时启用 `require_structured_outputs: true` 和 `require_artifact_manifest: true`：后者从 Cadence 暴露的 library/analog-run 路径推导 project 与 scratch Maestro 根，只枚举确切 history 下的核心 `netlist`、`input.scs`、PSF/结果、Spectre log 及同名 `.rdb/.msg.db`，用远端 `sha256sum` 生成小型清单，再经 Bridge 公共下载接口读回。核心网表、非空结果或日志缺失，路径逃逸，或者 project/scratch 同一逻辑文件内容冲突都会失败；显式关闭任一要求且证据缺失时 run record 只能是 `partial`。远端清单保留在 profile 的 `/data/xum` run root 下，本操作不下载完整波形、不配置/保存 setup、不写 OA。精确 history 路径绑定不等于名称唯一：命名/覆盖策略仍由已保存 setup 决定，VDA 当前不能在运行前证明该名称不存在。单点 nics4304 live 已覆盖 OA 到真实 `input.scs` 的 raw 参数核对；原生 CL、VDD×CL 和 test-scope CL×VDD-corner sweep 又覆盖 `input.scs` 显式 include 的 sibling `netlist` 输入束、exact-history RDB/log、global selection 与逐点/逐 corner 有效值。可选 `result_mapping` 在 strict sweep 之上要求一个 exact test、全部 sweep variable 的参数映射，以及每个 metric 的 exact output、预期 expression、scale/unit；同一个 sweep variable 可以绑定多个 OA 参数，例如 `VDD0.vdc` 与 `VIN0.v2`。worker 固定保存的 output state，executor 才把有限 RDB scalar 映射到 constraints/objective。缺失、空、非有限值、表达式漂移或全不可行都不会产生 selected point。单 test 环境 VDD corner/test-scope quality mapping 已 live；真实 process/temperature corner、multi-test mapping 和共源 L/VDD 联合搜索仍是后续 Gate。人工相关验证见 `docs/deferred-manual-gates.md`。

上一段“后续 Gate”专指已保存 Maestro/ADE setup。direct common-source `si`/Spectre 路径已经完成 L/VDD 搜索和固定设计 TT/SS/FF；两条状态不混为同一仿真真源。

需要证明原生 parametric sweep 真正进入仿真时，可在 `ade_run.sweep_verification` 中额外声明 exact tests/corners、各 scope 保存的逗号变量值、连续 expected points，以及每个 test/variable 对应的 `instance.oa_parameter`。该可选严格门强制同时打开 structured outputs、artifact manifest 和 simulator input consistency，并在运行前后精确回读 setup。若 Cadence 保留任意逐点目录，VDA 坚持每个 point/test 都有完整 `input.scs` 和结果；若完全没有逐点目录，只接受已 live 验证的 IC6.1.8 模式：每个 test 一个已哈希且有显式 include 关系的 `input.scs`+`netlist` 符号输入束、OA/Spectre 变量引用、一个 exact-history RDB、完成点数精确的 log，以及 RDB 中每点匹配变量和非空 scalar output。corner 模式再要求 exact global selection、scope precedence、真实 Maestro point 数与完整 raw Detail corner grid。默认要求 log 零错误；只有 `expected_output_evaluation_errors` 精确声明的未映射 legacy calculator 单元格才可逐点计入，实际 RDB `eval err` 集合、log 数量和 executor 重算必须完全一致，任何未解释错误仍失败。数据库模式明确记录 `exact_point_input_result_binding_verified=false`，不会虚构逐点文件。任务预期属于 `user_input`，setup/OA 属于 `bridge_readback`，input/RDB/log 属于 `eda_result`，对应与哈希聚合属于 `software_inference`。未声明该字段的普通 `ade.run` 不受限制。2026-07-22 nics4304 live 已从 global `CL=1f,2f,4f` 三点扩展到 global `VDD=0.8,0.9` 的六点二维 Gate，又闭合 test-scope CL × 三列 environmental-corner raw result grid。详见 `examples/tasks/inverter-ade-sweep-*.bridge.json`、`examples/tasks/inverter-ade-vdd-*.bridge.json`、[二维真实验证记录](docs/validation/2026-07-22-ade-inverter-vdd-cl-live.md)和[scoped-corner 真实验证记录](docs/validation/2026-07-22-ade-inverter-scoped-corner-live.md)。

自动修改 design variable 使用 `ade.variables.apply`。任务必须列出 `expected_tests`；使用 corner scope 时还必须列出 exact `expected_corners`。每个 value update 给出 `name`、显式 `expected_value`、`value`，以及可选的 `scope: global|test|corner`/`scope_name`；`expected_value: null` 表示要求变量在该 scope 不存在。同名变量可分别出现在不同 scope，但同一 scope 不能重复。`global_selection_updates` 则用 `expected_enabled → enabled` 对 global-variable 选择做 CAS，并在写前、即时和独立重开阶段比较完整 enabled/disabled 集合，拒绝未声明名称漂移。任何已配置 Maestro session 已打开时都会保守拒绝。该 operation 需要完整 OA-write 授权，只在所有 tests/corners/旧值/selection 匹配后逐项写入，保存一次 setup，再用全新 session 逐 scope 回读；不运行仿真或修改 schematic/test/analysis/output/corner membership。逗号列表只是该 scope 的 sweep 声明，必须由后续 strict `ade.run` 证明真正进入 simulator。

新增 enabled corner 使用独立 `ade.corners.apply`。它要求 exact tests 与旧 corner 有序列表，且只允许在末尾新增明确不存在的命名 corner；写后立即回读、保存一次、独立重开复核，不删除、改名或替换 corner，也不配置 model files、temperature 或 process section。反相器 live 中的 `VDA_LOW_VDD/VDA_NOMINAL_VDD` 仅承载 VDD scope override，仍使用 nominal `top_tt`，不能称为 PVT/process corner。

自动微调 analysis/output 使用 `ade.setup.apply`。任务必须列出 exact `expected_tests`；每个 analysis update 给出目标 test/analysis、完整旧 `enabled/options`（不存在时为 `null`）、目标 enabled 和 option delta。output 只支持新增：命名 net output 要求 `signal_name`，point output 要求 calculator `expression`，可附 `lt`/`gt` spec。worker 在任何 writer 前读取全部旧 analysis 并确认所有 output 名不存在；任一不符零写入。写后逐项结构化回读，全部一致才保存一次，再用全新 session 核对持久化状态。它复用 Bridge public writer 和 SKILL channel，没有修改 Bridge；但首版不替换已有 output，也只接受可精确回读的扁平 analysis option。成功只证明 setup 配置持久化为 `bridge_readback`，不代表 analysis 已运行或 output 已产生 `eda_result`。

共源任务省略 `analysis` 时保持向后兼容的 `dc`。AC 必须显式设置 `analysis: "ac"` 与 `ac_sweep`；线性度使用 `analysis: "transient"` 与 `linearity_sweep`；普通噪声使用 `analysis: "noise"` 与 `noise_sweep`。固定质量组合使用 `analysis: "quality"`，并强制同时声明上述三种 sweep；任一子分析不完整、参数不一致或共享 DC 指标不一致都会拒绝整个候选。worker 只做一次 OA 回读与 `si` 网表生成，再从同一网表分别运行三种 Spectre wrapper；组合逻辑标为 `software_inference`，连续指标仍保持 `eda_result`。所有显式和默认 sweep 字段都进入 token 与证据。线性度在一个 Spectre nested sweep 中运行按幅度递增的相干正弦，P1dB 未被声明范围包围时只报告 unresolved；noise 对 Bridge 已下载的普通 noise PSF 做频带积分，不把 AC 或 transient 数据包装成噪声。`load_ff` 是动态分析的可选 testbench 负载，不写 OA。若 `design.tune` 的搜索维度只有 `bias_v/vdd_v/load_ff` 这类 testbench 条件，计划和 executor 不要求或执行 OA 写入；若搜索包含 W/L/RD/RS，则仍逐候选写入、回读、checkpoint，并只提交最佳可行 OA 参数。`operating_conditions` 是 common-source 的显式可选有限验证集合，可用于 `simulation.run`、`design.tune` 或 `design.close_loop`；省略时仍是原有单条件流程。启用后每个候选只暂存一次 OA、生成一份已核对 `si` 网表，再跨所有条件运行；每个条件必须独立完整并通过，objective 取保守最坏值。逐条件 VDD 与候选 `vdd_v` 不能同时声明；若条件省略 VDD，则共同继承该候选 VDD。

差分对电源抑制使用显式 `analysis: "psrr"` 与 `ac_sweep`。每个候选只回读一次 OA、生成并核对一份 `si` 网表，再依次运行 1 V 平衡差模、1 V VDD AC 注入和 1 V VSS AC 注入；供电注入时 INP、INN 和 BIAS 保持对地 DC 参考。三次 DC 工作点、频率网格和网表 SHA 必须一致。固定输出契约下计算 `PSRR+=|Ad/Avdd|` 与 `PSRR-=|Ad/Avss|`，同时保留 supply-to-output gain、低频 PSRR、扫频最差 PSRR、声明评估频带内最差值和首次下降 3 dB 频点。每份根 `ac.ac` 在临时文件清理前记录字节数与 SHA-256。波形和 OP 属于 `eda_result`，OA 属于 `bridge_readback`，比值、交点与一致性判断属于 `software_inference`。首版 worker 要求真实 OA 尾管，拒绝用理想尾源 wrapper 代替 VSS 敏感电路。2026-07-24 nominal active-load 单点和四点只读搜索均已完成；四点 `analysis_complete=true` 且 OA 前后不变，但临时 `20 dB` 带限门全不可行。

人工指定实例参数时使用 `instance_parameter_updates`，例如 `MN0.fingers="2"` 或 `RD0.r="22k"`。VDA 保留原始字符串，不猜单位、别名、枚举或布尔编码，也不因通用 reader 对空值/长值的摘要策略而提前拒绝 Bridge 可接受的请求；写后改用独立的目标 CDF 值相等检查。该固定写入可用于独立 `parameters.apply`，也可与模板 semantic 参数或显式 raw sweep 组合；请求标为 `user_input`，真实 OA 确认标为 `bridge_readback`，demo 结果仍只标为 `software_inference`。首次不一致时至多按声明顺序重放一次，计划会明确披露；仍不一致则失败。CDF 的 `editable`/`display` 元数据只作诊断，不能作为 allowlist，因为真实 smoke 已出现 `RD0.r` 报告不可编辑但能持久化的情况。CDF callback 引起的其他参数联动会保留在完整 Bridge 回读里，但只有任务明确请求且真实保持的字段会宣称确认。有限调优另用 `instance_parameter_space` 显式列出实际 CDF 字段及有限字符串值；它不会把全部可读字段自动纳入搜索，也不会收窄独立 `parameters.apply` 的 Bridge 别名能力。

2026-07-20 的专用 `vda_param_surface_001` live smoke 已闭合 `MN0.fingers=2` 和 `RD0.r=22K` 的 callback、立即 OA 回读和独立再次回读。相同任务中的 `MN0.m=2` 被 PDK callback 恢复为 `1`，因此保留为失败边界；这说明“VDA 能尝试 Bridge 参数”不等于“每个 PDK CDF 字段都可物理持久化”。多字段写入不是 OA 事务，失败可能留下已保存的前缀字段。

源极退化的增量实现不会重置未点名参数：共源 semantic 写入只向 Bridge 发送任务实际包含的 `W/L/RD/RS` 字段，不再附带 `fingers=1` 或 `m=1`。transform 强制用 append mode 打开已有 cellview，拒绝已有未保存改动，编辑 batch 失败时 purge 本次未保存缓存；前后独立回读再逐项核对 MN0/RD0 的完整参数、master、位置、pins 和 nets。add 只允许 MN0.S 改接 NSRC、增加 RS0/NSRC 和设置 RS0.r；remove 只接受无参数的显式动作，并几何唯一选择 RS0 两条 VDA stub 后删除。两者均幂等。remove 可绑定 add 前由 Bridge 回读的实例/pin/标签/导线 placement SHA-256，保存后不一致即失败。

`vda_param_surface_001` 和 `vda_cs_ac_tradeoff_001` 已依次覆盖同一 cellview 原位退化、DC、nominal/退化 AC、bias/load、W/RD/RS、线性度/功耗/noise 和三分析质量调优；多次 transport 失败都保留为 `system_event` 并经 OA readback/checkpoint 恢复。2026-07-22 闭合 L/VDD 四点写回和固定设计三条件 PVT 验证；2026-07-23 又闭合不写 OA 的两候选 PVT-aware bias 搜索，并在独立新 cell 上完成 source degeneration add→DC/AC→remove 的可逆 Gate。恢复后 placement、`si` 网表 SHA-256 及 17 个 DC/28 个 AC 指标均与 add 前完全一致；随后 `MN0.fingers=1/2` 两点又闭合 callback、定向回读、不同同源网表、GBW 选优、最佳写回和首候选 transport 恢复。尚未 live 闭合的是 OA 设计变量跨 PVT 写回、复杂多字段 callback 联合搜索和更复杂拓扑。完整 live 证据见 `docs/validation/` 下对应记录。

## 安全模型

真实写入需同时满足：

1. CLI 提供 `--execute`。
2. CLI 提供与当前任务和计划一致的 token。
3. 任务设置 `allow_remote_write: true`。
4. `target.library` 等于任务声明的 `allowed_library`。
5. cell 名满足 `required_cell_prefix`，默认 `vda_`。
6. 默认 `replace_existing: false`。

仿真虽不修改 OA，也会创建远端 scratch，因此需要 `allow_remote_compute: true`。密码、Bridge `.env` 和 license 内容不进入任务或 run record。

自动 netlisting 产物按 profile 写在 `/data/xum/virtuoso_bridge_smoke/vda_<task>_<nonce>/`，保留 `si.env`、`si` 日志、结构网表和 wrapper 供审计。run record 保存路径、SHA-256、参数一致性和波形指标，不以 return code 0 或文件存在单独判定成功。

## 为什么暂不做成 Skill

当前先把稳定能力做成普通本地工具：契约、状态机、执行边界和证据格式都可以独立测试。Codex 可以调用 CLI 充当上层 agent；当命令和边界稳定后，再决定是否封装成 Skill 或 MCP。这样不会把尚未稳定的实验流程固化成提示词约定。

## 文档

- [L5 路线与定义](docs/l5-roadmap.md)
- [系统架构](docs/architecture.md)
- [三类起步电路与验收门](docs/initial-circuits.md)
- [首个决策记录](docs/decisions/0001-l5a-first.md)
- [PDK 默认使用晶圆厂 CMOS 的决策](docs/decisions/0002-foundry-cmos-pdk-default.md)
- [2026-07-19 反相器 L5A smoke](docs/validation/2026-07-19-inverter-l5a-smoke.md)
- [2026-07-27 standalone Spectre 轻量拓扑预评估本地 Gate](docs/validation/2026-07-27-standalone-netlist-preview-local.md)
- [2026-07-27 standalone Spectre 轻量拓扑预评估 live Gate](docs/validation/2026-07-27-standalone-netlist-preview-live.md)
- [2026-07-27 候选域到 netlist preview 的确定性编译本地 Gate](docs/validation/2026-07-27-preview-candidate-compiler-local.md)
- [2026-07-27 standalone preview 九点预筛与 OA 参考校准 live Gate](docs/validation/2026-07-27-preview-candidate-selection-live.md)
- [2026-07-27 preview shortlist 到 OA 同源复核与耗时 live Gate](docs/validation/2026-07-27-preview-shortlist-oa-handoff-live.md)
- [2026-07-28 差分对未见候选域 prospective preview shortlist live Gate](docs/validation/2026-07-28-differential-pair-preview-prospective-live.md)
- [2026-07-28 用户拓扑 design-context 本地纵切](docs/validation/2026-07-28-user-topology-design-context-local.md)
- [2026-07-28 existing-schematic 通用 DC/AC 本地 Gate](docs/validation/2026-07-28-existing-schematic-generic-dc-ac-local.md)
- [2026-07-28 existing-schematic 通用 DC/AC live Gate](docs/validation/2026-07-28-existing-schematic-generic-dc-ac-live.md)
- [2026-07-28 existing-schematic 通用有限实例参数闭环本地 Gate](docs/validation/2026-07-28-existing-schematic-generic-tuning-local.md)
- [2026-07-31 existing-schematic 拓扑与参数联合闭环本地 Gate](docs/validation/2026-07-31-existing-schematic-topology-parameter-close-loop-local.md)
- [2026-07-31 existing-schematic 拓扑与参数联合闭环真实 Gate](docs/validation/2026-07-31-existing-schematic-topology-parameter-close-loop-live.md)
- [2026-07-31 existing-schematic staged multi-analysis 真实 Gate](docs/validation/2026-07-31-existing-schematic-staged-multi-analysis-live.md)
- [2026-07-31 winner-only PVT、多拓扑与一层 hierarchy Gate](docs/validation/2026-07-31-existing-schematic-winner-verification-multi-topology-hierarchy.md)
- [2026-07-31 existing-schematic 多 topology alternative 真实闭环 Gate](docs/validation/2026-07-31-existing-schematic-multi-alternative-live.md)
- [2026-07-31 existing-schematic 一层 child 参数调优真实 Gate](docs/validation/2026-07-31-existing-schematic-hierarchical-parameter-tuning-live.md)
- [2026-07-31 existing-schematic 自动接入编译器本地 Gate](docs/validation/2026-07-31-existing-schematic-onboarding-local.md)
- [2026-07-31 existing-schematic resolution→TaskSpec 本地 Gate](docs/validation/2026-07-31-existing-schematic-onboarding-resolution-local.md)
- [2026-07-31 PMOS 有源负载共源级自动接入与联合微调真实 Gate](docs/validation/2026-07-31-pmos-loaded-common-source-onboarding-live.md)
- [2026-07-31 onboarding topology refinement 与 winner-only quality 编译本地 Gate](docs/validation/2026-07-31-onboarding-refinement-resolution-local.md)
- [2026-07-31 onboarding post-refinement CDF promotion 编译本地 Gate](docs/validation/2026-07-31-onboarding-post-refinement-promotion-local.md)
- [2026-07-31 existing-schematic CDF→si binding 自动发现本地 Gate](docs/validation/2026-07-31-existing-schematic-parameter-binding-discovery-local.md)
- [2026-07-19 共源放大器 Gate 2A DC smoke](docs/validation/2026-07-19-common-source-gate2a-dc-smoke.md)
- [2026-07-19 显式实例参数能力验证](docs/validation/2026-07-19-explicit-instance-parameters.md)
- [2026-07-20 源极退化原位变更实现验证](docs/validation/2026-07-20-source-degeneration-in-place.md)
- [2026-07-20 源极退化原位微调与同源 DC 真实验证](docs/validation/2026-07-20-source-degeneration-live.md)
- [2026-07-23 共源源极退化可逆拓扑微调真实验证](docs/validation/2026-07-23-common-source-reversible-topology-live.md)
- [2026-07-20 共源复数 AC 指标与调优能力实现](docs/validation/2026-07-20-common-source-ac-implementation.md)
- [2026-07-20 共源与源极退化只读同源 AC 真实验证](docs/validation/2026-07-20-common-source-ac-live.md)
- [2026-07-20 共源 AC 控制变量与 W/RD/RS 真实调优](docs/validation/2026-07-20-common-source-ac-design-tuning-live.md)
- [2026-07-20 共源功耗、线性度与 noise 实现](docs/validation/2026-07-20-common-source-quality-local.md)
- [2026-07-20 共源功耗、线性度与 noise 只读真实验证](docs/validation/2026-07-20-common-source-quality-live.md)
- [2026-07-21 共源多 analysis 质量组合本地验证](docs/validation/2026-07-21-common-source-quality-bundle-local.md)
- [2026-07-21 共源多 analysis 质量组合真实验证](docs/validation/2026-07-21-common-source-quality-bundle-live.md)
- [2026-07-21 共源质量驱动设计参数写回真实验证](docs/validation/2026-07-21-common-source-quality-design-tuning-live.md)
- [2026-07-21 ADE 双向人工交接本地实现](docs/validation/2026-07-21-ade-human-handoff-local.md)
- [2026-07-21 ADE 后台运行与结果回收本地实现](docs/validation/2026-07-21-ade-background-run-local.md)
- [2026-07-21 ADE background exact-history 产物清单本地实现](docs/validation/2026-07-21-ade-background-artifact-manifest-local.md)
- [2026-07-21 Maestro 全局变量 CAS patch 本地实现](docs/validation/2026-07-21-ade-variable-patch-local.md)
- [2026-07-21 Maestro scoped 变量 CAS 扩展本地实现](docs/validation/2026-07-21-ade-scoped-variable-patch-local.md)
- [2026-07-21 Maestro analysis/output setup patch 本地实现](docs/validation/2026-07-21-ade-setup-patch-local.md)
- [2026-07-22 ADE 原生 sweep 逐点同源证据本地实现](docs/validation/2026-07-22-ade-native-sweep-consistency-local.md)
- [2026-07-22 ADE 原生 CL sweep 同源闭环真实验证](docs/validation/2026-07-22-ade-native-sweep-consistency-live.md)
- [2026-07-22 ADE 反相器 delay/skew/供电能量 constraints 真实验证](docs/validation/2026-07-22-ade-inverter-quality-constraints-live.md)
- [2026-07-22 ADE 反相器 VDD×CL 二维质量 Gate](docs/validation/2026-07-22-ade-inverter-vdd-cl-live.md)
- [2026-07-22 ADE 反相器 scoped-variable × environmental-corner Gate](docs/validation/2026-07-22-ade-inverter-scoped-corner-live.md)
- [2026-07-22 共源 L/VDD 质量调优与真实 PVT Gate](docs/validation/2026-07-22-common-source-length-vdd-pvt-live.md)
- [2026-07-23 共源可选 PVT-aware 调优 live Gate](docs/validation/2026-07-23-common-source-optional-pvt-tuning-live.md)
- [2026-07-23 差分对 Gate 3 同源 DC/AC/CMRR/线性度真实验证](docs/validation/2026-07-23-differential-pair-gate3-live.md)
- [2026-07-23 差分对 Gate 4 真实尾管多分析同源验证](docs/validation/2026-07-23-differential-pair-real-tail-gate4-live.md)
- [2026-07-23 差分对 Gate 5 对称源极退化可逆全分析验证](docs/validation/2026-07-23-differential-pair-source-degeneration-gate5-live.md)
- [2026-07-23 差分对 Gate 6 PMOS 电流镜负载本地实现](docs/validation/2026-07-23-differential-pair-current-mirror-gate6-local.md)
- [2026-07-23 差分对 Gate 6 PMOS 电流镜负载同源闭环真实验证](docs/validation/2026-07-23-differential-pair-current-mirror-gate6-live.md)
- [2026-07-23 差分对 PSRR 三次同网表 AC 本地实现](docs/validation/2026-07-23-differential-pair-psrr-local.md)
- [2026-07-24 差分对 PSRR 三次同网表 AC 真实单点](docs/validation/2026-07-24-differential-pair-psrr-live.md)
- [2026-07-24 差分对带限 PSRR 四点搜索与 transport 恢复](docs/validation/2026-07-24-differential-pair-psrr-search-live.md)
- [2026-07-24 theory-first gm/Id 尺寸分析本地 Gate](docs/validation/2026-07-24-theory-first-sizing-local.md)
- [2026-07-24 Gate 6 theory 一阶模型真实校准](docs/validation/2026-07-24-theory-calibration-live.md)
- [2026-07-24 通用 MOS 小信号网络本地 Gate](docs/validation/2026-07-24-generic-small-signal-local.md)
- [2026-07-24 TSMC N28 独立 MOS characterization 真实 Gate](docs/validation/2026-07-24-tsmc28-mos-characterization-live.md)
- [2026-07-24 Gate 7B 共源小信号真实验证](docs/validation/2026-07-24-common-source-small-signal-validation-live.md)
- [2026-07-24 Gate 7C 源极退化共源小信号迁移](docs/validation/2026-07-24-source-degenerated-small-signal-migration-live.md)
- [2026-07-24 反相器驱动比例校准 live Gate](docs/validation/2026-07-24-inverter-drive-ratio-calibration-live.md)
- [2026-07-25 进程与资源生命周期审计](docs/validation/2026-07-25-process-resource-lifecycle.md)
- [2026-07-25 资源取消、盘点与保留策略 follow-up Gate](docs/validation/2026-07-25-resource-cancellation-retention.md)
- [2026-07-25 反相器 Wp/Wn 细化 live Gate](docs/validation/2026-07-25-inverter-ratio-refinement-live.md)
- [2026-07-25 差分对 theory-seeded Gate 8 live](docs/validation/2026-07-25-differential-pair-theory-seeded-gate8-live.md)
- [2026-07-25 原子候选与真实工作点局部重线性化本地 Gate](docs/validation/2026-07-25-atomic-candidate-op-relinearization-local.md)
- [2026-07-26 通用 topology-delta 可逆契约本地 Gate](docs/validation/2026-07-26-generic-topology-delta-local.md)
- [2026-07-26 通用 topology-delta 新 cellview 真实 Gate](docs/validation/2026-07-26-generic-topology-delta-live.md)
- [2026-07-26 topology post-save 恢复与 master/CDF 迁移本地 Gate](docs/validation/2026-07-26-topology-recovery-master-migration-local.md)
- [2026-07-26 topology master/CDF 迁移真实同源 Gate](docs/validation/2026-07-26-topology-master-migration-live.md)
- [2026-07-26 共栅微调与 topology recovery 本地 Gate](docs/validation/2026-07-26-common-source-cascode-and-topology-recovery-local.md)
- [2026-07-26 共栅微调与 topology recovery live Gate](docs/validation/2026-07-26-common-source-cascode-and-topology-recovery-live.md)
- [2026-07-26 有源负载差分对与对称源退化组合本地 Gate](docs/validation/2026-07-26-differential-pair-active-source-degeneration-local.md)
- [2026-07-26 有源负载差分对与对称源退化组合 live Gate](docs/validation/2026-07-26-differential-pair-active-source-degeneration-live.md)
- [2026-07-28 existing-schematic 通用有限实例参数闭环本地 Gate](docs/validation/2026-07-28-existing-schematic-generic-tuning-local.md)
- [2026-07-29 existing-schematic 通用有限实例参数闭环真实 Gate](docs/validation/2026-07-29-existing-schematic-generic-tuning-live.md)
- [延期的人工 ADE Gate](docs/deferred-manual-gates.md)
