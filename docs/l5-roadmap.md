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

PDK 路线默认按晶圆厂 CMOS 工艺推进：当前以 TSMC N28 为基线，后续优先通过独立 profile 接入 TSMC/SMIC 的实际晶体管 PDK。TSV、hybrid-bonding 等封装/3D PDK 不进入普通电路设计的默认路径；若未来需要，将作为显式选择和独立 Gate，而不是当前 profile 的替代品。

当前实现状态：`OA schematic -> si -> Spectre -> metrics`、供电能量积分、失败注入和候选级 checkpoint/resume 已通过本地测试。2026-07-19 live 结果覆盖 OA/`si` 参数一致性、非空 timing/current 波形、收紧规格、不可行 + 预算耗尽恢复，以及一个经历 3 次 tunnel 中断后仍完成 9/9 候选、最佳参数写回和独立 OA 回读的恢复任务。反相器 L5A 的同源有限闭环与显式恢复 Gate 已通过；Bridge 本地隔离补丁又通过强制断链只读 smoke，闭合 Windows stale state 与调用边界自动重建。运行中传输的随机 reset/timeout 仍是跨 Gate 的底层可靠性债务。

Gate 2A 又把相同执行语义扩展到电阻负载 NMOS 共源级：新 OA cellview 的 MN0/RD0 结构、W/L/R 回读和 `si` 网表一致性通过；显式保存的 Spectre DC OP 提供 Id/VGS/VDS/VDSAT/gm/gds，6 点 W/Vbias 搜索完成 3 个可行点、3 个线性区点、最佳 W 写回和最终独立紧规格复核。该结果只闭合 common-source nominal DC；当时 AC、source degeneration、noise 和 corner 均未验证。

2026-07-20 已按“微调已有 schematic、避免重建”的目标实现并真实验证源极退化原位 transform：同一 cellview 只改 MN0.S 网名并新增 RS0/NSRC，前后审计保护 MN0/RD0、pins、位置和未点名参数；同一个 common-source adapter 随后读写 RS、自动解析退化网表、提取 NSRC DC 指标并完成 6 点 Vbias×RS 搜索。一次 transport 中断从候选 3 续跑，最佳 `Vbias=0.35 V, RS=2 kΩ` 写回并由全新 worker 独立回读。该证据把 source-degenerated DC execution/tuning smoke 升级为 live verified；随后只读 AC 也已过门，但 AC 调优和完整设计质量闭环仍未通过。

同日已把共源 AC 做成正式能力，而不是一次性脚本：同一 OA→`si` 网表运行 DC OP 和复数 AC，并提取 gain、首个 −3 dB bandwidth、GBW、unity-gain 与相位。nominal/退化只读 smoke、各 6 点 bias/load 搜索、同参数只加 RS 的控制变量对比，以及 W/RD/RS 预算/不可行/完整 8 点搜索均已真实通过。完整搜索选择并回读 W=1.0 µm、RD=20 kΩ、RS=1 kΩ；多次 transport 中断都在 OA 恢复或独立 readback 后从 checkpoint 继续。状态升级为 **bounded common-source AC design-parameter tuning and recovery verified**。

设计质量分析已完成本地实现和单点 live Gate：实际 `VDD_SRC:p` DC 功耗与独立 KCL、相干 transient 幅度 sweep 的 gain/HD2/HD3/THD/P1dB/平均功耗，以及 ordinary noise 的输出/输入参考频带积分继续复用同一 OA→`si` worker。专用 cell 的 5 点 100 MHz sweep 解析出 `P1dB=88.32 mV peak` 和 150 mV 点 `THD=13.16%`；211 点 1 kHz–10 GHz PSF 得到输出/输入参考积分噪声 3.304/0.983 mV RMS。真实目录形状暴露的 DC info 覆盖和 transient 端点问题已在 VDA 层修正，没有改 Bridge。当前升级为单点 design-quality analysis execution verified，尚未升级为质量驱动的参数/规格闭环。

2026-07-21 已实现并真实验证固定 `analysis: quality` 组合：同一候选只生成一次经 OA/`si` 一致性核对的网表，再分别运行 AC、相干 transient 和 noise；三项指标共同进入原有 constraints/objective、候选预算和 checkpoint 语义。参数或共享 DC 指标不一致会硬失败，任一子分析不完整会拒绝整个候选。4 点 bias/load 搜索得到 2 个可行和 2 个 THD/功耗超限点，最高 GBW 点被质量约束正确拒绝；2/4 预算耗尽、全不可行和真实首候选 transport 中断后的 checkpoint 恢复也已通过，before/after OA 参数完全一致。状态升级为 **read-only multi-analysis common-source quality tuning and recovery verified**；设计参数质量写回和 corner 仍待验证。

随后同一专用 cell 完成 8 点 W/RD/RS 质量搜索：8/8 候选都完成 OA 写入回读、同源 netlist、AC、linearity 和 noise；候选 4 的真实 transport 中断在独立 OA 回读后从 4 恢复，没有重复 1–3。GBW objective 写回 `W=1 µm/RD=20 kΩ/RS=1 kΩ`；全不可行任务恢复该基线且 selection 为空。线性度优先的两点控制任务则自动写回 RS=2 kΩ，使 THD 降低 41.93%、P1dB 提升 37.26%、DC 功耗降低 17.27%，代价是 GBW 降低 29.30% 与输入参考噪声增加 15.03%。状态升级为 **bounded common-source multi-analysis design-parameter tuning, writeback, and recovery verified**。

实例参数面现已在任务契约、planner、demo、Bridge worker 和双重定向回读中实现。`existing_schematic` 不要求目标符合反相器或共源模板，可保留 Bridge 的完整结构读取并向任意已有实例透传 Bridge 字符串参数；空值或长值不再因通用摘要不可见而被 VDA 拒绝。固定模板还允许 semantic 与实例参数组合，并以最终 OA 同时满足两组请求为成功条件。通用只读 live smoke 在 Gate 2A cell 上读到 MN0 的 233 个 CDF 字段、RD0 的 2 个字段及完整 geometry/nets/pins；专用 `vda_param_surface_001` 又闭合 `fingers=2`、`r=22K` 的 callback、立即回读和独立 after 回读。`m=2` 被 PDK callback 恢复为 `1`，且多字段失败留下已保存前缀，证明该能力不能外推为任意字段可持久化或事务式写入。

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

需要用户操作 Virtuoso/ADE 的验证已按用户决定延期，并集中记录在 [`deferred-manual-gates.md`](deferred-manual-gates.md)：包括人工修改/保存/重跑后的双向交接、旧 ADE L state 备份后迁移与重开，以及相同 history/output 的人工数值交叉检查。这些项目不阻塞后台自动化实现，但在真实完成前仍保留为未验证边界；延期记录本身不构成远端授权。

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
  -> 共源 nominal DC (Gate 2A 已通过)
  -> 显式实例参数面 (可持久化字段 live 双重回读已验证)
  -> 受控拓扑小变更（同一既有 cellview 原位增加 source degeneration）
  -> 各拓扑 AC gain/bandwidth（nominal/退化只读同源 smoke 已通过）
  -> 有限 AC trade-off 与失败/预算/恢复路径（已通过）
  -> 功耗 + transient 线性度 + noise（单点只读 live 已通过）
  -> 多 analysis 质量约束与受预算调优（W/RD/RS 写回、失败门与恢复已通过）
  -> ADE 双路径（prepare + analysis/output add-only patch + background run/resume/raw-input consistency + global CL/VDD sweep + test-scope CL × VDD environmental-corner + delay/skew/energy constraint mapping 已 live；capture 与人工交接待验证）
  -> 共源 L/VDD + 多 analysis + 固定设计有限真实 PVT（已通过）
  -> PVT-aware 设计候选调优（下一自动化 Gate）
  -> 差分对
  -> L5B 单模块闭环
  -> layout/DRC/LVS/PEX Gate
```

每一级只有在真实 Bridge smoke、结构回读、指标解析和失败注入均通过后才升级状态。

反相器可靠性 Gate 1R、共源 nominal DC、显式实例字段、源极退化原位 transform/DC tuning、AC/quality、W/RD/RS/L/VDD 写回、固定设计 TT/SS/FF，以及预算/不可行/transport 恢复均已有 live 证据。Gate 2A 现在可以让不同 objective 在同一候选证据上得到不同 OA 设计，并对一个固定设计做跨条件最坏值判定。下一自动化硬门是把相同 `operating_conditions` 证据门接入有限 `design.tune`：每个设计候选都必须跨全部 PVT 条件完成，随后验证最佳 OA 写回、全不可行、预算耗尽和 transport checkpoint。通过后再进入差分对。ADE 的真实 PVT/multi-test、已有 output 安全替换，以及人工打开/修改/重跑和旧 ADE L 迁移继续是独立 Gate；完成前仍不能升级为可重复的 L5B 单模块规格闭环。
