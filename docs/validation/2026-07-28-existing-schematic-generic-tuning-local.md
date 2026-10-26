# 2026-07-28 existing-schematic 通用有限实例参数闭环本地 Gate

## 结论

`existing_schematic design.tune` 已接入现有有限候选与 checkpoint 状态机，不需要增加共源、
差分对或其他电路专用 executor。当前状态是 **generic existing-schematic finite raw-instance
tuning orchestration locally verified**。本 Gate 没有连接 Bridge、运行 Spectre 或写 OA，
不能代替真实同源调优证据，也不是 L5B closure。

> 2026-07-29 follow-up：下述已准备任务现已真实执行。三点 OA→`si`→Spectre AC、最终
> 写回、任务外独立 OA 回读和一次 `WinError 10054` checkpoint resume 均已闭合；详见
> [真实 Gate](2026-07-29-existing-schematic-generic-tuning-live.md)。本页继续保留本地 fixture
> 覆盖和执行前边界，不以新 live 结果改写原始本地证据。

> 2026-07-31 follow-up：受控单-delta `existing_schematic design.close_loop` 已在本地开放，
> 详见[拓扑与参数联合闭环本地 Gate](2026-07-31-existing-schematic-topology-parameter-close-loop-local.md)。
> 下文“尚未开放”仍是本页在 2026-07-28 当时的边界，不应解读为当前代码状态。

## 契约边界

- 目标必须是已存在的 `schematic`；`create_if_missing` 和 `replace_existing` 均拒绝。
- 必须同时声明 `design_context`、typed `generic_simulation` 和显式 `dc` 或 `ac`。
- 搜索只接受实际 raw CDF/OA 字段的 `instance_parameter_space`，或只含
  `instance_parameter_updates` 的原子 `candidate_set`。未知电路不接受模板 semantic
  `parameter_space`、semantic candidate 字段或 `theory_seed`。
- 每个固定或搜索实例字段必须同时位于 context 参数权限和
  `netlist_parameter_bindings`；因此不能只证明 OA 值改变，却不核对它进入 `si` 网表。
- 独立 `parameters.apply` 保持原 Bridge 字符串能力，不受本 Gate 的仿真绑定要求收窄。
- `design.close_loop` 尚未对通用既有 schematic 开放；本 Gate 只闭合用户已给 topology 后的
  参数细化。

## 执行复用

planner 生成：

```text
probe -> inspect -> design.context.bind -> parameters.stage -> netlist/sweep
      -> results.select -> parameters.finalize -> inspect.after -> persist
```

executor 直接复用现有实现：

- 从完整 OA readback 取得每个目标字段的初值；
- 逐候选调用通用 `parameters.apply`，保存原请求、Bridge 实际应用映射和定向回读；
- 每点调用同一个 `simulate_existing_schematic`，由其完成 OA→`si` 参数一致性与 DC/AC；
- 候选完成边界原子保存 checkpoint；预算不足只允许 `best_evaluated`；
- 有可行点时再次写入并独立回读最佳字段；全域不可行时恢复搜索前值；
- transport interruption 记为 `system_event`，先尝试恢复初值，再由独立 OA readback 验证
  checkpoint 允许状态，并只重试未完成候选。

通用 schematic 没有模板 semantic 参数。executor 因此把其 semantic baseline 明确定义为空，
只比较 exact raw instance state；没有为未知 topology 发明 W/L/R 等别名。

## 本地验证

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\vda.exe plan `
  examples\tasks\existing-schematic-generic-ac-tune.demo.json
```

结果：

- Python：`762 passed in 5.92s`；
- 任务示例：原有 `201/201` 全量 plan passed，新增真实 Gate task 又独立 plan 通过，当前为
  `202/202`；
- 新通用 tune 示例生成 10 步计划，含 OA write 与 remote compute，且不含
  `schematic.ensure`/create/replace；示例安全授权保持 false，只用于 plan。

新增六项专项测试覆盖：

1. 三点 `MN0.w` AC 域完整执行，选择第二点并再次写回，search scope 为
   `best_in_declared_discrete_domain`；
2. 全域不可行时 selection 为空并恢复初始 `MN0.w`；
3. `max_iterations=2/3` 时只提交已完成前缀中的最佳点，状态为 partial、scope 为
   `best_evaluated`；
4. 第二候选仿真 transport interruption 后，失败 action 为 `system_event`，OA 先恢复，
   checkpoint 保留候选 1，resume 从候选 2 继续并最终完成；
5. 未被 OA→`si` binding 覆盖的搜索字段在 TaskSpec 阶段拒绝；
6. 原子候选保持声明顺序，semantic candidate 字段对 generic existing schematic 拒绝。

fixture 的 OA 读写是 `bridge_readback`，候选仿真量模拟为 `eda_result`，context binding、
constraint、objective 和 selection 是 `software_inference`；由于 adapter 是本地 fixture，以上
只证明编排和恢复语义，不构成电路性能证据。

## 未验证边界与下一 Gate

- 尚未真实验证 callback 后参数映射、逐候选 `si` 参数值、Spectre 指标、最佳 OA 写回和最终
  独立 readback 共同一致。
- 尚未验证 callback 耦合或派生字段，例如 `Wfg × fingers × m`；live 首轮应只选已在目标
  OA/`si` 中直接映射且可恢复的一个字段。
- 层次化网表、tran/noise/PVT 和跨 analysis 通用调优仍未开放。
- 真实 Gate 仍需单独列出 target、OA write、remote compute、`/data/xum` 路径、覆盖风险和
  本次 plan token；本地完成不构成远端授权。

下一道最小真实 Gate 应复用已完成只读 DC/AC 的同一已知 cellview，冻结一个 2–3 点 raw
实例参数域，逐点核对 OA readback、`si` 参数 binding 和 EDA metric，再验证最佳写回、全不可行
恢复和一次 checkpoint resume。只有这条真实链通过后，才考虑把 topology-delta 与参数搜索
组合成 `existing_schematic design.close_loop`。

已准备但未执行的第一步 task 为
`examples/tasks/existing-schematic-generic-cascode-ac-tune.bridge.json`：目标
`vb_pdk_smoke/vda_cs_cascode_gate_001/schematic`，只搜索
`MNCAS.Wfg=[750.0n,1u,1.25u]`，三点都核对 9 项 OA→`si` binding；当前基线 `750.0n`
排在第一且任务没有 objective，因此只要基线仍满足已验证约束，最终会写回原基线。计划 token
为 `7a6eff43d767a16f`。该文件中的安全开关不构成执行授权。
