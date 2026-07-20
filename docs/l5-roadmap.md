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

此外，人工可以通过实例名、原始 CDF 参数名和值字符串要求一次独立参数写入；VDA 必须保留请求、写入前值、立即 OA 回读和独立再次回读。该能力不等于系统已经知道任意参数的物理含义，也不自动扩大优化搜索空间。

L5A 不允许自行发明无限搜索范围、修改 PDK、覆盖未知 cell、把 demo 模型当 EDA 结果，或在缺少证据时宣布 closure。

当前实现状态：`OA schematic -> si -> Spectre -> metrics`、供电能量积分、失败注入和候选级 checkpoint/resume 已通过本地测试。2026-07-19 live 结果覆盖 OA/`si` 参数一致性、非空 timing/current 波形、收紧规格、不可行 + 预算耗尽恢复，以及一个经历 3 次 tunnel 中断后仍完成 9/9 候选、最佳参数写回和独立 OA 回读的恢复任务。反相器 L5A 的同源有限闭环与显式恢复 Gate 已通过；Bridge 本地隔离补丁又通过强制断链只读 smoke，闭合 Windows stale state 与调用边界自动重建。运行中传输的随机 reset/timeout 仍是跨 Gate 的底层可靠性债务。

Gate 2A 又把相同执行语义扩展到电阻负载 NMOS 共源级：新 OA cellview 的 MN0/RD0 结构、W/L/R 回读和 `si` 网表一致性通过；显式保存的 Spectre DC OP 提供 Id/VGS/VDS/VDSAT/gm/gds，6 点 W/Vbias 搜索完成 3 个可行点、3 个线性区点、最佳 W 写回和最终独立紧规格复核。该结果只闭合 common-source nominal DC；当时 AC、source degeneration、noise 和 corner 均未验证。

2026-07-20 已按“微调已有 schematic、避免重建”的目标实现并真实验证源极退化原位 transform：同一 cellview 只改 MN0.S 网名并新增 RS0/NSRC，前后审计保护 MN0/RD0、pins、位置和未点名参数；同一个 common-source adapter 随后读写 RS、自动解析退化网表、提取 NSRC DC 指标并完成 6 点 Vbias×RS 搜索。一次 transport 中断从候选 3 续跑，最佳 `Vbias=0.35 V, RS=2 kΩ` 写回并由全新 worker 独立回读。该证据把 source-degenerated DC execution/tuning smoke 升级为 live verified；随后只读 AC 也已过门，但 AC 调优和完整设计质量闭环仍未通过。

同日已把共源 AC 做成正式能力，而不是一次性脚本：同一 OA→`si` 网表运行 DC OP 和复数 AC，并提取 gain、首个 −3 dB bandwidth、GBW、unity-gain 与相位。nominal/退化只读 smoke、各 6 点 bias/load 搜索、同参数只加 RS 的控制变量对比，以及 W/RD/RS 预算/不可行/完整 8 点搜索均已真实通过。完整搜索选择并回读 W=1.0 µm、RD=20 kΩ、RS=1 kΩ；多次 transport 中断都在 OA 恢复或独立 readback 后从 checkpoint 继续。状态升级为 **bounded common-source AC design-parameter tuning and recovery verified**。

设计质量分析已完成本地实现和单点 live Gate：实际 `VDD_SRC:p` DC 功耗与独立 KCL、相干 transient 幅度 sweep 的 gain/HD2/HD3/THD/P1dB/平均功耗，以及 ordinary noise 的输出/输入参考频带积分继续复用同一 OA→`si` worker。专用 cell 的 5 点 100 MHz sweep 解析出 `P1dB=88.32 mV peak` 和 150 mV 点 `THD=13.16%`；211 点 1 kHz–10 GHz PSF 得到输出/输入参考积分噪声 3.304/0.983 mV RMS。真实目录形状暴露的 DC info 覆盖和 transient 端点问题已在 VDA 层修正，没有改 Bridge。当前升级为单点 design-quality analysis execution verified，尚未升级为质量驱动的参数/规格闭环。

实例参数面现已在任务契约、planner、demo、Bridge worker 和双重定向回读中实现。`existing_schematic` 不要求目标符合反相器或共源模板，可保留 Bridge 的完整结构读取并向任意已有实例透传 Bridge 字符串参数；空值或长值不再因通用摘要不可见而被 VDA 拒绝。固定模板还允许 semantic 与实例参数组合，并以最终 OA 同时满足两组请求为成功条件。通用只读 live smoke 在 Gate 2A cell 上读到 MN0 的 233 个 CDF 字段、RD0 的 2 个字段及完整 geometry/nets/pins；专用 `vda_param_surface_001` 又闭合 `fingers=2`、`r=22K` 的 callback、立即回读和独立 after 回读。`m=2` 被 PDK callback 恢复为 `1`，且多字段失败留下已保存前缀，证明该能力不能外推为任意字段可持久化或事务式写入。

Bridge 隔离分支进一步加入幂等 SSH 有界退避和仅限 payload 发送前的 tunnel 自愈。新的 9 点压力任务仍在候选 8 发生一次本地端口拒绝，但 OA 恢复、候选前缀和续跑均正确，最终 9/9 与最佳写回成功；确定性同-client smoke 已覆盖 pre-send 自愈。payload 发送后的不确定错误仍不自动重放，这是保留的可靠性边界而不是跳过的工作。

## L5B：单模块设计代理（产品目标）

面向反相器、单管放大器、差分对等单模块，由规格驱动完成更完整的设计过程：

- 从受控拓扑目录中选择或拒绝拓扑。
- 自动建立 testbench、analysis、output 和 sweep。
- 处理 DC operating point、AC、tran、noise 和多 corner。
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
  -> 多 analysis 质量约束与受预算调优
  -> 差分对
  -> 多 analysis + corner
  -> L5B 单模块闭环
  -> layout/DRC/LVS/PEX Gate
```

每一级只有在真实 Bridge smoke、结构回读、指标解析和失败注入均通过后才升级状态。

反相器可靠性 Gate 1R、共源 nominal DC、显式实例字段、源极退化原位 transform/DC tuning、只读 AC 条件搜索、W/RD/RS AC 写入调优，以及单点功耗/linearity/noise live 分析均已通过。Gate 2A 现在具备有边界的 DC+AC 参数闭环和 DC+AC+transient+noise 执行证据；下一道硬门是把这些 analysis 组合成受预算的质量约束/目标，验证可行、不可行、预算耗尽和恢复，再做有限 corner 与 L/VDD 联合搜索。通过这些项前不能升级为可重复的 L5B 单模块规格闭环。
