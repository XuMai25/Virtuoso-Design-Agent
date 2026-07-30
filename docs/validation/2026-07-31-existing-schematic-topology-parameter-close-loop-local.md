# 2026-07-31 existing-schematic 拓扑与参数联合闭环本地 Gate

## 结论

`existing_schematic design.close_loop` 已开放一个受控、拓扑无关的本地纵切：在用户已有
schematic 的 hash-bound 基线附近，比较基线与一份预声明可逆 topology-delta，并在两条路径
上运行同一组完整 raw-instance 参数候选。当前状态是 **single-delta existing-schematic
topology-and-parameter close-loop orchestration locally verified**。

本 Gate 没有连接 Bridge、运行 `si`/Spectre 或写 OA；本地 fixture 标成 `eda_result` 的数值只
用于验证状态机，不能作为电路性能证据，也不能把项目升级为 L5B closure。

## 为什么这不是随机拓扑搜索

- controller 不生成候选。候选必须由 `candidate_set` 或有限实例参数域预先声明；可以来自
  `user_input`、理论尺寸分析或 OP 局部模型。`software_inference` 来源必须绑定 SHA-256。
- 首版只允许一个 baseline 和一个 local alternative，不做大范围拓扑枚举。
- 两个 topology variant 必须使用同一组完整候选 tuple。一个 tuple 中的 W/L/R 等字段原子
  保持，不会拆成互不对应的单字段试探。
- `objective` 必填，`max_iterations` 必须至少等于 `2 × candidate_count`；不允许用不完整前缀
  决定并提交某个 topology。
- 完全同分保留 baseline，避免没有收益时制造结构改动。

因此，上游仍应先用基尔霍夫、小信号模型、PDK 表、真实 OP 导数或 standalone preview 缩小
范围；本 controller 负责把已经有依据的小域送入同源 OA 真值比较和安全提交。

## 契约与执行

新增 `ExistingSchematicTopologyRefinementSpec`，绑定：

- baseline/alternative ID；
- 一份 direction=`forward`、含 exact inverse 的 `TopologyDeltaExecutionSpec`；
- alternative 独立的 `DesignContext` 与 `GenericOaSimulationSpec`；
- 可选的 alternative-only 固定实例参数。

alternative-only 固定参数只允许作用于本 delta 新增的实例，并且必须同时由 alternative
context 的 `fixed` 权限和 alternative OA→`si` binding 覆盖。保留实例上的固定/搜索参数仍由
两条路径共享，避免暗中改变比较条件。

planner 生成 15 步显式计划：

```text
probe
  -> baseline inspect/context bind/stage/sweep
  -> exact baseline parameter restore
  -> forward topology delta
  -> alternative inspect/context bind/stage/sweep
  -> topology+parameter select
  -> atomic finalize or exact restore
  -> final inspect/persist
```

executor 把两个 N 点域展平为全局 candidate index `1..2N`。每点记录 topology variant ID、
topology SHA、原子 candidate ID、实际指标与来源；`SearchAudit` 记录总候选数和 variant 数。
只有 `2N` 点全部完成才使用本次指标判约束和 objective：

- alternative 胜出：保持 alternative topology，提交其完整参数并回读；
- baseline 胜出：恢复 alternative 参数，执行 exact inverse，再提交 baseline 参数并回读；
- 全域不可行：恢复执行前 baseline topology 与参数；
- 任一证据不完整：不把前缀最佳点包装成 topology winner。

## Checkpoint 与恢复边界

checkpoint 新增 expected/pending topology variant 与 SHA。transport interruption 后，executor
只接受当前 OA 完整等于 baseline 或 alternative 指纹；可识别状态会先归一到 baseline，再从
首个未完成全局 index 继续，已完成候选不重跑。当前图若为未知或部分 delta 状态，则拒绝自动
覆盖并保留未验证状态；这与 Bridge 自身只在 exact post-save 状态上执行 inverse recovery 的
边界一致。

## 证据分层

- OA topology、CDF 写入/回读：`bridge_readback`；
- `si`/Spectre 原始标量、波形和 manifest：真实执行时应为 `eda_result`；
- context binding、topology fingerprint、constraints、objective、selection 与恢复判断：
  `software_inference`；
- 用户声明的 topology envelope、候选和规格：`user_input`，或带来源 hash 的
  `software_inference`。

本实现只复用现有 adapter 的 `inspect_schematic`、`apply_parameters`、`verify_parameters`、
`transform_schematic` 和 `simulate` 边界；没有修改或复制第三方 Bridge。

## 本地验证

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\vda.exe plan `
  examples\tasks\existing-schematic-topology-parameter-close-loop.demo.json
```

结果：

- Python：`773 passed in 5.68s`；
- 全部任务示例：`206/206` plan passed（含新增只读 final-inspect task）；
- 示例生成 15 步计划，要求 remote write 与 remote compute，但示例安全开关均为 false；
- 示例 plan token：`0dec7e9e4b62b1b6`。

专项测试覆盖：

1. 缺 objective 和预算小于完整 `2×N` 域时在 TaskSpec 阶段拒绝；
2. 两字段原子 tuple 由 objective 选中并完整提交，不把各字段拆开拼接；
3. alternative 胜出时提交 alternative topology 与其参数；
4. baseline 胜出时 exact inverse 后提交 baseline 参数；
5. 全域不可行时精确恢复搜索前 baseline；
6. alternative 候选 transport interruption 后恢复 baseline，保留 completed prefix，并从正确
   的全局 candidate index resume；
7. delta 新增实例可声明受绑定的固定参数，保留实例不得借 alternative-only 字段绕过共同域；
8. source-degeneration fixture 完整执行新增 `RS0`、固定并回读其 `r`、比较四点，然后把
   alternative topology、`MN0.w` 与 `RS0.r` 一起提交；
9. planner 完整披露两阶段 sweep、commit-or-restore 与最终独立回读。

## Live follow-up 与保留边界

本 Gate 随后已在用户明确授权的新
`vb_pdk_smoke/vda_existing_close_loop_gate_001/schematic` 上执行。create 继续使用
`replace_existing=false`；close-loop 没有沿用模板旧 hash，而是从 create 后独立 inspect 编译
fresh exact contract。最终 plan token 为 `4ac167b456ae3949`。

真实四点 OA→`si`→Spectre AC 全部完成，topology/parameter consistency、271 点波形和 artifact
manifest 均匹配。GBW objective 选择 baseline common-source 的
`MN0.Wfg=1.1u, RD0.r=18.5K`；alternative exact inverse 后，任务外 inspect 确认 baseline SHA、
实例/net/pin 和胜出参数一致。candidate 1、2 后发生一次真实 `WinError 10054`，controller 恢复
exact baseline 并从 checkpoint index 3 续跑，没有重算已完成点。

因此本地文档中的第一项待验证已闭合；完整数值、哈希、证据分层、资源清理与产物见
[live Gate](2026-07-31-existing-schematic-topology-parameter-close-loop-live.md)。仍未闭合的是：

- controller 的真实全域不可行路径；本地状态机已覆盖；
- controller 内 topology save 后故障注入；底层 generic delta 有独立 live recovery 证据；
- hierarchy、派生 CDF、tran/noise/PVT/mismatch、多 alternative 和并发人工 editor；
- 从 nominal DC/AC 扩到按成本分阶段的通用 multi-analysis refinement。

下一步不应继续在已知四点域追加随机候选，而应增加可迁移的 staged analysis 契约：DC/OP
先证伪工作区，AC 对幸存者选优，再只按任务需要对少数最终点追加 noise/transient/PVT。
