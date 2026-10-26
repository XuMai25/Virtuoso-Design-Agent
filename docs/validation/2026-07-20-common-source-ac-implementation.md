# 2026-07-20 共源复数 AC 指标与调优能力实现

状态：**bounded common-source AC design-parameter tuning and recovery verified; design-quality closure pending**。

本记录首先证明 VDA 代码、任务契约和离线回归已经具备共源 AC 能力。随后同日完成的两次只读远端 smoke 验证了 nominal 与源极退化 cell 的真实复数 PSF；专用 cell 上又完成控制变量变换、W/RD/RS 写入调优、预算耗尽、不可行恢复和 transport checkpoint/resume，并继续通过线性度/真实功耗与 ordinary noise 只读 smoke。只读 AC 证据见 `2026-07-20-common-source-ac-live.md`，写入调优见 `2026-07-20-common-source-ac-design-tuning-live.md`，质量分析见 `2026-07-20-common-source-quality-live.md`。这些结果仍不能称为 Gate 2 完整设计质量闭环。

## 实现范围

- `TaskSpec` 新增显式 `analysis` 与结构化 `ac_sweep`。共源省略 analysis 时仍为 `dc`；AC 必须声明 start/stop，点密度、参考点数和参考窗平坦度进入 plan token。
- nominal 与 source-degenerated common-source 继续共用一个 Bridge action、一个 OA→`si` parser 和一个 executor。没有新建第二套 topology、手写 DUT deck 或 Maestro view。
- AC wrapper 保留 DC OP 和器件标量保存，VIN 使用 DC bias + unit AC source；`load_ff` 若声明则只作为外部 testbench capacitor，不写 OA。
- Bridge 现有 PSFASCII parser 已支持 `freq` 和 complex signals。VDA 只消费其 `ac_freq/ac_IN/ac_OUT` 结果，没有修改 `virtuoso-bridge-lite`。

## 指标定义

先计算复数传递函数：

```text
H(f) = VOUT(f) / VIN(f)
```

- `low_frequency_gain_v_per_v`：前 `reference_points` 个复数 H 的均值幅度。
- `low_frequency_gain_db` / `low_frequency_phase_deg`：同一低频复数参考的幅相。
- `bandwidth_3db_hz`：相对低频参考下降 `10 log10(2)` dB 的首个向下交点；在 dB 与 `log10(f)` 上插值。
- `gain_bandwidth_product_hz`：`low_frequency_gain_v_per_v × bandwidth_3db_hz`。
- `unity_gain_frequency_hz`：首个向下 0 dB 交点，单独报告，不把它包装成 GBW。
- 同时保留 peak gain、peaking、带宽/单位增益处相位、交点 bracket 和插值方法。

从真实复数波形计算出的连续指标标为 `eda_result`；交点定义、DC 饱和分类和 analysis 完整性是显式的软件规则。任务声明的 analysis/sweep/bias/load 标为 `user_input`，省略的默认字段标为 `software_inference`。

## 失败与非单调路径

- frequency/VIN/VOUT 为空、长度不一致、非有限、频率非严格递增或 VIN AC 为零：指标提取失败，不能产生候选 EDA 证据。
- 低频参考窗变化超过阈值：保留可得 gain/phase，但 bandwidth/GBW 不解析，candidate 标为 incomplete。
- 扫频终点前没有 −3 dB 交点：不使用 stop frequency 冒充 bandwidth；run 至少为 `partial`。
- 多个向下 −3 dB 交点：采用首个交点，同时记录多交点和再次穿越警告。
- unity-gain 不在扫描范围：独立记录 warning；若任务把该指标列为 constraint/objective，缺失仍令候选不可行。
- DC 工作点不是 saturation：AC 波形可以保留作诊断，但不标为 design-metric complete。

## 调优行为

- `simulation.run` 可只读运行 DC + AC 并逐条判规格。
- `design.tune` / `design.close_loop` 的 W/L/RD/RS 维度沿用逐候选 OA 暂存、回读、重新 netlist、checkpoint 和最佳点提交。
- 若 `design.tune` 只有 bias/VDD/load 等 testbench 维度，planner 不再声明 OA write，executor 不调用 `parameters.stage` 或 best write；最终独立 inspect 必须证明 OA 参数未变。
- AC metrics 可直接用于 constraints 与 objective；本地测试已覆盖以 GBW 最大化选择并写回最佳 W，以及纯 bias/load 搜索不写 OA。

## 本地验证

环境：Windows，Python 3.13.14，pytest 9.1.1。

```text
115 passed
catalog: passed
all example task plans: 37 passed
git diff --check: passed
virtuoso-bridge-lite parser tests: 10 passed in 0.18s
```

覆盖包括：模型校验、plan 副作用、复数一阶响应、短扫频、非平坦参考、非单调多交点、Bridge worker 结构证据、simulation.run、含 OA 参数的 AC tune、纯 testbench tune 和既有 DC/transform/checkpoint 回归。

新增可审查任务：

- `examples/tasks/common-source-ac-verify.bridge.json`
- `examples/tasks/common-source-source-degeneration-ac-verify.bridge.json`
- `examples/tasks/common-source-ac-tune.demo.json`

## 后续真实验证

同日两次远端任务均成功，且没有 OA 写动作：

- nominal：271 点复数 AC，`gain=4.022 V/V`、`bandwidth=5.632 GHz`、`GBW=22.653 GHz`、`unity=21.970 GHz`；
- source-degenerated：271 点复数 AC，`gain=3.238 V/V`、`bandwidth=4.175 GHz`、`GBW=13.519 GHz`、`unity=12.948 GHz`。

两次均通过 DC saturation、OA/`si` topology/参数一致性、低频参考平坦度和单一 −3 dB/0 dB 交点检查，`analysis_complete=true`。真实路径、哈希、交点 bracket、Spectre warnings 和 run record 见 [共源与源极退化只读同源 AC 真实验证](2026-07-20-common-source-ac-live.md)。

随后在专用 `vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic` 上，以完全相同的 W/L/RD/bias/load 保存 nominal 基线，再只加入 `RS=2 kΩ`，得到 gain −36.98%、bandwidth −20.12%、GBW −49.66% 的控制变量结果。W/RD/RS 完整 8 点搜索按 GBW 选择并回读 `W=1.0 µm, RD=20 kΩ, RS=1 kΩ`；3/8 预算任务和 2 点人为不可行任务分别验证了前缀选择与初始 OA 恢复。完整证据见 [共源 AC 控制变量与 W/RD/RS 真实调优](2026-07-20-common-source-ac-design-tuning-live.md)。

## 第三方边界

本轮只读参考：

- `virtuoso-bridge-lite/examples/02_spectre/02_cap_dc_ac.py`
- `virtuoso-bridge-lite/examples/02_spectre/assets/cap_dc_ac/tb_cap_dc_ac.scs`
- `virtuoso-bridge-lite/src/virtuoso_bridge/spectre/parsers.py`
- `virtuoso-bridge-lite/skills/spectre/references/netlist_syntax.md`

`C:\Users\aknigsesl\tools\virtuoso-bridge-lite` 未被修改。AC 复用它已有的 standalone Spectre runner 和 complex PSF parser，因此没有新增第三方补丁、备份或分支兼容负担。

## 尚未验证

- W/RD/RS 与 bias/load 已分别在有边界网格中通过，但尚未验证包含 L/VDD 的联合搜索或更大组合空间。
- 当前控制变量 A/B 只覆盖这个固定设计点和 `RS=2 kΩ`，不能外推为源极退化在所有偏置、负载或 corner 下的普遍定量结论。
- noise、真实 VDD 功耗和 transient 线性度已经过单点只读 live Gate；尚未共同驱动参数调优，也没有多频点、corner、输入电容、面积和后仿真证据。
- 跨 analysis 的综合目标尚未定义为可验收规格。

## 下一道 Gate

下一步把已经 live 的 DC+AC+linearity+noise 组合为受预算的质量约束/目标，覆盖可行、不可行、预算耗尽和 transport 恢复；随后加入有限 corner，并补 L/VDD 或经预算约束的联合搜索。当前状态仍是 **bounded common-source AC design-parameter tuning plus one-point quality analysis verified**，不是完整 L5B 单模块规格闭环。
