# 2026-07-28 existing-schematic 通用 DC/AC live Gate

## 结论

`existing_schematic simulation.run` 的 typed 通用 worker 已在真实 TSMC N28 OA 上完成首个
只读 DC/AC Gate。它没有调用 common-source/cascode 专用仿真分支，但对同一 OA、偏置和负载
得到与旧专用路径一致的 OP 与 AC 结果。当前状态是 **generic flat OA-to-si DC/AC execution
verified on one nominal cascode stage**，不是跨任意拓扑的 L5B 规格闭环。

## 范围与授权

- profile：`nics4304_tsmc28`，nominal `top_tt`；
- target：`vb_pdk_smoke/vda_cs_cascode_gate_001/schematic`；
- 当前拓扑：`MN0 + MNCAS + RD0`，nets=`IN/NCAS/OUT/VCAS/VDD/VSS`；
- OA write：`false`；remote compute：`true`；`replace_existing=false`；
- DC token：`702dded3416afbf6`；AC token：`b6dc8eb4cf52cba8`；
- testbench：`VDD=0.9 V`、`VIN=0.35 V`、`VCAS=0.545 V`，AC 再加 `CL=2 fF`。

## 真实失败链与修复

第一次任务锁定了历史 inverse 后的普通共源 SHA，但当前 OA 已处于共栅级联态。前置
`design.context.bind` 读到 `2e27d1c6...` 并在 `si` 前拒绝；没有远端计算或 OA 写入。

按真实回读重编译后，前置 inspect/context 通过，但 worker 内二次 TOCTOU 回读得到不同 SHA。
根因是前置 `inspect_existing_schematic` 的 canonical topology 包含逻辑 pin 对应的 physical
master/xy/orient，而通用 simulation worker 只把裸逻辑 pin 送入同一 fingerprint。修复后 worker
复用 `_schematic_geometry_bundle`，再以完全相同的 summary 做 context audit；没有放宽 hash。
新增测试把 expected hash 绑定到 physical-pin canonical summary，旧实现会稳定失败。

修复后的第一次 DC 在 `si` 返回时遇到 SSH/socket `WinError 10054`，保留为 `system_event`，
没有电路结果。共享 tunnel 随后已停止；VDA 用静默 `bridge start` 恢复连接，远端只读审计确认
`si=0`、`spectre=0` 后再重试。DC 与 AC 重试均成功。

## 同源一致性证据

- topology SHA：DC/AC 均为
  `2e27d1c68012bc1e7adc0da936a648febde4b685cfa1391512a2d401fb291a51`；
- `si` netlist SHA：DC/AC 及旧专用路径的同尺寸候选均为
  `4a2b4d21c32557788cfc9ae537f53427ead757ebe3f115044af1ac95f7b4de7b`；
- exact instance/model/node 集合 matched；
- 9 项绑定 matched：`MN0/MNCAS.Wfg→w`、`l→l`、`nf→nf`、`simM→multi`，以及
  `RD0.r→r`；
- DC manifest：10 files，完整；AC manifest：11 files，完整；
- 两次 guard 均安装并回读相同 SHA-256，timeout=`600 s`、TERM/KILL grace=`10 s`。

| 指标 | 通用 worker 真实结果 |
|---|---:|
| VOUT DC | 0.3810253015 V |
| MN0 / MNCAS Id | 25.9487225 / 25.9487669 µA |
| MN0 / MNCAS VDS | 0.1731028792 / 0.2079224223 V |
| gain | 6.441244289 V/V (16.17939541 dB) |
| bandwidth | 3.612726923 GHz |
| GBW | 23.27045666 GHz |
| unity-gain | 22.18952138 GHz |

把通用结果与 2026-07-26 同一 OA/同一 `si` SHA 的专用 candidate 9 对照，12 项 OP 与 5 项 AC
指标全部数值一致；浮点换算后的最坏相对差为 `4.0048e-16`。这证明本 Gate 的通用 testbench、
signal/OP 保存和指标提取没有改变该实例的真值，不证明其他拓扑自动成立。

## 证据来源与资源

- OA/physical pin/CDF readback：`bridge_readback`；
- `si` netlist、DC OP、复数 AC、PSF 和 simulator artifacts：`eda_result`；
- context/topology/parameter comparison、AC metric extraction 和跨路径比较：
  `software_inference`；
- target、testbench、约束、授权与 token：`user_input`；
- topology precondition failure 与 `WinError 10054`：`system_event`。

最终只读资源审计：local transient=`0 entries/0 bytes`，remote `si=0`、`spectre=0`，
Maestro session=`0`；两个既有 `virtuoso` 进程保持共享基线。retained evidence 按既有策略不删除。

## 仍未闭合与下一 Gate

- 只验证一个 nominal flat MOS/R 拓扑；没有证明层次化 cell、派生 CDF、不同 foundry/profile、
  transient、noise、PVT 或任意自然语言拓扑；
- 本 Gate 没有 OA write；通用候选搜索、checkpoint、最佳点写回与恢复后来只在本地 fixture
  闭合，尚无真实 OA-write 证据；
- `VDD_SRC:p` 保留 Spectre p-terminal 原始负号，尚未增加通用“消耗功率”符号约定；
- 现有 `instance_parameter_space/candidate_set`、通用 `parameters.apply`、本 worker 和
  checkpoint/recovery 已完成本地组合；下一步是在同一已知 cell 上用很小的显式参数域做
  OA-write live Gate，并要求最终参数、`si` 网表和 Spectre 指标三者一致。详见
  [本地调优记录](2026-07-28-existing-schematic-generic-tuning-local.md)。
