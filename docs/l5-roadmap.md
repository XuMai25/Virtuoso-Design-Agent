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

L5A 不允许自行发明无限搜索范围、修改 PDK、覆盖未知 cell、把 demo 模型当 EDA 结果，或在缺少证据时宣布 closure。

当前实现状态：`OA schematic -> si -> Spectre -> metrics`、供电能量积分、失败注入和候选级 checkpoint/resume 已通过本地测试。2026-07-19 live 结果覆盖 OA/`si` 参数一致性、非空 timing/current 波形、收紧规格、不可行 + 预算耗尽恢复，以及一个经历 3 次 tunnel 中断后仍完成 9/9 候选、最佳参数写回和独立 OA 回读的恢复任务。反相器 L5A 的同源有限闭环与显式恢复 Gate 已通过；Bridge 本地隔离补丁又通过强制断链只读 smoke，闭合 Windows stale state 与调用边界自动重建。运行中传输的随机 reset/timeout 仍是跨 Gate 的底层可靠性债务。

Gate 2A 又把相同执行语义扩展到电阻负载 NMOS 共源级：新 OA cellview 的 MN0/RD0 结构、W/L/R 回读和 `si` 网表一致性通过；显式保存的 Spectre DC OP 提供 Id/VGS/VDS/VDSAT/gm/gds，6 点 W/Vbias 搜索完成 3 个可行点、3 个线性区点、最佳 W 写回和最终独立紧规格复核。该结果只闭合 common-source nominal DC；AC、source degeneration、noise 和 corner 仍未验证。

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
  -> 共源 AC gain/bandwidth
  -> 源极退化 DC -> AC
  -> 差分对
  -> 多 analysis + corner
  -> L5B 单模块闭环
  -> layout/DRC/LVS/PEX Gate
```

每一级只有在真实 Bridge smoke、结构回读、指标解析和失败注入均通过后才升级状态。

反相器可靠性 Gate 1R 已通过 VDA 显式 checkpoint/resume 路径：Bridge 恢复后能核对 OA、继续剩余候选、保留原始基线，并在最终写回中断时只重试 finalize。Bridge 本地补丁 `9e52844` 已让 stale state/调用边界自动重建通过确定性测试和 live smoke，但不能外推为运行中 transport 永不掉线。共源 Gate 2A nominal DC 现已通过；下一道硬门是在最终 DC 偏置点上建立同源 AC gain/bandwidth 和有限 trade-off，再让源极退化拓扑独立通过 DC→AC。`gate_area_proxy_um2` 仍只是尺寸代价；真实供电能量/平均功率已经加入，但尚无 corner、输入电容或版图面积。
