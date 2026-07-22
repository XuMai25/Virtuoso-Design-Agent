# 2026-07-23 差分对 Gate 3 nominal DC 本地实现

状态：**differential-pair nominal DC contract implemented locally; live TSMC N28 validation pending**。

本轮没有连接 nics4304、没有写远端 OA、没有运行远端 Spectre，也没有修改 `virtuoso-bridge-lite`。新增能力位于 VDA 的既有 task、planner、demo、subprocess adapter 和 Bridge worker 边界内；离线 demo 结果全部是 `software_inference`，不能作为电路性能证据。

## 固定首版拓扑

目标 DUT 是电阻负载 NMOS 差分对：

- `MN0`: `D=OUTP, G=INP, S=TAIL, B=VSS`
- `MN1`: `D=OUTN, G=INN, S=TAIL, B=VSS`
- `RD0`: `PLUS=VDD, MINUS=OUTP`
- `RD1`: `PLUS=VDD, MINUS=OUTN`
- 顶层 pins/nets：`INP, INN, OUTP, OUTN, TAIL, VDD, VSS`

OA DUT 不含尾电流源、输入源或固定分析。理想尾电流源、两个匹配共模输入源和 VDD/VSS 只位于本次 Spectre wrapper，因此同一 cellview 后续可由人工 ADE 接管。

## 参数与写入边界

OA semantic 参数：

- `input_width_um`：同时写 MN0/MN1 的 `wf`
- `length_um`：同时写 MN0/MN1 的 `l`
- `load_resistance_ohm`：同时写 RD0/RD1 的 `r`

testbench-only 参数：

- `tail_current_ua`
- `common_mode_v`
- `vdd_v`

`schematic.create` 与 `parameters.apply` 会拒绝把 testbench-only 参数持久化。显式 `instance_parameter_updates` 和 `instance_parameter_space` 仍可使用 Bridge 的原始 CDF 字符串面；不过固定差分对 adapter 在写后会重新检查两支路 semantic/geometry 对称性，不能以原始字段绕过当前模板契约。`existing_schematic` 的通用 Bridge 透传能力没有被收窄。

## 同源证据链

本地 worker 已实现：

```text
OA schematic readback
  -> exact MN0/MN1/RD0/RD1 topology/master/pins
  -> symmetric Wfg/fingers/m/total-width/L/R extraction
  -> si -batch netlist
  -> exact nodes/models + symmetric w/nf/multi/total-width/l/r parsing
  -> OA/netlist semantic and geometry equality
  -> external VCM/VDD/ideal-tail-current DC wrapper
  -> root dcOp.dc + dcOpInfo.info
  -> branch OP, node/device VGS/VDS, tail/supply/load KCL
  -> saturation, offset, swing, gm/gds, intrinsic gain and VDD power
  -> constraints/objective/checkpoint/final OA readback
```

以下任一情况硬失败，不作为普通规格不可行候选：

- 四实例、pins、nets、端口或 master 不精确；
- 两只 NMOS 的单指宽、指数量、multiplicity、总宽或 L 不同；
- 两只负载 R 不同；
- OA 与 `si` 参数/geometry 不一致；
- DC/OP 文件缺失、为空或来源有歧义；
- 输入/VDD/VSS 节点不匹配测试台设定；
- 任一器件节点 VGS/VDS 与 OP 标量不一致；
- 请求尾电流与 source current 相差超过 1%；
- 支路和对尾源、支路和对 VDD source、任一支路对相应负载的 KCL 残差超过 1%。

## 指标与来源

连续指标包括：两支路电流、支路和、尾源/电源/负载电流及残差、两支路 VGS/VDS/VDSAT/饱和余量、输出共模/差模/绝对 offset、上侧和饱和侧摆幅余量、两支路 gm/gds、最小 intrinsic gain 和实际 VDD 功耗。真实运行时这些量来自 Spectre 节点/source current/OP 并标为 `eda_result`；OA 结构和参数属于 `bridge_readback`；任务显式测试台值属于 `user_input`；双管饱和分类、KCL/一致性判定与搜索选择属于 `software_inference`。

## 本地验证

新增测试覆盖：

- 平衡和不平衡的纯指标提取；
- DC-only task 契约与 AC 拒绝；
- planner 副作用和外部尾源说明；
- 精确 OA topology、对称 semantic/geometry 和破坏性反例；
- 两支路 semantic 写入映射；
- `si` parser 的 topology/geometry/R 对称和反例；
- wrapper 不复制 DUT topology、保存双支路 OP；
- 节点/器件一致性、三类 KCL 和错误拒绝；
- subprocess action 路由和完整 mocked worker evidence；
- demo 的成功写回、全不可行恢复和预算截断。

`examples/tasks/differential-pair-dc-close-loop.demo.json` 已离线完成 3/3 候选并选择 `input_width_um=2.0 µm`；`branch_current_mismatch_percent=0`、`tail_current_mismatch_percent=0`、`output_offset_abs_mv=0`、`dc_supply_power_uw=45`。这些数值来自分析 demo，仅证明编排、约束和选择语义。run record：

```text
artifacts/runs/differential-pair-dc-close-loop-demo/local-20260723.json
artifacts/runs/differential-pair-dc-close-loop-demo/local-20260723.checkpoint.json
```

全部 92 个示例任务已成功生成计划；Python 全量回归为 `414 passed`，`git diff --check` 通过。

## 下一次 live smoke 的副作用清单

本轮未执行下列动作。建议下一次用一次明确确认覆盖这一组有序 Gate：

1. 目标：`vb_pdk_smoke/vda_diffpair_gate3_001/schematic`。
2. OA 写入：第一步非覆盖创建；之后只有 design-parameter Gate 逐候选写 W/L/R 并在最佳可行点提交，失败时恢复基线。
3. 远端计算：单点 nominal DC、只改 `tail_current_ua/common_mode_v` 的只读搜索、再做有限 W/RD/tail 搜索。
4. 远端路径：`/data/xum/virtuoso_bridge_smoke/vda_differential-pair-*_<unique-id>`；不写 `/home/xum`。
5. 覆盖风险：所有任务 `replace_existing=false`。若目标已存在但不精确匹配固定差分对，创建/检查必须失败；不得删除或覆盖未知 cellview。
6. 任务文件：`differential-pair-create.bridge.json`、`differential-pair-inspect.bridge.json`、`differential-pair-dc-verify.bridge.json`、`differential-pair-dc-bias-tune.bridge.json`、`differential-pair-dc-design-tune.bridge.json`、`differential-pair-dc-design-infeasible.bridge.json` 和 `differential-pair-dc-design-budget.bridge.json`。独立 canonical/raw 写入样例也已提供，但不应在 nominal Gate 前抢先执行。

## 尚未闭合

- 没有真实 OA create/readback、`si` netlist、Spectre DC/OP 或性能数值。
- 真实 PDK 对双管完全相同 nominal 条件下的数值 offset 可能接近零；当前没有 mismatch/Monte Carlo，因此不能宣称失配设计能力。
- 没有 differential AC gain/bandwidth、common-mode gain、CMRR、输入共模范围或 transient。
- 没有差分对 ADE/Maestro setup；人工 ADE 兼容原则已保留，但尚未 live 交接。
- 没有差分对 PVT；按项目策略它是 nominal 稳定后的可选加严项，不作为第一步默认成本。
- 没有验证真实多字段 CDF callback 耦合、差分对 raw parameter 搜索或 transport checkpoint；这些必须在 live Gate 保留独立证据。

下一道 Gate 不是立即增加 AC，而是按上述清单完成 nominal create/readback、单点 DC、只读 testbench 搜索、设计参数写回、全不可行、预算和 transport recovery。全部成立后，才进入 differential AC/CMRR 和输入共模范围。
