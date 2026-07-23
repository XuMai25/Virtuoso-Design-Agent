# 2026-07-23 差分对 PSRR 三次同网表 AC 本地实现

日期：2026-07-23
状态：**PSRR same-source execution contract locally implemented; live Spectre/PSF evidence was pending at this snapshot**。

后续状态：2026-07-24 已在 nominal TSMC N28 Gate 6 cell 上完成不写 OA 的真实三次 AC 单点，见 [`2026-07-24-differential-pair-psrr-live.md`](2026-07-24-differential-pair-psrr-live.md)。本文件保留为 live 前的本地契约与测试快照。

## 目标与边界

本 Gate 在已经真实闭合的 Gate 6 PMOS 电流镜负载差分对上增加电源抑制分析，不改变 OA topology，不新建仿真框架，也不修改 `virtuoso-bridge-lite`。新增能力是正式任务契约 `analysis: "psrr"`，可独立用于 `simulation.run`，也可复用现有 `design.tune`/checkpoint/selection 状态机。

当前只证明本地代码、规划和 synthetic worker 流程可执行。本文没有真实 nics4304 Spectre 数值，不能把 demo 中的 40 dB/60 dB 合成值当作电路性能，也不能把本 Gate 写成 live closure。

## 分析定义

每个候选只生成并核对一次目标 OA schematic 的 `si` 网表，然后使用该同一远端网表运行三份 Spectre AC wrapper：

1. 平衡差模：`VINP=0.5∠0° V`、`VINN=0.5∠180° V`，故 `INP-INN=1 V`；
2. 正电源注入：VDD 为 `1∠0° V` AC，VSS 无 AC；
3. 负电源注入：VSS 为 `1∠0° V` AC，VDD 无 AC。

供电注入时 INP、INN 和 BIAS 保持理想对地 DC 参考。首版 worker 要求 OA 中存在真实 `MNTAIL`，明确拒绝用外部理想尾源 wrapper 代替 VSS 敏感电路。

对于 Gate 6 的单端输出契约：

```text
Ad   = OUTN / (INP - INN)
Avdd = OUTN / VDD
Avss = OUTN / VSS

PSRR+ = |Ad / Avdd|
PSRR- = |Ad / Avss|
```

电阻负载差分输出变体仍沿用 `(OUTP-OUTN)` 输出模式。若完全对称模型产生精确零 supply-to-output transfer，当前实现报告无法得到有限 PSRR，而不伪造无穷大标量。

## 完整性门

三次 AC 必须同时满足：

- 共享同一个 `si` netlist 路径与 SHA-256；
- 频点数量和逐点频率相同；
- 差模输入、被注入供电和输出复数波形均非空、有限且长度匹配；
- 三次独立 DC OP 的支路电流、尾电流、供电电流、TAIL 电压、输出共模和饱和余量一致；
- current-mirror 变体还核对镜像电流误差和 PMOS 最小饱和余量；
- 正、负 PSRR 的低频参考窗平坦，并在声明 sweep 内包围首次下降 3 dB 交点。

任一网格漂移、DC 漂移、零激励、零供电传输、空波形或未包围交点都会保留为失败/未完成，不因 Spectre return code 为 0 而宣称 PSRR 完整。

## 指标与证据

每个供电方向分别保存：

- low-frequency PSRR（V/V、dB、phase）；
- peak PSRR、peak frequency 和 peaking；
- PSRR 首次下降 3 dB 频点与该点 phase；
- supply-to-output low-frequency gain（V/V、dB）；
- 声明 sweep 内最小 PSRR 与 stop-frequency PSRR。

组合指标为 `minimum_low_frequency_psrr_db` 和 `minimum_psrr_db_over_sweep`，便于规格判定和有限搜索。

证据分类保持如下：

- OA topology、实例和参数回读：`bridge_readback`；
- `si` 网表、三次 Spectre DC OP 与复数 AC 数据：`eda_result`；
- `Ad/Avdd/Avss` 比值、PSRR、交点、网格与 DC 一致性判断：`software_inference`；
- 任务显式声明的 analysis、sweep、BIAS/common-mode/VDD/load：`user_input`；默认字段仍标 `software_inference`。

demo adapter 只生成解析器可检查的确定性合成响应，整个结果继续标为 `software_inference`。

## 搜索与写入边界

新增示例包括：

- `differential-pair-current-mirror-psrr.bridge.json`：单点、只读 OA、三次远端 AC；
- `differential-pair-current-mirror-psrr-bias-load-tune.bridge.json`：`tail_bias_v × load_ff` 四点有限搜索。

这两个维度都是 testbench 条件，因此第二个任务也不写 OA。若以后把 W/L 加入 parameter space，仍必须走现有逐候选 OA 写入、回读、checkpoint、最佳写回或初始恢复状态机；本次没有为 PSRR 绕过安全边界。

## 本地验证

执行结果：

```text
455 passed in 1.23s
138/138 example task plans passed
```

新增测试覆盖：

- schema 只允许差分对 PSRR，并强制 `ac_sweep`；
- planner 明示三次 AC、同网表/DC/网格和公式；
- 两份供电 deck 只注入对应 VDD 或 VSS，输入无 AC；
- 纯指标层提取 active-load PSRR+/PSRR−、supply gain、扫频最差值与 3 dB 频点；
- 网格不一致和零供电激励拒绝；
- subprocess adapter 传递 sweep，并为三次顺序运行放大外层 timeout；
- fake Spectre worker 真实经过 differential→positive→negative 三次运行，绑定同一网表并核对三次 DC；
- demo `simulation.run` 与不写 OA 的两点 `design.tune`；
- 旧 AC/CMRR、transient、noise、ADE、搜索和恢复回归未退化。

## 未验证边界与下一道 Gate

- 尚未在 `vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic` 上运行真实三次 AC；
- 尚不知道 nominal `top_tt` 的真实 PSRR+/PSRR−、高频形状以及 1 THz sweep 是否包围 3 dB 下降；
- 尚未执行真实 bias/load PSRR 搜索、不可行、预算或 transport 中断恢复；checkpoint 目前以完整候选为原子，候选中途失败会重跑该候选的三次 AC；
- 尚未验证 PVT、mismatch/Monte Carlo、bias reference 跟随 VSS 的其他定义、ADE/Maestro PSRR setup 或人工重跑；
- 尚未处理 slew/settling 和输出驱动边界。

下一步是对现有 Gate 6 cell 做一次不写 OA 的只读 live 单点：三次远端 AC，共用一份自动 `si` 网表，先验证真实 supply coupling、DC/网格一致性、非空 PSF 和交点包围。只有该点完整后，才执行可选的四点 `tail_bias_v × load_ff` 搜索。
