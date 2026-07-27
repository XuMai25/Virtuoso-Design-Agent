# 快速 preview shortlist 工作流

## 目的

这是一条以后实际设计时默认使用的降成本路径，不是下一轮需要重复执行的验证 Gate：

```text
理论/经验/局部模型给出有限候选
  -> 一次 standalone Spectre preview 批处理
  -> 在 OA 真值前冻结 shortlist
  -> 只对 shortlist 做 OA -> si -> Spectre
  -> 对真实 winner 做回读和必要质量复核
```

它复用现有 VDA task、planner、Bridge adapter、checkpoint 和 evidence record，不增加第二套
仿真器或远端执行框架。目的只是减少昂贵的 OA 写入、`si` netlisting 和完整分析次数，同时
保留人工直接改参数、局部仿真和 ADE 介入能力。

## 默认决策

| 条件 | 默认路径 |
|---|---|
| 只有 1–3 个候选 | 直接走普通 OA/ADE；preview 的固定开销通常不值得 |
| 4 个及以上、已有显式有限候选 | 先 preview，默认冻结 top-3，再只跑三点 OA |
| 只是人工指定一个参数改动 | 直接 `parameters.apply`，不强迫进入筛选流程 |
| 只需建图、读图或单点仿真 | 使用对应正交 operation，不进入完整闭环 |
| preview 不支持该分析或结构 | 直接 OA/`si` 或 ADE，不临时拼手写网表冒充支持 |
| noise、linearity、PVT、mismatch 等最终质量项 | 默认只对 OA 真值 winner 运行；任务明确要求时才扩展 |

是否值得 preview 用同一条简单成本判据：

```text
Tpreview < (N - k) * Toa_per_candidate
```

其中 `N` 是完整候选数，`k` 是 shortlist 大小。估计必须来自最近相同服务器/分析路径的
action timestamps；没有计时依据时不伪造精确收益。当前两个 live 参照为：

- 已知共栅九点域：preview + 三点 OA 实测 `301.553 s`，相对九点 OA 的
  `811.503 s` 减少 `62.840%`；
- 未见差分对八点域：preview + 三点 OA 按同一完整 run 的 action timestamps 估算
  `354.176 s`，相对重建的无中断八点 OA `733.694 s` 减少 `51.727%`；该数不是独立
  三点实测。

## 固定阶段

### 0. 生成有限候选

候选优先来自 KCL/gm-Id、真实 PDK characterization、工作点局部模型或人工明确输入；不先
随意列大网格再让 Spectre 猜答案。每个候选必须是不可拆分的 atomic tuple，保存来源、ID、
参数和 SHA-256。最终最优仍只由真实 EDA 指标决定。

若使用 `op-relinearize` 产生 prospective 域，应设
`include_measured_anchor=false`，避免把已经运行过的 anchor 或历史点重新计入新一轮候选。

### 1. 编译 preview task

```powershell
.\.venv\Scripts\vda.exe preview-task-from-candidates `
  <compile-policy.json> `
  <candidate-source.json> `
  <preview-task-template.json> `
  --output <preview-task.json>

.\.venv\Scripts\vda.exe plan <preview-task.json>
```

这一步只在本地映射 typed MOS/R/C/电源字段并生成计划。不得访问 OA、启动 Virtuoso、运行
远端计算或写入任何 cellview。

### 2. 一次批量运行 preview

在一次获授权的 `simulation.run` 中运行全部 preview variants；不要逐候选启动 Bridge。
preview 只需覆盖决定 shortlist 所必需的廉价 DC/AC 指标。默认不在这里增加 noise、
transient、PVT 或 ADE。

preview 的绝对值不是设计真值。粗约束只用于排除明显错误工作区；若规格边界落在最近同类
preview 的已知误差范围内，该约束不得在 preview 阶段硬淘汰候选，应留到 OA 复核。

### 3. 在读取 OA 真值前冻结 shortlist

```powershell
.\.venv\Scripts\vda.exe preview-shortlist `
  <prospective-policy.json> `
  <preview-task.json> `
  <preview-run.json> `
  <full-oa-candidate-task.json> `
  --output <frozen-shortlist.json>
```

默认 `k=3`。policy、完整 OA task hash、候选顺序、粗约束、objective 和 utility 阈值必须在
真值前固定。若 preview 在 cutoff 附近存在明显并列或约束落入误差带，应在看 OA 结果前
增大 `k`；不得看到真值后再改 shortlist 并称为 prospective。

### 4. 只编译并执行 shortlist OA task

```powershell
.\.venv\Scripts\vda.exe oa-task-from-preview-shortlist `
  <frozen-shortlist.json> `
  <full-oa-candidate-task.json> `
  --id <shortlist-task-id> `
  --output <shortlist-oa-task.json>

.\.venv\Scripts\vda.exe plan <shortlist-oa-task.json>
```

输出仍是普通 `design.tune`/`design.close_loop` task：复用原 target、规格、objective、安全
策略、checkpoint 和 OA→`si` worker，只把候选域缩到冻结名单。它必须重新 plan，并在真实
执行前按安全规则一次性列明 target、OA 写入、远端计算、远端路径、覆盖风险和新 token。
preview 的授权不能传递给 OA task。

同一 shortlist 应作为一个批任务执行：保持一个隐藏 Bridge tunnel，逐点 checkpoint，已完成
候选不重跑；传输失败后先独立 OA 回读，再从未完成 index 恢复。结束后显式关闭 tunnel 并
核对本地/远端 Spectre、`si`、Maestro 和 PowerShell 子进程。

### 5. 只对真实 winner 做必要质量复核

shortlist 中的真实指标决定 winner，随后完成最佳参数写回和独立 OA 回读。noise、linearity、
ICMR、PSRR、PVT 或 ADE history 只按当前设计任务的规格运行；不为了“流程看起来完整”自动
附加所有 analysis。PVT 继续是可选项。

## 何时才跑完整 OA 候选域

完整 reference 域不再是日常默认，也不需要为了再次证明本工作流而立即应用到另一种拓扑。
只在以下情况运行：

- preview renderer、筛选 policy 或候选映射代码发生实质变化；
- PDK/profile、model section、corner 或 simulator 版本改变；
- shortlist OA 全部不可行，或真实排序与 preview/theory 出现明显冲突；
- cutoff 候选落在已知误差带内，扩大 shortlist 仍不足以消除决策风险；
- 进行有计划的周期性抽查，而不是每个设计都做完整 reference。

完整域 audit 使用 `preview-shortlist-audit` 检查 winner retention、排序和误差，但 audit 是
工作流质量监测，不是每个设计的必经步骤。

## Agent 默认行为

以后遇到适用任务，Agent 应：

1. 先估计 `N/k/Tpreview/Toa_per_candidate`，证明 preview 确实节省时间；
2. 复用本文件和现有模板，不重新讨论或发明流程；
3. 把所有 preview candidates 合并为一次远端 compute，把所有 OA shortlist candidates
   合并为一次 checkpointed task；
4. 默认不跑完整 OA reference、不重复已完成候选、不自动加入 PVT；
5. 需要 OA 写入时只请求一次精确批量授权；
6. 报告机器实测时间与 Agent 无法精确计时的思考成本，二者不得混为一个数字。

这套默认不会限制 Bridge 原有功能。用户仍可直接指定任意允许的实例/CDF 参数、手工打开
ADE、只运行一个 analysis，或绕过 preview 直接要求 OA 真值。

## 最小证据链

一次正常 fast path 至少保留：

```text
candidate source + hash
preview compile policy/template + hash
preview task/plan token/run + raw manifest
prospective policy + frozen shortlist + frozen_at
shortlist OA task/new plan token
OA readback + si netlist consistency + EDA metrics
winner writeback + independent final readback
checkpoint/system events + final process inventory
```

其中 raw Spectre 指标为 `eda_result`，OA/`si`/进程回读为 `bridge_readback`，候选生成、排序、
shortlist、时间估计和规格聚合为 `software_inference`，policy 与授权为 `user_input`。
