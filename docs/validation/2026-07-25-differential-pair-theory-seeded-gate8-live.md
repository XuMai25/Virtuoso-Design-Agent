# 2026-07-25 差分对理论先导 Gate 8 真实验证

## 结论

Gate 8 已把 Gate 7D 的真实 TSMC N28 器件表/held-out 验证接到正常 VDA
`design.tune` 状态机，并在既有
`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic` 上完成六个原子理论候选的
OA 写入、独立回读、自动 `si` netlisting、Spectre DC/差模 AC/共模 AC、约束判定、
checkpoint/resume 和最佳点写回。最终选择 `theory-seed-002`，不是理论排名第一的
`theory-seed-001`。

这证明的是：**real-PDK theory shortlist → bounded same-source EDA selection verified at
nominal top_tt**。理论候选生成门通过，但逐点预测精度门未通过；因此理论层目前是比
人工随手列值更有依据的候选生成器，不是可替代 Spectre 的数值预言器，也不是连续或
全局优化器。PVT 本 Gate 按授权未执行。

## 授权与目标

- 目标：`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`
- PDK：`nics4304_tsmc28`，nominal `top_tt`，27 °C，0.9 V
- OA 写入：允许，只修改既有 `MN0/MN1`、`MP0/MP1`、`MNTAIL` 的 W/L
- 远端计算：允许，自动 OA→`si`→Spectre
- 覆盖：`replace_existing=false`，不新建或覆盖 cellview
- 远端根：`/data/xum/virtuoso_bridge_smoke/vda_*`，不写 `/home/xum`
- 计划 token：`7b1c4dea025eaa53`
- task SHA-256：`b7be222b54f611f8a1a6dd22dee9f91f7a9190617b7fb8fe775319c708110096`
- 最终 run SHA-256：`7be715fe80c299ae6a250f50582e30d6ae31fe3acfab3bf539e77ab22e6d517f`

任务文件为
`examples/tasks/differential-pair-current-mirror-theory-seed-gate8.bridge.json`。它仍需
匹配 plan token 与显式执行参数，提交到仓库不构成以后再次写 OA 的授权。

## 从真实器件表到原子候选

`vda theory-request-from-validation` 只接受通过的 Gate 7D validation hash 和三张 exact
W/L、31 项 `si` signature 的真实 characterization run hash。它从已验证器件表的 VGS
轴、当前 held-out DC 偏置和 signed `dQi/dVj + cjd/cjs` 数据派生 theory request；规则和
规格来自显式 policy。原始器件/电路结果属于 `eda_result`，OA 结构属于
`bridge_readback`，派生表点、方程和排序属于 `software_inference`，policy 属于
`user_input`。

理论求解穷尽 64 个声明组合，12 个满足其一阶约束。`vda theory-seed-task` 保留理论前两
名，再用归一化 log-distance maximin 从其余可行点中选择四个覆盖点。候选以完整 tuple
原子存储，executor 不对 Wn/Wp/Wtail 做笛卡尔积。连续理论宽度在生成 plan 前按显式
`0.005 µm` 网格和 half-up 规则量化；量化方式、网格、request/result/policy hash 和理论
域穷尽状态全部进入 task、plan token 和 run record。

首个未量化尝试提供了必要的失败证据：理论请求 `Wn=1.316019 µm`，OA 实际回读
`1.315 µm`，VDA 在仿真前拒绝参数一致性，并把初始 `Wn=1.5/Wp=1.5/Wtail=0.8 µm`
精确恢复。随后才固定 5 nm 网格重新生成任务；没有降低回读容差或把量化误差静默吞掉。

## 六点真实搜索

固定条件为 `L=30 nm`、`BIAS=0.32 V`、`VCM=0.55 V`、`VDD=0.9 V`、
`CL=0.5 fF`。每点均来自自己的 OA 写入/回读和自动 `si` netlist；表中性能是
Spectre `eda_result`。

| seed | Wn/Wp/Wtail (µm) | 可行 | 功耗 (µW) | 增益 (V/V) | BW (GHz) | GBW (GHz) | CMRR (dB) |
|---|---:|:---:|---:|---:|---:|---:|---:|
| 001 | 1.315/1.180/1.090 | 是 | 18.6865 | 3.7776 | 4.2483 | 16.0485 | 34.1711 |
| 002 | 1.315/1.180/0.605 | 是，选中 | 10.9810 | 3.7277 | 2.6895 | 10.0258 | 34.8150 |
| 003 | 2.205/1.980/0.600 | 否 | 11.3620 | 3.7096 | 1.9766 | 7.3326 | 35.5821 |
| 004 | 1.135/1.980/0.600 | 否 | 10.8231 | 3.5831 | 2.1810 | 7.8149 | 34.9887 |
| 005 | 1.000/1.745/0.890 | 是 | 15.3072 | 3.5818 | 3.2220 | 11.5405 | 34.3824 |
| 006 | 2.190/1.000/1.005 | 是 | 18.0669 | 3.8998 | 3.7156 | 14.4898 | 34.6361 |

约束要求全信号管饱和、负载电流失配不超过 1%、输出摆幅余量至少 0.1 V、功耗不超过
25 µW、增益至少 3 V/V、BW 至少 2.5 GHz、GBW 至少 9 GHz、peaking 不超过 3 dB、
CMRR 至少 30 dB；目标是在可行点中最小化真实 DC 功耗。四点可行，故 seed 002 被
EDA 选中并写回。其最小输出摆幅余量为 `0.2120 V`，五个器件均处于饱和区。

selected netlist 位于
`/data/xum/virtuoso_bridge_smoke/vda_differential-pair-current-mirror-theory-seed-gate8-bridge_9019fe54ed6f/netlist`，
SHA-256 为 `5ed747565bff138be935252af874cb5ef86e5265a26d44acd714b0de89f80b0f`。
最终独立 `schematic.inspect` 再次确认 5 个实例、8 条网络、8 个 pin 和三组尺寸完全匹配。

搜索审计只声明 `best_in_declared_discrete_domain`；六点已全部执行，
`continuous_optimum_claim=false`、`global_optimum_claim=false`。

## 中断与恢复

网格任务在前五点完成、准备第六点时发生 `WinError 10054`。该事件被分类为 transport
`system_event`，不是电路不可行。executor 将 OA 恢复到运行前基线，checkpoint 保存
前五点；独立 inspect 确认基线后从 `next_candidate_index=6` 继续，没有重放不确定 payload
或已完成前缀。续跑完成第六点、选择 seed 002 并独立回读。后续 ICMR 在第六点后再次
遇到相同 transport reset，也按 6/10 checkpoint 和独立 OA 回读从第七点继续到 10/10。

## 选中点的只读质量复核

这些 follow-up 不再写 OA：

- PSRR：低频 PSRR+ `11.4828 dB`、PSRR− `13.4344 dB`。它只通过本任务现有 DC/AC
  完整性条件，没有闭合 PSRR；数值仍明显低于此前仅用于证伪方向的临时 20 dB 门。
- ordinary noise（1 kHz–1 GHz）：输入参考积分噪声 `758.831 µV_rms`，输出积分噪声
  `2.8123 mV_rms`；通过当前 `1000 µV_rms` 工程门。
- coherent transient：5/20/50 mV peak 三点的最大 THD `1.5169%`，50 mV 时增益压缩
  `0.2668 dB`，平均供电功耗 `10.9941 µW`。没有达到 1 dB 压缩，故 P1dB 未包围。
- ICMR：0.35–0.75 V 在声明的 50 mV 网格上可行；0.80 V 因输出摆幅余量约
  `0.03 V < 0.05 V` 不可行。这里只能报告离散网格，不外推连续边界。

## 理论预测校验

`vda theory-seed-validate` 用 task/run SHA-256、plan token、候选顺序、原子参数、理论
provenance、`eda_result` metric source 和完整离散域审计进行 CAS。固定 25% 逐点门得到：

- EDA 可行 4/6，比例 `0.6667`，高于预先声明的 0.5；候选生成门通过。
- 理论首选 seed 001，EDA 最小功耗选择 seed 002；推荐不一致。
- 24 个功耗/增益/BW/GBW 对照中 8 个超过 25%；逐点预测精度门失败。
- seed 002/005/006 四项预测都通过；seed 001/003/004 至少一项失败。seed 001 的功耗、
  BW、GBW 误差分别为 `107.78%/61.84%/50.66%`。

最终 validation 状态为 `partial`。该结果不能通过在同一六点上拟合后再自证修复；它揭示
固定 BIAS 下尾管宽度改变真实电流，而当前一阶理论候选仍以解析支路电流反解尺寸，跨
工作点的点预测没有充分重线性化。下一理论 Gate 应在少量第一遍真实 DC OP 后更新每个
候选的 gm/gds/cap/current，再用未参与校正的 held-out 候选验证；Spectre 仍负责最终选择。

## 资源与第三方边界

Gate 后只读 inventory 得到远端 `spectre=0`、`si=0`、Maestro session=0；两个远端
Virtuoso 进程属于共享 CAD 环境。连续两次新的只读 inspect 后，本地仍是同一组
`ssh.exe` PID `38784/41248`，两者启动时间均为 `16:29:14`，没有新增 Python/SCP。
这是一条有意复用的 tunnel/jump 双进程链，不是每次调用泄漏。资源 inventory 为 dry-run，
没有删除本地或远端证据。

本 Gate 没有修改 `virtuoso-bridge-lite`。所有新增契约、编译、验证和文档都在 VDA
仓库中，故不产生新的第三方补丁/分支兼容负担。

## 证据文件与未验证边界

主要本地记录：

- 最终搜索：`artifacts/runs/differential-pair-current-mirror-theory-seed-gate8-bridge/run-20260725-grid-resumed.json`
- checkpoint：`.../checkpoint-20260725-grid-live.json`
- 独立回读：`.../inspect-selected-20260725.json`
- follow-up：`.../psrr-selected-20260725.json`、`noise-selected-20260725.json`、
  `linearity-selected-20260725.json`、`icmr-selected-resumed-20260725.json`
- 理论对照：`artifacts/theory/differential-pair-gate8-seed-validation.json`
- 资源 dry-run：`.../resource-audit-after-gate8-20260725.json`

未验证边界包括 PVT、mismatch/Monte Carlo、PSRR 规格闭合、P1dB 包围、slew/settling、
负载/输入摆幅的二维质量面、ADE setup/history 交接，以及理论 DC OP 重线性化后的独立
held-out 精度。这些边界不影响本 Gate 已证明的原子 theory-seeded 同源有限搜索，但阻止
把它升级为完整 L5B 设计质量闭环。
