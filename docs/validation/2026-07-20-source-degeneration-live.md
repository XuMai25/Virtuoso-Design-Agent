# 2026-07-20 源极退化原位微调与同源 DC 真实验证

## 结论

状态：**source-degenerated common-source DC tuning and bounded AC design-parameter tuning verified; design-quality closure pending**。

这次验证的重点不是人工跑通一个电路，而是确认 VDA 已具备可重复调用的原位微调能力：任务通过 `schematic.transform` 在同一个已有 cellview 上应用固定、可审计的最小拓扑 delta；之后继续复用既有 inspect、parameter apply、OA→`si`、Spectre DC、有限搜索、checkpoint/resume 和最终回读能力。没有新增一次性远端脚本，也没有复制 Bridge。

目标与环境：

- OA：`vb_pdk_smoke/vda_param_surface_001/schematic`
- PDK profile：`nics4304_tsmc28`
- Bridge：`virtuoso-bridge 0.7.0`
- 远端 scratch：`/data/xum/virtuoso_bridge_smoke/vda_common-source-source-degeneration-*`
- 未修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`；本轮修复全部位于 VDA。

## 产品能力契约

当前 transform 只接受 `common_source + source_resistance_ohm`，对同一 `library/cell/view` 执行：

```text
MN0.S: VSS -> NSRC
新增 RS0: NSRC -> VSS
```

正式能力包含以下保护：

- planner 明确披露一次 OA 写入、前后两次只读 inspect，并声明不新建或替换目标 cellview。
- 目标必须精确匹配 VDA nominal common-source 或已退化变体；未知实例、连线或非唯一 MN0.S 标签会拒绝执行。
- 对已有 schematic 强制使用 Bridge editor `mode="a"`，不继承其面向创建的 `mode="w"` 默认值。
- preflight 拒绝已有未保存改动的目标；编辑 batch 失败时只 purge 本次未保存的目标缓存，清理路径不调用 `dbSave`。
- transform 后 executor 再做独立差分审计：MN0/RD0 的 master、位置、方向、完整 CDF 参数和顶层 pins 必须保持；nets 只允许增加 NSRC；RS0 必须严格连接 NSRC/VSS 且阻值匹配。
- 重复相同 transform 幂等；已经退化时改变阻值只写 RS0，不增加第二个器件。
- 共源 semantic 写入只发送任务实际点名的 W/L/RD/RS，不再隐式重置 `fingers` 或 `m`。

源极退化没有第二套执行器。相同 common-source worker 动态识别 nominal 与 degenerated variant：

- OA 回读在 RS0 存在时增加 `source_resistance_ohm`。
- `si` parser 要求 `MN0(OUT IN NSRC VSS)`、`RD0(VDD OUT)`、`RS0(NSRC VSS)`。
- DC wrapper 额外保存 NSRC；VGS/VDS 以 NSRC 为源极参考，并独立核对 RD0 与 RS0 两条 KCL。
- `source_resistance_ohm` 直接进入既有 `parameter_space`、逐候选 OA 暂存、checkpoint、规格判定和最佳点写回。

多指器件的宽度语义也在本次真实网表上补齐：

```text
finger_width_um = OA.Wfg = si.w / si.nf
fingers         = OA.fingers = si.nf
multiplicity    = OA.m = si.multi
total_width_um  = finger_width_um * fingers * multiplicity
```

因此 `Wfg=1u, fingers=2, m=1` 与 `si: w=2u, nf=2, multi=1` 被证明为一致，而不是把 1 µm 与 2 µm 误报为同一字段。

## 首次写入事故与恢复

首次真实 transform 记录：

- `artifacts/runs/common-source-add-source-degeneration/run-20260719T201212Z.json`
- 失败：Bridge schematic editor 未显式指定 mode，继承默认 `mode="w"`；随后 SKILL batch 失败，目标被落盘为空。
- 独立回读 `run-20260719T201242Z.json` 与关闭 clean cache 后的 `run-20260719T202140Z.json` 均确认实例、网络和 pins 为空。
- 只读检查目标 view/cell 目录只发现当前 `sch.oa`、元数据与本会话锁，没有显式 OA 备份或其他 view。

没有盲目重试。事故前独立回读 `run-20260719T195311Z.json` 已保存完整基线：MN0/RD0、四个 pins、位置、连线、`Wfg=1u`、`l=30n`、`fingers=2`、`m=1`、`RD0=22K`。恢复使用正式 VDA 能力并分别留证：

- 基线模板恢复：`artifacts/runs/common-source-parameter-surface-baseline-recovery/run-20260719T202535Z.json`
- `fingers=2` 与 `RD0=22K` 定向写入/回读：`artifacts/runs/parameter-surface-apply-editable-bridge/run-20260719T202611Z.json`

恢复后才重新执行 transform。成功记录：

- `artifacts/runs/common-source-add-source-degeneration/run-20260719T202839Z.json`
- delta：仅 `MN0.S VSS→NSRC`、新增 RS0/NSRC；MN0 的 W/L/fingers/m、RD0、原四 pins、位置和方向保持。

该事故是 VDA 现有对象编辑模式选择错误，不是 Bridge 本身需要修改。修复已通过 append-mode、未保存改动拒绝和失败 purge 的回归测试。

## OA→si→Spectre 单点

首次单点在仿真前被一致性门拦截：

- `artifacts/runs/common-source-source-degeneration-dc-op/run-20260719T202913Z.json`
- 原因：旧 parser 把 OA 单指宽 `Wfg=1u` 与网表总指宽 `w=2u` 直接比较。
- 真实网表器件行为：`MN0 ... l=30n w=2u nf=2 multi=1`、`RD0 r=22K`、`RS0 r=1K`。

修正四量几何语义后，单点记录：

- `artifacts/runs/common-source-source-degeneration-dc-op/run-20260719T203656Z.json`
- OA 与 `si`：拓扑、W/L/fingers/m/总宽、RD、RS 全部一致；netlist SHA-256 已进入 run record。
- `Vbias=0.45 V, VDD=0.9 V, RS=1 kΩ`
- `Id=37.894 µA`、`VGS=0.4121 V`、`VDS=0.02842 V`、`VDSAT=0.12755 V`
- `saturation_margin=-99.13 mV`，因此正确判为 non-saturation / `partial`。
- RD0 与 RS0 电流不一致分别约 `0.00171%` 和 `0.00172%`，节点/器件电压一致。

这条记录证明执行链和证据闭合，不证明该偏置点满足设计规格。

## 6 点 Vbias × RS 调优与恢复

搜索空间：`Vbias={0.35,0.45} V × RS={500,1000,2000} Ω`；约束包含 `Id≥5 µA`、饱和余量 `≥50 mV`、输出摆幅余量 `≥100 mV` 和两条 KCL mismatch `≤1%`；objective 为最大输出摆幅余量。

首次执行 `run-20260719T203849Z.json` 在完成候选 1、2 后遇到 `WinError 10054`。VDA 没有把 transport 中断当成不可行点，保存了 checkpoint，并执行 `parameters.restore.interrupted`。恢复时又暴露一个本地契约缺口：candidate record 包含 OA 回读补入的 W/L/RD，而旧 validator 只接受任务文件显式字段。修复后任务字段必须匹配；额外字段只能来自 checkpoint 的 canonical OA 基线且值必须匹配，未知或被篡改字段仍拒绝。

随后使用同一 checkpoint 从候选 3 续跑，未重复前两点。最终记录：

- `artifacts/runs/common-source-source-degeneration-dc-tune/run-20260719T204523Z.json`
- checkpoint：`run-20260719T203849Z.checkpoint.json`，最终 `next_candidate_index=7`、`complete=true`、`pending_oa_parameters=null`

| # | Vbias (V) | RS (Ω) | Id (µA) | saturation/swing margin (mV) | 可行 |
| ---: | ---: | ---: | ---: | ---: | :---: |
| 1 | 0.35 | 500 | 35.09 | 17.02 | 否 |
| 2 | 0.35 | 1000 | 31.95 | 76.29 | 否 |
| 3 | 0.35 | 2000 | 26.57 | 178.95 | 是 |
| 4 | 0.45 | 500 | 38.91 | -113.22 | 否 |
| 5 | 0.45 | 1000 | 37.89 | -99.13 | 否 |
| 6 | 0.45 | 2000 | 35.78 | -69.20 | 否 |

最佳点为候选 3：`Vbias=0.35 V, RS=2 kΩ`。`parameters.apply.best` 成功，run 内 after-readback 确认 RS0=2 kΩ。全新 worker 的独立最终回读：

- `artifacts/runs/common-source-source-degeneration-inspect/run-20260719T205157Z.json`
- MN0/RD0/RS0，nets=`IN/NSRC/OUT/VDD/VSS`，原四 pins 保持
- `Wfg=1u, fingers=2, m=1, RD0=22K, RS0=2K`
- topology variant=`source_degenerated_common_source`

## 证据分类

- `user_input`：RS、Vbias、VDD、约束、搜索空间与真实执行授权。
- `bridge_readback`：变换前后结构、参数写入确认、最佳 RS 写回和独立最终 OA 回读。
- `eda_result`：`si` 网表、Spectre DC 节点/器件标量及由这些标量直接计算的数值指标。
- `software_inference`：工作区分类、规格通过/失败、候选排序与 objective 选择。

没有用 return code 0、OA 对象存在或命令成功单独宣称 closure。

## 本地回归

新增覆盖：existing-cell append mode、未保存目标拒绝、失败 purge 不保存、严格拓扑 delta、多指 OA/`si` 四量一致性、失败 netlist 路径留证、带 canonical OA 补全字段的 checkpoint 恢复，以及反相器 worker 必须返回结构化仿真证据。最终本地结果为 `94 passed`；`vda catalog` 成功，25 个 example task 全部可生成计划且 0 失败。

## 仍未闭合

- 这是一种正式但受控的 transform 能力，不是任意图重写 DSL；当前只支持已知 common-source→source-degenerated-common-source delta。
- 当前没有自动逆变换。编辑 batch 在保存前失败可 purge 未保存内容；若 `dbSave` 已成功而后置差分审计失败，尚无通用 OA snapshot 自动回滚，run 会失败并保留真实状态。
- transport 运行中仍可能断开；本次 checkpoint/resume 证明能恢复这次边界，不证明底层网络永不失败。
- 该真实 cell 随后已取得 271 点复数 PSF，并解析 `gain=3.238 V/V`、`bandwidth=4.175 GHz`、`GBW=13.519 GHz` 和 `unity=12.948 GHz`；证据见 [2026-07-20-common-source-ac-live.md](2026-07-20-common-source-ac-live.md)。专用 cell 的 W/RD/RS AC design tuning 也已通过，见 [2026-07-20-common-source-ac-design-tuning-live.md](2026-07-20-common-source-ac-design-tuning-live.md)；noise、corner、输入电容和版图面积仍未验证。
- 这里只能称为 L5A 受控执行与有限调优通过，不能称为完整放大器设计闭环。

## 下一道 Gate

nominal 与 source-degenerated 两个已验证拓扑的同源 AC、bias/load 条件搜索和专用 cell 的 W/RD/RS 写入调优均已通过。下一步定义并测量线性度/失真、noise 和功耗，再加入有限 corner，并补 L/VDD 或经预算约束的联合搜索。完成前，Gate 2 仍不能升级为可重复的 L5B 单模块规格闭环。
