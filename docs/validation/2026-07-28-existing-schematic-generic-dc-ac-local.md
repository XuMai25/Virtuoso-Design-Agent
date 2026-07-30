# 2026-07-28 existing-schematic 通用 DC/AC 本地 Gate

## 目标

在不新增反相器、共源或差分对专用分支的前提下，让用户提供的既有 OA schematic 能表达
一条通用、受控的 OA→`si`→Spectre DC/AC 路径。本 Gate 只验证 VDA 契约、路由、解析和
失败边界，不连接 Bridge 或宣称真实电路性能。

## 实现

- 新增 `GenericOaSimulationSpec`，只接受 typed voltage/current source、R/C load、单端或
  差分电压表达式、命名 DC voltage/source current/MOS OP metric 和显式 OA 参数到 `si`
  参数绑定。所有对象名经过保守标识符校验；raw Spectre、SKILL、shell 或任意 deck 字段
  因 `extra=forbid` 被拒绝。
- `existing_schematic simulation.run` 必须携带 `design_context`、显式 `dc`/`ac` analysis 和
  `generic_simulation`；不接受 semantic parameters、隐式建图或 replace。AC 还必须声明
  sweep、transfer 以及至少一个非零 AC source。
- planner 固定为 `probe → inspect → design.context.bind → netlist → simulate → evaluate →
  persist`。该 operation 只读 OA、运行远端计算，不包含 OA 写入。
- Bridge subprocess adapter 增加一个 action，但仍调用原 `_generate_oa_netlist`、远端 Spectre
  process guard、runner、PSF parser 和 manifest；没有修改或复制 Bridge。
- worker 在 `si` 前重新读取同一 schematic 并重做 context audit，减少 inspect 与 netlist
  之间的状态漂移。随后要求 flat primitive OA/`si` instance、model、node 完全一致，并逐项
  比较声明的 CDF→netlist 数值。
- DC 提取声明的差分/单端节点电压、source current 和 OP 标量；AC 复用既有复数 transfer
  提取器得到低频 gain/phase、首个 -3 dB bandwidth、GBW 和 unity-gain。缺 signal、空波形、
  非有限值、参数漂移和未解析带宽都会失败或保持 incomplete，不能用 return code 代替。

## 本地验证

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m virtuoso_design_agent plan examples\tasks\existing-schematic-generic-ac.demo.json
```

结果：`756 passed`；示例 plan 生成 7 步，只含 OA read、remote compute 和 local evidence write。

新增 11 项测试覆盖：

- 合法 DC/AC 契约、计划顺序和无 OA write；
- 缺 context、越权参数映射、动态 analysis、raw deck 字段和零 AC 激励拒绝；
- typed wrapper 渲染；
- OA/`si` topology、参数一致与未知 testbench node 拒绝；
- executor 证据顺序、transport interruption 的 `system_event` 记录；
- subprocess action/payload 路由；
- mocked worker 的 DC、OP、复数 AC、bandwidth 和 artifact manifest；
- 空 AC 波形拒绝。

## 证据与未验证边界

- 本 Gate 没有 SSH、远端计算、OA 写入或真实 Spectre；测试结果是 `software_inference`。
- 真实执行时，OA readback 是 `bridge_readback`；`si` netlist、PSF 和 simulator artifacts 才是
  `eda_result`；context/topology/parameter comparison 与 metric extraction 是
  `software_inference`。
- 首版只支持 nominal flat MOS/R/C primitive schematic 和直接数值 CDF 映射。层次化设计、
  source/load 已存在于 OA 的完整 testbench、Wfg×fingers 等派生关系、tran/noise/PVT 均未
  验证。通用 writeback/tuning/checkpoint 的本地后续 Gate 已完成，但真实 OA 写入仍待验证。
- 单次只读 simulation transport interruption会保留失败 action，且没有需要恢复的 OA 写入；
  worker 继续使用既有协作取消、Windows Job Object、remote guard 和最终资源关闭路径。

## 下一道 Gate

选择一个不依赖专用 circuit worker、且 primitive/CDF 映射明确的新 `vda_` cellview，先只读
inspect 并冻结 context/plan，再执行一次 DC 和一次 AC smoke。执行前仍需列出目标、远端路径、
OA write=false、remote compute=true 和 plan token，取得单独确认。通过后才把该 worker 接到
通用 instance-parameter candidate/checkpoint/writeback，而不是立即增加更多 analysis。

后续实际先复用了已有专用路径真值的共栅级联 cellview，而没有立即再建一个新拓扑；这样能在
相同 OA/`si` SHA 下直接证伪通用 worker 的节点、参数和指标偏差。该 live Gate 已通过，详见
[通用 DC/AC live 记录](2026-07-28-existing-schematic-generic-dc-ac-live.md)。这仍只是一种拓扑，
不把单点一致性外推为跨任意电路保证。

该 worker 随后已接入通用有限 raw-instance 搜索状态机；本地契约、最佳提交、全不可行恢复、
预算和 transport resume 见
[通用有限调优本地记录](2026-07-28-existing-schematic-generic-tuning-local.md)。
