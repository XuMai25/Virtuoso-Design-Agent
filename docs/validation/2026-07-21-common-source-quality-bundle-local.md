# 2026-07-21 共源多 analysis 质量组合本地验证

状态：**quality-bundle orchestration locally verified; live multi-candidate Spectre tuning pending**。

本轮把已经分别通过 live smoke 的共源 AC、相干 transient 线性度和 ordinary noise 组合成正式任务能力。没有修改 `virtuoso-bridge-lite`，也没有新增 executor、拓扑模板、手写 DUT deck 或 UI/framework。

## 契约与执行语义

- 新增 `analysis: "quality"`，只对 `common_source` 的 simulation/tuning operation 开放。
- 任务必须同时声明 `ac_sweep`、`linearity_sweep` 和 `noise_sweep`；三组完整字段进入 plan token。
- `simulation.run` 和 `design.tune` 继续调用原有 `adapter.simulate` 与候选选择器；constraints/objective 可以同时引用 gain/BW/GBW、P1dB/THD/power、noise 和 DC 工作区指标。
- worker 对每个候选只读一次 OA、生成并核对一次 `si` 网表，然后让 AC、transient 和 noise wrapper 都引用该远端结构网表。`timeout_seconds` 仍是每个子分析的上限，subprocess 总等待上限相应扩展为三倍加固定 worker 余量。
- 三个结果合并前必须具有相同实际参数、相同网表证据和一致的重复 DC 指标/证据来源。任一分析不完整会让整个候选 `analysis_complete=false`；参数或共享指标不一致直接失败，不平均、不静默覆盖。

## 证据分类

- OA 结构和参数：`bridge_readback`
- `si` 网表与 Spectre 连续指标：`eda_result`
- demo 的全部指标：`software_inference`
- 固定组合成员、结果合并、重复指标一致性和完整性门：`software_inference`
- 用户显式给出的 `analysis: quality` 和 sweep 字段：`user_input`；worker 从质量组合派生的单项 analysis 名称：`software_inference`

run record 在顶层保存组合完成状态，并在 `evidence.analyses.{ac,transient,noise}` 下保留各自 testbench、operating point、响应诊断、warnings 和 tool version。成功仍不能由 return code、网表存在或某一个子分析成功单独推出。

## 已通过的本地路径

- 质量契约接受三组 sweep，拒绝任一缺失字段。
- planner 明确披露一次 OA/`si` 核对、三项仿真和“任一缺证据即拒绝”。
- demo `simulation.run` 同时产生 DC、AC、P1dB/THD/power 和积分噪声指标，并让联合约束参与候选可行性。
- 注入空 noise 证据后，整个质量候选变为 incomplete/partial，不能由 AC 和线性度成功掩盖。
- 注入实际参数不一致或重复 DC 指标不一致后，bundle 拒绝合并。
- 纯 `bias_v × load_ff` 的 `design.tune` 不产生 parameter stage/finalize/restore OA action；2/4 候选预算任务只评价前缀并标为 partial。
- worker 假件记录到一次 `_generate_oa_netlist` 和三次 Spectre 调用，三个子分析的 netlist evidence 完全相同。
- 离线示例 `common-source-quality-tune.demo.json` 完成 4/4 候选，选择 `bias_v=0.32 V`、`load_ff=1 fF`、`dc_supply_power_uw=6.3`；这些数值只属于 `software_inference`。

最终回归：

```text
141 passed in 0.43s
41/41 example task plans passed
vda catalog passed
git diff --check passed
```

## 未验证边界与下一道 Gate

- 本轮没有执行远端计算或 OA 写入，不能把 worker 假件和 demo 当成真实电路性能证据。
- 尚未证明三个真实 Spectre 子分析在一个候选中共享同一网表并全部完成，也未验证远端 bundle 的实际耗时、scratch 形状和 run-level tool version。
- 尚未跑质量约束下的可行/不可行真实候选、预算耗尽或 transport checkpoint/resume。
- 当前 bundle 每候选运行三个独立 Spectre wrapper；只复用 OA/`si` 网表，不复用一个 Spectre process 或 raw result。若 live profiling 证明启动成本主导，再基于证据优化，不能先把多 analysis 塞进未验证的单 deck。
- 尚未做 W/L/RD/RS/VDD 的质量驱动写回、有限 corner、多频点线性度、输入电容、真实面积、Monte Carlo 或 post-layout。

下一道 Gate 使用 `examples/tasks/common-source-quality-bias-load-tune.bridge.json` 在既有 `vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic` 上做 4 点只读 `bias×load` 质量搜索。执行前仍需单独确认目标、远端计算和 scratch；任务声明 `allow_remote_write=false`、`replace_existing=false`，不得写 OA。先通过可行组合，再派生不可行、预算耗尽和 transport resume；之后才考虑少量设计参数写回与 corner。
