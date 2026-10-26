# 2026-07-23 差分对 PMOS 电流镜负载 Gate 6 本地实现

## 结论

状态：**Gate 6 current-mirror load locally implemented; live same-source OA/Spectre evidence pending**。

本记录只证明 VDA 的任务契约、exact-delta 变换、参数面、OA/`si` 边界解析、DC 证据计算和动态输出定义已经实现并通过本地测试。没有执行远端 OA 写入、`si` 或 Spectre，因此没有新的 `eda_result`，也不宣称电流镜负载差分对已经设计闭合。

后续状态：同日已完成 nominal TSMC N28 live Gate，见 [`2026-07-23-differential-pair-current-mirror-gate6-live.md`](2026-07-23-differential-pair-current-mirror-gate6-live.md)。本文件保留为 live 前的本地契约与测试快照。

## 固定拓扑与可逆变换

前向 action：`replace_resistive_load_with_current_mirror`。

前置拓扑必须是未加源极退化的 Gate 4 真实尾管差分对：

```text
MN0 (OUTP INP TAIL VSS)
MN1 (OUTN INN TAIL VSS)
MNTAIL (TAIL BIAS VSS VSS)
RD0 (VDD OUTP)
RD1 (VDD OUTN)
```

允许的唯一拓扑变化：

```text
- RD0 (VDD OUTP)
- RD1 (VDD OUTN)
+ MP0 (OUTP OUTP VDD VDD)  # diode-connected reference
+ MP1 (OUTN OUTP VDD VDD)  # mirrored single-ended output branch
```

`MN0/MN1/MNTAIL`、顶层 `BIAS/INP/INN/OUTP/OUTN/TAIL/VDD/VSS`、全部既有 nets 和未点名参数必须保持。前向变换只删除 RD0/RD1 的 VDA 自有 terminal wire/label stubs；反向 `restore_resistive_load` 只删除 MP0/MP1 自有 stubs，并在原坐标按显式 `load_resistance_ohm` 恢复 RD0/RD1。反向任务可声明 `expected_restored_placement_sha256`。

当前不允许同时存在 `RS0/RS1` 和 `MP0/MP1`。这不是 Bridge 能力缩减：通用 `existing_schematic` 显式实例字段仍可透传；限制只属于 Gate 6 固定差分对模板的真实性证据边界。

## 参数与同源边界

- 新 OA semantic 参数：`pmos_load_width_um`、`pmos_load_length_um`。
- `parameters.apply` 和 `design.tune` 会把任一声明字段对称写入 MP0/MP1，并逐只回读。
- active-load topology 拒绝 `load_resistance_ohm`；resistive-load topology 拒绝 PMOS load semantic 参数。
- OA inspect 要求 MP0/MP1 同 master、精确节点和匹配 W/L；`si` parser 独立要求相同条件，并解析 finger width、nf、multiplicity、total width 与 L。
- OA/`si` 的 semantic 参数、输入管 geometry、尾管 geometry 和电流镜 geometry 必须逐项一致；不以 netlisting return code 或文件存在代替一致性。

## DC 证据定义

Spectre wrapper 为 MP0/MP1 保存 `ids/vgs/vds/vdsat/gm/gds`。worker 必须验证：

1. MP0 的 `VGS/VDS` 分别匹配 `OUTP-VDD`、`OUTP-VDD`；MP1 匹配 `OUTP-VDD`、`OUTN-VDD`。
2. `|MP0.ids|` 与 `|MN0.ids|`、`|MP1.ids|` 与 `|MN1.ids|` 分别匹配。
3. 两只 PMOS 的镜像电流误差单独报告；VDD 源电流还要与两支 NMOS 电流和匹配。
4. PMOS 饱和余量使用 `VSD-|VDSAT|`；输入 NMOS、负载 PMOS 与尾管的区域分别保留。
5. `minimum_output_swing_margin_v` 取输入 NMOS 下侧饱和余量和 PMOS 上侧饱和余量的最小值。
6. 任一负载 KCL residual 超过 1% 直接判为证据失败；器件不在饱和区则保留完整结果和 warning，由 constraint 判不可行。

新增连续或分类指标包括：

- `current_mirror_current_mismatch_percent`
- `load_p/load_n_current_mismatch_percent`
- `load_p/load_n_saturation_margin_v`
- `minimum_load_saturation_margin_v`
- `both_load_saturation_region`
- `all_signal_devices_saturation_region`
- PMOS gm/gds 与最小 intrinsic gain

## 动态分析定义

电流镜负载的目标输出是 OUTN，OUTP 是镜像参考节点。因此：

- 差模 AC：`OUTN/(INP-INN)`；
- 共模 AC：`OUTN/((INP+INN)/2)`；
- CMRR：上述两条复数传输函数之比；
- transient：输入仍是 `VINP-VINN`，输出基波/THD/P1dB 从 OUTN 提取；
- noise：`noise (OUTN 0)`，输入参考仍是唯一 `VIN_DIFF`；
- `load_ff`：只放在 OUTN，不再在两端各放一只；
- run evidence：保存 `output_mode=single_ended_outn`，避免把 `differential_*` 指标名误读为差分输出。

电阻负载 Gate 3–5 默认仍使用 `OUTP-OUTN`，没有被此变更覆盖。

## 本地测试与计划验证

本地结果：

```text
446 passed in 1.20s
8/8 differential-pair-current-mirror task plans passed
catalog passed
python compileall passed
git diff --check passed
```

新增测试覆盖：

- action 参数必须精确匹配，恢复 placement hash 只能用于 restoring action；
- planner 明示 RD↔PM 的固定 delta 和远端写副作用；
- OA 与 `si` 接受精确 PMOS 镜像，拒绝单边、混合负载、非对称 W/L 和错误节点/model；
- PMOS W/L 同时更新两只负载；
- DC 节点/器件 OP、镜像、支路 KCL、VDD KCL、PMOS 区域与联合摆幅；
- 人为注入 MP1 电流误差会因负载 KCL 超过 1% 失败；
- AC 单端 OUTN 传输、noise 输出、单端负载电容和 PMOS OP save；
- demo create→tail→current mirror→PMOS width apply→AC/CMRR→restore 端到端流程，全部证据仍标 `software_inference`。

对应任务：

- `differential-pair-current-mirror-create.bridge.json`
- `differential-pair-current-mirror-tail.bridge.json`
- `differential-pair-current-mirror-transform.bridge.json`
- `differential-pair-current-mirror-dc.bridge.json`
- `differential-pair-current-mirror-ac.bridge.json`
- `differential-pair-current-mirror-linearity.bridge.json`
- `differential-pair-current-mirror-noise.bridge.json`
- `differential-pair-current-mirror-restore.bridge.json`

## 第三方边界

本实现只修改 VDA 仓库，复用 Bridge 已公开的 schematic edit、CDF 参数写入、structured readback、`si`、Spectre 和文件传输接口。没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`，因此不需要第三方补丁备份或私有分支迁移。

## Live Gate 前置确认

拟执行目标：

- library/cell/view：`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`
- OA 写入：是；先非覆盖 create、tail transform、current-mirror transform，最终可执行显式 restore
- 远端计算：是；先 DC，证据通过后逐项 AC/CMRR、ICMR、transient、noise
- 远端产物：`/data/xum/virtuoso_bridge_smoke/` 下按 task/run 隔离的 scratch
- 覆盖已有对象：否，`replace_existing=false`

该精确范围仍需用户在 live 执行前确认；本地实现本身不是远端授权。

## 未验证边界

- 尚无真实 OA placement、PMOS CDF 回读或恢复 SHA。
- 尚无真实 `si` PMOS instance/model/geometry 和 OA 一致性。
- 尚无真实 DC 偏置点，示例 `BIAS=0.30 V/VCM=0.55 V` 只是初始探针，不保证可行。
- 尚无真实 AC/CMRR/ICMR/transient/noise 或 PMOS W/L/BIAS/输入管 W 有限搜索。
- 未验证 active load 与 source degeneration 组合、PVT、mismatch/Monte Carlo、PSRR、slew/settling 或 ADE/Maestro 人工交接。
- 反向 placement 精确恢复只有本地契约，必须由 live OA readback 证明。

只有 create/transform/readback、自动 `si`、DC KCL/区域通过后，才能把 Gate 6 称为同源执行链；动态分析和有限选优全部完成前仍不能称为设计质量闭合。
