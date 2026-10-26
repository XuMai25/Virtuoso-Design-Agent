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

因此截至首轮 smoke，状态应记为 **L5A execution loop verified, design-quality closure pending**。

## 下一道 Gate

1. 用 `si` 或 Maestro 从目标 OA schematic 生成仿真 deck，消除双源。
2. 加入输入电容/动态能量或面积代理，使尺寸优化存在真实权衡。
3. 收紧 delay/skew，并增加 power/area/overshoot 等真实权衡；不可行规格的禁止写回路径已通过单候选 live test。
4. 再进入共源/源极退化：先 DC operating point，后 AC gain/bandwidth。

## 同日后续实现与第二轮 live smoke：OA/si 路径

代码阶段先只做本地验证；在用户随后明确授权“只读 OA、允许远端计算、不写 OA、不覆盖对象”后，又对新路径执行了第二轮 live smoke。上文旧数据仍保留为模板 deck 的历史证据，不能与本节同源结果混用。

代码已改为：

1. 先通过 Bridge 回读目标 OA 的实例、网络、pins 和 `W/L`。
2. 在 `/data/xum/virtuoso_bridge_smoke/vda_<task>_<nonce>/` 使用唯一 run directory，调用 `simInitEnvWithArgs(...)` 生成基础 `si.env`。
3. 补齐实测所需的 `simViewList`、`simStopList`、`spectreFormatter` 等字段，再运行 `si -batch ... -command nl`。
4. 不接受 return code 单证据：同时要求日志含 `End netlisting`、无失败 marker、netlist 非空，并解析 `MN0/MP0` 的 nodes、master、`w/l`。
5. OA 回读与 `si` 网表参数不一致时停止。Spectre wrapper 只定义 model、VDD/VSS、输入 pulse、负载、tran 和 save，不再重复 MOS 拓扑。
6. 波形为空或缺少 `time/IN/OUT` 时停止；成功后才提取 delay、rise/fall、skew、VOH/VOL、overshoot/undershoot。
7. 调优任务逐候选暂存 OA 参数并回读；无可行点或可恢复中断时恢复搜索前参数。计划中新增显式 `parameters.stage` 和 `parameters.finalize` 远端写步骤。
8. 新增 `gate_area_proxy_um2=(Wn+Wp)L`，其来源明确标为 `software_inference`；其他波形指标为 `eda_result`。

本地实际验证：

```text
39 passed
python -m compileall -q src tests
vda catalog
vda plan examples/tasks/inverter-close-loop.demo.json
vda plan examples/tasks/inverter-simulate.bridge.json
```

覆盖的新增失败边界包括：空 netlist、`si` 日志没有完成 marker、请求参数与 OA/网表不一致、空 waveform、搜索预算耗尽，以及搜索被中断后的参数恢复。

### 第二轮 live smoke：只读 OA/si/Spectre

执行范围：

- 目标：`vb_pdk_smoke/vda_inv_l5a_001/schematic`
- operation：`simulation.run`
- OA 写入：否，`allow_remote_write=false`
- 远端计算：是，`allow_remote_compute=true`
- 覆盖已有对象：否，`replace_existing=false`
- 成功 run record：`artifacts/runs/inverter-simulate-bridge/run-20260719T073424Z.json`
- 远端 run directory：`/data/xum/virtuoso_bridge_smoke/vda_inverter-simulate-bridge_e6f8c535ae8e/`

首次执行在 `bridge.probe` 即失败：本地 `127.0.0.1:65347` 没有 listener，run record 保存在 `artifacts/runs/inverter-simulate-bridge/run-20260719T073109Z.json`。该次没有进入 OA inspect、netlist 或 Spectre。按 Bridge 标准流程启动 tunnel 后，status 确认 daemon 用户和 SSH 用户均为 `xum`、Virtuoso 6.1.8 和 Spectre 21.1.0 可用，原任务与同一 token 重试成功。失败没有被静默降级。

OA 回读与 `si` 网表证据：

| 项目 | OA readback | `si` netlist |
| --- | --- | --- |
| `MN0` | `tsmcN28/nch_lvt_mac`, `Wfg=600n`, `l=30n` | `(OUT IN VSS VSS) nch_lvt_mac`, `w=0.6 µm`, `l=0.03 µm` |
| `MP0` | `tsmcN28/pch_lvt_mac`, `Wfg=1.2u`, `l=30n` | `(OUT IN VDD VDD) pch_lvt_mac`, `w=1.2 µm`, `l=0.03 µm` |
| nets/pins | `IN/OUT/VDD/VSS` | 实例节点匹配 |

- `si` 日志包含 `End netlisting Jul 19 15:34:12 2026`。
- 结构网表 SHA-256：`d2503f130ab621944f75037b19515f12ec7b10323bf08a7d78b33afef87ef4a4`。
- wrapper SHA-256：`ed385da568acd94b893eceb38f796eed916e9bc893804edb49b1f7ca0752a6e5`；它只提供 model、VDD/VSS、输入 pulse、`CL=2 fF`、tran 和 save，没有第二份 MN0/MP0 拓扑。
- OA 与网表语义参数一致性结果为 `matched`。

Spectre 返回 786 个 `time/IN/OUT` 样本，实际指标为：

| 指标 | 结果 |
| --- | ---: |
| `tPHL` | 4.125 ps |
| `tPLH` | 2.868 ps |
| 平均 delay | 3.497 ps |
| rise | 3.912 ps |
| fall | 5.757 ps |
| rise/fall skew | 1.845 ps |
| 稳态 VOH | 0.899991 V |
| 稳态 VOL | 0.0000492 V |
| overshoot | 0.029918 V |
| undershoot | 0.026178 V |
| `gate_area_proxy_um2` | 0.054 |

`delay <= 45 ps` 与 `skew <= 8 ps` 均通过。波形指标标为 `eda_result`，OA 结构标为 `bridge_readback`，显式 `VDD/CL` 标为 `user_input`，面积代理仍标为 `software_inference`。Spectre 给出 0 errors、3 个 `scalefactor` scope warnings 和 8 notices；这些 warning 保留在 run record 中。

仿真后又执行独立只读 `schematic.inspect`，记录为 `artifacts/runs/inverter-inspect-bridge/run-20260719T073539Z.json`。实例、网络、pins 与 `Wn=0.6 µm/Wp=1.2 µm/L=0.03 µm` 均与仿真前相同，因此这次只读 smoke 没有改变目标 OA。

### 第二轮结束时仍未验证

- 逐候选 OA 暂存、最终提交、不可行恢复和预算耗尽恢复在新同源路径上的真实远端行为。
- 进程被强制终止、主机断电等不可恢复中断仍可能留下未知 OA 状态。
- 当前 run record 中 Spectre `tool_version` 字段为空；版本 21.1.0 来自执行前 Bridge status，而不是该次 simulation result 自身。
- `gate_area_proxy_um2` 未经版图、电容或功耗校准；尚无动态能量/平均功耗实测。
- 当前 delay/skew 约束依然宽松，只证明判定路径，不证明设计质量优化充分。

### 第二轮后的下一道 Gate

只读同源路径现已 live verified。下一步是在用户重新明确授权 OA 写入后做小规模 `design.tune`：每个候选必须有 OA 暂存与回读、对应网表哈希和波形；可行路径要提交最佳点，不可行与预算耗尽路径要恢复初始 OA。通过后再加入动态能量/功耗实测并收紧 delay/skew/overshoot 约束，之后才进入 Gate 2。

因此第二轮结束状态是 **L5A execution loop verified, OA-netlisting read-only path live verified, staged tuning and design-quality closure pending**。

## 同日第三轮：供电能量权衡、收紧规格与恢复路径

### 1. 供电能量与功率

wrapper 新增保存 `VDD_SRC:p`。指标窗口取相邻两次 VIN 50% 上升沿，使用梯形积分计算电压源向电路提供的总能量：

- `supply_energy_per_cycle_fj = -VDD * integral(I(VDD_SRC:p) dt)`
- `average_supply_power_uw = supply_energy / cycle_period`

它包含周期内泄漏，不称为纯动态开关能量。电流波形、周期、能量和功率均标为 `eda_result`；电流极性错误、没有完整输入周期或波形长度不一致会失败。本地测试总数增加到 42 项。

只读校准记录为 `artifacts/runs/inverter-simulate-bridge/run-20260719T075709Z.json`。在 `Wn=0.6 µm/Wp=1.2 µm/L=0.03 µm`、`VDD=0.9 V`、`CL=2 fF` 下：

- 输入周期：200 ps
- 每周期总供电能量：2.653931 fJ
- 平均供电功率：13.269653 µW
- delay：3.496515 ps
- skew：1.844652 ps

### 2. 收紧规格与 9 点结果

新任务 `examples/tasks/inverter-tune-energy.bridge.json` 使用：

- `delay <= 3.8 ps`
- `rise_fall_skew <= 2.5 ps`
- `overshoot <= 40 mV`
- `undershoot <= 40 mV`
- objective：最小化 `supply_energy_per_cycle_fj`

完整 9 点的同源 EDA 指标如下。前五点来自失败长 run 中已经完成的 candidate action，其余点来自独立单候选或只读同源 run；失败 action 没有被当成指标证据。

| Wn (µm) | Wp (µm) | delay (ps) | skew (ps) | overshoot (mV) | undershoot (mV) | energy/cycle (fJ) | avg power (µW) | 可行 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 0.4 | 0.8 | 4.176 | 2.528 | 25.875 | 21.654 | 2.334 | 11.672 | 否 |
| 0.4 | 1.0 | 4.088 | 3.368 | 28.526 | 23.023 | 2.441 | 12.204 | 否 |
| 0.4 | 1.2 | 4.066 | 4.024 | 30.634 | 24.327 | 2.546 | 12.730 | 否 |
| 0.5 | 0.8 | 3.884 | 1.315 | 25.583 | 22.966 | 2.388 | 11.942 | 否 |
| 0.5 | 1.0 | 3.773 | 2.125 | 28.195 | 24.169 | 2.495 | 12.474 | 是 |
| 0.5 | 1.2 | 3.725 | 2.718 | 30.254 | 25.329 | 2.600 | 12.999 | 否 |
| 0.6 | 0.8 | 3.694 | 0.463 | 25.886 | 24.075 | 2.442 | 12.208 | 是 |
| 0.6 | 1.0 | 3.562 | 1.266 | 27.882 | 25.142 | 2.549 | 12.743 | 是 |
| 0.6 | 1.2 | 3.497 | 1.845 | 29.918 | 26.178 | 2.654 | 13.270 | 是 |

最小能量可行点是 `Wn=0.6 µm/Wp=0.8 µm`。相对原先只最小化 delay 得到的 `0.6/1.2`，它用约 0.197 ps 的 delay 增量换取约 8.0% 的每周期供电能量下降，并显著减小 skew；比较值由上述 `eda_result` 计算。

该候选的成功单点调优记录为 `artifacts/runs/inverter-candidate-n06-p08/run-20260719T085645Z.json`。最终又用独立 `parameters.apply` 提交，记录为 `artifacts/runs/inverter-apply-n06-p08/run-20260719T091004Z.json`；随后 `artifacts/runs/inverter-inspect-bridge/run-20260719T091039Z.json` 回读确认 OA 为 `600n/800n/30n`。

### 3. 不可行与预算耗尽恢复

`examples/tasks/inverter-tune-budget-infeasible.bridge.json` 声明 4 个候选，但 `max_iterations=1`，并设置不可能规格 `delay <= 1 ps`。真实记录 `artifacts/runs/inverter-tune-budget-infeasible-bridge/run-20260719T091415Z.json` 显示：

- 只评估 1/4 候选，run status 为 `partial`。
- note 明确记录 `search budget exhausted`。
- 候选 `0.4/0.8` 的 delay 为 4.176 ps，不可行。
- `parameters.restore` 成功，`schematic.inspect.after` 回到 `0.6/0.8/0.03`。
- 独立后读 `artifacts/runs/inverter-inspect-bridge/run-20260719T091449Z.json` 再次确认恢复结果。

### 4. 长任务 transport 失败边界

完整 9 点单进程并未成功：

- `artifacts/runs/inverter-tune-energy-bridge/run-20260719T080555Z.json` 完成前 5 点后 tunnel 消失。
- `artifacts/runs/inverter-tune-energy-bridge/run-20260719T083939Z.json` 完成前 4 点后再次中断。
- Windows ControlMaster 路径还出现 stale PID state，Bridge 0.7.0 的 `os.kill(pid, 0)` 抛出 `SystemError`；关闭 ControlMaster并增加 SSH keepalive 仍未消除随机 reset/timeout。
- transport 不可用时 `parameters.restore` 也会失败。VDA 将 run 标为 `failed` 并保留 `OA state is unverified`，没有静默宣称恢复成功。
- 外部重建 Bridge 后，独立 OA 回读能确定最后成功暂存点，再通过显式 `parameters.apply` 恢复基线或提交全局最佳。这个过程有 `bridge_readback` 证据，但目前不是 VDA 自动 resume。

本轮没有修改 Bridge 源码；结束时已停止本任务创建的 tunnel，临时 SSH keepalive 文件也已删除。远端 si/Spectre 审计目录继续保留在 `/data/xum/virtuoso_bridge_smoke/`。

### 第三轮结论与下一道 Gate

电路证据层已经完成同源 timing/energy 权衡、收紧规格、全局最佳写回，以及不可行/预算耗尽恢复。不能把它表述为“完整长任务可靠闭环”，因为随机 transport 中断后仍需外部恢复。

下一道门是 Gate 1R：优先在 Bridge 层解决 Windows stale state 与 SSH/tunnel reset，或在 VDA 增加不复制 SSH 的候选 checkpoint/resume，使 Bridge 恢复后能继续剩余候选并保留原始 OA 基线。Gate 1R 通过后再进入单 MOS 放大器。

本轮结束状态是 **L5A same-source timing/energy evidence verified, final OA commit verified, uninterrupted transport recovery pending**。

## 同日第四轮：候选 checkpoint/resume 与 Gate 1R

### 1. 实现与本地失败注入

调优执行器新增独立 `ExecutionCheckpoint`，不修改或复制 Bridge 的 SSH、SCP 和 tunnel 实现。checkpoint 在同目录临时文件写完后原子替换，至少保存：

- task id、plan token、adapter 与原始开始时间；
- 搜索前 OA 语义参数、最后确认 OA 参数和待确认写入参数；
- 连续完成的 candidate 前缀、完整 actions/notes 和 `next_candidate_index`；
- `complete` 状态。

Bridge worker 抛出的 `BridgeWorkerError` 现在属于 `AdapterInterrupted`：它会暂停搜索，不会把没有 EDA 证据的候选计作“不可行”后继续。恢复时先核对 task/token/adapter 和 candidate 前缀，再重新 probe 与 `schematic.inspect.resume`。当前 OA 必须属于原始基线、最后确认/待确认写入或任务声明候选，否则拒绝自动覆盖。只有最终写回或基线恢复与 `schematic.inspect.after` 一致后，checkpoint 才变为 `complete=true`。

新增测试覆盖：

- 第二候选发生 Bridge 型中断后 checkpoint 保留候选 1，并恢复初始 OA；
- 恢复后从候选 2 继续，候选 1 不重复，最终得到 9 个候选；
- plan token 改变时在任何 adapter action 前拒绝 checkpoint；
- 当前 OA 位于声明搜索空间外时拒绝写入；
- CLI 调优默认创建完整 checkpoint。

本地最终结果为 `47 passed`，`compileall`、catalog、plan 和 `git diff --check` 通过；其中额外覆盖“9 个仿真已完成但最终写回中断”的恢复，确认续跑不会重复 Spectre。

### 2. 真实中断与续跑过程

任务仍为 `examples/tasks/inverter-tune-energy.bridge.json`，目标仅为 `vb_pdk_smoke/vda_inv_l5a_001/schematic`，`replace_existing=false`，token 为 `3acb44d683b35efc`。checkpoint：

`artifacts/runs/inverter-tune-energy-bridge/gate1r-20260719.checkpoint.json`

真实执行保留了 3 次 transport 中断，而不是覆盖失败记录：

1. `run-20260719T104857Z.json`：候选 1 完成；候选 2 已暂存，但 wrapper 上传 SSH timeout。checkpoint 为候选前缀 `[1]`、`next_candidate_index=2`。
2. Bridge 外部重建后，`inverter-inspect-bridge/run-20260719T105453Z.json` 独立回读 OA 为 `0.4/1.0/0.03`，与候选 2 一致。`run-20260719T105520Z.json` 从候选 2 继续并完成候选 2、3；候选 4 暂存时 tunnel 再次消失。checkpoint 前缀变为 `[1,2,3]`、下一点为 4。
3. `run-20260719T110037Z.json` 从候选 4 继续，完成候选 4–9；最终 `parameters.apply.best` 时 tunnel 中断。checkpoint 已含完整 `[1..9]`，`next_candidate_index=10`，因此候选 EDA 不需重跑。
4. `run-20260719T111029Z.json` 从索引 10 恢复，只做独立 OA 回读、重新选优、最佳写回和最终回读，status 为 `succeeded`。

每次 Bridge 0.7.0 重建前都先确认 `state.json` 指向本任务的 `nics4304-cad1:65347`、记录 PID 已不存在且端口没有 listener，再删除精确 stale state；未删除配置或其他状态，未修改 Bridge 源码。transport 不可用期间的 `parameters.restore.interrupted` 失败均作为 `system_event` 保留。最终成功 run 仍含 6 个历史 failed actions 和 3 个 `schematic.inspect.resume`，但候选列表恰好为索引 1–9，失败 action 没有变成 EDA 指标。

### 3. 最终选择与一致性

完整 checkpoint 重现了第三轮相同的可行性和排序：可行点为 `0.5/1.0`、`0.6/0.8`、`0.6/1.0` 和 `0.6/1.2`，最小能量可行点仍为 `0.6/0.8/0.03`。最终选择指标：

- delay：3.693813 ps
- rise/fall skew：0.463205 ps
- overshoot：25.886 mV
- undershoot：24.075 mV
- 每周期总供电能量：2.441636 fJ
- 平均供电功率：12.208178 µW

最终 checkpoint 为 `complete=true`、`next_candidate_index=10`。成功 run 中 `parameters.apply.best` 与 `schematic.inspect.after` 均成功；独立记录 `artifacts/runs/inverter-inspect-bridge/run-20260719T111052Z.json` 再次以 `bridge_readback` 确认 OA 为 `Wn=0.6 µm/Wp=0.8 µm/L=0.03 µm`。

### 第四轮结论与下一道 Gate

Gate 1R 已通过“VDA 显式 checkpoint/resume + Bridge 外部恢复”路径：原始 OA 基线和完成候选前缀跨中断保留，既能从候选边界继续，也能在全部仿真完成但最终写回中断时只恢复 finalize。它不等于 Bridge 原生 transport 已自愈；Windows stale PID、随机 SSH timeout 和自动 tunnel 重建仍需在 Bridge 层单独解决。

反相器当前状态更新为 **inverter L5A same-source bounded closure and explicit checkpoint/resume recovery verified**。下一道产品 Gate 是单 MOS 共源/源极退化放大器的 DC operating point；进入新远端 target 前仍需新的明确授权。

## 同日第五轮：Bridge stale state 与调用边界自动恢复

### 1. 第三方修改隔离

Bridge 第三方仓库先在原始提交 `dc9a4ec5acb1971f683e4717ff9e5a635ba588cf` 建立备份引用 `codex/backup-vda-transport-dc9a4ec`，再在本地分支 `codex/vda-transport-recovery` 修改。代码/测试提交为 `9e52844463cceaf12bbc39966a7b2db5c7238357`，Bridge 内补丁文档提交为 `32dc36cbbd08ccf6543b22dd85f9e5f7de08bb1d`；没有推送第三方 `origin`。逐文件范围和升级方法记录在 `docs/third-party/virtuoso-bridge-local-patch.md` 及 Bridge checkout 的 `LOCAL_VDA_PATCH.md`。

补丁只处理两个由现场证据直接定位的问题：Windows 端口不可达时不再用 `os.kill(pid, 0)` 判断 stale PID；`VirtuosoClient.from_env` 在没有运行中 tunnel 时真正调用 `SSHClient.warm()`。同时让相关 PID 清理防御 `SystemError`。没有修改 SSH 认证、SKILL、Spectre、远端目录、state schema 或文件传输协议。

### 2. 测试证据

- 修改前隔离 Bridge 基线：`78 passed, 1 deselected`。
- 新增后的目标测试：`18 passed`。
- 修改后隔离 Bridge 全套：`82 passed, 1 deselected`。
- deselected 的 `test_tunnel_state_reads_legacy_cache_path` 是补丁前已存在的 Windows `HOME`/legacy-cache 路径假设问题，不通过修改无关测试掩盖。

### 3. 强制断链只读 smoke

目标保持为 `vb_pdk_smoke/vda_inv_l5a_001/schematic`；`allow_remote_write=false`、`allow_remote_compute=false`、`replace_existing=false`。第一次从无 tunnel 状态直接执行 VDA inspect 成功，记录为：

`artifacts/runs/inverter-inspect-bridge/bridge-recovery-prekill-20260719.json`

state 给出本地端口 65347、PID 69580。只读核对确认该 PID 是监听 65347 的 Windows OpenSSH 后，精确终止 PID，但故意保留 state。此时 patched `SSHClient.is_running()` 返回 `false`，没有再出现此前的 `WinError 87/SystemError`。未手工删除 state、未调用 `restart`，直接重跑同一 task 后自动创建新 PID 86280 并成功，记录为：

`artifacts/runs/inverter-inspect-bridge/bridge-recovery-postkill-20260719.json`

两份 run 都为 `succeeded`；`schematic.inspect` 证据均为 `bridge_readback`，共同得到实例 `MN0/MP0`、nets/pins `IN/OUT/VDD/VSS` 和 `Wn=0.6 µm/Wp=0.8 µm/L=0.03 µm`。结束时通过 Bridge `stop` 清理，新 PID 消失、state 不再存在。没有 OA 写入或 Spectre 运行。

### 第五轮结论与未验证边界

Windows stale PID 崩溃和“新 client 没有真正 warm tunnel”这两个确定性问题已经由 Bridge 本地隔离补丁、单测和强制断链 live smoke 闭合。仍未闭合的是传输已经进行时的随机 SSH reset/timeout：本补丁没有加入 keepalive，也没有改变重试/文件传输实现，因此不能宣称 Bridge transport 全面自愈；此类中断仍由 VDA 显式 checkpoint/resume 保存候选边界并在新调用中恢复。

反相器产品 Gate 仍保持已通过，不需要为了等待随机网络故障复现而阻塞 Gate 2；下一道 Gate 是单 MOS 放大器 DC operating point，但任何新 library/cell 的真实写入仍需明确目标和授权。

## 同日第六轮：幂等退避、pre-send 恢复与 9 点压力回归

### 1. Bridge 追加原子补丁

在同一备份隔离分支上追加两条独立提交：

- `f8fdb9ed7e91c3194675876dcc4b16a06b77a7eb`：现有 SSH command/upload/download 的瞬态重试不再连续立即执行，而是在后续尝试前等待 1 秒、3 秒；普通 connect timeout 纳入瞬态分类，认证、host key、DNS 等确定性错误仍立即失败。
- `2f41293aa8c4f297470298e27ccd7747046b3913`：`_execute_skill_once` 只把 `connect()` 在 `sendall()` 前的拒绝标成私有异常；managed client 可 warm 并重试一次。任何可能已发送 SKILL payload 的异常不自动重放。

Bridge 内最新逐文件记录为 `e74379a7e72c4886a1b578b221f770b62cd23433`。修改后隔离测试为 `91 passed, 1 deselected`；新增测试明确覆盖有界退避、非瞬态立即失败、ControlMaster 既有 fallback、pre-send-only 标记、只恢复一次、warm 失败和未标记错误不重放。

### 2. 9 点压力任务的真实失败与恢复

仍使用 `examples/tasks/inverter-tune-energy.bridge.json`、token `3acb44d683b35efc` 和目标 `vb_pdk_smoke/vda_inv_l5a_001/schematic`。OA 写入、远端计算已授权，`replace_existing=false`。首次压力记录：

`artifacts/runs/inverter-tune-energy-bridge/bridge-backoff-stress-20260719.json`

该进程连续完成候选 1–7，在 `parameters.stage.8` 的首次 RAMIC TCP connect 遇到 `Connection refused to 127.0.0.1:65347`。这不是 direct SSH upload，因此有界退避不能处理。VDA 随后的 `parameters.restore.interrupted` 成功，把 OA 恢复到搜索前 `0.6/0.8/0.03`，checkpoint 保持 `next_candidate_index=8`、候选前缀 1–7，失败 action 和 note 均保留。

同一 checkpoint 在新进程中无需手删 state 即恢复：

`artifacts/runs/inverter-tune-energy-bridge/bridge-backoff-stress-resume1-20260719.json`

恢复先 probe 和独立 OA 回读，再跳过候选 1–7，完成候选 8–9、最佳点写回与最终回读。checkpoint 最终 `complete=true`、`next_candidate_index=10`，选中参数仍为 `0.6/0.8/0.03 µm`，delay 3.693813 ps、skew 0.463205 ps、每周期供电能量 2.441636 fJ、平均功率 12.208178 µW。失败没有变成 EDA 指标，也没有重复前 7 个 Spectre 点。

最终又从无 tunnel 状态执行独立只读 `schematic.inspect`，记录 `artifacts/runs/inverter-inspect-bridge/bridge-post-transport-patch-final-20260719.json` 以 `bridge_readback` 确认 `MN0/MP0`、`IN/OUT/VDD/VSS` 和 `Wn/Wp/L=0.6/0.8/0.03 µm`；结束后 tunnel 与 state 均清理。

### 3. 同-client pre-send 强制断链 smoke

为直接覆盖上述失败窗口，先让一个 `VirtuosoClient` 自己建立并持有 tunnel，再调用其 runner 精确停止 PID 126188、保留 stale state，随后在同一 client 上执行只读 `1+2`。patched client 在 payload 发送前发现拒绝，warm 新 PID 136176，SKILL 返回 `3`；结束时 state 和进程均清理。单测同时用 fake socket 断言 connect 拒绝后 `sendall()` 没有被调用。

### 第六轮结论与边界

现在已闭合三类可安全判定的问题：Windows stale PID、下次调用缺失 auto-warm、同一 client 的 pre-send connect refusal；幂等 SSH 传输也有有界退避。尚不能自动处理的是 payload 发送后的连接丢失，因为无法证明 OA SKILL 是否已经执行；盲目重放会破坏证据与副作用边界。因此该区域继续交给 VDA checkpoint/resume，不是停止努力，而是明确选择可审计恢复而非不安全重试。

本轮没有在 `2f41293` 之后再启动一轮全新的 9 点无中断 sweep；已有压力失败+恢复记录和确定性 pre-send smoke 分别证明 VDA 兜底与 Bridge 精确自愈窗口。不能据此宣称 transport 全面可靠或网络 reset 根治。
