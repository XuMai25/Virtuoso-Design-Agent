# 2026-07-23 共源原始实例参数有限调优真实验证

## 结论

状态：**bounded explicit-instance-parameter tuning, same-source evidence, writeback, and recovery verified; arbitrary CDF semantics and L5B closure pending**。

VDA 已将独立 `parameters.apply` 的实例字符串参数能力接入受控有限搜索，而没有在 Bridge 中增加第二套参数系统。真实 Gate 在已有的新建测试对象 `vb_pdk_smoke/vda_cs_topology_patch_001/schematic` 上只搜索 `MN0.fingers=["1","2"]`，两个候选都经 OA callback、定向回读、`si -batch` 自动网表和 Spectre DC+AC；GBW objective 选择并写回 `fingers="2"`，最终独立定向回读一致。

这证明的是“用户明确点名的实际 CDF 字段可进入有限闭环”，不是 VDA 自动理解、筛选或可安全修改全部 PDK 字段。

## 任务与安全范围

- target：`vb_pdk_smoke/vda_cs_topology_patch_001/schematic`；
- topology：nominal common-source，实例 MN0/RD0；
- 固定 OA semantic：Wfg=1 µm、L=0.03 µm、RD=10 kΩ；
- 固定 testbench：bias=0.35 V、VDD=0.9 V、CL=2 fF；
- raw search：`MN0.fingers=["1","2"]`；
- analysis：1 kHz–1 THz、30 points/decade 的复数 AC，同时保留 DC OP；
- constraints：饱和、KCL、gain≥1 V/V、bandwidth≥1 MHz；
- objective：最大化 GBW；max_iterations=2；
- OA write 与 remote compute 均显式允许；`replace_existing=false`；
- 没有新建或覆盖其他 cellview，没有修改 Bridge 或 Obsidian Vault。

任务示例为 `examples/tasks/common-source-raw-fingers-ac-tune.bridge.json`，plan token 为 `5bd63ca7eeee3397`。

## 新增执行契约

1. `instance_parameter_space` 每个维度声明 exact instance、actual CDF parameter 和 1–32 个原始字符串值，最多 12 个维度；重复维度、重复值以及与固定 `instance_parameter_updates` 重叠均拒绝。
2. 固定 raw 字段、raw sweep 和 canonical `parameter_space` 形成一个确定性笛卡尔积，但生成阶段即按 `max_iterations` 截断，不能先物化巨大空间。
3. 搜索字段必须能在未过滤 OA inspect 中按实际名字读到。VDA 不猜 `nf→fingers` 等别名，因为恢复前必须知道 exact old value；独立 `parameters.apply` 仍保留 Bridge 原有别名能力。
4. 每个候选先保存 semantic/raw pending state，再只把该具体点交给 Bridge；请求、Bridge 实际应用字段、callback 后立即 targeted readback 必须一致，随后才能 netlist/simulate。
5. candidate record 分开保存 `parameters`、`instance_parameters` 与 callback 后 `oa_parameters`。checkpoint 同时保存 initial/expected/pending 的 semantic 与 raw OA 状态。
6. 只有完整仿真候选才推进 `next_candidate_index`；transport interruption 不把当前点记为不可行或已完成。
7. 最佳候选再次写回；executor 最后独立 inspect，并同时比较 canonical semantic 与 targeted raw 值。全不可行则恢复两组初始值。
8. demo 指标仍只能标为 `software_inference`；本 Gate 的性能证据来自真实 `eda_result`。

最终本地回归为 `399 passed`；全部 `82/82` 个 example task 均可生成计划，`git diff --check` 通过。

## 真实候选结果

| fingers | OA/si 总宽度 | gain (V/V) | bandwidth (GHz) | GBW (GHz) | unity (GHz) | Id (µA) | DC power (µW) | saturation margin (V) | 可行 |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | :---: |
| 1 | 1 µm | 3.98703 | 9.92762 | 39.5817 | 38.3987 | 42.7715 | 38.4944 | 0.362648 | 是 |
| 2 | 2 µm | 4.30374 | 13.5638 | 58.3750 | 57.2258 | 66.3739 | 59.7365 | 0.132377 | 是 |

两点都 `analysis_complete=true`，各有 271 个复数 AC 样本。更高 fingers 在本任务下提高 GBW，但也提高电流/功耗并降低饱和余量；由于本任务只把最低饱和条件作为 guard，最终按 GBW 选择 fingers=2。该选择不代表所有规格下 fingers=2 都更优。

## OA、netlist 与结果一致性

候选 1：

- requested/confirmed：`MN0.fingers="1"`；
- netlist geometry：finger width=1 µm、nf=1、multi=1、total width=1 µm；
- netlist SHA-256：`40fbda2349340e4be6863507e70e4560b1a39dbf47061e15a48da56c9ca2aa77`；
- wrapper SHA-256：`1dbec3cc6c12ce995e82783c5c2efce25029495645c3dca7cb08eca1a04853f3`。

候选 2：

- requested/confirmed：`MN0.fingers="2"`；
- netlist geometry：finger width=1 µm、nf=2、multi=1、total width=2 µm；
- netlist SHA-256：`cb5cb1a533cd17297a74666d0c50570130ad5ae4238e2864b7c61e4bf1896a72`；
- wrapper SHA-256：`3923b0e5e832acce73ef77ad14862023f23a3aef1eb1f2609305791d61d0f134`。

因此性能差异对应真实不同的 `si` 网表，而不是只改了 OA 表面字符串。最终 `parameters.apply.best` 和独立 `schematic.inspect.after` 都得到：

```text
device_width_um=1.0
length_um=0.03
load_resistance_ohm=10000
MN0.fingers="2"
confirmation_method=independent_targeted_cdf_equality
```

## Transport 中断与恢复

首次运行在 candidate 1 已完成 RD=10 kΩ/fingers=1 的写入和定向回读后，`si -batch` 遇到 `WinError 10054`。失败 scratch 保留在：

`/data/xum/virtuoso_bridge_smoke/vda_common-source-raw-fingers-ac-tune_5a7f9a43f2fb`

该事件标为 `system_event`，没有生成 candidate，也没有解释成规格不可行。VDA 随即恢复搜索前 `RD=20 kΩ/fingers=1`；checkpoint 为 `complete=false`、`next_candidate_index=1`、pending 两组状态均空。续跑先独立 OA inspect，确认与 checkpoint baseline/expected 匹配，再重试 candidate 1，完成两个点、最佳写回和最终回读。最终 checkpoint 为 `complete=true`、`next_candidate_index=3`，同时保留首次失败 action/note。

记录：

- 首次失败：`artifacts/runs/common-source-raw-fingers-ac-tune/live-20260723.json`；
- checkpoint：`artifacts/runs/common-source-raw-fingers-ac-tune/live-20260723.checkpoint.json`；
- 完成记录：`artifacts/runs/common-source-raw-fingers-ac-tune/live-resume1-20260723.json`。

## 证据来源

- `user_input`：目标、raw 字段和值、固定参数、sweep、constraints、objective 与授权；
- `bridge_readback`：OA 完整结构、callback 后 requested/applied/confirmed 字段、semantic 参数和最终独立 targeted readback；
- `eda_result`：`si` 网表、Spectre DC/AC、原始复数波形与连续指标；
- `software_inference`：OA/netlist 一致性、饱和分类、交点/GBW、constraint 与候选排序；
- `system_event`：首次 `si -batch` transport reset。

## 尚未闭合与下一道 Gate

- 未证明 `MN0` 的全部 233 个 CDF 字段可持久化、相互独立或有合理物理搜索边界；已知 `m=2` 会被当前 PDK callback 恢复成 `1`。
- raw search 不猜 Bridge alias；想搜索 `fingers` 必须写实际字段名。一次独立 `parameters.apply` 仍可使用 Bridge 支持的 alias。
- callback 可能联动未声明字段；完整 inspect 会保留变化，但 VDA 只对显式字段和已知 semantic 参数作成功声明。
- 本 Gate 只跑 nominal TT 单条件 AC，不是 quality bundle、PVT-aware raw 写回或 full signoff。
- 失败恢复是逐字段精确重放，不是跨任意多实例 callback 的 OA 事务或 snapshot rollback。

下一默认产品 Gate 转向 Gate 3 差分对：先闭合固定模板的结构创建/回读和 nominal DC 支路平衡、尾电流与工作区，再加入差模 AC、CMRR 和有限参数搜索。PVT 继续保持可选加严项，不默认附加到首轮拓扑验证。
