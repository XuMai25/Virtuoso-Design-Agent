# 2026-07-20 源极退化原位变更实现验证

> 状态更新：本文保留真实写入前的实现与只读预检记录。随后完成的写入事故、恢复、成功 transform、OA→`si`→Spectre DC、6 点 RS 调优、checkpoint 续跑和独立最终回读，见 [2026-07-20-source-degeneration-live.md](2026-07-20-source-degeneration-live.md)。共源 AC 的正式实现见 [2026-07-20-common-source-ac-implementation.md](2026-07-20-common-source-ac-implementation.md)，后续 nominal/退化只读真实 AC 见 [2026-07-20-common-source-ac-live.md](2026-07-20-common-source-ac-live.md)。下文“只覆盖本地/尚未执行”的表述是 smoke 前的历史状态，不代表当前结论。

## 目标

验证 VDA 能把源极退化当成对已有 VDA 共源 schematic 的小改动，而不是新建相似模板或复制一套后续流程。目标 cell、view、MN0、RD0 和顶层 pins 均保留；唯一拓扑 delta 是：

```text
MN0.S: VSS -> NSRC
新增 RS0: NSRC -> VSS
```

本记录当前只覆盖本地实现与离线测试，不包含新的远端 OA 写入或真实 Spectre 通过结论。

## 实现契约

- 新增正交 operation：`schematic.transform`。当前仅 `common_source` 支持，并且任务参数必须恰好是 `source_resistance_ohm`。
- transform 针对同一 `library/cell/view`，不调用 `schematic.create`，不删除或替换 cellview，`replace_existing` 保持 `false`。
- 变更前要求目标精确匹配 VDA nominal common-source，或已经精确匹配源极退化变体；未知实例/连线拒绝执行。
- nominal 变体只把 MN0.S 附近唯一的 VSS wire label 改为 NSRC，并通过 Bridge 公共 schematic editor 新增 `analogLib/RS0` 及两个 terminal label。
- 变更后重新结构化回读。MN0/RD0 的完整 CDF 参数、master、位置、顶层 pins 必须与变更前一致；nets 只允许增加 NSRC；RS0 必须严格连接 NSRC/VSS 且阻值匹配请求。
- 重复相同 transform 幂等，不会增加第二个 RS0；在已退化拓扑上给出新阻值时只更新 RS0.r。
- 未修改 `virtuoso-bridge-lite`。VDA 只组合其公开 editor、instance/terminal-label builder 和 `set_instance_params`，VDA 特有的严格 label 选择留在本仓库。

## 后续流程复用

没有新增第二个 executor 或 source-degenerated worker 管线。`common_source` 的 inspect、apply、netlist、DC 和 tune 动态识别两个 topology variant：

- OA 语义参数在 RS0 存在时增加 `source_resistance_ohm`。
- `si` parser 对退化变体要求 `MN0(OUT IN NSRC VSS)`、`RD0(VDD OUT)`、`RS0(NSRC VSS)`，并把三者参数与 OA 回读核对。
- DC wrapper 对退化变体额外保存 NSRC；VGS/VDS 以 NSRC 为源极参考，同时检查 MN0/RD0 和 MN0/RS0 两组 KCL。
- `source_resistance_ohm` 可直接进入既有有限 `parameter_space`、checkpoint、规格判定和最佳点写回。
- 共源 semantic 参数应用改为只发送任务实际点名的 W、L、RD、RS；不再隐式写 `fingers=1` 或 `m=1`，避免微调时重置人工参数。

## 本地证据

新增测试覆盖：

- operation/catalog/planner 的最小参数与远端写入披露。
- 同一 cell 原位 delta、MN0/RD0/pins/参数保留和 executor 二次审计。
- 重复调用幂等、只改 RS0 阻值、伪造既有实例变化时整次 run 失败。
- nominal 与退化 `si` 网表解析、NSRC 电压、两组 KCL、空 NSRC 结果失败。
- demo 中 RS 有限搜索复用既有 DC tuning 路径，证据保持 `software_inference`。

完整测试：`87 passed`；24 个 example task 均可生成计划，新 transform 的 CLI 计划明确显示一次 `remote_write`、两次只读 inspect，且不包含远端计算。另有独立的退化 DC 单点和 6 点 `Vbias × RS` 调优任务，证明无需把 transform 强迫绑定到完整闭环。在真实 smoke 前，以上只能证明软件契约和编排行为。

## 真实只读预检

目标：`vb_pdk_smoke/vda_param_surface_001/schematic`。本轮没有 OA 写入，也没有运行远端仿真。

- VDA 只读 run：`artifacts/runs/parameter-surface-inspect-bridge/run-20260719T195311Z.json`
- 状态/证据：`succeeded` / `bridge_readback`
- 结构：MN0、RD0；IN/OUT/VDD/VSS 四个 nets 和四个 pins
- 参数基线：`MN0.Wfg=1u`、`MN0.l=30n`、`MN0.fingers=2`、`MN0.m=1`、`RD0.r=22K`
- 随后用 transform 将执行的同一几何选择表达式做只读 SKILL preflight，返回 `topology_variant=common_source`、`source_label_selection=unique`；语义参数回读为 `W=1.0 µm`、`L=0.03 µm`、`RD=22 kΩ`

因此 label 选择语法和当前目标上的唯一性已有真实 `bridge_readback`，但赋值、新增 RS0、保存、再次回读和仿真仍未执行。

## 未验证边界

- MN0.S label 定位已在真实目标上只读返回唯一，但 rename/dbSave 尚未执行；目标几何变化后仍会重新 preflight，不唯一时失败而不猜测。
- 尚未证明真实 OA 回读能得到 MN0.S=NSRC、RS0/NSRC，或 `si` 能输出预期退化网表。
- 尚未运行退化电路 Spectre DC，因此 NSRC、器件 OP、RD0/RS0 KCL 和可行偏置都没有 `eda_result`。
- 当前没有自动逆变换；不得在需要保留 nominal 状态的唯一 cellview 上把本操作当成可自动回滚的事务。
- AC gain、bandwidth、GBW、noise 和 corner 未实现或验证。

## 下一道 Gate

在明确授权的专用已有 cellview 上执行一次：

```text
transform -> 独立结构/参数回读 -> OA si netlist -> Spectre DC OP
```

只有 MN0/RD0/pins 保留、RS0/NSRC 正确、OA/网表参数一致、NSRC 非空、两组 KCL 与工作区规格同时成立，才能称为 **source-degenerated common-source DC same-source smoke verified**。随后才进入 nominal/退化 AC gain-bandwidth 对比。
