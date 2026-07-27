# 2026-07-27 standalone preview 九点候选预筛与 OA 参考校准 live Gate

## 结论

在用户明确批准的只读远端计算范围内，既有 9 点共栅级联候选被确定性编译为 9 个
standalone Spectre variant，并与一个固定普通共源基线一起完成 10/10 真实 TSMC N28
DC/AC。新增 `vda preview-select` 随后对 task/run/reference hash、候选身份、重渲染 deck、
artifact manifest、非空波形、进程边界和参考 OA→`si` 穷尽域做自动审计。

preview GBW top-3 为 `cascode-seed-009/007/003`；既有 OA→`si` 真值 top-3 为
`009/003/007`。两侧 winner 都是 `009`，Spearman ρ=`0.933333`，参考可行点 recall 与
逐点可行性 agreement 都为 `1.0`。当前状态可以升级为：

**retrospectively calibrated standalone Spectre top-3 screening verified for the known
common-source/cascode nine-point domain; prospective cross-topology utility and final OA
closure remain unverified**

它证明的是“可把本拓扑的 9 个 OA 真值候选预筛到 3 个”，不是 preview 数值等价于 OA，
也不是连续或全局最优。

## 授权与执行边界

- OA target：无；
- OA access/write：否；
- remote compute：是；
- `replace_existing=false`；
- PDK/profile：`nics4304_tsmc28`，TSMC N28 `top_tt`，27 ℃；
- AC：10 kHz–1 THz，30 points/decade；
- 计划 token：`37cbd9002824f337`；
- 远端非覆盖 root：
  `/data/xum/virtuoso_bridge_smoke/vda_netlist_preview_common-source-cascode-candidate-preview_58921a1bc106`；
- 未运行 `si` 或 Maestro，未创建/修改 cellview；
- 未修改 `virtuoso-bridge-lite` 或 Obsidian Vault。

沙箱内首次 doctor 因 DNS 不能解析 `nics4304-cad1` 而停止，未建立本次 scratch；这属于
`system_event`，不是电路不可行。随后使用 Bridge 原生 tunnel 完成获批的远端计算。运行
结束、资源审计完成后，tunnel 已显式停止并复核为 `NOT running`。

## Hash-bound 输入与输出

| 对象 | SHA-256 |
|---|---|
| 9 点候选源 | `cac79804395d001f4cd2fcdfa0acc4498f5bd10c099888476df6546b7f7e71c4` |
| 编译后 preview task | `4492b955673fb387f62d00aefab4d3980e6574d6c11cde0d55f38807f031864a` |
| 10-variant live run | `90cdffc52acd339a135c3339e3ef445d71817c7538b042270b59f01117fef17e` |
| OA→`si` 九点参考 run | `11aed37b22f1028fc43e43835a6069280b4048ddf4bba32fe3b0617bfb83e060` |
| selection policy 文件 | `4a16fae0f8140d2c1ea94754f8fd8a2a4bc073d78c94cabbe5ac014c4f16e81f` |
| selection result | `15515aa73435e4a85bb132830d05abaf99291dff2f5044b4ec092fa056249e64` |

policy 的 canonical SHA-256 为
`7a71cde5e374c00412fc3b707f97360f5e69011cca196e8b9bee7f15962a10a6`。
任务中的 `variant_source_ids` 按原顺序一一映射
`cascode_candidate_001..009 -> cascode-seed-001..009`；参考 run 的
`candidate_set_source.generator/id` 为
`vda.cascode-seed/common-source-cascode-op-seed-20260726-live`，其
`cascode_seed_result_sha256` 与上述候选源 hash 相同。

原始文件保留在：

```text
artifacts/theory/common-source-cascode-preview-task-20260727.json
artifacts/runs/common-source-cascode-candidate-preview/run-20260727T-candidate-preview-live.json
artifacts/runs/common-source-cascode-ac-seeded/run-20260726T-live-real-network.json
artifacts/theory/common-source-cascode-preview-selection-20260727.json
```

## 执行与完整性

- 固定共源基线加 9 个候选全部 `analysis_complete=true`，无 analysis issue；
- 每个 variant 都有 241 个非空复数 AC sample；
- 每个 variant 都有 8 项非空 deck/PSF/log/guard manifest 和独立聚合 hash；
- validator 从 typed graph 与当前 PDK profile 重新生成每份 Spectre deck，10/10 的 SHA
  同 run `deck_sha256` 和 manifest 中唯一 `.scs` 项一致；
- 每个远端运行都记录 `bounded_remote_process=true`；
- Spectre 21.1.0 返回 0 error；共同的 3 个 `SFE-1131` option warning 与 2 个 notice 保留，
  没有被静默删除或包装成 analysis failure；
- reference run 确认 9/9 离散域穷尽，selection scope 为
  `best_in_declared_discrete_domain`，连续/全局最优声明均为 false。

`preview-select` 对以下情况硬拒绝：task/run/reference hash 漂移、候选 source/ID/order
不一致、重渲染 deck 或 manifest 不一致、空/短 AC、未包围的 analysis、未安装进程 guard、
非 `/data/xum` 路径、preview 指标来源降级、参考域未穷尽或参考 winner 与 objective 不符。
如果证据完整但排序相关性不足、遗漏参考 winner 或粗约束产生空 shortlist，则保留结果并
返回 `partial`，不会把筛选器失效误报成传输/证据损坏。

## 九点排序

| candidate | preview GBW (GHz) | preview rank | OA→`si` GBW (GHz) | reference rank |
|---|---:|---:|---:|---:|
| 001 | 23.4544 | 4 | 21.5589 | 5 |
| 002 | 22.4948 | 8 | 20.4651 | 8 |
| 003 | 23.7820 | 3 | 22.4408 | 2 |
| 004 | 22.6017 | 7 | 20.7603 | 7 |
| 005 | 21.7327 | 9 | 19.7337 | 9 |
| 006 | 22.8287 | 6 | 21.5894 | 4 |
| 007 | 24.2539 | 2 | 22.3394 | 3 |
| 008 | 23.2241 | 5 | 21.1859 | 6 |
| 009 | 24.7049 | 1 | 23.2705 | 1 |

当前 policy 的 shortlist size=`3`，表示约 3× OA 候选成本压缩。它要求：全部 MOS 仍在
saturation region、gain≥2 V/V、bandwidth≥1 MHz，再按 GBW 最大化。九个 preview 和九个
参考点都通过这些粗护栏。

没有把 OA task 的 `minimum_saturation_margin_v≥20 mV` 原样用于 preview：preview 对
003/006/009 的最小余量只有 `13.16/10.46/14.92 mV`，会错误淘汰真实 OA winner 009，
而参考 OA run 的九点均满足其完整约束。这一差异说明 preview 的工作区分类适合做硬
region 证伪，但未经校准的绝对 margin 不能代替 OA 真值。

## 数值误差

| 指标 | 平均绝对误差 | 最大绝对误差 | 最大误差点 |
|---|---:|---:|---|
| low-frequency gain | 2.8319% | 4.3891% | 009 |
| −3 dB bandwidth | 8.1306% | 11.0378% | 009 |
| GBW | 8.1979% | 10.1298% | 005 |
| DC supply power | 19.1426% | 19.4513% | 005 |

功耗约 19% 的系统偏差仍明显，所以 selection policy 不包含绝对功耗门，也不能据此做
最终 power closure。top-3 和 ρ 门是在同一已知结果上完成的事后校准；policy 显式记录
`assessment_mode=retrospective_calibration`。未来第一份未见拓扑必须以
`prospective_validation` 运行，不能在看到 OA 真值后再改 shortlist 或阈值并称为预测通过。

## 自动化测试

- preview selection/compile/runner/CLI 专项：`45 passed`；
- 全量 Python：`711 passed`；
- `examples/tasks/*.json`：`196/196` 可 plan；
- `python -m compileall -q src tests`：通过；
- `python -m virtuoso_design_agent catalog --json`：通过；
- `git diff --check`：通过。

新增测试不仅覆盖成功路径，还覆盖 task/run hash 漂移、重渲染 deck 不一致、metric evidence
source 降级、短/空 AC、参考域未穷尽、候选源 hash 不一致、低排序相关性、漏掉参考 winner
和完整证据下的空 shortlist。后两类必须返回 `partial`，前六类必须硬拒绝。

## 资源审计

最终只读资源记录：

```text
artifacts/probes/common-source-cascode-preview-resources-20260727.json
SHA-256 f666d86ce21ba84de40c6ce11240020c2460d9981f655147a3519dd23a5b0145
```

- 本地 VDA transient entry：0；
- `spectre=0`、`si=0`；
- Maestro session=0，VDA-managed=0；
- 既有 `virtuoso=2`，与本 Gate 无新增 Maestro session；
- 本次远端 evidence root：1,613,171 B；
- `deletion_performed=false`；
- Bridge tunnel 最终为 `NOT running`。

远端 evidence root 有意保留用于复核，不是孤立进程；删除它仍需单独的精确路径授权。
第三方 Bridge 仓库在检查时工作树干净，本 Gate 没有修改其代码。

## 证据分类与边界

- DC/OP/AC、gain/BW/GBW/power、deck/PSF/log hash：`eda_result`；
- Bridge/Spectre probe、远端路径、进程和 session inventory：`bridge_readback`；
- variant→candidate mapping、粗约束、rank、Spearman、误差和 shortlist：
  `software_inference`；
- task、policy、阈值和执行授权：`user_input`；
- 初次 DNS failure：`system_event`。

本 Gate 没有把 return code 0、文件存在或某个最大值单独称为设计完成。successful
shortlist 确定性编译回普通 atomic OA 验证任务的增量 Gate 已于同日完成：已知域 top-3
经 3/3 OA→`si`→Spectre 重放后仍保留 winner 009，并实测相对九点 OA 减少 71.667% wall
time。该 follow-up 见
[preview shortlist OA handoff live Gate](2026-07-27-preview-shortlist-oa-handoff-live.md)。
第一份未参与本次校准的新拓扑仍必须 prospectively 检查“top-k 保留真实 winner”；在此
之前，当前结论只适用于这一个 TSMC N28 nominal 共源/共栅候选域。
