# 2026-07-23 差分对 Gate 3 同源 DC/AC/CMRR/线性度真实验证

状态：**differential-pair nominal same-source DC/AC/CMRR/linearity, bounded writeback, read-only tuning, and transport recovery verified**。

## 授权与执行边界

本轮经用户明确授权，在现有 `vb_pdk_smoke` library 下新建且不覆盖：

```text
vb_pdk_smoke/vda_diffpair_gate3_001/schematic
```

- PDK/profile：`nics4304_tsmc28` / TSMC N28；
- OA write：只用于新建该 cell、有限设计搜索的候选暂存、恢复和最佳点写回；
- remote compute：只写 `/data/xum/virtuoso_bridge_smoke/` 下的任务 scratch；
- `replace_existing=false`；
- 没有修改 `virtuoso-bridge-lite`，没有读取或修改 Bridge 凭据；
- 没有运行 ADE/Maestro、PVT、noise、mismatch、layout、DRC/LVS/PEX。

目标 DUT 固定为四个 OA 实例：

```text
MN0: OUTP INP TAIL VSS
MN1: OUTN INN TAIL VSS
RD0: VDD OUTP
RD1: VDD OUTN
```

理想尾电流源、输入源、有限尾源输出电阻和输出负载都只存在于本次 Spectre wrapper，不写入 DUT。最终 OA 为 `W=2 µm`、`L=30 nm`、每侧 `RD=8 kΩ`。

## 从既有流程迁移的能力

差分对没有另建 planner、executor、搜索器或 Bridge 副本，而是接入此前反相器/共源已经使用的 operation 和状态机：

- `schematic.create` / `schematic.inspect`；
- `parameters.apply`；
- `simulation.run`；
- `design.tune` / `design.close_loop`；
- token、远端写授权、library/cell 前缀、non-overwrite 检查；
- 逐候选 OA 暂存、定向回读、自动 `si` netlist、Spectre、约束、选优、checkpoint、恢复和最终回读。

本 Gate 真实覆盖了 nominal DC、差分 AC、配对差模/共模 AC、输入共模有限搜索、差分 transient 和只读尾电流×负载 AC 搜索。因此此前 direct `si`/Spectre 的核心执行链已经迁移到该固定新拓扑；尚不能把 ADE、noise、PVT 或任意拓扑编辑称为已无缝迁移。

## OA、网表与写回

新 cell 创建后，Bridge 结构化回读确认四个实例、七个 pins/nets、master 和连接。VDA 进一步核对 MN0/MN1 的单指宽、`nf/fingers`、`multi/m`、总宽和 L，以及 RD0/RD1 的 R；`si` 网表再独立核对同一组 geometry 和节点。

主要记录：

- 单点 DC：`artifacts/runs/differential-pair-dc-verify/live-20260723-retry5.json`；
- 9 点尾电流×共模只读搜索：`artifacts/runs/differential-pair-dc-bias-tune/live-20260723-resume1.json`；
- 2/9 预算截断：`artifacts/runs/differential-pair-dc-design-budget/live-20260723.json`；
- 全不可行并恢复初值：`artifacts/runs/differential-pair-dc-design-infeasible/live-20260723.json`；
- 18 点设计搜索与写回：`artifacts/runs/differential-pair-dc-design-tune/live-20260723-v2-resume2.json`。

18 点搜索在候选 3 和候选 9 分别遇到 transport reset。两次都没有把网络错误当成电路不可行，也没有跳过未完成候选；VDA 通过独立 OA readback 确认已保存状态，再从 checkpoint 继续，最终完成 18/18 并写回最佳 OA。最终 `si` netlist SHA-256 为：

```text
45282f46941299d49327ca88bcc0a36279d2bb050426d6c4b76ec4fec75cd588
```

后续 AC、共模范围和 transient 记录都再次得到该 SHA；动态 testbench 条件没有改变 DUT。

## Nominal DC

最终 OA 在 `Itail=50 µA`、`VCM=0.45 V`、`VDD=0.9 V` 下：

- 两支路约 `25.0002 µA`；
- 尾电流失配 `0.000858%`；
- 电源/负载 KCL 误差 `0.000562%`；
- 两管均通过饱和判定，最小饱和余量 `0.4253 V`；
- 摆幅余量 `0.25 V`；
- VDD 功耗约 `45 µW`。

这些值来自器件 OP、源电流和节点电压，不是由退出码推断。

## 差分 AC、带宽、GBW 与负载权衡

记录：`artifacts/runs/differential-pair-ac-verify/live-20260723.json`。

平衡输入采用 `VINP=+0.5 Vac`、`VINN=-0.5 Vac`，从 `(OUTP-OUTN)/(INP-INN)` 的复数响应提取：

| 指标 | 真实结果 |
|---|---:|
| 低频差分增益 | 2.99194 V/V（9.519 dB） |
| 首个 −3 dB 带宽 | 29.1619 GHz |
| GBW | 87.2507 GHz |
| unity-gain frequency | 86.8035 GHz |
| AC 点数 | 181 |

只读四点尾电流×每端负载搜索记录为 `artifacts/runs/differential-pair-ac-tail-load-tune/live-20260723.json`。四个点都保持同一 OA/netlist SHA：

| Itail | 每端 CL | gain | bandwidth | GBW | VDD power |
|---:|---:|---:|---:|---:|---:|
| 40 µA | 0.5 fF | 2.600 | 17.945 GHz | 46.665 GHz | 36.000 µW |
| 50 µA | 0.5 fF | 2.992 | 18.621 GHz | 55.714 GHz | 45.000 µW |
| 40 µA | 2.0 fF | 2.600 | 8.597 GHz | 22.356 GHz | 36.000 µW |
| 50 µA | 2.0 fF | 2.992 | 8.975 GHz | 26.852 GHz | 45.000 µW |

在该任务的约束和 GBW 目标下选择 `50 µA/0.5 fF`。这是已评估有限网格内的最优 testbench 条件，不是连续全局最优，也没有写 OA。

## 配对 AC 与 CMRR

理想尾电流源的小信号输出电阻为无穷大，不能用它直接声明一个现实 CMRR。本任务显式在理想 DC sink 上并联 `1 MΩ` 的 testbench-only `tail_output_resistance_ohm`，然后从同一份 OA/`si` 网表分别运行：

1. 平衡差模 AC；
2. 同相共模 AC。

VDA 要求两次 DC OP 和频率网格一致，再计算复数传输函数比值。成功记录为 `artifacts/runs/differential-pair-cmrr-verify/live-20260723-cmrr-bandwidth.json`：

- 差模低频增益 `2.99796 V/V`；
- 差模带宽 `29.1777 GHz`；
- 差模 GBW `87.4738 GHz`；
- 共模低频增益 `0.00352676 V/V`（`−49.0525 dB`）；
- 低频 CMRR `58.5890 dB`（`850.061 V/V`）；
- CMRR 相对低频值首次下降 3 dB 的带宽 `306.381 MHz`。

共模增益会在高频上升，并不存在本 sweep 内的“共模传输自身低通 −3 dB 带宽”。第一次记录 `artifacts/runs/differential-pair-cmrr-verify/live-20260723.json` 因错误要求该指标而保留为 `partial`；实现随后改为 CMRR 频响的下降 3 dB 定义并真实重跑成功。旧记录没有被改写成成功。

## 输入共模范围采样

主记录 `artifacts/runs/differential-pair-common-mode-range/live-20260723-resume1.json` 覆盖 `0.20–0.85 V` 十点。候选 9 后发生 `WinError 10054`，恢复后通过 `artifacts/runs/differential-pair-inspect/recovery-common-mode-range-20260723.json` 独立确认 OA，再从 9/10 checkpoint 完成。

补充上边界记录 `artifacts/runs/differential-pair-common-mode-upper-edge/live-20260723.json`：

- `0.25 V`：TAIL 为 `−6.94 mV`，失败；
- `0.30 V`：TAIL 为 `37.07 mV`，通过；
- `0.875 V`：最小余量 `62.29 mV`，通过；
- `0.90 V`：两管仍满足当前饱和分类，但最小余量 `40.44 mV < 50 mV`，失败。

所以当前只能报告采样通过区间 `0.30–0.875 V`，低边界夹在 `0.25–0.30 V`，高边界夹在 `0.875–0.90 V`。它不是连续解析 ICMR，也不包含真实尾管的 compliance 限制。

## 差分 transient、THD 与 P1dB

记录：`artifacts/runs/differential-pair-linearity-verify/live-20260723.json`。

Spectre nested sweep 使用 100 MHz 相干正弦、每端 1 fF，对七个差分输入 peak 幅度 `0.005/0.02/0.05/0.10/0.15/0.20/0.25 V` 逐点运行。VINP/VINN 分别施加 `+vindiff/2` 和 `−vindiff/2`，且每点都用波形确认实际 `VINP-INN` 基波与声明幅度一致。

- 小信号差分增益 `2.99118 V/V`；
- 输入 P1dB `110.905 mV peak`；
- 输出 P1dB `293.524 mV peak`；
- 250 mV peak 输入下增益 `1.82968 V/V`，压缩 `4.269 dB`；
- 同一点 THD `16.5355%`、HD3 `−15.748 dBc`；
- HD2 `−287 dBc`，只能视为平衡理想模型下的数值底噪；
- 全电路最大平均 VDD 功耗约 `45.0004 µW`。

差分传输量使用 `differential_*` namespace，VDD 积分量使用 `transient_*_supply_power_*`，不把单端振幅或器件支路功耗混成差分指标。

## 证据来源

- `user_input`：目标 cell、有限候选、VDD、尾电流、共模、尾源输出电阻、负载、sweep、约束和目标；
- `bridge_readback`：OA instances/nets/pins、W/L/R、最终参数和恢复后的独立结构回读；
- `eda_result`：`si` netlist、Spectre DC/AC/transient 波形、器件 OP、源电流、KCL、连续指标和文件哈希；
- `software_inference`：饱和分类、频率交点、CMRR 比值/带宽、P1dB 插值、约束判定、有限网格选优和 checkpoint 状态；
- `system_event`：transport reset/`WinError 10054`。

## 本地验证

- `pytest`：`426 passed`；
- `vda catalog`：成功；
- 必需反相器 demo plan：成功；
- 全部 example task plan：`98/98` 成功；
- `git diff --check`：成功。

这些测试验证契约、planner、metrics、adapter 边界、失败处理和既有流程兼容性；电路数值仍只由上述 live `eda_result` 支撑。

## 尚未验证与下一 Gate

1. 把 wrapper 理想尾源升级为真实尾电流晶体管/偏置网络，并验证其 compliance、输出电阻和可调参数；
2. differential output/input-referred noise 及其与增益/带宽/功耗的权衡；
3. 差分对 ADE/Maestro setup、人工打开/调整/重跑和结果交接；
4. mismatch/Monte Carlo、器件失配引起的 offset/CMRR；
5. 可选 PVT；它保持显式 opt-in，不成为普通 nominal 任务的默认成本；
6. layout、DRC、LVS、PEX 和 signoff。

下一默认 Gate 是在保持现有四实例 DUT、同源 netlist 和恢复语义的前提下，以受控增量拓扑加入真实尾管/偏置，再逐项复跑 DC、差分 AC/带宽/GBW、CMRR、输入共模范围和 transient；通过后才加入 differential noise。这样验证的是能力迁移，而不是只跑一个孤立数值。
