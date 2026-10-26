# 2026-07-23 差分对 PMOS 电流镜负载 Gate 6 live 验证

## 结论

状态：**Gate 6 current-mirror-load same-source bounded closure verified at nominal TSMC N28; broader design-quality closure pending**。

本轮在全新 `vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic` 上完成了非覆盖 create、真实尾管增量变换、RD→PMOS 电流镜增量变换、OA 结构/参数回读、自动 `si`、Spectre DC、差模/共模 AC、CMRR、ICMR、相干 transient、ordinary noise、testbench 条件搜索、输入管/PMOS 负载宽度搜索、预算耗尽、全不可行恢复、checkpoint/resume、精确 topology restore 和最终有源负载重建。最终 cell 留在已回读并重新通过 DC 的 `MN0/MN1/MNTAIL/MP0/MP1` 状态。

这仍是固定拓扑、单一 nominal PDK/model section 和显式有限网格内的 L5A 结果，不是任意拓扑综合，也不是完整 L5B 单模块闭环。

## 目标与边界

- library/cell/view：`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`；
- PDK profile：`nics4304_tsmc28`，TSMC N28，nominal `top_tt`；
- Bridge：`virtuoso-bridge 0.7.0`；
- `replace_existing=false`，create 返回 `created=true`、`already_exists=false`；
- 远端 `si` 产物位于 `/data/xum/virtuoso_bridge_smoke/` 下的独立 task/run 目录；
- 没有修改 `virtuoso-bridge-lite`，只使用其公开 OA/SKILL、`si`、Spectre 和传输接口。

固定有源负载拓扑为：

```text
MN0    (OUTP INP  TAIL VSS)
MN1    (OUTN INN  TAIL VSS)
MNTAIL (TAIL BIAS VSS  VSS)
MP0    (OUTP OUTP VDD  VDD)  # diode-connected mirror reference
MP1    (OUTN OUTP VDD  VDD)  # single-ended OUTN branch
```

动态输入是 `INP-INN`，主输出是 `OUTN`，`OUTP` 是镜像参考节点。所有动态 run evidence 均记录 `output_mode=single_ended_outn`。

## live 暴露并修正的实例命名错误

第一版 local 实现使用 `PM0/PM1`。OA 回读与 `si` 网表在节点、model、W/L 上都一致，但 Spectre 把以 `P` 开头的实例名解析为 `port` 元件，真实 DC 在网表第 26、33 行得到：

```text
ERROR (SFE-1703): Wrong number of nodes. Port instance support 2 or 3 terminals ...
```

该失败保存在 `06-dc-initial-20260723.json` 与带完整 `spectre.out` 上下文的 `07-dc-diagnostic-20260723.json`，没有被解释成偏置不可行。VDA 随后：

1. 精确恢复 RD0/RD1 和原 placement；
2. 将 Gate 6 的实例、OA/`si` parser、参数写入、OP save、指标键、demo、planner、executor、测试和文档统一改为 Spectre 安全的 `MP0/MP1`；
3. 增加通用 Spectre 失败详情提取，run record 会保留全部错误与 `spectre.out` 错误上下文；
4. 重新变换并独立 OA 回读后再运行 DC。

Bridge 仓库未修改。修正后的初始 OA 网表 SHA-256 为：

```text
21db02c89fc3db3e15354ef53a3e5d8c2762b7b4c0ae30a1b7d3db1137a125cf
```

初始 DC、AC/CMRR、transient 和 noise 四类分析均由该同一 SHA 的 OA 自动网表运行。

## 初始点同源证据

初始 OA 几何为：

- `MN0/MN1: W=2.0 µm, L=30 nm`；
- `MNTAIL: W=0.8 µm, L=30 nm`；
- `MP0/MP1: W=2.0 µm, L=30 nm`。

testbench 条件为 `VDD=0.9 V`、`VCM=0.55 V`、`BIAS=0.30 V`，动态负载为 `1 fF`。DC 通过 OA→`si` 参数/拓扑一致性、器件 OP 节点关系、两支路 KCL、VDD KCL、饱和区和联合摆幅检查：

- 两支 NMOS 电流约 `5.7798 µA`；
- 最大负载支路 KCL 误差 `0.00230%`；
- 电流镜电流误差 `6.72e-6%`；
- 五只信号器件均在饱和区；
- 最小联合输出摆幅 `0.19195 V`；
- DC 功耗 `10.4038 µW`。

初始动态结果：

| 指标 | 结果 |
|---|---:|
| 低频增益 | `3.6869 V/V` (`11.333 dB`) |
| -3 dB 带宽 | `1.5934 GHz` |
| GBW | `5.8748 GHz` |
| unity-gain frequency | `5.1737 GHz` |
| 低频 CMRR | `35.2398 dB` |
| 50 mVpeak THD | `2.0703%` |
| 50 mVpeak 增益压缩 | `0.2969 dB` |
| 50 mVpeak 输出 | `0.3513 Vpp` |
| transient 最大平均功耗 | `10.4178 µW` |
| 1 kHz–1 GHz 输入参考积分噪声 | `647.28 µVrms` |
| 1 kHz–1 GHz 输出积分噪声 | `2.3476 mVrms` |

第一次 transient 任务因声明了不存在的 `differential_small_signal_thd_percent` 而正确返回 `partial`。该任务契约被修正为正式指标 `differential_max_thd_percent`；相同真实波形在 `15-linearity-corrected-20260723.json` 中通过。没有把“指标名错误”包装成电路不合格。

## ICMR 与只读 testbench 搜索

### 10 点 ICMR

`BIAS=0.30 V` 下对 `VCM=0.35–0.80 V`、步进 `0.05 V` 运行 10 个完整 DC 候选。checkpoint 为 `complete=true`、`next_candidate_index=11`、10 个候选、无 pending OA：

- `0.35–0.75 V` 全部满足 `0.05 V` 联合摆幅 guard；
- `0.80 V` 时全部器件仍报告饱和，但联合摆幅仅约 `0.03 V`，因此不可行；
- 最大摆幅候选为 `VCM=0.60 V`，约 `0.19475 V`；
- OA 参数全程未写。

因此本轮只证明 **50 mV 离散步进下的可行窗口为 0.35–0.75 V**；没有测试 0.35 V 以下，也不把离散边界表述为连续精确 ICMR。

### 6 点 bias/load AC 搜索

在 `VCM=0.55 V` 下搜索：

```text
BIAS = [0.28, 0.30, 0.32] V
CL   = [0.5, 2.0] fF
```

全部 6 点完整且可行，最佳为 `BIAS=0.32 V, CL=0.5 fF`：

- 增益 `3.7169 V/V`；
- BW `2.5281 GHz`；
- GBW `9.3969 GHz`；
- unity `8.1453 GHz`；
- CMRR `35.3228 dB`；
- DC 功耗 `14.6549 µW`。

候选 3 的共模 AC 上传、候选 4 的差模 raw 下载分别遇到真实 SSH timeout。两次失败都作为 `system_event` 保留；doctor 恢复后从同一 checkpoint 的候选索引 3、4 续跑，没有重复已完成前缀。最终 checkpoint 为 `complete=true`、6 个候选、无 pending OA。该搜索只改变 testbench 条件，没有写 OA。

## OA 几何搜索、预算和不可行路径

在 `BIAS=0.32 V, VCM=0.55 V, CL=0.5 fF` 下声明：

```text
MN0/MN1 input_width_um = [1.5, 2.0]
MP0/MP1 pmos_load_width_um = [1.5, 2.0, 2.5]
```

`MN0/MN1` 与 `MP0/MP1` 每个候选均对称写入、逐只回读、重新 `si`，然后运行差模/共模 AC。六点结果为：

| Wn (µm) | Wp-load (µm) | gain (V/V) | BW (GHz) | GBW (GHz) | CMRR (dB) | power (µW) |
|---:|---:|---:|---:|---:|---:|---:|
| 1.5 | 1.5 | 3.742 | 2.976 | **11.135** | 34.845 | 14.292 |
| 1.5 | 2.0 | 3.679 | 2.625 | 9.660 | 35.069 | 14.312 |
| 1.5 | 2.5 | 3.634 | 2.344 | 8.518 | 35.214 | 14.327 |
| 2.0 | 1.5 | 3.777 | 2.830 | 10.691 | 35.091 | 14.632 |
| 2.0 | 2.0 | 3.717 | 2.528 | 9.397 | 35.323 | 14.655 |
| 2.0 | 2.5 | 3.674 | 2.279 | 8.371 | 35.469 | 14.671 |

六点全部满足约束，按 GBW 选择并写回 `Wn=1.5 µm, Wp-load=1.5 µm`。在相同 testbench 条件下，相对 `2.0/2.0 µm` 候选的 GBW 提升约 `18.5%`；这不是跨偏置/负载的纯几何外推。

额外 guard：

- 预算任务声明相同 6 点但 `max_iterations=1`；首点可行并写回，状态为 `partial`，明确记录“只是在已评估前缀内最佳”；
- 完整 6 点随后覆盖预算前缀并提交全声明网格最佳；
- 不可行任务用两点 PMOS W 和 `gain >= 100 V/V`，两个完整候选均只因增益规格失败；执行器未提交候选并恢复搜索前最佳 OA；
- 三个 checkpoint 均为 `complete=true`、无 pending OA。

## 最终最佳点

最终 OA 几何：

- `MN0/MN1: W=1.5 µm, L=30 nm`；
- `MNTAIL: W=0.8 µm, L=30 nm`；
- `MP0/MP1: W=1.5 µm, L=30 nm`。

最终 OA 自动网表 SHA-256：

```text
bcd59efe00b8c3d5e51c90b0ae262714f2b22b659ad503d98e5b1ca625557535
```

最终 DC、AC/CMRR、transient、noise，以及恢复后重建的最终 DC 都独立得到同一 SHA。最终结果：

| 指标 | 结果 |
|---|---:|
| DC 功耗 | `14.2920 µW` |
| 最小联合输出摆幅 | `0.21310 V` |
| 最大负载 KCL 误差 | `0.00256%` |
| 电流镜误差 | `4.33e-6%` |
| 低频增益 | `3.7421 V/V` (`11.462 dB`) |
| -3 dB 带宽 | `2.9756 GHz` |
| GBW | `11.1351 GHz` |
| unity-gain frequency | `9.7019 GHz` |
| 低频 CMRR | `34.8451 dB` |
| 50 mVpeak THD | `1.5089%` |
| 50 mVpeak 增益压缩 | `0.2687 dB` |
| 50 mVpeak 输出 | `0.3584 Vpp` |
| transient 最大平均功耗 | `14.3083 µW` |
| 1 kHz–1 GHz 输入参考积分噪声 | `699.24 µVrms` |
| 1 kHz–1 GHz 输出积分噪声 | `2.6035 mVrms` |

声明幅度上限内压缩未达到 1 dB，因此 P1dB 仍是 unresolved；没有外推到 sweep 之外。

## 可逆恢复与最终留存状态

真实尾管 + RD0/RD1 基线 placement SHA-256：

```text
d18e638165fcb2fa5fce24b7499ba982d0c145afad2198ef887d4e85d58d1f07
```

在所有搜索和最终动态分析后，VDA 使用新 `MP0/MP1` remove 路径恢复 RD0/RD1。恢复 SHA 与基线精确相等，同时已调优 `MN0/MN1 W=1.5 µm` 和 `MNTAIL` 参数保持。随后用最佳 `MP0/MP1 W=1.5 µm` 重新变换，最终 active placement SHA 为：

```text
28a5b4e9dbd8f246fff74978f560c040a94d7b638afdccabf8bc805f983e279d
```

独立 OA inspect 确认最终实例精确为 `MN0,MN1,MNTAIL,MP0,MP1`，最终新 `si` 网表和 DC 再次通过。因此目标 cell 最终留在经 OA readback、网表一致性和 DC 证据确认的最佳有源负载状态。

## 证据来源

- `eda_result`：`si` 生成的 simulator netlist、Spectre DC/AC/transient/noise PSF、连续 OP/波形/频响/噪声指标；
- `bridge_readback`：OA 实例、节点、pins、CDF W/L、transform 前后结构、placement SHA；
- `software_inference`：KCL 重算、饱和区分类、CMRR 比值、-3 dB/GBW/unity 交点、THD/P1dB 判定、约束与有限网格选优、checkpoint 状态；
- `user_input`：目标 cell、PDK profile、VDD/VCM/BIAS/CL、sweep、约束和显式搜索空间。

## 本地回归

live 修正、任务样例和文档同步后重新运行完整本地验收：

```text
448 passed in 1.33s
136/136 example task plans passed
catalog passed
python compileall passed
git diff --check passed
no PM0/PM1 in executable source, tests, or example tasks
```

代表性记录位于：

```text
artifacts/runs/differential-pair-current-mirror-gate6/11-dc-mp-initial-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/12-ac-cmrr-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/15-linearity-corrected-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/14-noise-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/16-icmr-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/17-ac-bias-load-tune-resume2-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/18-geometry-budget-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/19-geometry-tune-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/20-geometry-infeasible-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/23-final-ac-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/24-final-linearity-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/25-final-noise-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/26-final-restore-20260723.json
artifacts/runs/differential-pair-current-mirror-gate6/29-final-active-dc-20260723.json
```

## 未验证边界与下一道 Gate

- 只验证 nominal `top_tt`；差分对 PVT 是显式可选项，本轮未运行；
- 未验证 mismatch/Monte Carlo、PSRR、slew/settling、输出驱动范围或更细 ICMR 边界；
- 未验证 `RS0/RS1 + MP0/MP1` 组合、任意拓扑编辑或更复杂有源负载；
- PMOS L 已在 transform/apply/readback/`si` 契约中可调且 live 写入为 30 nm，但本次有限搜索只扫 W，没有搜索 L；
- 两次真实 SSH timeout 仍说明 transport 稳定性不是已解决问题；VDA 的 checkpoint/resume 正确恢复，但不能据此宣称底层传输无故障；
- ADE/Maestro 人工打开、修改、重跑及该差分对的 saved setup/history 交接未验证；
- P1dB 未被 5–50 mVpeak 幅度范围包围。

下一道 Gate 应从“固定 PMOS 镜像负载差分对”升级到更接近单模块设计质量的验证：优先加入显式 PSRR、slew/settling/输出摆幅或可选 PVT；若要继续拓扑能力，再单独定义 `source degeneration + active load` 的组合 Gate。PVT 不应自动附加到每个任务。
