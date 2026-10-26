# 2026-07-29 existing-schematic 通用有限实例参数闭环真实 Gate

## 结论

`existing_schematic design.tune` 已在真实 TSMC N28 OA schematic 上完成一次三点 raw CDF
参数搜索：逐点写入和回读、OA→`si` 自动网表、Spectre AC、指标/约束判定、checkpoint
中断恢复、最终参数提交以及任务外独立 OA 回读均已闭合。当前状态升级为
**generic existing-schematic finite raw-instance OA-write tuning live verified for one flat,
nominal AC width sweep**。

这证明的是用户给定 topology 后的通用有限参数细化和恢复状态机，不是连续空间最优、设计质量
闭合或 L5B closure。本任务没有 objective；三个点全部可行时，排序保留声明顺序，因此最终写回
首点 `750.0n`。不得把该选择解释为 `750.0n` 的 AC 性能优于另外两点。

## 授权与目标

- target：`vb_pdk_smoke/vda_cs_cascode_gate_001/schematic`
- PDK profile：`nics4304_tsmc28`
- operation/circuit：`design.tune` / `existing_schematic`
- 唯一搜索字段：`MNCAS.Wfg=[750.0n,1u,1.25u]`
- OA 写入：是，只逐点暂存该字段并最终提交选择值
- 远端计算：是，三次 OA→`si`→Spectre AC
- `replace_existing=false`；没有新建或替换 cellview
- plan token：`7a6eff43d767a16f`
- 任务文件：`examples/tasks/existing-schematic-generic-cascode-ac-tune.bridge.json`

远端根分别为：

```text
/data/xum/virtuoso_bridge_smoke/vda_existing-schematic-generic-cascode-ac-tune-live_f99b55464994
/data/xum/virtuoso_bridge_smoke/vda_existing-schematic-generic-cascode-ac-tune-live_ff0fb68a1e63
/data/xum/virtuoso_bridge_smoke/vda_existing-schematic-generic-cascode-ac-tune-live_761ed9e816cd
```

## 三点 EDA 结果

约束为 `0.1 V <= output_dc_v <= 0.8 V`、低频增益 `>=3 V/V`、带宽 `>=1 GHz`。

| index | `MNCAS.Wfg` | output DC (V) | gain (V/V) | BW (GHz) | GBW (GHz) | unity (GHz) | 结果 |
|---:|---:|---:|---:|---:|---:|---:|---|
| 1 | `750.0n` | 0.381025 | 6.441244 | 3.612727 | 23.270457 | 22.189521 | complete, feasible |
| 2 | `1u` | 0.366186 | 6.671325 | 3.427300 | 22.864635 | 21.765959 | complete, feasible |
| 3 | `1.25u` | 0.355531 | 6.814006 | 3.257386 | 22.195848 | 21.114507 | complete, feasible |

连续 AC 标量来自 Spectre，候选证据标为 `eda_result`；约束判定、排序和
`best_in_declared_discrete_domain` 边界是 `software_inference`。完整三点域已穷尽，但
`continuous_optimum_claim=false`、`global_optimum_claim=false`。

## OA、网表与结果一致性

三点均满足：

- OA 定向回读分别确认 `MNCAS.Wfg=750.0n/1u/1.25u`，属于 `bridge_readback`；
- `si` 网表中的 `MNCAS.w` 与同点 OA 值逐字符串匹配；全部 9 项声明的
  CDF→netlist binding 均为 `matched`；
- flat primitive topology 与 OA 一致，三点 topology SHA-256 均为
  `2e27d1c68012bc1e7adc0da936a648febde4b685cfa1391512a2d401fb291a51`；
- 三份 Spectre artifact manifest 均完整，远端 guard 均为 bounded process。

逐点 `si` 网表 SHA-256 为：

| `MNCAS.Wfg` | netlist SHA-256 |
|---:|---|
| `750.0n` | `4a2b4d21c32557788cfc9ae537f53427ead757ebe3f115044af1ac95f7b4de7b` |
| `1u` | `f56d1deae8522a9c459fedf4b0e8829dbd81f649dac990f23a9f7d063c5f9204` |
| `1.25u` | `0a3e7f250047c0fea30b7e72dc8180729c9ba3e3b6048af0767c4696091f5c35` |

因此本 Gate 不是“OA 值改变但 Spectre 仍跑旧网表”的双源路径。

## 真实中断与 checkpoint 恢复

候选 1、2 已完整落盘后，进入候选 3 的 `parameters.stage`、执行 `read_schematic` 时遇到：

```text
BridgeWorkerError: RuntimeError: read_schematic SKILL error:
Socket error: [WinError 10054] 远程主机强迫关闭了一个现有的连接。
```

VDA 将失败 action 记为 `system_event`。恢复动作开始时 OA 回读仍是上一个已确认点 `1u`，随后
恢复初始 `750.0n` 并定向回读；因此不声称失败的 stage 已把 `1.25u` 持久化。checkpoint
保留两个已完成候选、`next_candidate_index=3`，没有产生推荐结果。中断后只读资源审计确认
`spectre=0`、`si=0`、Maestro session=0，随后同一 task、token 和 OA 基线通过独立 resume
readback，从候选 3 继续。候选 1、2 没有重复运行。

最终 run record 同时保留原始 WinError note 和
`resumed candidate search at index 3 after independent OA readback`，没有静默抹去失败链。

## 最终 OA 与资源状态

`parameters.apply.best` 请求、实际应用和定向回读都确认 `MNCAS.Wfg=750.0n`。随后任务内
`schematic.inspect.after` 成功；任务外又执行独立只读
`common-source-cascode-inspect`，确认：

```text
instances=3, nets=6, pins=5
MN0.Wfg=1u
MNCAS.Wfg=750.0n
RD0.r=20K
```

起跑前与结束后的远端资源计数均为 `spectre=0`、`si=0`、
VDA-managed Maestro session=0、既有 Virtuoso=2；本地 transient entry 和 cancel marker 均为
0。本轮启动的共享 Bridge tunnel 已显式停止，`vda bridge status` 随后报告 tunnel not running。
没有删除远端证据目录，也没有修改第三方 Bridge。

三次 `simulation.candidate` action 合计约 259.9 s；从首次 run record 开始到 resume 完成共
383.55 s，其中包含 WinError、基线恢复以及恢复前的独立检查等待，不能当作纯 Spectre 时间。

## 记录与本地回归

- 首次失败记录：
  `artifacts/runs/existing-schematic-generic-cascode-ac-tune/live-20260729.json`
- 完整 checkpoint：
  `artifacts/runs/existing-schematic-generic-cascode-ac-tune/live-20260729.checkpoint.json`
- 恢复后成功记录：
  `artifacts/runs/existing-schematic-generic-cascode-ac-tune/live-20260729-resumed.json`
- 任务外 OA 回读：
  `artifacts/runs/existing-schematic-generic-cascode-ac-tune/post-gate-inspect-20260729.json`
- 资源审计：同目录下 `preflight-resources.json`、`interruption-resources.json`、
  `postflight-resources.json` 和 `after-stop-local-resources.json`
- Python 回归：`762 passed in 4.01s`

## 未验证边界与下一 Gate

- 只验证一个 flat、nominal `top_tt` 共栅级联电路和一个直接映射的 `Wfg -> w` 字段；不能
  外推到 hierarchy、派生总宽度、fingers/multiplicity callback 耦合、tran/noise/PVT。
- 本任务没有 objective，不能证明通用 worker 已能按真实设计目标做质量选优。
- 全不可行恢复和预算耗尽已有本地通用测试，但尚未在这条通用 OA-write 路径做 live；本次
  transport interruption 的真实恢复已闭合。
- `existing_schematic design.close_loop` 仍未开放。下一道有价值的 Gate 应先补一个带显式
  objective 的通用多字段小域，并在真实结果上验证 winner 写回；随后再把同一 design context
  下的预声明 topology-delta 与参数细化组合，而不是扩大随机候选数。
