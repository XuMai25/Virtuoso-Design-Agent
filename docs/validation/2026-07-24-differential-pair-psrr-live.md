# 2026-07-24 差分对 PSRR 三次同网表 AC live 验证

日期：2026-07-24（run record 使用 UTC 文件时间 `20260723T...Z`）
状态：**nominal TSMC N28 same-source PSRR execution verified at one operating point; PSRR design-quality acceptance and tuning pending**。

## 授权范围

- 目标：`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`
- PDK profile：`nics4304_tsmc28`
- OA 写入：否
- 远端计算：是；一次自动 `si` netlisting，随后差模、VDD 注入、VSS 注入三次 Spectre AC
- 覆盖已有对象：否；`replace_existing=false`
- 搜索：否；只执行 `tail_bias_v=0.32 V`、`common_mode_v=0.55 V`、`VDD=0.9 V`、`load_ff=0.5 fF` 单点

## 执行记录

首次 CLI 调用处于本地网络沙箱，SSH DNS 被阻断：

```text
artifacts/runs/differential-pair-current-mirror-psrr-bridge/run-20260723T165018Z.json
```

错误是 `Could not resolve hostname nics4304-cad1`，发生在 Bridge/Python 探测之前，属于本地执行环境限制，不是 netlist、Spectre 或电路不可行。随后以同一 plan token 在获准的真实网络环境重新执行：

```text
artifacts/runs/differential-pair-current-mirror-psrr-bridge/run-20260723T165117Z.json
```

该 run 状态为 `succeeded`，Bridge probe 返回 `virtuoso-bridge 0.7.0`，总运行时间约 102 s。独立只读 OA 回读记录为：

```text
artifacts/runs/differential-pair-current-mirror-inspect-bridge/run-20260723T165353Z.json
```

## OA、网表与 wrapper 绑定

独立回读确认前后 topology 都是：

```text
pmos_current_mirror_load_nmos_differential_pair_with_tail_device
```

实例仍为 `MN0/MN1/MNTAIL/MP0/MP1`，nets/pins 仍为 `BIAS/INN/INP/OUTN/OUTP/TAIL/VDD/VSS`。前后 semantic 参数逐项相同：

```text
input_width_um       = 1.5
length_um            = 0.03
pmos_load_width_um   = 1.5
pmos_load_length_um  = 0.03
tail_width_um        = 0.8
tail_length_um       = 0.03
```

自动 `si -batch` 网表：

```text
remote path:
/data/xum/virtuoso_bridge_smoke/vda_differential-pair-current-mirror-psrr-bridge_46c9ccfd9eb7/netlist

SHA-256:
bcd59efe00b8c3d5e51c90b0ae262714f2b22b659ad503d98e5b1ca625557535
```

OA 与 `si` 的 topology、W/L 和 geometry 均为 `matched`。三份 wrapper 哈希分别是：

```text
differential:    94227386451b090287867cd3e0f6a4cdf4c9009cd986f84cf7a8db42544a437f
VDD injection:   3d38e4fc1f469f11aed5811b3a4369fef36b7db1200bedd749f6260ea77d616d
VSS injection:   98603f3e9fdf1cb9467a04748f0094fa55df82be418a1ca2bb5dd1253095cfd2
```

两份供电 wrapper 都记录 `netlist_binding=same_si_netlist_sha256`。

## 完整性检查

- 三次 AC 各有 181 个复数频点，范围 `1 kHz–1 THz`；
- 频率网格一致性：`matched`；
- 三次 DC OP 一致性：`matched`；
- 正、负 PSRR 低频参考窗变化分别为 `4.62e-13 dB` 和 `8.88e-13 dB`，均为 flat；
- 两条 PSRR 曲线都只出现一次向下 3 dB 交点并被 sweep 包围；
- `analysis_complete=true`，`analysis_issues=[]`，`analysis_warnings=[]`；
- Spectre 三次均为 0 errors；每次有 PDK `SFE-1131 scalefactor` scope warning，已保留在 run record，未影响数据解析。

DC 点全部信号器件在饱和区，最小联合输出摆幅余量为 `0.2131 V`，供电功耗为 `14.292 µW`。任务中的两条约束只有“全部信号器件饱和”和“摆幅余量至少 0.1 V”，两条均通过。

## 真实 PSRR 结果

输出契约为差分输入、`OUTN` 单端输出：

| 指标 | PSRR+ / VDD | PSRR− / VSS |
| --- | ---: | ---: |
| low-frequency supply gain | `0.99389 V/V` (`-0.0533 dB`) | `0.79513 V/V` (`-1.9912 dB`) |
| low-frequency PSRR | `3.7651 V/V` (`11.5156 dB`) | `4.7063 V/V` (`13.4535 dB`) |
| first −3 dB frequency | `3.7958 GHz` | `2.7121 GHz` |
| PSRR at 1 THz / sweep minimum | `-15.2974 dB` | `-13.3918 dB` |

同一差模运行得到 `Ad=3.7421 V/V`、差模带宽 `2.9756 GHz` 和 GBW `11.1351 GHz`，与 Gate 6 最终 AC 点一致。组合最差低频 PSRR 为 `11.5156 dB`，全 sweep 最差为 `-15.2974 dB`。

这些值说明流程已经闭合，但当前 nominal operating point 的电源到输出耦合接近 unity，PSRR 并不好。任务没有声明 PSRR 数值门，因此 `feasible=true` 只表示 DC 饱和与摆幅约束通过，绝不表示 PSRR 规格合格。

## 证据分类

- OA 结构与参数：`bridge_readback`
- `si` 网表、DC OP、三组复数 AC 和 supply gain：`eda_result`
- PSRR 比值、3 dB 交点、网格/DC 一致性和组合最差值：`software_inference`
- analysis、sweep 和 testbench 条件：显式字段为 `user_input`

run record 保存了网表和三份 wrapper 的路径与 SHA-256、三次 DC PSF 的 SHA-256、181 点解析完整性及所有标量。直接 Spectre 路径当前没有把三份完整 AC PSF 文件复制到长期本地产物目录；它证明了解析后的真实波形链，但还不是 ADE exact-history 那种完整 raw-result manifest。这一差异保留为证据边界。

## live 后本地回归

```text
455 passed in 0.93s
138/138 example task plans passed
python -m compileall -q src tests passed
vda catalog passed
git diff --check passed
```

第三方 `virtuoso-bridge-lite` 工作树仍然干净，分支保持 `codex/vda-transport-recovery`；本 Gate 没有修改 Bridge。

## 当前结论与下一道 Gate

可以称为：

> **Gate 6 PSRR same-source execution verified at one nominal TSMC N28 point.**

不能称为：

- PSRR 设计质量闭合；
- bias/load 搜索已完成；
- PVT、mismatch 或 Monte Carlo PSRR 已验证；
- BIAS 随 VSS 跟随的另一种偏置参考定义已验证；
- ADE/Maestro PSRR 人工交接已打通。

下一步先根据用途给出明确的 PSRR+/PSRR− 规格和频带。若继续自动探索，可执行已准备的四点 `tail_bias_v=[0.30,0.32] V × load_ff=[0.5,2.0] fF` 只读搜索；但它只能判断这两个 testbench 条件是否改善现状，不应在没有规格的情况下把“最大 PSRR”包装成设计完成。之后再决定是否调整 OA W/L、偏置参考结构或拓扑。
