# 2026-07-25 原子候选与真实工作点局部重线性化本地 Gate

## 结论

本 Gate 完成两项可复用能力：

1. `TaskSpec.candidate_set` 可以把 semantic、testbench 和原始 CDF 参数保留在一个完整
   tuple 中执行，不再由 executor 做笛卡尔积展开。
2. `vda op-relinearize` 可以从 hash-bound real-Bridge tuning record 的真实工作点和性能
   指标拟合局部一阶响应，并用未参与拟合的 held-out candidates 决定能否生成下一轮任务。

共源级和有源负载差分对的既有真实记录都通过了预先声明的 training/heldout 门，并分别从
27 个局部组合编译出 6 个候选。状态因此是：

> **real-EDA-record local OP relinearization and atomic candidate compilation verified**

本轮没有连接 Bridge、没有远端计算、没有 OA read/write，也没有修改第三方 Bridge。
生成的 6 点 task 尚未运行 Spectre，所以不能称为新候选已验证、预算收益已验证、最佳设计
已更新或连续/全局最优。

## 原子候选契约

`candidate_set` 只用于 `design.tune`/`design.close_loop`，并满足以下边界：

- 每个 candidate 有稳定 ID、完整 semantic/testbench 参数、可选 raw
  `instance_parameter_updates` 和可选 predicted metrics。
- 一个 set 内所有 tuple 的字段面相同，ID 和参数值组合唯一。
- 固定 semantic/raw 字段不能与候选字段重叠；固定 raw 与候选 raw 在同一实例上按字段
  深合并，例如固定 `MN0.m` 同时候选调整 `MN0.fingers`。
- `candidate_set` 与 `parameter_space`、`instance_parameter_space`、`theory_seed` 互斥；
  executor 按原顺序执行，候选数就是 tuple 数。
- `software_inference` 来源必须至少绑定一个 SHA-256；source、ID、预测值进入 candidate、
  checkpoint 和 `search_audit`。
- predicted metrics 不控制最终规格或排序。正常 adapter 返回的 simulation metrics 才进入
  constraints/objective；demo 仍只能证明编排。

这不会替换或缩窄 Bridge 原有 `parameters.apply`。人工仍可直接传入 Bridge 接受的 raw
CDF 字符串；逐维 semantic/raw space 也继续保留。

## 局部模型与拒绝条件

policy 显式声明 source task/run SHA-256、anchor、互斥 training/heldout index、参数边界、
proposal step/量化网格、工作点与性能指标、逐指标 training/heldout error gate、局部筛选
constraints 和 objective。

模型以真实 anchor 为固定截距，对
`(parameter - anchor) / proposal_step` 做一阶最小二乘。以下情况在生成候选前拒绝：

- source 不是成功的 `virtuoso-bridge-subprocess` run；
- candidate 不完整，或任一建模 metric 的来源不是 `eda_result`；
- task/run hash 或 task ID 漂移；
- training 与 heldout 重叠，anchor 不在 training，或训练点不足；
- 参数扰动不能独立张成所有声明维度；
- 未建模 semantic 输入在训练/留出点变化；
- raw CDF 字符串随点变化但没有先建立 numeric semantic 映射；
- 任一 metric 的 training 或 heldout error 超门；
- 通过验证的模型仍找不到 predicted-feasible proposal。

结果失败时状态为 `partial`，保存逐点 actual/predicted/error 和失败 metric，但
`candidate_set=null`。编译器只接受 passed result，并把 policy、source run、result 和 task
template 全部 hash 绑定。template 可以保留比局部筛选更多的最终 EDA constraints，但不能
删除或修改局部模型实际使用的 constraint，也不能改变固定参数或 objective。

## 真实记录回放

### 共源级 W/RD/RS

来源：

- task：`common-source-quality-design-tune`
- run：`artifacts/runs/common-source-quality-design-tune/live-resume1-20260721.json`
- source SHA-256：
  `45a1c262225ea6b771f0d89dce5cac4c040d2cdee633d4f3c470ad26e15c069f`
- policy：
  `examples/theory/common-source-op-relinearization-policy.json`
- anchor：candidate 5，`W=1.0 µm, RD=20 kΩ, RS=1 kΩ`
- training：`[1,2,3,5,6,7]`
- heldout：`[4,8]`

模型覆盖三个 OP 指标 `Id/gm/gds`，以及 swing、gain、BW、P1dB、输入参考积分噪声、
DC power 和 GBW 七个性能指标。OP 门为 15%，performance 门为 20%。所有建模值在来源
record 中都是 `eda_result`。

| 结果 | 数值 |
| --- | ---: |
| training 最大误差 | 17.823%（output swing） |
| heldout 最大误差 | 11.450%（output swing） |
| 局部组合 | 27 |
| 编译候选 | 6 |

六个 tuple 为：

1. `1.0 µm / 20 kΩ / 1.00 kΩ`（已测 anchor 控制点）
2. `1.1 µm / 19 kΩ / 0.75 kΩ`
3. `1.1 µm / 20 kΩ / 0.75 kΩ`
4. `1.0 µm / 19 kΩ / 0.75 kΩ`
5. `1.1 µm / 21 kΩ / 0.75 kΩ`
6. `1.0 µm / 20 kΩ / 0.75 kΩ`

局部模型没有把 binary saturation、mismatch 和一阶拟合训练误差较大的 THD 用于预筛；
生成的 full quality template 仍保留这三类原 constraints，下一轮必须由真实 EDA 判定。
另用同一真实记录把 `max_thd_percent` 强制加入 20% 模型门做反证：training error 为
`32.181%`，heldout error 为 `18.500%`。结果正确返回 `partial`、
`candidate_set=null`，并记录 `training error gate failed for: max_thd_percent`；没有因为
heldout 单独通过而生成候选。

### PMOS 电流镜负载差分对

来源：

- task：`differential-pair-current-mirror-theory-seed-gate8-bridge`
- run：
  `artifacts/runs/differential-pair-current-mirror-theory-seed-gate8-bridge/run-20260725-grid-resumed.json`
- source SHA-256：
  `7be715fe80c299ae6a250f50582e30d6ae31fe3acfab3bf539e77ab22e6d517f`
- policy：
  `examples/theory/differential-pair-op-relinearization-policy.json`
- anchor：candidate 2，`Wn/Wp/Wtail=1.315/1.180/0.605 µm`
- training：`[1,2,3,4]`
- heldout：`[5,6]`

模型覆盖输入支路、负载和尾管的 8 个 current/gm/gds OP 指标，以及 load mismatch、
output swing、power、gain、BW、GBW 和 peaking 7 个性能指标。所有指标均通过 15% OP、
20% performance 门。

| 结果 | 数值 |
| --- | ---: |
| training 最大误差 | 0%（anchor + 每维一个独立扰动恰好确定一阶模型） |
| heldout 最大误差 | 8.262%（minimum output swing） |
| 局部组合 | 27 |
| 编译候选 | 6 |

training 误差为零不是泛化证据；真正的独立检查是 candidates 5/6 的 heldout。六个待测
tuple 为 anchor 加五个局部宽度组合，均按 5 nm 网格量化。full template 继续保留
`all_signal_devices_saturation_region` 和 `low_frequency_cmrr_db`，它们没有被局部预测替代。

## 命令与本地产物

```powershell
.\.venv\Scripts\vda.exe op-relinearize `
  examples\theory\common-source-op-relinearization-policy.json `
  artifacts\runs\common-source-quality-design-tune\live-resume1-20260721.json `
  --output artifacts\relinearization\common-source-op-relinearization.json
.\.venv\Scripts\vda.exe candidate-task-from-relinearization `
  artifacts\relinearization\common-source-op-relinearization.json `
  examples\theory\common-source-op-relinearization-task-template.json `
  --output artifacts\relinearization\common-source-op-relinearized-task.json
```

差分对使用对应的 `differential-pair-op-relinearization-*.json` policy/template。四个生成产物
位于 ignored `artifacts/relinearization/`，均保留在本机：

| 文件 | SHA-256 |
| --- | --- |
| common-source result | `84b4a7889798179793d3db67f959489f90287532b4b6a3371c3a4e71397d4af8` |
| common-source task | `ac9cfa0aa9f625db51c599dcaec0782892219d8bc80855f6f010404effbea8d4` |
| differential-pair result | `f6728aada557bcae242dc889db9af5da6accff98b411aca214ba3964c95caf39` |
| differential-pair task | `85a4e9e704a56847340fe2f1e7cd3635078fa3fbcd9cd8f49ed9dd64e2e92e96` |

两份生成 task 均已通过 `vda plan`。plan 显式显示 OA candidate staging、自动 `si`、
Spectre sweep、EDA-only final selection、finalize/restore 和 checkpoint；plan token 只属于该
本地产物，不构成本轮远端执行授权。

## 测试与失败注入

新增测试覆盖：

- mixed semantic/testbench/raw tuple 不做乘积，固定/候选 raw 同实例深合并；
- inference source 缺 hash、字段面不一致、重复 tuple、固定字段重叠、与 space 组合均拒绝；
- 模型预测排名与 adapter 仿真排名相反时，最终仍选择 adapter 指标；
- exact affine training/heldout 通过并编译 full task；
- heldout outlier 返回 `partial` 且不能编译；
- demo source、未建模输入变化和奇异训练矩阵拒绝；
- CLI result/task round trip。

最终本地验证：

```text
python -m pytest
602 passed in 3.55s

全部 examples/tasks/*.json：155/155 plan passed
vda catalog：passed
vda plan examples/tasks/inverter-close-loop.demo.json：passed
```

## 尚未验证与下一 Gate

- 新 6 点尚未运行 OA→`si`→Spectre，因此预测误差、实际可行比例和预算收益未知。
- `candidate_set` 的通用 semantic/raw 执行已有本地 adapter 测试；新的 raw CDF 原子组合
  尚未做 live OA callback/checkpoint smoke。
- 重线性化只接受 numeric semantic 维度。任意 raw CDF 字符串必须先由拓扑/PDK 适配层
  定义数值含义；这不会限制人工直接写 raw 参数。
- 当前是一阶局部模型；更复杂电路可改 policy、参数、指标和 proposal 网格，但不能把通过
  一种拓扑的误差外推到另一种拓扑或 PVT。
- PVT 仍为可选项，不默认增加本 Gate 成本。

下一 live Gate 应先执行共源级 6 点 full quality task，因为它有过定 training 和 AC、
linearity、noise 完整规格；需要新的明确授权，并列出目标
`vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic`、OA write、remote compute、`/data/xum`
scratch 和 `replace_existing=false`。只有该 Gate 通过后，才执行差分对 6 点并比较 Gate 8
旧 theory ranking、局部预测和真实 EDA selection。
