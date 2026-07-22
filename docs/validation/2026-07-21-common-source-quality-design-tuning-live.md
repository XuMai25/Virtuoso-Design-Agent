# 2026-07-21 共源质量驱动设计参数写回真实验证

状态：**bounded common-source multi-analysis design-parameter tuning, writeback, and recovery verified; L/VDD, corner, and broader topology pending**。

本轮在 `vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic` 上把已经通过只读 Gate 的 `analysis: quality` 接入现有 W/RD/RS OA candidate staging、最佳写回和 checkpoint。没有新建或替换 cellview；`replace_existing=false`。本轮会修改已有 MN0/RD0/RS0 参数，已由用户明确授权；远端产物仍位于 `/data/xum/virtuoso_bridge_smoke/`。第三方 Bridge 保持 `codex/vda-transport-recovery@e74379a` 且未修改。

## 环境、任务与授权

- Bridge doctor：`connected=true`、Bridge 0.7.0、SKILL probe `3`、profile `nics4304_tsmc28`
- Virtuoso/Spectre：沿用已验证的 6.1.8-64b / 21.1.0 nics4304 环境
- 完整 8 点任务：`examples/tasks/common-source-quality-design-tune.bridge.json`
- 完整任务 token：`34fbf376b9dca616`
- 全不可行任务：`examples/tasks/common-source-quality-design-tune-infeasible.bridge.json`
- 全不可行 token：`e75f8eddadb8f37e`
- 线性度优先任务：`examples/tasks/common-source-quality-linearity-priority.bridge.json`
- 线性度优先 token：`487f44ff7d57e2fc`

固定 testbench 为 L=0.03 µm、bias=0.35 V、VDD=0.9 V、load=1 fF。完整搜索空间为 W=`[0.5,1.0] µm`、RD=`[20,22] kΩ`、RS=`[1,2] kΩ`。每个候选先写 OA 并回读，再生成一份经 OA/`si` 一致性核对的网表，供 AC、100 MHz 四幅度相干 transient 和 1 kHz–10 GHz ordinary noise 三个 wrapper 复用。

## 8 点 W/RD/RS 质量搜索

最终记录：

```text
artifacts/runs/common-source-quality-design-tune/live-resume1-20260721.json
artifacts/runs/common-source-quality-design-tune/live-20260721.checkpoint.json
```

约束同时覆盖 saturation、输出摆幅、KCL、gain、bandwidth、P1dB、THD、输入参考噪声和 DC 功耗；objective 为最大化 GBW。

| # | W | RD | RS | Gain | BW | GBW | P1dB | THD | Input noise | DC power | Feasible |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 1 | 0.5 µm | 20 kΩ | 1 kΩ | 3.1220 | 8.7372 GHz | 27.2775 GHz | 116.388 mV | 9.323% | 1312.853 µV | 16.383 µW | yes |
| 2 | 0.5 µm | 20 kΩ | 2 kΩ | 2.5349 | 8.0998 GHz | 20.5323 GHz | 145.366 mV | 9.667% | 1431.719 µV | 13.958 µW | yes |
| 3 | 0.5 µm | 22 kΩ | 1 kΩ | 3.2901 | 8.1523 GHz | 26.8216 GHz | 110.853 mV | 9.277% | 1303.489 µV | 15.954 µW | yes |
| 4 | 0.5 µm | 22 kΩ | 2 kΩ | 2.6998 | 7.5125 GHz | 20.2823 GHz | 136.196 mV | 8.906% | 1420.707 µV | 13.670 µW | yes |
| 5 | 1.0 µm | 20 kΩ | 1 kΩ | 3.7006 | 8.1849 GHz | 30.2890 GHz | 88.318 mV | 13.161% | 983.071 µV | 22.337 µW | yes |
| 6 | 1.0 µm | 20 kΩ | 2 kΩ | 2.9683 | 7.2143 GHz | 21.4143 GHz | 121.226 mV | 7.643% | 1130.847 µV | 18.479 µW | yes |
| 7 | 1.0 µm | 22 kΩ | 1 kΩ | 3.8316 | 7.7283 GHz | 29.6119 GHz | 80.077 mV | 15.202% | 979.340 µV | 21.583 µW | yes |
| 8 | 1.0 µm | 22 kΩ | 2 kΩ | 3.1325 | 6.7325 GHz | 21.0892 GHz | 113.297 mV | 8.968% | 1124.558 µV | 18.015 µW | yes |

8/8 候选均 `analysis_complete=true`，AC/transient/noise completion 全为 true，`parameter_consistency=matched`、`shared_metric_consistency=matched`、`oa_netlist_reuse=one_verified_netlist`。GBW objective 选择候选 5；`parameters.apply.best` 和独立 `schematic.inspect.after` 都回读：

```text
device_width_um = 1.0
length_um = 0.03
load_resistance_ohm = 20000.0
source_resistance_ohm = 1000.0
```

本轮 before 已经是候选 5，因此最终值没有相对基线改变；但 8 个 candidate stage 均真实改变并回读 OA，finalize 也独立执行。后面的线性度优先控制任务验证了 objective 改变时最终 OA 确实改变。

八个成功 netlist 的远端目录与 SHA-256 分别为：

```text
1 /data/xum/virtuoso_bridge_smoke/vda_common-source-quality-design-tune_ad208045d422/netlist d826c56eee43af70edfa1503582f4ec6b333cc99b7e2b60cdcdf96d0d474dda6
2 /data/xum/virtuoso_bridge_smoke/vda_common-source-quality-design-tune_7e791e3a3803/netlist 90e662a710be77c4d29d27199a989ae49bf6b808caa4737da0e4e79715c277cd
3 /data/xum/virtuoso_bridge_smoke/vda_common-source-quality-design-tune_ec0db6f52037/netlist a7c27e4d44fc15f3ddce1fcfc2280580625e06823ca98eb27b7ffbaa45daaf15
4 /data/xum/virtuoso_bridge_smoke/vda_common-source-quality-design-tune_cb8c60d60f73/netlist b1229ed0cddfa513bbe758b648ba3efbc51c26651b58ae1f8f94338925e27144
5 /data/xum/virtuoso_bridge_smoke/vda_common-source-quality-design-tune_3c9944e4f917/netlist 2c529799349ac466606367015a038e22c4ab7048b8b21e89092146515de7b232
6 /data/xum/virtuoso_bridge_smoke/vda_common-source-quality-design-tune_6a3ba10cce08/netlist cdddb1f631d4baaec731c6c4567077ea2c330be5efdbc9156e6f02289e8d91f4
7 /data/xum/virtuoso_bridge_smoke/vda_common-source-quality-design-tune_366797ae1038/netlist 4aaa96fa6e54fbe6eb089542a2d0bf7217cbabc6bfc402269fa2cbc82c29b10e
8 /data/xum/virtuoso_bridge_smoke/vda_common-source-quality-design-tune_b16c29e18537/netlist ac09b86fb808d14505e3874c2204224577a318d5bcc9812e77b8c6514e28c5dc
```

## 真实 transport 中断与恢复

首次记录为：

```text
artifacts/runs/common-source-quality-design-tune/live-20260721.json
```

候选 4 在 Spectre upload 阶段遇到 SSH connect timeout；随后 `parameters.restore.interrupted` 的 OA readback 又遇到 `WinError 10054`。run 正确为 `failed`，checkpoint 为 `complete=false`、`next_candidate_index=4`、3 个完成候选，并同时保留候选 4 的最后确认 OA 与待恢复初始值。它没有继续候选 5，也没有把 transport failure 包装成 EDA infeasible。

Bridge doctor 恢复连接后，以同一 task/token/checkpoint 续跑。`schematic.inspect.resume` 独立读到 OA 仍为候选 4 的 `0.5 µm/22 kΩ/2 kΩ`，属于 checkpoint 允许状态；执行器只重跑 4–8，没有重复 1–3。最终 checkpoint 为 `complete=true`、`next_candidate_index=9`、8 个候选、`pending_oa_parameters=null`。

## 全不可行恢复与 selection 语义

最终有效记录：

```text
artifacts/runs/common-source-quality-design-tune-infeasible/live-rerun1-20260721.json
artifacts/runs/common-source-quality-design-tune-infeasible/live-rerun1-20260721.checkpoint.json
```

两个 W=0.5 µm、RD=20 kΩ、RS=`[1,2] kΩ` 候选都完整运行三项分析，但人为要求 gain≥100、BW≥100 GHz、P1dB≥0.2 V、THD≤1%、输入参考噪声≤100 µV 和功耗≤1 µW，因此均不可行。执行器没有 `parameters.apply.best`，而是执行 `parameters.restore`，独立 after readback 恢复 `1.0 µm/20 kΩ/1 kΩ`。

首次 guard 还暴露出记录语义问题：OA 虽然正确恢复，旧代码仍把“最接近”的不可行候选放入 `selected_parameters`，CLI 因而打印 `Selected parameters`。VDA 已改为在 tuning 无可行候选时保持 `selected_parameters=null`、`selected_metrics=null`；所有尝试仍保留在 `candidates`。真实重跑确认 CLI 只输出 partial note，不再显示未提交选择。

## Objective 改变会改变 OA 设计

线性度优先记录：

```text
artifacts/runs/common-source-quality-linearity-priority/live-20260721.json
artifacts/runs/common-source-quality-linearity-priority/live-20260721.checkpoint.json
```

该任务固定 W=1 µm、RD=20 kΩ，只比较 RS=1/2 kΩ；约束保持一致，objective 改为最小化 `max_thd_percent`。两点都可行，执行器自动选择 RS=2 kΩ 并通过 `parameters.apply.best` 与独立 after readback 写入现有 OA：

| 指标 | RS=1 kΩ | RS=2 kΩ | 变化 |
|---|---:|---:|---:|
| THD | 13.161% | 7.643% | −41.93% |
| P1dB | 88.318 mV | 121.226 mV | +37.26% |
| DC power | 22.337 µW | 18.479 µW | −17.27% |
| gain | 3.7006 | 2.9683 | −19.79% |
| bandwidth | 8.1849 GHz | 7.2143 GHz | −11.86% |
| GBW | 30.2890 GHz | 21.4143 GHz | −29.30% |
| input noise | 983.071 µV | 1130.847 µV | +15.03% |

最终 OA 当前为 `W=1 µm, L=0.03 µm, RD=20 kΩ, RS=2 kΩ`。这不是宣称 RS=2 kΩ 普遍更优，而是证明相同候选证据在不同显式 objective 下会产生不同、可回读的设计选择。

两点 netlist 路径和 SHA-256：

```text
/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-linearity-priority_f620965bf3ab/netlist 2c529799349ac466606367015a038e22c4ab7048b8b21e89092146515de7b232
/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-linearity-priority_d130580be31c/netlist cdddb1f631d4baaec731c6c4567077ea2c330be5efdbc9156e6f02289e8d91f4
```

## 证据分类与边界

- OA topology、逐候选写入和最终回读：`bridge_readback`
- `si` 网表、DC OP、AC、transient、noise 连续数据：`eda_result`
- sweep、约束、objective 与候选网格：任务输入 `user_input`
- 饱和区、交点、THD/P1dB/噪声积分、约束和排序：`software_inference`
- SSH timeout、`WinError 10054` 与失败 action：`system_event`

## 本地回归

```text
python -m pytest: 143 passed
all examples/tasks/*.json plan validation: 46/46 passed
vda catalog: common_source = Gate 2 quality design tuning/recovery verified
```

新增回归把现有质量组合、8 点 OA staging、最佳 finalize 和独立 readback 组合起来；不可行测试要求 `selected_parameters` 与 `selected_metrics` 为空。没有修改或复制 Bridge。

## 未闭合边界

- 仍是 nominal PDK/温度条件，没有 corner 或 Monte Carlo。
- L 与 VDD 尚未进入真实质量搜索；当前 task 也没有输入电容、真实面积或 post-layout 指标。
- 三项 analysis 仍分别启动 Spectre；尚未用 profiling 证明需要合并启动。
- 每项 `tool_version` 仍为 `None`，Spectre 21.1.0 来自既有环境探测而非本次 run 自带证据。
- 本轮只覆盖一个 source-degenerated common-source cell，不能外推到差分对。

下一道 Gate 是小规模 L/VDD 加有限 corner，验证同一设计在多个工作条件下的可重复规格；随后进入差分对。当前可以称为 **bounded common-source multi-analysis design-parameter tuning, writeback, and recovery verified**，仍不能称为完整 L5B 单模块设计代理。
