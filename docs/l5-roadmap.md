# L5 路线与工作定义

这里的 L5A/L5B/L5C 是本项目内部的工程分级，不宣称是行业标准。分级重点不是“是否能调用 Virtuoso”，而是工具能否围绕目标形成有边界、有证据、能恢复的执行闭环。

## L5A：受限自主闭环（当前）

输入是已知 PDK、固定电路模板、明确规格、参数搜索空间和最大迭代次数。系统可以：

1. 生成可审查计划。
2. 在明确授权后建图或读取已有图。
3. 运行有限候选点仿真。
4. 从波形提取指标并逐条判规格。
5. 选择当前搜索边界内的最佳候选。
6. 将参数应用回 OA，并结构化回读。
7. 保存证据和失败原因。
8. 在显式交接点捕获人工 ADE setup/history 与真实仿真结果，不覆盖人工状态。

此外，人工可以通过实例名、原始 CDF 参数名和值字符串要求一次独立参数写入；VDA 必须保留请求、写入前值、立即 OA 回读和独立再次回读。该能力不等于系统已经知道任意参数的物理含义，也不自动扩大优化搜索空间。

L5A 不允许自行发明无限搜索范围、修改 PDK、覆盖未知 cell、把 demo 模型当 EDA 结果，或在缺少证据时宣布 closure。

为降低新拓扑早期评估成本，L5A 现增加可选 `netlist_preview` 层：理论/KCL/gm-Id 先给出
少量具体候选，再由受校验结构直接生成 standalone foundry Spectre DC/AC deck。该层不
启动 Virtuoso、`si` 或 Maestro，不创建/修改 OA，只把 raw simulator 结果记为
`eda_result`、A/B 差值记为 `software_inference`。它只负责淘汰明显差的结构；最终候选
仍须进入 OA→`si`→Spectre 或 ADE。契约、planner、worker mock、空波形和无 OA executor
路径已本地通过；2026-07-27 nics4304 live smoke 又闭合两份 241 点 AC、DC OP、逐文件
hash manifest 和进程归零。级联/共源的 gain/BW/GBW A/B 方向与既有 OA→`si` 一致，
但绝对数值误差最高超过 20%，所以状态只升级为 standalone directional preview live，
不能用该层宣称 schematic-driven 性能或设计闭环。

2026-07-27 又补齐理论候选到该预评估层的本地交接：
`vda preview-task-from-candidates` 复用现有 `candidate_set`/`theory_seed`，按 policy 显式列出的
ID 和顺序把候选 semantic 参数映射到一个 typed graph placeholder。它拒绝来源/PDK
漂移、参数遗漏、固定值漂移、raw CDF 更新、重复目标和含糊的 placeholder objective，
并把 source/policy/template hash 与 `variant -> candidate ID` 写进输出。既有 9 点真实
cascode seed 已确定性编译成 1 个固定共源基线 + 9 个共栅候选并通过 plan；本 Gate 没有
连接 Bridge、运行远端计算或写 OA。获单独批准后，下一道 Gate 已完成 10/10 份真实
Spectre preview、逐 deck 重渲染与 manifest/候选身份审计，并用新增 `vda preview-select`
对照既有 OA→`si` 九点离散域。preview top-3=`009/007/003`，参考 top-3=`009/003/007`，
Spearman ρ=`0.9333`，两侧 winner 都是 `009`；gain/BW/GBW/power 最大误差分别为
`4.39%/11.04%/10.13%/19.45%`。因此该同拓扑预筛可把后续 OA 真值计算由 9 点缩到 3 点，
但 policy 明确标为 `retrospective_calibration`：下一未见拓扑仍需 prospective 验证，不能
把本次排序或误差门槛直接外推。全过程无 OA target/write，执行后 Spectre/si/Maestro
均归零，Bridge tunnel 已恢复为停止状态。

PDK 路线默认按晶圆厂 CMOS 工艺推进：当前以 TSMC N28 LVT profile 为缺省；同工艺的 `nch_mac/pch_mac` 通过继承式 `nics4304_tsmc28_svt` 显式选择。NMOS 共源的 LVT/SVT OA/`si`/Spectre round-trip 已 live，PMOS 和其他拓扑仍需独立 Gate。后续优先通过独立 profile 接入 TSMC/SMIC 的实际晶体管 PDK。profile 继承只复用静态工艺配置，不复用性能证据。TSV、hybrid-bonding 等封装/3D PDK 不进入普通电路设计的默认路径；若未来需要，将作为显式选择和独立 Gate，而不是当前 profile 的替代品。

当前实现状态：`OA schematic -> si -> Spectre -> metrics`、供电能量积分、失败注入和候选级 checkpoint/resume 已通过本地测试。2026-07-19 live 结果覆盖 OA/`si` 参数一致性、非空 timing/current 波形、收紧规格、不可行 + 预算耗尽恢复，以及一个经历 3 次 tunnel 中断后仍完成 9/9 候选、最佳参数写回和独立 OA 回读的恢复任务。反相器 L5A 的同源有限闭环与显式恢复 Gate 已通过；Bridge 本地隔离补丁又通过强制断链只读 smoke，闭合 Windows stale state 与调用边界自动重建。运行中传输的随机 reset/timeout 仍是跨 Gate 的底层可靠性债务。

2026-07-24 又在隔离的 `vda_inv_ratio_calibration_001` 上固定 `Wn=0.6 µm`，完整比较 `Wp/Wn=1/1.25/1.5/2`，粗网格以 0.212 ps skew 选择 1.25。2026-07-25 follow-up 在相同 nominal `top_tt`、0.9 V、2 fF、5 ps input edge 下完整实测 `1.20/1.25/1.30/1.35`；1.25 的 hash/指标精确复现，1.30 skew 为 0.364 ps，而 1.20 以 0.0394 ps 成为新离散域最佳。checkpoint 还真实恢复了一次 raw 下载 timeout，最终 OA 独立回读为 `Wn/Wp=0.60/0.72 µm`。因此 profile 的简单可覆盖缺省更新为 `0.6 µm + 1.20`；多个 CL/input slew 和可选 PVT 仍待鲁棒性复核。显式任务值和已有 OA 值始终优先。

Gate 2A 又把相同执行语义扩展到电阻负载 NMOS 共源级：新 OA cellview 的 MN0/RD0 结构、W/L/R 回读和 `si` 网表一致性通过；显式保存的 Spectre DC OP 提供 Id/VGS/VDS/VDSAT/gm/gds，6 点 W/Vbias 搜索完成 3 个可行点、3 个线性区点、最佳 W 写回和最终独立紧规格复核。该结果只闭合 common-source nominal DC；当时 AC、source degeneration、noise 和 corner 均未验证。

2026-07-20 已按“微调已有 schematic、避免重建”的目标实现并真实验证源极退化原位 add：同一 cellview 只改 MN0.S 网名并新增 RS0/NSRC，前后审计保护 MN0/RD0、pins、位置和未点名参数；同一个 common-source adapter 随后读写 RS、自动解析退化网表、提取 NSRC DC 指标并完成有限搜索。2026-07-23 又补齐显式 remove：仅删除 RS0 与两条 VDA stub、恢复 MN0.S/VSS，并可把 add 前 placement SHA-256 作为 CAS 基线。新 cell 的 add→DC/AC→remove 一次通过，恢复后的 OA placement、nominal `si` 网表和连续 EDA 指标完全一致；该能力仍是已知拓扑的固定 delta，不是通用图重写。

同日已把共源 AC 做成正式能力，而不是一次性脚本：同一 OA→`si` 网表运行 DC OP 和复数 AC，并提取 gain、首个 −3 dB bandwidth、GBW、unity-gain 与相位。nominal/退化只读 smoke、各 6 点 bias/load 搜索、同参数只加 RS 的控制变量对比，以及 W/RD/RS 预算/不可行/完整 8 点搜索均已真实通过。完整搜索选择并回读 W=1.0 µm、RD=20 kΩ、RS=1 kΩ；多次 transport 中断都在 OA 恢复或独立 readback 后从 checkpoint 继续。状态升级为 **bounded common-source AC design-parameter tuning and recovery verified**。

设计质量分析已完成本地实现和单点 live Gate：实际 `VDD_SRC:p` DC 功耗与独立 KCL、相干 transient 幅度 sweep 的 gain/HD2/HD3/THD/P1dB/平均功耗，以及 ordinary noise 的输出/输入参考频带积分继续复用同一 OA→`si` worker。专用 cell 的 5 点 100 MHz sweep 解析出 `P1dB=88.32 mV peak` 和 150 mV 点 `THD=13.16%`；211 点 1 kHz–10 GHz PSF 得到输出/输入参考积分噪声 3.304/0.983 mV RMS。真实目录形状暴露的 DC info 覆盖和 transient 端点问题已在 VDA 层修正，没有改 Bridge。当前升级为单点 design-quality analysis execution verified，尚未升级为质量驱动的参数/规格闭环。

2026-07-21 已实现并真实验证固定 `analysis: quality` 组合：同一候选只生成一次经 OA/`si` 一致性核对的网表，再分别运行 AC、相干 transient 和 noise；三项指标共同进入原有 constraints/objective、候选预算和 checkpoint 语义。参数或共享 DC 指标不一致会硬失败，任一子分析不完整会拒绝整个候选。4 点 bias/load 搜索得到 2 个可行和 2 个 THD/功耗超限点，最高 GBW 点被质量约束正确拒绝；2/4 预算耗尽、全不可行和真实首候选 transport 中断后的 checkpoint 恢复也已通过，before/after OA 参数完全一致。状态升级为 **read-only multi-analysis common-source quality tuning and recovery verified**；设计参数质量写回和 corner 仍待验证。

随后同一专用 cell 完成 8 点 W/RD/RS 质量搜索：8/8 候选都完成 OA 写入回读、同源 netlist、AC、linearity 和 noise；候选 4 的真实 transport 中断在独立 OA 回读后从 4 恢复，没有重复 1–3。GBW objective 写回 `W=1 µm/RD=20 kΩ/RS=1 kΩ`；全不可行任务恢复该基线且 selection 为空。线性度优先的两点控制任务则自动写回 RS=2 kΩ，使 THD 降低 41.93%、P1dB 提升 37.26%、DC 功耗降低 17.27%，代价是 GBW 降低 29.30% 与输入参考噪声增加 15.03%。状态升级为 **bounded common-source multi-analysis design-parameter tuning, writeback, and recovery verified**。

实例参数面现已在任务契约、planner、demo、Bridge worker 和双重定向回读中实现。`existing_schematic` 不要求目标符合反相器或共源模板，可保留 Bridge 的完整结构读取并向任意已有实例透传 Bridge 字符串参数；空值或长值不再因通用摘要不可见而被 VDA 拒绝。固定模板还允许 semantic 与实例参数组合，并以最终 OA 同时满足两组请求为成功条件。通用只读 live smoke 在 Gate 2A cell 上读到 MN0 的 233 个 CDF 字段、RD0 的 2 个字段及完整 geometry/nets/pins；专用 `vda_param_surface_001` 又闭合 `fingers=2`、`r=22K` 的 callback、立即回读和独立 after 回读。`m=2` 被 PDK callback 恢复为 `1`，且多字段失败留下已保存前缀，证明该能力不能外推为任意字段可持久化或事务式写入。

2026-07-23 又把 raw CDF 参数作为显式、有限的 `instance_parameter_space` 接入原有 candidate/checkpoint 状态机。它不自动枚举完整 CDF，也不改变独立 `parameters.apply`：搜索必须使用未过滤 OA readback 中实际存在的字段名和值字符串，并与固定实例字段及 semantic space 形成一个受预算截断的笛卡尔积。真实共源 Gate 搜索 `MN0.fingers=["1","2"]`；两个点均完成 callback 后定向回读、OA→`si` 网表与 DC/AC，网表总宽度从 1 µm 变为 2 µm，最终按 GBW 写回 fingers=2。第一次 candidate 1 的 `si -batch` transport reset 在恢复初始 RD/fingers 后从 index 1 重试，未跳点。状态升级为 **bounded explicit-instance-parameter tuning, same-source evidence, writeback, and recovery verified**；多字段 callback 耦合、alias 搜索、PVT-aware raw 写回和自动参数物理语义仍未闭合。

同日进入并真实闭合 Gate 3 nominal 主线。新建不覆盖的 `vb_pdk_smoke/vda_diffpair_gate3_001/schematic` 完成 `MN0/MN1/RD0/RD1` 精确回读、OA→`si` 双管/双负载 geometry、单点 DC、只读尾电流/共模搜索、18 点 W/RD/尾电流设计搜索、最佳写回、全不可行、预算和 transport checkpoint；最终 OA 为 `W=2 µm/L=30 nm/RD=8 kΩ`。18 点搜索在两个不同候选发生 transport reset，均经独立 OA 回读后续跑并最终完成。状态升级为 **bounded differential-pair nominal DC writeback and recovery verified**。

同一最终 OA 随后完成差模 AC、有限尾源输出电阻下的差模/共模配对 AC、输入共模范围和相干 transient。无额外负载时差模增益 `2.992 V/V`、带宽 `29.18 GHz`、GBW `87.25 GHz`、unity `86.80 GHz`；显式 `1 MΩ` 外部尾源输出电阻得到低频 CMRR `58.59 dB` 和 CMRR 下降 3 dB 带宽 `306.38 MHz`。0.20–0.90 V 的 12 点共模采样在 transport reset 后从 9/10 checkpoint 恢复，当前 50 mV 余量门下通过点为 0.30–0.875 V。100 MHz/每端 1 fF 的 7 点差分 transient 得到输入 P1dB `110.9 mV peak` 和 250 mV 输入下 THD `16.54%`。四点尾电流×负载 AC 只读搜索也复用原有限搜索/选择状态机。所有动态任务的 `si` SHA 与最终 OA 一致。状态升级为 **differential-pair nominal same-source DC/AC/CMRR/linearity and read-only tuning verified**；这是 Gate 3 的明确边界，理想尾源能力继续保留。

Gate 4 随后在新 `vda_diffpair_tail_gate4_001` 上以 exact-delta transform 只增加 OA `MNTAIL/BIAS`。5 点 BIAS 搜索选中 0.40 V；三点尾管宽度 checkpoint 按最小功耗写回 0.8 µm。最终同一 OA/`si` 路径得到差模增益 `2.928 V/V`、带宽 `13.806 GHz`、GBW `40.418 GHz`、低频 CMRR `21.135 dB`、CMRR 带宽 `19.069 GHz`；10 点 ICMR、六点 transient 和 141 点 differential noise PSF 也全部完成。首轮 ICMR 状态误分类、transient 下载 timeout 和首版 noise 共模漂移均保留失败 record，并在修正/doctor 后成功重跑。状态升级为 **differential-pair real-tail multi-analysis same-source and bounded writeback verified**；ADE 人工交接、mismatch 和可选 PVT 仍待独立 Gate。

Gate 5 随后在新 `vda_diffpair_deg_gate5_001` 上验证对称源极退化。exact-delta add 只把 `MN0.S/MN1.S` 分到 `NSP/NSN` 并加入 `RS0/RS1` 后汇回 TAIL；500 Ω 的 OA 回读、`si` 网表、两支路电阻电流和 KCL 完全绑定。既有 DC/差模与共模 AC/10 点 ICMR/六点 transient/noise 全部无缝复用；相对同参数无退化基线，输入 P1dB 提升 29.19%、200 mV THD 降低 30.11%，代价是低频增益降低 17.89%、GBW 降低 22.22%、输入参考积分噪声增加 17.82%。250/500 Ω 两点有限搜索按 P1dB 选择并独立回读 500 Ω。两次 remove 都把 placement SHA 精确恢复到 add 前值，恢复网表和 DC metrics 逐项相同。状态升级为 **differential-pair reversible symmetric source-degeneration, full-analysis migration, and bounded RS writeback verified**；PVT 仍按任务可选，未作为默认成本。

同日继续完成 Gate 6 live。新 action 在全新 `vda_diffpair_active_gate6_001` 上把 `RD0/RD1` 精确替换为 `MP0/MP1` 电流镜，并按显式电阻值反向恢复；PMOS W/L 进入 semantic apply/search 面。OA/`si` parser 拒绝混合负载、单边缺失、错误 master/node 和几何不匹配；DC 增加 PMOS OP、支路 KCL、镜像误差、上下管工作区与联合摆幅；AC/CMRR/transient/noise 使用显式 `OUTN` 单端输出。真实 Gate 覆盖 10 点 ICMR、6 点 bias/load、6 点 Wn/Wp、预算耗尽、全不可行恢复、两次 transport timeout checkpoint/resume、最佳 OA 写回、精确 RD restore 和最终 active-load 重建。首版 `PM0/PM1` 因 Spectre `P` 前缀被解释为 port 而失败，保存日志、恢复基线并统一更名为 `MP0/MP1` 后闭合。状态升级为 **Gate 6 current-mirror-load same-source bounded closure verified at nominal TSMC N28**；可选 PVT、P1dB 包围、slew/settling、mismatch 和 ADE handoff 仍待后续 Gate。

随后启动 Gate 6Q 的第一项设计质量指标：差分对 `analysis: psrr`。每个候选只生成一次 OA/`si` 网表，并运行平衡差模、VDD 注入和 VSS 注入三次复数 AC；三次 DC 工作点、频率网格和网表 identity 必须一致。指标明确区分 `Avdd/Avss`、低频 PSRR+/PSRR−、扫频最差值、可选声明频带内最差值和首次下降 3 dB 频点；三份根 AC 文件在临时目录清理前分别绑定大小与 SHA-256。纯 `tail_bias_v/load_ff` 搜索沿用不写 OA 的有限搜索/选择状态机。2026-07-24 nominal 单点得到低频 `11.5156/13.4535 dB`；随后四点搜索在 `1 kHz–100 MHz` 得到组合最差 `11.4275–11.5125 dB`，四点均通过饱和、摆幅、增益、带宽和功耗护栏，但全未通过临时 `20 dB` 证伪门。`load_ff` 对带内值无改善，BIAS 提升仅约 `0.085 dB`；两次 transport 中断从同一 checkpoint 经独立 OA 回读恢复，最终 4/4 完整、零参数选择、OA 不变。状态升级为 **band-limited PSRR search and recovery verified; bias/load-only closure falsified on the declared grid**。因此后续 Gate 转向有边界的器件几何，不再密扫已证伪的两个 testbench 旋钮；该 OA 写入 Gate 的结果如下。

该 OA 几何 Gate 随后在同一 cell 上真实执行：`input/PMOS-load/tail L` 各取 `0.03/0.06 µm`，8 点逐一写入、回读、自动 `si`、三路 AC 和 checkpoint。8 个点全部通过饱和、摆幅、增益、带宽和功耗护栏，只有临时 `20 dB` PSRR 门失败；最佳观测点为三种 L 全部 `0.06 µm` 的 `19.7438 dB`，但没有被提交。更平衡的短尾管点 `Lin=Lp=0.06 µm/Ltail=0.03 µm` 为 `19.7123 dB`、带宽 `1.1186 GHz`、GBW `10.8095 GHz`；把尾管也加长只改善 `0.0315 dB`，却把带宽降至 `0.3305 GHz`、GBW 降至 `3.2940 GHz`。一次候选 3 写后回读 transport reset 先恢复初始 OA，再由同一 checkpoint 从 index 3 续跑；8/8 后因零可行点恢复三组 `L=0.03 µm`，最终运行内回读和额外独立 inspect 均一致。状态升级为 **OA length sensitivity and infeasible recovery verified; provisional PSRR closure short by 0.256 dB**。下一自动 Gate 固定尾管 30 nm，只对输入对/PMOS L 做小范围显式细化；任何过门点还必须复跑 CMRR、linearity、noise 和必要的可选 PVT，不能因单一 PSRR 指标升级 L5B。

2026-07-24 又加入 theory-first 本地 Gate，避免把少量人工枚举点中的最大值误称为最优。首版针对 Gate 6 固定拓扑，输入有限且带来源声明的 gm/Id characterization 表域，用 KCL、小信号和单极点关系反解每个表点组合满足 BW/GBW 的最小电流与 W，并输出功耗、面积、余量、约束裕量、限制项、寄生渐近上限和局部敏感性。程序会完整计数和穷尽声明的离散表域；现有 `design.tune` 也新增结构化 `search_audit`，预算截断只能称 `best_evaluated`。无论哪条路径，连续/全局最优声明固定为 false。随后 `vda theory-calibrate` 消费 Gate 6 六点真实 OA/`si`/Spectre 记录，在固定 `top_tt`、BIAS/VCM/VDD/CL 和 30 nm L 下拟合 topology-local 增益修正与等效电容；六点留一最大 gain/BW/GBW 误差为 `0.083%/0.373%/0.457%`。一次不写 OA 的新鲜同点执行逐值复现旧结果，模型误差为 `0.069%/0.320%/0.390%`。因此状态升级为 **topology-local one-pole calibration verified at nominal top_tt; standalone PDK gm/Id characterization pending**。该局部系数不自动注入 `vda theory`，也不授权 OA 写回。

同日进一步加入 topology-independent small-signal matrix core，避免把上述局部校准误当作通用理论能力。新的器件表按 model/polarity/L/偏置保存 width-normalized `Id/W、gm/Id、gds/Id、gmb/Id` 和端子电容，网络请求只描述 MOS/R/C 节点图、固定 AC 边界和输入/输出表达式，不接受 topology 分类。共源、源退化共源、NMOS 差分对和 PMOS 共源已用同一矩阵组装路径通过解析测试；浮空、偏置漂移和真实数据缺 hash 会拒绝。初始状态为 **topology-independent small-signal matrix core locally verified; live PDK characterization and OA/si graph binding pending**。

Gate 7A 随后闭合第一个待项：新增无 OA target 的 `device.characterize`，在一个独立 Spectre DC deck 中表征 `nch_lvt_mac/pch_lvt_mac`。最终 nominal `top_tt`/27 ℃、W=1 µm、L=30/60 nm 的 240 点表和 4 个真实 VGS 留出点全部通过；最坏归一化误差 `13.70% < 25%`，deck、`dcOp.dc`、`dcOpInfo.info`、log 和 `spectre.out` 共六项绑定 manifest SHA-256，NMOS/PMOS bias/IDS 符号逐点匹配，OA access/write 均为 false。原始 OP 是 `eda_result`，归一化点和插值审计是 `software_inference`。45 nm 留出几何的真实 PDK 失败和 96 点粗网格的 3/4 留出失败均保留并用于修正合法几何和采样/误差定义。状态升级为 **standalone TSMC N28 MOS characterization live verified at nominal top_tt; si graph binding and held-out circuit validation pending**。这仍不是任意拓扑综合或最终规格证据。

Gate 7B 已闭合首个纵向共源点。`vda small-signal-validate` 从 real Bridge run records 绑定只读 OA target、结构化 `si` 实例、真实 DC OP、原始 AC 网格和文件 hash；在 exact L 平面内插值 VGS/VDS/VSB，并拒绝外推。第一次 W=1 µm 表因物理宽度不匹配而拒绝；第二次 W=0.5 µm 但没有 OA 扩散/LDE 参数的表得到 gds `32.20%`、GBW `25.84%` 误差，按原门限保留 partial。实现随后把最多 64 项、禁止覆盖 `w/l/nf/m/multi` 的安全 numeric Spectre 参数签名加入 task/artifact，并要求与 `si` 集合和值完全相同。匹配 MN0 的 31 项签名表完成 60+1 点，留出最坏 `7.42% < 25%`；held-out circuit 的 Id/gm/gds/VDSAT、gain/phase/BW/GBW 全部通过原 policy。状态升级为 **nominal common-source same-geometry small-signal validation verified**。

Gate 7C 随后完成源极退化迁移。旧任务的 W=0.5 µm 与当前 OA W=1 µm 不一致时在 Spectre 前被拒绝；新增 `vda characterization-task-from-run` 从新鲜 real-si MN0 自动提取 exact model/W/L/31 参数签名，并把来源 run/netlist/signature hash 与用户审查的安全偏置网格一起写入 standalone 表征任务。1 µm 表完成 60+1 点，留出最坏 `15.61% < 25%`。同一 binder 从结构化图绑定 `MN0.S=NSRC` 和 `RS0(NSRC,VSS)=2 kΩ`，要求 DC source-current consistency matched；未增加源退化专用 AC 公式。held-out Id/gm/gds/VDSAT 误差为 `17.59%/17.69%/21.36%/2.82%`，gain/BW/GBW 误差为 `0.374 dB/13.52%/17.16%`，全部通过 Gate 7B 原固定门限。状态升级为 **source-degenerated common-source same-source small-signal migration verified at nominal top_tt**。下一步扩到多 MOS、不同 polarity/角色的差分对。自动表征任务生成当前只支持 nf=1/m=1；每种新签名仍需对应表，PVT 可选，理论结果不授权 OA 写回。

多 MOS 迁移的本地基础先完成后，Gate 7D 已执行三张真实 TSMC N28 表和 held-out OA→`si`→Spectre 差分对对照。输入 NMOS、PMOS 负载和尾 NMOS 表各自绑定 exact W/L、31 项 `si` 参数签名、来源实例、同一电路 run/netlist hash；missing/ambiguous/W/signature/source drift 和未使用表均拒绝。legacy 五电容模型的 BW 预测约 `5.27 GHz`，完整 signed 4×4 `dQi/dVj` 后仍为 `5.2356 GHz`；电路内实际 `cxx` 与三表最坏只差 `0.74%`，因而没有靠加密网格或抬高门限掩盖问题。补入与本征电荷矩阵分开的 `cjd/cjs` 后，预测/实际 BW 为 `3.0987/2.9756 GHz`，GBW 为 `11.6800/11.1351 GHz`，误差 `3.97%/4.67%`；增益误差 `0.063 dB`，相位及五个器件的 DC/电容检查全部通过原 policy。全过程只读 OA、Bridge 未修改。状态升级为 **differential-pair exact three-plane same-source small-signal validation verified at nominal top_tt**。多指/多重器件、其他 PVT、mismatch/noise 和不同输出表达式仍是后续边界。

Gate 8 随后完成 theory→EDA 的受控交接。新本地 derivation 入口把 Gate 7D validation hash、三张 characterization run/hash、实际 DC 偏置和 policy 绑定成 64 组合的真实 PDK theory request；12 个一阶可行点经“理论排名前缀 + log-distance maximin”生成 6 个完整原子 tuple，并在 plan 前按声明的 5 nm OA 网格量化。executor 没有把 Wn/Wp/Wtail 展开成笛卡尔积，而是在既有 active-load cell 上逐点 OA 写入/回读、自动 `si`、Spectre DC/差模 AC/共模 AC，遇到候选 6 transport reset 后恢复基线并从 checkpoint 续跑。4/6 真实可行，功耗 objective 选择理论第二名 `Wn/Wp/Wtail=1.315/1.180/0.605 µm`，得到 `10.981 µW`、`3.7277 V/V`、`2.6895 GHz` BW、`10.0258 GHz` GBW 和 `34.815 dB` CMRR；独立 OA readback 一致。只读 follow-up 又完成 PSRR、noise、三点 transient 和 10 点 ICMR/checkpoint，PVT 按本 Gate 授权未运行。状态升级为 **real-PDK theory shortlist to bounded same-source EDA selection verified at nominal top_tt**。

Gate 8 同时保留了理论边界：6 点中 4 点可行使候选生成门通过，但理论第一名并非 EDA 选择；功耗/增益/BW/GBW 的 24 个预先声明对照有 8 个超过 25%，故 prediction-accuracy Gate 为 partial。下一理论 Gate 不在这六点上过拟合，而是用第一遍真实 DC OP 对候选重线性化，再用未参与校正的 held-out tuple 复核；只有 EDA 指标可以决定最终写回。PSRR+ 仍只有 `11.4828 dB`，P1dB 未包围，故 Gate 8 也没有补齐完整设计质量闭环。

2026-07-25 已完成该下一步的本地能力 Gate，但尚未执行新候选。Task contract 新增通用 `candidate_set`：一个候选可同时携带 OA semantic、testbench 和原始 CDF 字段，固定 raw 字段按字段深合并；候选不会同逐维 space 组合或再展开成笛卡尔积。来源为 `software_inference` 时必须 hash 绑定，预测只进 provenance，最终选择仍由本次仿真证据决定。`vda op-relinearize` 则只消费成功 real-Bridge run 中的 `eda_result`，强制分离训练/留出 index、拒绝不可辨识扰动和未建模输入漂移，并要求每个指标同时通过训练及留出误差门，失败时不生成任务。

首轮真实记录回放没有连接 Bridge。共源 W/RD/RS 记录用 6 个训练点和 2 个留出点验证 3 个 OP + 7 个性能指标；最坏训练/留出误差为 `17.823%/11.450%`，均低于预声明 `15%` OP、`20%` performance 门。差分对 Gate 8 记录用 4 个训练点和 2 个留出点验证 8 个 OP + 7 个性能指标；最坏留出误差 `8.262%`。两者都从局部 27 点空间只编译出 6 个原子候选；task template 继续保留局部模型未覆盖的 saturation、THD、CMRR 等完整 EDA constraints。该本地状态是 **real-EDA-record local OP relinearization and atomic candidate compilation verified**，本身不代表新候选已跑 Spectre。

2026-07-26 共源六点进一步完成真实 OA→`si`→AC/transient/noise、候选 1 transport 失败后的基线恢复/checkpoint resume、6/6 全规格可行、最佳写回和独立 OA 回读。模型与 EDA 都选择 `W/RD/RS=1.1 µm/19 kΩ/0.75 kΩ`；GBW 为 `34.3965 GHz`，比 anchor 高 `13.561%`。新增 `vda op-relinearization-validate` 以 exact result/task/run 自动审计候选 provenance、完整域和原 held-out error limit：GBW 最大误差 `4.505%`，但候选 5 output swing 为 `22.479% > 20%`。因此状态升级为 **common-source atomic local-response shortlist to same-source EDA selection verified; live pointwise model accuracy partial**。下一轮共源必须在新 anchor 重新留出验证或缩小 trust region，不能放宽门。PVT 保持可选。

同日先完成不连接 Bridge 的新 anchor 刷新。实现新增 heldout 参数方向覆盖门：每个声明参数至少要在一个留出点变化。原三维 W/RD/RS 刷新因此拒绝，因为 candidates 5/6 的 RS 都与 anchor 相同；这避免把未验证的 RS 灵敏度包装成已校准。随后固定 `RS=750 Ω`，用 candidates `[2,3,4]` 训练 W/RD、`[5,6]` 留出，两维均有覆盖，10 个指标最坏历史留出误差为 `0.568%`。新小域 `W=1.05/1.10 µm × RD=18.5/19/19.5 kΩ` 的六点随后全部完成 OA→`si`→AC/transient/noise；6/6 可行，预测与 EDA 都选择 `1.1 µm/18.5 kΩ/750 Ω`，GBW=`34.65534 GHz`。exact validator 的 60/60 项比较全部通过，最坏新点误差为 `0.353712%`。两次 transport `system_event` 都安全恢复并从 checkpoint 续跑，最终 OA 独立回读一致。当前状态是 **held-out-covered common-source W/RD local response to same-source EDA selection verified at nominal top_tt**；继续 RS 调优前仍需要独立 RS 探针，PVT 保持可选且未运行。

差分对六点随后完成独立 live Gate。预检先回读 Gate 8 anchor，六个 `Wn/Wp/Wtail` tuple 再逐点完成 OA 暂存/回读、`si`、双支路 DC、差模 AC 和共模 AC/CMRR。一次 DNS/SCP 和两次 `WinError 10054` 都被分类为 `system_event`；执行器恢复并独立回读 anchor 后从未完成 index 续跑，最终 6/6 可行。局部模型与 EDA 都选择最小功耗点 `1.215/1.080/0.555 µm`：功耗 `10.2971 µW`、gain `3.71568 V/V`、BW `2.68269 GHz`、GBW `9.96800 GHz`、CMRR `34.8249 dB`。exact validator 对旧 result 使用其 canonical-hash-bound 原 policy 恢复已预声明的误差 floor，90/90 比较全过，最坏新点误差 `2.5693%`；最佳 OA 独立回读一致。当前状态是 **differential-pair held-out local-response shortlist to same-source EDA selection verified at nominal top_tt**。下一 Gate 不再重复尺寸六点，而是只读复核新最佳点的 PSRR、noise、linearity/P1dB 和 ICMR；PVT 继续按任务可选。

Bridge 隔离分支进一步加入幂等 SSH 有界退避和仅限 payload 发送前的 tunnel 自愈。新的 9 点压力任务仍在候选 8 发生一次本地端口拒绝，但 OA 恢复、候选前缀和续跑均正确，最终 9/9 与最佳写回成功；确定性同-client smoke 已覆盖 pre-send 自愈。payload 发送后的不确定错误仍不自动重放，这是保留的可靠性边界而不是跳过的工作。

2026-07-21 又完成 `ade.prepare` + `ade.capture` 的本地纵向实现。`prepare` 只在已有 design schematic 且目标 Maestro view 不存在时新建持久化 Spectre test，保存后重新打开核对；已有 view 一律拒绝，不配置 analysis/stimulus/sweep/output。人工补全并运行后，`capture` 只读核对聚焦的 `library/cell/maestro`，默认要求 setup 已保存，捕获 setup、指定/最新 history、Spectre netlist、PSF/log 和 ADE 逐 sweep 点 output/spec，并生成逐文件及聚合 SHA-256。两者都不把准备或捕获成功算作 VDA 规格 closure。Bridge 当前公开的持久化后端是 Maestro；旧 ADE L state 非破坏迁移、VDA-managed variable sweep/corner 和 live nics4304 prepare/capture 仍待 Gate，因此此项当前只能称为 **local bidirectional human-operated ADE handoff contract implemented**。

同日新增 `ade.run` 本地纵向能力：不打开或聚焦 GUI，以独立 background Maestro session 运行一个已保存 setup 中的原生 analysis/parametric sweep，并把 history 中逐 point 参数、output、spec 和 pass/fail 作为 `eda_result` 回收。随后补齐 exact-history + unique-runtime 产物门、显式 history/scratch 恢复、`/data/xum` 临时 session 目录定向与恢复，以及可选 OA→`input.scs` 一致性。2026-07-21 live Gate 在新 `vda_manual_ade_handoff_001/maestro` 上运行一次 transient；首轮 EDA 已完成但证据清单受 IC6.1.8 `csh` buffer 和本机 SCP DNS 影响失败，后续均按 `Interactive.0` 恢复而没有重算。最终 Detail 表返回 `VoutAvg=364 mV`、spec pass，清单含 65 个 simulator-input、1 个 RDB result 和 2 个 log，test design、OA instance/nodes 与 19 组 raw 参数映射一致。状态升级为 **live background Maestro same-source execution, evidence recovery, and raw-input consistency verified**；history 名唯一性、通用 constraint 映射、CDF `Wfg` 派生语义、变量 sweep/corner 仍未闭合，因此没有升级 L5B closure。

随后新增并扩展 `ade.variables.apply`：任务声明 exact tests、可选 enabled corners，以及每个 global/test/corner design variable 的 scope、旧值和新值；还可用独立 CAS 更新 global-variable enabled/disabled selection，并要求未声明名称在完整集合中保持。全部前置条件匹配后逐项写入，只保存一次 setup，再用全新 background session 逐 scope/selection 复核持久化值。`null` 可断言变量在该 scope 原先不存在，逗号字符串可请求该 scope 的原生 sweep。已有已配置 Maestro session 时保守拒绝；请求标为 `user_input`，三阶段回读标为 `bridge_readback`。单独执行该 Gate 不证明变量进入网表或仿真。2026-07-22 先在专用 TSMC N28 Maestro view 上真实闭合 global CL 三点，随后又闭合 test-scope `CL=1f,4f`、named-corner VDD=0.8/0.9、global VDD=0.9 以及 CL enabled→disabled；后续严格 corner Gate 证明这些状态实际进入结果链。

同日新增正交 `ade.corners.apply`：它只在 exact tests/旧 corner 顺序匹配时 add-only 新增命名 enabled corner，逐项回读、保存一次、独立重开；不删除、改名、替换或偷偷配置 model/temperature。live 从内建 `Nominal` 增加 `VDA_LOW_VDD/VDA_NOMINAL_VDD`。这两个名字在当前 Gate 只承载 VDD override，仍使用 nominal `top_tt`，因此不能冒充 process/temperature corner。

现在又新增正交的 `ade.setup.apply`：在 exact tests 下，对声明 analysis 的完整旧 enabled/options 做 CAS，并只新增明确不存在的命名 net/point output 与可选 lt/gt spec。worker 在首个 writer 前读完全部旧 analysis 和 output absence；随后复用 Bridge public analysis/output/spec writer，逐项立即回读，只保存一次，再独立重开核对。已有 output 不会被替换，未声明 setup 状态也不会被包装成已核验；analysis/output 配置仍是 `bridge_readback`，没有仿真就没有 `eda_result`。2026-07-21 nics4304 live Gate 已把默认 disabled transient 改为 `stop=300p/maxstep=1p`，新增 `/IN`、`/OUT` 和带 `>0.1` spec 的 `average(VT("/OUT"))`，并在独立重开中全部匹配；首次 expression 序列化失败发生在 save 前且重开证明零持久化。状态升级为 **live declared-analysis CAS and add-only output/spec persistence verified**；已有 output 替换、变量/corner scope 与完整 setup 指纹仍待验证。

2026-07-22 为 `ade.run` 增加可选 `sweep_verification` 严格门，并在 TSMC N28 上真实闭合。任务声明 exact tests/corners、各 scope 保存的逗号 sweep、连续 expected points 和 OA `instance.parameter` 绑定；worker 在运行前后回读同一 setup，RDB 每点必须有匹配参数和非空 scalar output。原先本地实现假设 exact history 会保留逐点 `input.scs`；live 运行证明 IC6.1.8 实际只保留共享 runtime `input.scs` + sibling `netlist` 和 exact-history RDB。VDA 因而保留两个互斥严格模式：出现任意逐点目录就要求全点 input/result 完整；完全没有时才允许共享符号输入束 + exact-history RDB/completion log，且必须同时核对 include 关系、两份输入 hash、OA/Spectre 变量引用、一个声明 retained point、RDB 全点值/非空 output、精确完成点数和零仿真错误。数据库模式明确保持 `exact_point_input_result_binding_verified=false`，不把共享 netlist 冒充逐点文件。最终 `Interactive.0` 给出 CL=`1f/2f/4f` 和 `VoutAvg=415.5/419.8/428.3 mV`，3 点完成、0 错误；证据失败后按固定 history/scratch 恢复，没有重算。最终本地回归 `288 passed`、`59/59` example plans。状态升级为 **native Maestro single-variable sweep same-source execution and evidence recovery verified**；二维 sweep、corner、多 test/multi-analysis 和通用 constraint 映射仍待 live。

同日继续闭合有物理意义的 ADE 质量 Gate：以 add-only 方式保存并独立重开核对 `Tphl/Tplh/Delay/Rise/Fall/RiseFallSkew/SupplyEnergyCycle`，再把 exact output、预期 calculator expression、SI-to-ps/fJ scale 与 VDA metric 显式绑定。worker 在 run 前后指纹化 output state；executor 只从严格 sweep 的有限 RDB scalar 构造候选，原始值保留为 `eda_result`，换算、constraints 和选优标为 `software_inference`。`Interactive.1` 的 CL=1/2/4 fF 分别得到 delay=2.882/3.766/5.503 ps、skew=1.223/2.112/3.939 ps、周期供电能量=1.659/2.497/4.147 fJ；三点都通过 6 ps/5 ps/5 fJ 约束，能量 objective 选中 1 fF。随后固定同一 history/scratch 的表达式指纹恢复没有重算或写 OA/setup。最终本地回归为 `314 passed`、`61/61` example plans。状态升级为 **native Maestro inverter scalar-to-constraint quality selection verified**；周期供电能量包含泄漏与短路电流，不称为纯动态能量。

下一步的二维 Gate 也已真实闭合：在同一 schematic 中把 `VDD0.vdc` 与 `VIN0.v2` 都改为 `VDD` 符号，global `VDD=0.8,0.9` 保存并独立重开，七个 VDD-aware outputs 以 add-only 方式持久化。`Interactive.2` 的 `VDD×CL` 六点全部进入同一个共享符号 `input.scs`+`netlist` 和 exact-history RDB；五点通过原 6 ps/5 ps/5 fJ 约束，能量 objective 选择 `0.8 V/1 fF`，得到 delay=3.254 ps、skew=1.425 ps、周期供电能量=1.309 fJ。第一次本地等待边界先于 worker 清理窗口终止，但远端已完成；孤立 background session 按旧 record 恢复 runtime path 后关闭，后续固定同一 history/scratch 恢复没有重算或写 OA/setup。旧固定阈值 `Rise/RiseFallSkew` 在三个 0.8 V 点产生的 6 个 `eval err` 被 exact test/output/point 契约、RDB 单元格和 log 数量双层核对，未解释错误为零；默认仍要求 0 error，mapped output 不得进入该声明。最终本地回归 `323 passed`、`65/65` example plans。状态升级为 **native Maestro two-variable VDD×CL same-source quality selection and recovery verified**；下一自动化门转为有限 corner/test-scope，L/VDD 联合设计搜索仍未闭合。

有限 scope/corner Gate 随后真实闭合。`CL=1f,4f` 位于 test scope，global CL selection 明确 disabled；global VDD=0.9，两个 named corner 覆盖 0.8/0.9 V。Bridge 0.7.0 的普通 Detail parser 只保留 Nominal 值，因此 VDA 通过其公开 `include_raw=True` 读取原始 CSV，按 2 个 Maestro parametric point × 3 个 corner 列构造 6 个可审计 case，并保留 raw hash。`Interactive.4/5` 的 4 个低压 legacy error 全部被精确解释，5 个 case 可行，仍选中 0.8 V/1 fF。状态升级为 **native Maestro test-scope × environmental-corner quality selection and bounded timeout recovery verified**；这里仍是 nominal model 下的环境 VDD corner，不等于真实 PVT。

2026-07-22 又在 direct common-source 路径闭合下一自动化 Gate。`L=[0.03,0.04] µm × VDD=[0.8,0.9] V` 四点三分析搜索全部可行，GBW objective 写回并复核 `L=0.03 µm`，VDD 保持 testbench 条件。随后同一 OA/同一 `si` 网表在 TT/25℃/0.90V、SS/125℃/0.81V、FF/−40℃/0.99V 下运行九项 Spectre 分析；每个条件都保留原始指标，全部满足规格，objective 按跨条件最坏值聚合。状态升级为 **bounded common-source L/VDD quality tuning and fixed-design PVT verification verified**。当前缺口收敛为 PVT-aware 设计候选调优、ADE 真实 PVT/multi-test 映射和差分对，而不是 L/VDD 或固定设计 process/temperature 执行能力。

2026-07-23 将 `operating_conditions` 作为显式可选项接入 `design.tune`/`design.close_loop`。不声明时仍按 nominal 单条件运行；声明后每个候选必须跨完整条件集，按最坏值选优。真实只读 Gate 对 `bias_v=[0.35,0.40] V` 跑完 18 项分析，`0.40 V` 因多角 THD、SS 摆幅和 FF 功耗失败，选择 `0.35 V`，OA 前后不变。状态升级为 **optional PVT-aware testbench tuning verified**。OA 设计变量的跨 PVT 最佳写回、全不可行、预算和 completed-prefix transport resume 已有本地测试，但 live 仍是可选加严 Gate，不作为所有调优的默认成本。

需要用户操作 Virtuoso/ADE 的验证已按用户决定延期，并集中记录在 [`deferred-manual-gates.md`](deferred-manual-gates.md)：包括人工修改/保存/重跑后的双向交接、旧 ADE L state 备份后迁移与重开，以及相同 history/output 的人工数值交叉检查。这些项目不阻塞后台自动化实现，但在真实完成前仍保留为未验证边界；延期记录本身不构成远端授权。

2026-07-25 又完成 Bridge/Spectre/ADE 资源生命周期 Gate。重复 10 次真实只读 worker 请求没有新增本地 Python/Spectre/SSH、句柄或 `vda_*` temp；合成超时先证伪 `taskkill /T`，随后 Windows Job Object 真实杀净 worker 的 120 秒后代进程。远端 direct Spectre 使用已哈希回读的 timeout guard。follow-up 再加入 cancel marker/父进程 watchdog，让 Python `finally` 在强杀前有 30 秒恢复 Maestro runtime、关闭 session/client；既有 Maestro view 的真实 timeout 故障注入后，独立 inventory 得到 Spectre/si/Maestro session 均为 0，空诊断 root 经逐层检查后精确删除。`vda resources [--remote]` 现可只读盘点本地 temp、持久 evidence、远端 EDA process/session、age/size 和 exact pin；默认不删除。状态升级为 **known VDA-owned process lifecycles bounded, Maestro cancellation live-verified, and retained evidence inventory available**。硬件/OS 崩溃与历史 evidence 的用户确认删除仍不是自动 GC。

2026-07-26 先完成通用 topology-delta 的本地契约 Gate，随后在全新 `vda_generic_topology_delta_001` 上完成首个 live Gate。结构 snapshot 对实例 master/端子/位置属性、net 和 pin 做确定性 SHA-256；八类 operation 可序列化并自动生成逆向 patch。live contract 把普通共源级增量变为 `MN0.S→NSRC + RS0(NSRC,VSS)`，before/after SHA 分别为 `4471c939...d4dea9`/`68e0cbcd...a67ed`；RS0=`1K` 经过 OA 回读并进入 `si`，退化 DC 的 source current mismatch 为 `0.002723%`。inverse 后独立 readback 精确恢复 before SHA，普通共源 DC 再次成功。状态升级为 **bounded generic topology-delta OA execution, same-source netlisting, and exact inverse restoration verified**。

同日继续完成 post-save recovery 与 `replace_master + CDF` 本地 Gate，随后在新 `vda_master_migration_001` 做真实 round-trip。`MN0: nch_lvt_mac -> nch_mac` forward 的 before/after SHA 为 `4471c939...d4dea9`/`17ddebdb...8d5d1`；独立 OA 回读的 233 项 CDF 只有 `description/model` 随 flavor 改变，Wfg/l/fingers/m 保持。`si` 明确导出 `nch_mac`，SVT 的 271 点 AC 得到增益 `4.0184 V/V`、BW `4.5914 GHz`、GBW `18.4502 GHz`；inverse 后 topology、完整实例参数、LVT netlist SHA 和核心 DC/AC 标量与 before 精确一致。状态升级为 **controlled symbol-master/CDF migration, same-source simulation, and exact inverse restoration live verified**。post-save 自动 recovery 仍只有本地故障注入，不由正常 inverse 代替。

下一轮先在本地把通用 delta 的物理边界补到 pin 与完整 placement，随后已在两个新 cellview 完成 live。真实 PDK callback 把声明的 `MN0.m=2` 归一回 `1` 后，post-save 审计按预期失败，VDA 仅在新鲜 topology 匹配时执行 inverse；独立 readback 恢复原 topology、placement 和参数，原请求仍保留失败。共栅 Gate 则只增加 `NCAS/VCAS/MNCAS/VCAS pin` 并重连 `MN0.D`，真实 pin figure 的自动 OA 名用逻辑 pin 绑定得到稳定 placement SHA；部分恢复后缺失 terminal stub 的路径也以 exact instTerm/current-net CAS 和最小重建闭合。hash-bound OP 分析器生成的同一 9 个原子 tuple 先跑 DC、再跑 AC，对应自动 `si` 网表 9/9 匹配且两轮 9/9 可行；AC 离散域最佳点 gain=`6.4412 V/V`、BW=`3.6127 GHz`、GBW=`23.2705 GHz`。exact inverse 后普通共源 netlist、17 个 DC 指标和两次 28 项 AC/DC 指标一致。状态升级为 **recoverable common-source-to-cascode bounded same-source DC/AC selection live verified at nominal top_tt**；noise/linearity/PVT 与完整质量选拓扑仍待后续 Gate。

同日又在新 `vda_diffpair_active_deg_generic_001` 上把同一 contract 组合到 Gate 6 PMOS 电流镜负载：before/after SHA 为 `d3fe4b73...31b93`/`68c9d2e2...559a`，`RS0=RS1=500 ohm` 经过 OA 回读并进入七实例 `si` 网表；DC 的 PMOS/RS/尾管 KCL 全部 matched，随后差模 AC/CMRR、noise、transient/THD、十点 ICMR 和 nominal PSRR 均复用既有 worker。真实结果为增益 `3.5523 V/V`、BW `2.0576 GHz`、GBW `7.3093 GHz`、低频 CMRR `34.446 dB`、输入参考积分噪声 `817.84 uV RMS`；`50 mV_peak` 时 THD `1.411%` 且 P1dB 未包围，PSRR+/- 只有约 `11.06/13.08 dB`。inverse 后独立 readback 精确恢复 before SHA，恢复态 DC 再次成功。状态升级为 **active-load plus symmetric source-degeneration generic-delta and full nominal analysis migration live verified**。参数值继续走独立 CDF/semantic 契约；通用 wire/shape snapshot、pin 几何和并发 editor 尚未闭合，post-save recovery 仍待 live 故障注入。

## L5B：单模块设计代理（产品目标）

面向反相器、单管放大器、差分对等单模块，由规格驱动完成更完整的设计过程：

- 从受控拓扑目录中选择或拒绝拓扑。
- 自动建立 testbench、analysis、output 和 sweep。
- 处理 DC operating point、AC、tran、noise 和多 corner。
- 自动流程与可人工打开、调整、重跑的 ADE setup/history 双向交接。
- 使用更有效的优化策略，同时保留参数边界和预算。
- 识别不可行规格、模型异常和仿真失败，而不是无限重试。
- 生成可复核的设计报告和未闭合项目。

L5B 的完成标准是“单模块规格闭环可重复”，不是能偶尔跑出一组好看的波形。

## L5C：物理实现闭环

在 L5B 之上加入 layout、DRC、LVS、PEX 和 post-layout 迭代：

- 参数化或约束驱动的版图生成/修改。
- DRC/LVS 结果结构化解析与定位。
- PEX 后重新评估规格并回到电路或版图参数。
- 区分 block-level 规则、芯片级规则和无法自动修复的上下文。
- 保留每轮 schematic/layout/netlist/report 的可追溯关系。

现有 Bridge、`auCdl -> strmout -> Calibre` 和 Maestro/Spectre 已经触及 L5C 所需的执行机制，但“机制可调用”不等于 L5C 自主闭环已经成立。PEX 与自动修复尤其仍是独立 Gate。

## 当前推进顺序

```text
反相器 L5A
  -> nominal 反相器驱动比例校准（粗网格与 1.20/1.25/1.30/1.35 细化均 live；1.20 为可覆盖缺省；多 CL/input slew/PVT 鲁棒性可选）
  -> 共源 nominal DC (Gate 2A 已通过)
  -> 显式实例参数面 (可持久化字段 live 双重回读已验证)
  -> 受控拓扑小变更（同一既有 cellview 原位增加 source degeneration）
  -> 各拓扑 AC gain/bandwidth（nominal/退化只读同源 smoke 已通过）
  -> 有限 AC trade-off 与失败/预算/恢复路径（已通过）
  -> 功耗 + transient 线性度 + noise（单点只读 live 已通过）
  -> 多 analysis 质量约束与受预算调优（W/RD/RS 写回、失败门与恢复已通过）
  -> ADE 双路径（prepare + analysis/output add-only patch + background run/resume/raw-input consistency + global CL/VDD sweep + test-scope CL × VDD environmental-corner + delay/skew/energy constraint mapping 已 live；capture 与人工交接待验证）
  -> 共源 L/VDD + 多 analysis + 固定设计有限真实 PVT（已通过）
  -> 可选 PVT-aware 候选调优（testbench bias 已 live；OA 设计变量写回待可选 live）
  -> 差分对 nominal DC + OA 写回/失败/预算/恢复（已 live）
  -> 差分对 AC/CMRR/输入共模范围/transient 线性度（已 live）
  -> 差分对真实尾管/偏置网络 + DC/AC/CMRR/ICMR/transient/noise（已 live；PVT 按任务可选）
  -> 差分对对称源极退化 + 全分析迁移 + RS 写回 + 精确 remove/restore（已 live）
  -> 差分对 active-load/current-mirror exact-template Gate（nominal OA/si/DC/AC/CMRR/ICMR/transient/noise/有限搜索/恢复已 live）
  -> theory-first gm/Id 尺寸估算（本地方程/离散域穷尽/最优性边界已实现；Gate 6 topology-local Spectre 校准与独立 TSMC N28 MOS 表已过）
  -> 通用 MOS 小信号网络（矩阵、stamp/MNA、独立器件表、nominal/源退化共源 held-out 已 live；Gate 7D 三表 signed cxx+cjd/cjs 差分对已通过）
  -> theory-seeded 差分对有限优化与 Spectre 复核（Gate 8 已 live；候选生成通过、逐点预测精度 partial，PVT 可选未跑）
  -> 通用原子 candidate_set + real-EDA OP 局部重线性化（共源首轮、新 anchor W/RD 和差分对 Wn/Wp/Wtail 六点均已 live、写回并完成 exact 审计；共源 RS 需独立探针）
  -> 差分对 PSRR+/PSRR- 三次同网表 AC（nominal、bias/load 只读与三种 L 的 OA 八点搜索已 live；临时 20 dB 门仍未闭合）
  -> 通用 topology-delta 契约（add/remove/reconnect/master/CDF/pin/placement 与 post-save exact recovery 均已在新 cellview live；仍是受控 contract，不是任意 OA editor）
  -> 共源→共栅原位微调（新 cellview 已完成同一 9 点 DC→AC、对应网表 9/9 匹配、checkpoint、exact inverse 和基线身份检查；完整 quality A/B 不再默认，未覆盖 residual 只在可能改变 objective 时补）
  -> OP 导数驱动的通用小信号预筛（既有共源/共栅真实 run 本地重放：增益误差 0.158%/0.646%；未来 gmb+dQi/dVj+cjd/cjs 保存面已实现、本地测试通过、live capture 待做）
  -> 结构化 standalone Spectre 轻量 A/B（共源/共栅级联已完成无 OA/si/Maestro 的 TSMC N28 live preview、完整 manifest 和进程归零；方向与 OA→si 一致，绝对值不作同源复现）
  -> active-load + 对称源极退化组合拓扑（新 cellview forward/readback/七实例 si/DC/AC/CMRR/noise/transient/ICMR/PSRR/inverse/恢复态 DC 均已 live；质量闭环未过）
  -> L5B 单模块闭环
  -> layout/DRC/LVS/PEX Gate
```

每一级只有在真实 Bridge smoke、结构回读、指标解析和失败注入均通过后才升级状态。

反相器可靠性 Gate 1R、共源 nominal/源退化/quality/PVT、显式实例字段，以及差分对 nominal、真实尾管、对称源退化和 PMOS 电流镜有源负载均已有 live 证据。Gate 7A–7D 又把独立 TSMC N28 器件表、通用 MOS/R/C 矩阵和 exact-signature held-out 验证接到共源及三表差分对；Gate 8 证明 theory seed 能进入正常同源 Spectre 搜索，但其逐点预测仍为 partial。通用 `candidate_set` 与 real-EDA OP 重线性化随后把共源局部 27 组合压缩为 6 个原子 tuple，并真实完成 6/6 quality、transport resume、EDA 最佳写回和自动事后预测审计：推荐一致且 GBW 提升 `13.561%`，但 output swing 的 `22.479%` 误差保留为 partial。新 anchor 刷新进一步拒绝没有 RS 留出覆盖的假三维校准，并在固定 RS 后把 W/RD 两维最坏历史留出误差降到 `0.568%`；该新六点现已同源执行，预测与 EDA 同选 `1.1 µm/18.5 kΩ/750 Ω`，60/60 比较通过且最坏新点误差为 `0.354%`。差分对六点也已同源执行，预测与 EDA 同选最小功耗点 `1.215/1.080/0.555 µm`，90/90 比较通过且最坏新点误差为 `2.5693%`；三次 transport 失败均安全恢复并完成最佳 OA 写回。通用 topology-delta 已在共源、“PMOS 电流镜负载 + 对称源极退化”和“共源 + 共栅管”三个新 cellview 完成增量写入、同源仿真和精确 inverse 恢复；新增共栅 Gate 还真实覆盖 pin/placement、post-save recovery、同一 9 点 DC→AC 和基线身份检查。2026-07-27 的只读本地复盘进一步证明，同一通用矩阵仅用已保存 `gm/gds` 就能在 `0.646%` 内解释共栅低频增益；故默认 Gate 改为“PDK 理论 seed → 必要 DC → OP 导数矩阵预筛 → 最小 Spectre residual validation”，不再把完整 A/B sweep 当作第一反应。旧 run 缺少 `gmb/dQi/dVj/cjd/cjs`，所以共栅 BW/GBW/noise/linearity 仍未由该路径闭合；只有当设计 objective 可能因这些残差改变拓扑选择时才值得追加对应仿真。组合 Gate 的 P1dB 仍未包围、PSRR 仍低，因此这里只升级能力面，不升级设计质量。继续共源 RS 必须补独立探针，不能从二维结果外推。公开的低层 stamping/MNA 接口继续允许 Agent 为具体电路增加局部方程而不复制求解器。PSRR 临时 `20 dB` 门、slew/settling、P1dB 包围、输出驱动、可选差分对 PVT、mismatch/Monte Carlo、ADE 真实 PVT/multi-test、人工打开/修改/重跑和旧 ADE L 迁移仍是独立 Gate。跨 PVT 不默认附加；这些完成前仍不能升级为可重复的 L5B 单模块规格闭环。
