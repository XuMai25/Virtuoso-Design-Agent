# 2026-07-21 ADE background 同源执行与恢复 live Gate

## 结论

在用户授权的专用目标上，VDA 已真实闭合：

```text
既有 TSMC N28 OA schematic
  -> 新建非覆盖 Maestro test
  -> transient + output/spec CAS patch
  -> background Spectre
  -> Interactive.0 Detail 结果
  -> exact-history RDB/log + unique-runtime input manifest
  -> input.scs 与 Maestro design、OA 实例/节点/raw 参数核对
```

最终状态是 **live background Maestro same-source execution, evidence recovery, and raw-input consistency verified**。这不是完整设计规格闭环：当前只验证一个弱 `VoutAvg > 0.1 V` 命名 spec，`overall_spec` 为空，history 名唯一性、PDK `Wfg` 派生语义、ADE sweep/corner 和通用 VDA constraint 映射均未闭合。

## 环境与安全范围

- Bridge：`virtuoso-bridge 0.7.0`，本轮未修改第三方仓库。
- Virtuoso：`6.1.8-64b`；Spectre：`21.1.0`。
- profile：`nics4304_tsmc28`，默认晶圆厂工艺基线为 TSMC N28。
- 新目标：`vb_pdk_smoke/vda_manual_ade_handoff_001/maestro`。
- source design：`vb_pdk_smoke/bridge_tsmc28_mos_inv_tb_gnd_20260705_090720/schematic`。
- `replace_existing=false`；只创建原先不存在的 Maestro view，没有复制或修改 source schematic。
- background run 允许远端计算；持久化 setup patch 有显式 Maestro 写授权。所有 scratch/manifest 位于 `/data/xum/virtuoso_bridge_smoke`，没有把新产物写入 `/home/xum`。

## prepare 与 setup patch

`artifacts/runs/existing-maestro-prepare/live-20260721.json` 成功创建 test `VDA`，独立回读的 design 精确等于上述 source schematic。证据同时记录 `schematic_oa_write_performed=false`、`existing_maestro_overwritten=false`；创建时 analysis/sweep/output 均为空。

setup 任务声明完整旧状态：默认 disabled transient、无 outputs；目标为 `tran stop=300p, maxstep=1p`，新增 `/IN`、`/OUT` net outputs 和 `VoutAvg=average(VT("/OUT"))`，后者 spec 为 `> 0.1`。首次执行在 `save_setup` 前因 IC6.1.8 把 calculator expression 返回为未引用 S-expression 而失败；独立重开证明没有持久化任何部分。VDA 改用 `%L` 序列化 expression 后，`artifacts/runs/existing-maestro-setup-apply/live-retry1-20260721.json` 成功，关闭并重新打开的 tests、analysis/options、outputs/spec 全部匹配。没有运行仿真，也没有写 schematic。

## 首次运行与不重算恢复

`artifacts/runs/existing-maestro-run/live-20260721.json` 的 EDA 本体已经完成 `Interactive.0`，但 VDA 在随后生成产物清单时触发 IC6.1.8 `csh: command exceeds buffer`，因此整个 run record 正确标为 failed。保留的 history log 显示 1 个 point 完成、0 simulation errors、`VoutAvg=364m` 且 named spec pass；这份日志只用于定位，不能代替最终结构化证据。

后续任务显式给出：

- `resume_history=Interactive.0`；
- `resume_runtime_scratch_root=/data/xum/virtuoso_bridge_smoke/vda_ade_run_existing-maestro-run_d044ef4c90a9`。

planner 明确生成 `ade.run.resume`，worker 不调用 `run_and_wait`。恢复期间依次暴露并修正了：外层 `csh` 提前展开内层 shell `$`、复合 `find` 括号表达式、长 task slug、单命令 digest，以及本机 DNS 暂时不能解析 `nics4304-cad1` 导致的 SCP 失败。每次失败都保留 exact history、runtime scratch 和远端 manifest；没有把 transport/证据失败重分类为电路不可行，也没有重新跑 Spectre。

最终 `artifacts/runs/existing-maestro-run/recover-interactive0-retry13-20260721.json` 成功，并明确记录 `simulation_performed_by_this_invocation=false`、`history_recovery_performed=true`、`oa_write_performed=false`、`maestro_setup_write_performed=false` 和 session runtime directory 已恢复。

## 结构化结果与产物证据

IC6.1.8 的单点 Detail CSV 是六列 `Test,Output,Nominal,Spec,Weight,Pass/Fail`，没有 Bridge 0.7.0 parser 期待的 `Point` 列。VDA 只对严格匹配的临时 CSV 补 `Point=1` 后仍调用 Bridge 原 parser，并记录：

- 原 CSV SHA-256：`be6a57a2adfaf5e01a0ea4baeda3bdcfd880610f6051faf10379e1ff7efb7743`；
- 归一化 CSV SHA-256：`effc70db5eb2a4814cfc89a2f4dcbd7573be102dd2792b3ba299e9c7eacc951f`；
- 归一化判断：`software_inference`；output/spec 数值仍是 `eda_result`。

结构化 point 结果为：

| output | value | spec | pass/fail |
| --- | ---: | --- | --- |
| `Vin` | 空（net output） | — | — |
| `Vout` | 空（net output） | — | — |
| `VoutAvg` | `364e-3 V` | `> 0.1` | `pass` |

`overall_spec` 为空，`overall_yield` 返回 `(nil Yield 100 PassedPoints 1 ErrorPoints 1)`；因此只宣称命名 `VoutAvg` spec 通过，不把本次结果包装成所有输出或设计规格完整通过。

最终 manifest 位于 `/data/xum/virtuoso_bridge_smoke/vda_ade_manifest_5ea7d3003f14`，包含：

- `simulator_input=65`；
- `eda_result=1`（`Interactive.0.rdb`）；
- `run_log=2`（`.log`、`.msg.db`）；
- simulation fingerprint：`c382cdeb15eb2f9b490b6078880b9975a9589c8210580a20854f0e75f9b79851`。

Bridge `download_file` 始终先被调用。只有标准下载失败且路径严格匹配 VDA 生成的 manifest TSV 或 Bridge 生成的 Detail CSV 时，才通过现有 SKILL channel 做有 4096 行/2 MB 上限的文本读取。该 fallback 不接受任意 `/tmp` 路径，不传输 PSF，不复制 SSH/SCP，也没有改 Bridge。

## OA 到真实 `input.scs` 的一致性

manifest 绑定的 `input.scs` SHA-256 为 `f5979922cb1409d8c3ac01100e005c1062e0416a8cc725e543d545c72ccb2f87`。读取后再次计算 hash 一致，Design header 精确指向 source schematic。独立 OA readback 与 Spectre 输入比较通过：

- OA 中除 `GND0` ground symbol 外的实例集合，等于网表中的 `MP0/MN0/VIN0/VDD0/CL0`；
- `gnd!` 只按 Cadence 全局地规范化为 Spectre `0`，其余节点和 MOS `D/G/S/B`、source/cap `PLUS/MINUS` 顺序一致；
- model/master 一致；
- 共 19 组 raw 参数映射一致：MOS `l/w/nf/simM→multi`，pulse source 的 `v1/v2/per/td/tr/tf/pw/srcType`，DC source 的 `vdc/srcType`，以及 capacitor `c`；
- comparison SHA-256：`ff2478766b5c9ed0a3fe62281ce3724b947c16b1dc864e64e7b9ee5d2035441b`。

当前 OA `MP0.Wfg=1u`、`MN0.Wfg=500n`，而真实网表两者 literal `w=100n`。VDA 只确认 OA raw `w=100n` 进入网表，并把 `Wfg` 标为“PDK CDF effective-width relation 未验证”；不能把这两类参数混同。

## 实现与回归边界

- `ade.prepare` 现在允许目标 Maestro cell 与 source design cell 分离，并对 test 的实际 designObj 做独立回读。
- `ade.run` 的每 test project/results dir 只在当前 session 临时定向到 `/data/xum`，立即回读并在 close 前恢复；不保存 setup。
- `resume_history` 与 `resume_runtime_scratch_root` 必须成对，恢复不调用 `run_and_wait`。
- 产物清单分别绑定 exact-history companion 与 unique-runtime input，executor 对两类 location 使用不同一致性规则。
- `require_simulator_input_consistency` 默认关闭，避免缩窄未知已有 schematic；显式打开时，遇到没有已知 primitive contract 的实例会失败而不是假装核对完成。
- Bridge 第三方仓库没有修改；本轮兼容逻辑全部位于 VDA worker，并由本文记录。

本轮最终本地回归为 `253 passed in 0.55s`；全部 `examples/tasks/*.json` 为 `52/52 example plans passed`，`git diff --check` 通过。最终 live 成功记录为 `artifacts/runs/existing-maestro-run/recover-interactive0-retry13-20260721.json`。

## 未验证边界与下一道 Gate

1. 这次没有真实 ADE parametric sweep；下一自动 Gate 应让一个声明的 global/test/corner 变量实际被 schematic 引用，再验证逐点 `input.scs`/结果，而不只确认 setup 中存了变量。
2. `Wfg`、fingers、multi 和最终有效宽度的 PDK CDF 派生关系尚未从 netlisting 语义完全解释；当前只确认明确的 raw `w/l/nf/multi`。
3. history 命名与覆盖策略仍来自保存的 Maestro setup，VDA 没有证明 `Interactive.0` 在运行前不存在。
4. `VoutAvg > 0.1 V` 不是有意义的反相器设计质量规格；尚未在 ADE 路径提取 delay、rise/fall、能量并映射到 VDA constraints。
5. 人工打开/修改/保存/重跑、旧 ADE L state 备份迁移和人工数值交叉检查仍按用户决定延期；本次 background Gate 不替代这些项目。
6. 后续有限 corner、L/VDD 联合搜索和差分对仍未完成，不能升级为完整 L5B 单模块设计代理。
