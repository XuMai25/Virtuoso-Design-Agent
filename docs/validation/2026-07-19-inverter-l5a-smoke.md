# 2026-07-19 反相器 L5A smoke

## 环境

- Bridge：`virtuoso-bridge 0.7.0`
- Virtuoso：`6.1.8-64b`
- Spectre：`21.1.0 64bit`
- 远端执行主机：`cad52`，通过 `nics4304-cad1`
- 远端工作目录：`/data/xum/virtuoso_bridge_smoke`
- PDK profile：`nics4304_tsmc28`
- OA 目标：`vb_pdk_smoke/vda_inv_l5a_001/schematic`

本记录只描述这次 smoke，不外推到其他 PDK、library、analysis 或电路。

## 验证结果

### 1. Bridge doctor

只读 SKILL `1+2` 返回 `3`；tunnel、daemon 和 Spectre probe 均通过。

### 2. 单点 Spectre

参数：`Wn=0.5 µm`、`Wp=1.0 µm`、`L=0.03 µm`、`CL=2 fF`、`VDD=0.9 V`。

最终修正版指标：

| 指标 | 结果 |
| --- | ---: |
| 采样点 | 786 |
| `tPHL` | 4.147 ps |
| `tPLH` | 2.897 ps |
| 平均 delay | 3.522 ps |
| rise | 3.887 ps |
| fall | 5.957 ps |
| rise/fall skew | 2.070 ps |
| 稳态 VOH | 0.899986 V |
| 稳态 VOL | 0.0000269 V |
| overshoot | 0.024723 V |
| undershoot | 0.024146 V |

VOH/VOL 最初错误地使用全波形 max/min，会把过冲/欠冲误当作更好的逻辑电平。发现后已改为稳态窗口中位数，并把 overshoot/undershoot 单独输出；回归测试和 live 单点均重新通过。

### 3. OA 建图和结构回读

创建后回读：

- instances：`MN0`、`MP0`
- nets：`IN`、`OUT`、`VDD`、`VSS`
- pins：`IN`、`OUT`、`VDD`、`VSS`
- `MN0`：`tsmcN28/nch_lvt_mac`，`Wfg=500n`、`l=30n`、`fingers=1`
- `MP0`：`tsmcN28/pch_lvt_mac`，`Wfg=1u`、`l=30n`、`fingers=1`

首次新建曾失败：统一 `read_schematic()` 不适合探测不存在的 view，临时 SKILL 在 load 阶段报错。轻量 `ddGetObj` 证实目标仍为 `MISSING`，未留下部分 OA view。修复为“先 `ddGetObj`，存在后才统一回读”后重试通过，并加入回归测试。

### 4. 局部参数应用

同一个 cell 未重建，仅应用参数并前后回读：

| instance | before | after | unchanged |
| --- | --- | --- | --- |
| `MN0` | `Wfg=500n` | `Wfg=600n` | `l=30n, fingers=1` |
| `MP0` | `Wfg=1u` | `Wfg=1.2u` | `l=30n, fingers=1` |

这验证了 `parameters.apply` 可以作为独立任务使用。

### 5. 9 点受限闭环

搜索空间：`Wn={0.4,0.5,0.6} µm`，`Wp={0.8,1.0,1.2} µm`；上限固定为 9 次。9 次均得到 `eda_result`，无失败动作。

| Wn (µm) | Wp (µm) | delay (ps) | skew (ps) |
| ---: | ---: | ---: | ---: |
| 0.4 | 0.8 | 3.880 | 2.480 |
| 0.4 | 1.0 | 3.820 | 3.200 |
| 0.4 | 1.2 | 3.810 | 3.740 |
| 0.5 | 0.8 | 3.610 | 1.390 |
| 0.5 | 1.0 | 3.522 | 2.070 |
| 0.5 | 1.2 | 3.490 | 2.590 |
| 0.6 | 0.8 | 3.430 | 0.650 |
| 0.6 | 1.0 | 3.330 | 1.310 |
| 0.6 | 1.2 | 3.275 | 1.804 |

在 `delay <= 45 ps`、`skew <= 8 ps` 且 objective 为最小 delay 的任务下，所有候选都可行，最终选择 `Wn=0.6 µm, Wp=1.2 µm`；OA 回读确认写回为 `600n/1.2u`。

### 6. 不可行规格禁止写回

额外使用单候选 `Wn=0.4 µm, Wp=0.8 µm` 和不可能约束 `delay <= 1 ps` 运行真实 `design.tune`：

- 实测 delay：3.881 ps
- candidate feasible：`false`
- run status：`partial`
- `parameters.apply*` 动作数：0
- 独立 OA 后读：`MN0 Wfg=600n`、`MP0 Wfg=1.2u`

因此执行器保留了最佳尝试和失败指标用于诊断，但没有把不满足规格的参数写回 OA。

## 批判性结论

这次 smoke 证明的是执行闭环成立，不是设计策略已经成熟：

- 规格比实测宽松一个数量级，无法筛掉候选。
- objective 只有 delay，没有 power、energy、area 或输入电容代价，选择最大尺寸是预期结果。
- 当前 Spectre deck 由模板生成，与 OA schematic 共享语义参数，但不是从 OA 自动 netlist。
- 只覆盖 nominal transient；没有 DC、corner、Monte Carlo、noise 或负载变化。
- 过冲/欠冲约 24 mV，已经单列，但尚未成为约束。

因此当前状态应记为 **L5A execution loop verified, design-quality closure pending**。

## 下一道 Gate

1. 用 `si` 或 Maestro 从目标 OA schematic 生成仿真 deck，消除双源。
2. 加入输入电容/动态能量或面积代理，使尺寸优化存在真实权衡。
3. 收紧 delay/skew，并增加 power/area/overshoot 等真实权衡；不可行规格的禁止写回路径已通过单候选 live test。
4. 再进入共源/源极退化：先 DC operating point，后 AC gain/bandwidth。
