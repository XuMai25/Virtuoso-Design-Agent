# 2026-07-21 共源多 analysis 质量组合真实验证

状态：**read-only common-source quality tuning, budget/infeasible guards, and checkpoint recovery verified; design-parameter quality writeback and corner pending**。

本轮在 `vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic` 上执行固定 `analysis: quality`。目标是源极退化共源级；所有候选只改变 testbench 的 `bias_v` 与 `load_ff`，没有 parameter stage/apply/finalize、没有 OA 写入，也没有覆盖 cellview。

## 授权、环境与任务

- target：`vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic`
- `allow_remote_compute=true`
- `allow_remote_write=false`
- `replace_existing=false`
- Bridge：0.7.0，`codex/vda-transport-recovery@e74379a`，工作树干净
- Virtuoso/Spectre 环境沿用已验证的 nics4304 TSMC28 profile
- 主任务 token：`6245788e6626698c`
- 预算任务 token：`c35532f3a516a7dd`
- 全不可行任务 token：`9e97c13c2713f05f`

执行前 Bridge doctor 返回 `connected=true`、SKILL probe `3` 和 profile `nics4304_tsmc28`。主任务使用 VDA 提交 `524b8c2`。本轮没有修改第三方 Bridge。

每个候选只进行一次 OA 结构/参数回读和一次 `si` 网表生成，然后让 AC、相干 transient linearity 和 ordinary noise 三个 wrapper 引用该网表。三个子分析必须全部 `analysis_complete=true`，且实际参数、重复 DC 指标和指标证据来源一致，候选才进入约束和 objective 排序。

## 4 点联合质量搜索

记录：

```text
artifacts/runs/common-source-quality-bias-load-tune/live-20260721.json
artifacts/runs/common-source-quality-bias-load-tune/live-20260721.checkpoint.json
```

结果：

| # | Bias | Load | Gain | BW | GBW | P1dB | Max THD | Input noise | DC power | Feasible |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 1 | 0.35 V | 1 fF | 3.7006 | 8.1849 GHz | 30.2890 GHz | 88.318 mV | 13.161% | 983.071 µV RMS | 22.337 µW | yes |
| 2 | 0.35 V | 4 fF | 3.7006 | 2.7132 GHz | 10.0406 GHz | 88.319 mV | 13.141% | 983.071 µV RMS | 22.337 µW | yes |
| 3 | 0.40 V | 1 fF | 3.2303 | 9.9954 GHz | 32.2880 GHz | 56.129 mV | 26.095% | 993.446 µV RMS | 30.373 µW | no |
| 4 | 0.40 V | 4 fF | 3.2303 | 3.3681 GHz | 10.8800 GHz | 56.108 mV | 26.068% | 993.446 µV RMS | 30.373 µW | no |

四个候选的 AC/transient/noise 都完整。两个 0.40 V 候选虽然候选 3 的 GBW 达到全网格最高 32.288 GHz，但都同时违反 `max_thd_percent <= 20%` 和 `dc_supply_power_uw <= 30`，因此不能被只看 GBW 的 objective 选中。最佳可行点是 `bias=0.35 V, load=1 fF`。

该结果真实展示了负载和偏置权衡：1 fF 增至 4 fF 时 DC、噪声、低频增益和线性度基本不变，而带宽明显下降；偏置升至 0.40 V 提升了 1 fF 点的 GBW，却损失饱和余量、P1dB、THD 和功耗。

## 同源网表与 OA 不变性

主任务四个候选的 netlist 路径分别为：

```text
/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-bias-load-tune_dcb514474289/netlist
/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-bias-load-tune_7c3e321b4ee6/netlist
/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-bias-load-tune_07f1952ab30e/netlist
/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-bias-load-tune_362ba3494296/netlist
```

四份结构网表以及后续两个 guard 的所有成功网表都具有相同 SHA-256：

```text
2c529799349ac466606367015a038e22c4ab7048b8b21e89092146515de7b232
```

每个候选 evidence 都报告 `oa_netlist_reuse=one_verified_netlist`、`parameter_consistency=matched`、`shared_metric_consistency=matched`，并保存三个不同的 `input_from_oa_ac.scs`、`input_from_oa_transient.scs`、`input_from_oa_noise.scs` 路径。它们共享 DUT 网表，但 testbench 中的 bias/load 和 analysis 不同。

主任务、预算任务和恢复后的全不可行任务都进行了独立 final inspect。before/after 的 semantic parameters 完全相同：

```text
device_width_um = 1.0
length_um = 0.03
load_resistance_ohm = 20000.0
source_resistance_ohm = 1000.0
```

## 预算耗尽 guard

记录：

```text
artifacts/runs/common-source-quality-bias-load-budget/live-20260721.json
artifacts/runs/common-source-quality-bias-load-budget/live-20260721.checkpoint.json
```

任务声明 4 点空间但设置 `max_iterations=2`。两个完成候选都可行，选择已评估前缀中的 `0.35 V/1 fF`；最终状态正确为 `partial`，并明确记录：

```text
search budget exhausted after 2 of 4 declared candidates;
selection is only best within the evaluated prefix
```

checkpoint 为 `complete=true`、`next_candidate_index=3`、2 个候选。没有把前缀选择包装成全空间最优。

## 全不可行 guard 与真实 transport 恢复

首次记录：

```text
artifacts/runs/common-source-quality-bias-load-infeasible/live-20260721.json
```

候选 1 的 `si -batch` 遇到真实 `WinError 10054`。该 run 为 `failed`，`simulation.candidate.1` 标为 `system_event`，候选数为 0，checkpoint 保持 `complete=false`、`next_candidate_index=1`、`pending_oa_parameters=null`。没有把 netlist 失败伪装成不可行电路点。

Bridge doctor 重建可用连接后，使用相同 task、token 和 checkpoint 恢复：

```text
artifacts/runs/common-source-quality-bias-load-infeasible/live-resume1-20260721.json
artifacts/runs/common-source-quality-bias-load-infeasible/live-20260721.checkpoint.json
```

恢复先执行独立 `schematic.inspect.resume`，确认 OA 后从候选 1 重跑。两个 0.40 V 候选最终都三项完整，但均违反 THD 和 DC 功耗约束；run 正确为 `partial`，记录 “no feasible testbench condition was selected; OA parameters remained unchanged”。最终 checkpoint 为 `complete=true`、`next_candidate_index=3`、2 个候选，并保留原始失败 action 和恢复 note。

这验证了质量组合在“首个候选尚未形成可信证据”时的 transport recovery。它没有验证在已有一个或多个完成质量候选后跳过前缀；该边界此前已在 AC 调优中验证，但不能由本次结果重新宣称。

中断还暴露了一个证据缺口：当 `si -batch` 在返回结构网表前失败时，旧错误文本没有保存失败 scratch 的具体目录。后续 VDA worker 已改为在该错误中加入 `remote si run retained at <path>` 并补回归；已经发生的失败目录无法从旧 run record 反推。该修正不修改 Bridge。

## 证据分类

- OA 结构与参数：`bridge_readback`
- `si` 网表、DC OP、AC、transient 和 noise 连续指标：`eda_result`
- 饱和区、交点、P1dB/THD/噪声积分、组合成员、一致性和完整性判定：`software_inference`
- `quality`、sweep、bias/load、constraints 与 objective：显式任务字段为 `user_input`
- `WinError 10054` 与失败 action：`system_event`

成功不由 return code 单独判定；必须同时具备 OA/网表一致、三个子分析完整、共享指标一致、逐条约束判定和 final OA readback。

## 本地回归

在加入失败 `si` scratch 路径证据和两个 guard 任务后重新执行：

```text
python -m pytest: 142 passed
all examples/tasks/*.json plan validation: 43/43 passed
vda catalog: common_source = Gate 2 read-only quality tuning/recovery verified
```

新增错误路径测试直接注入 `WinError 10054`，要求异常包含 `/data/xum/virtuoso_bridge_smoke/vda_<task>_<nonce>`；它验证的是 VDA 证据保留，不声称底层 transport 故障已经消失。

## 未验证边界与下一道 Gate

- 本轮只搜索一个 source-degenerated cell 的 bias/load，未做 W/L/RD/RS/VDD 的质量驱动写回。
- 没有 corner、多频点线性度、输入电容、真实面积、Monte Carlo 或 post-layout。
- `SimulationResult.tool_version` 在三个子分析中仍为 `None`；Spectre 21.1.0 来自既有环境探测，不是这些 run 自带的版本证据。
- 当前每候选复用 OA/`si` 网表，但仍启动三个独立 Spectre run；是否需要进一步合并必须先以 live profiling 证明启动开销主导。
- 本次 resume 从候选 1 开始，没有新验证“跳过已完成质量候选前缀”。

下一道 Gate 是选择很小的 W/RD/RS（随后才是 L/VDD）质量搜索，在显式 OA 写授权下验证逐候选写入、最佳可行写回、全不可行恢复和 checkpoint；再加入有限 corner。在这些完成前，项目可称为 **read-only multi-analysis common-source quality tuning and recovery verified**，不能称为完整 L5B 单模块设计闭环。
