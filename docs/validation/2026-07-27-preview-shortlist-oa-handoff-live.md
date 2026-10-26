# 2026-07-27 preview shortlist 到 OA 同源复核与耗时 live Gate

## 结论

用户批准的 `009 → 007 → 003` 三点 shortlist 已确定性编译成普通
`design.tune` 任务，并在现有
`vb_pdk_smoke/vda_cs_cascode_gate_001/schematic` 上完成 3/3
OA→`si`→Spectre AC。三点全部可行，真实 GBW 仍选择 `cascode-seed-009`；最佳参数写回
后独立 OA 回读为 `cascode_common_source`，`MNCAS.Wfg=750n`、`MNCAS.l=30n`。

三点的参数、`si` 网表 SHA-256 和每点 40 项指标与原九点 OA 参考 run 的对应项精确一致，
最大绝对差为 0。成功的三点 CLI wall time 为 `229.924 s`，相对原九点的
`811.503 s` 减少 `71.667%`，即 `3.529×`。加上 standalone preview 的
`71.628 s`，两级 EDA 主路径合计 `301.553 s`，仍减少 `62.840%`，即 `2.691×`。

当前状态可表述为：

**hash-bound preview shortlist to normal same-source OA verification, winner retention,
and measured runtime reduction verified for the known retrospective nine-point domain**

它不证明下一种拓扑也能保留 winner，不把三点中的最大值称为连续或全局最优，也不把
preview 数值包装成 OA 同源结果。

## 授权与目标

- 用户确认执行本次 3 点 OA shortlist 耗时实测；
- target：`vb_pdk_smoke/vda_cs_cascode_gate_001/schematic`；
- OA write：是，只允许依次暂存 009/007/003 并写回真实最佳点；
- remote compute：是，3 次 OA→`si`→Spectre AC；
- `create_if_missing=false`、`replace_existing=false`；
- PDK/profile：`nics4304_tsmc28`，TSMC N28 `top_tt`；
- shortlist plan token：`0347903f11f02110`；
- 远端 root 前缀：
  `/data/xum/virtuoso_bridge_smoke/vda_common-source-cascode-ac-preview-shortlist_*`；
- 未修改 `virtuoso-bridge-lite` 或 Obsidian Vault。

该授权没有扩展为任意新 cellview、覆盖已有 view、删除远端证据或运行其他候选。

## 确定性交接契约

新增 `vda oa-task-from-preview-shortlist`：

1. 要求 selection 已 `succeeded` 且 utility Gate 通过；
2. 核对 PDK、analysis、candidate generator/source、候选源 SHA、完整域 ID/顺序和
   variant identity；
3. 逐字节绑定 selection 文件与完整 OA candidate task；
4. 保留 OA target、固定参数、constraints、objective 和 safety；
5. 只按 shortlist 排名抽取原子 tuple，并令 `max_iterations=3`；
6. 输出正常 `TaskSpec`，必须重新 plan，不继承 preview token 或授权。

对本 common-source cascode 参数面，编译器还生成
`expected_target_topology_variant=cascode_common_source`。executor 在任何 candidate stage
或 simulation 前比较 OA inspect 的 `topology_variant`；不匹配即失败，并明确记录没有
尝试候选 OA 写入。该检查只阻止错拓扑执行，不会自行改变拓扑。

## 首次失败与恢复

首次执行在 `4.113 s` 内失败。前一轮 topology round-trip Gate 已按设计把同名 cell 恢复
成普通 `common_source`，inspect 只看到 `MN0/RD0`；第一个 candidate stage 因 cascode
geometry 缺少 cascode topology 而拒绝，候选完成数为 0。`parameters.restore.interrupted`
随后成功，OA 仍是普通共源。该失败记为 `system_event`，不是候选不可行或 Spectre 结果。

在同一已授权 target/write 范围内，复用已 live 验证的
`common-source-cascode-forward.bridge.json`：只添加 `NCAS/VCAS/MNCAS/VCAS pin`，并把
`MN0.D` 从 `OUT` 重连到 `NCAS`。没有新建或替换 cellview；forward plan 用时
`0.445 s`，执行用时 `3.822 s`。随后才重跑原 token 的三点任务。

新增的 task-level 拓扑前置检查把这次经验固化为通用 fail-fast：未来同类错拓扑会在
`schematic.inspect.before` 后停止，而不是进入第一个 candidate stage。修复拓扑仍必须是
单独可审查、可授权的 topology-delta operation。

## 三点结果

| candidate | simulation wall (s) | GBW (GHz) | AC samples | `si` parameter consistency |
|---|---:|---:|---:|---|
| cascode-seed-009 | 73.005 | 23.270457 | 271 | matched |
| cascode-seed-007 | 72.263 | 22.339415 | 271 | matched |
| cascode-seed-003 | 73.562 | 22.440819 | 271 | matched |

三点均 `analysis_complete=true`、无 analysis issue，全部满足 saturation、上下管余量、
输出摆幅、KCL mismatch、gain 与 bandwidth 约束。声明三点域完整穷尽，selection scope 为
`best_in_declared_discrete_domain`；continuous/global optimum claim 均为 false。

最佳 009 的主要真实指标：

| metric | value |
|---|---:|
| low-frequency gain | 6.441244 V/V / 16.179395 dB |
| −3 dB bandwidth | 3.612727 GHz |
| GBW | 23.270457 GHz |
| unity-gain frequency | 22.189521 GHz |
| output swing margin | 0.172029 V |
| DC supply power | 23.353861 µW |

最佳参数为 `Wmain=1.0 µm`、`Lmain=0.03 µm`、`RD=20 kΩ`、`VIN=0.35 V`、
`VDD=0.9 V`、`Wcas=0.75 µm`、`Lcas=0.03 µm`、`VCAS=0.545 V`、`CL=2 fF`。

## 同源与参考一致性

- 每点网表均由目标 OA 经 `Cadence si -batch` 自动生成；
- OA readback 与网表的 main/cascode W/L、RD 和 topology 均 matched；
- wrapper 中的 VCAS 与候选请求 matched；
- 每点 raw DC/OP/AC 均来自 Spectre 21.1；
- 009 与 007 共享相同 OA geometry，所以 `si` 网表 SHA 同为
  `4a2b4d21c32557788cfc9ae537f53427ead757ebe3f115044af1ac95f7b4de7b`，
  两点不同的 VCAS 保留在各自 wrapper；
- 003 的 `si` 网表 SHA 为
  `f56d1deae8522a9c459fedf4b0e8829dbd81f649dac990f23a9f7d063c5f9204`；
- 新三点分别对应旧九点 reference index 9/7/3；每点参数完全相等，40/40 指标最大绝对
  差为 0，两个 run 的 winner 均为 009。

远端证据 root：

```text
/data/xum/virtuoso_bridge_smoke/vda_common-source-cascode-ac-preview-shortlist_3a36226bec1d
/data/xum/virtuoso_bridge_smoke/vda_common-source-cascode-ac-preview-shortlist_f6cc1a8602be
/data/xum/virtuoso_bridge_smoke/vda_common-source-cascode-ac-preview-shortlist_7e989a6fc789
```

## 耗时解释

| 路径 | wall time | 相对九点 OA |
|---|---:|---:|
| 原九点 OA→`si`→Spectre | 811.503 s | baseline |
| 成功三点 OA→`si`→Spectre | 229.924 s | −71.667%, 3.529× |
| preview + 成功三点 OA | 301.553 s | −62.840%, 2.691× |

三点运行前估计为 `244.553 s`，实测快 `14.629 s`，即 `5.982%`。本轮冷启动持久 Bridge
tunnel 为 `5.084 s`；计划、首次失败、forward topology plan/执行和成功三点的可测 CLI
开销合计约 `243.843 s`。资源审计另约 44 s，属于验证/清理检查，不是候选设计主路径。

Agent 阅读、诊断和决定复用哪个已验证 topology-delta 的思考时间没有独立机器计时器，
不能伪造一个精确数字。首次失败说明这种编排复杂度确实可能抵消部分仿真节省；新增显式
拓扑前置契约正是为了让未来任务在本地 plan/首次 OA inspect 时暴露该问题，避免再次进入
失败候选路径。

## 自动化回归

- shortlist handoff 与 topology precondition 专项：`9 passed, 105 deselected`；
- 全量 Python：`727 passed`；
- `python -m compileall -q src tests`：通过；
- `vda catalog --json`：通过；
- 由新编译器生成的三点任务可重新 plan，before/after 两个 topology 条件均进入 plan；
- `git diff --check`：通过。

负向测试覆盖 selection utility 失败、空 shortlist、candidate source/hash/order/analysis
漂移、cascode 参数面不完整，以及目标 OA 拓扑不匹配时在候选写入前终止。原有局部
`simulation.run`、逐维 tuning、显式参数应用和 topology-delta 执行器没有被替换。

## 资源与进程清理

只读资源记录：

```text
artifacts/probes/common-source-cascode-preview-shortlist-resources-20260727.json
SHA-256 f91957dc47b61a9e3ed4619febd13b77470efdc188bd8898b0975c1c6af7bdcd
```

- `spectre=0`、`si=0`；
- Maestro session=0，VDA-managed Maestro=0；
- 既有 `virtuoso=2`，本 Gate 未新增 Maestro session；
- 三个远端 evidence root 共 225,438 B，按保留策略未删除；
- 本地 shortlist run 目录 851,905 B / 4 files；
- `deletion_performed=false`；
- Bridge tunnel 已显式停止，随后复核为 `NOT running`；
- 本地 `ssh/scp/tar/vda/WindowsTerminal/OpenConsole` 进程数均为 0；
- 本次执行过程中用户确认不再出现空白 PowerShell 窗口。

## 证据对象与 SHA-256

| object | SHA-256 |
|---|---|
| 成功三点 run | `54510b1867229e0ea971f576bba5acb1405d3bcb9030e9590f17d44bbe74845a` |
| 首次拓扑不匹配 run | `e08711098a6b5e18bdf6aada1317b652c7488940d42136b368cb1391ff3cc6f2` |
| forward topology run | `36c51d4773898c3fa7ef062f2c2be87aea3254a59dc14280cad3e1acbe876486` |
| 原九点 OA 参考 run | `11aed37b22f1028fc43e43835a6069280b4048ddf4bba32fe3b0617bfb83e060` |
| 资源记录 | `f91957dc47b61a9e3ed4619febd13b77470efdc188bd8898b0975c1c6af7bdcd` |

原始本地记录位于：

```text
artifacts/runs/common-source-cascode-ac-preview-shortlist/oa-shortlist-timing-20260727.json
artifacts/runs/common-source-cascode-ac-preview-shortlist/oa-shortlist-timing-20260727-rerun1.json
artifacts/runs/common-source-cascode-forward/preview-shortlist-prerequisite-20260727.json
artifacts/runs/common-source-cascode-ac-seeded/run-20260726T-live-real-network.json
```

## 证据分类与边界

- Spectre DC/OP/AC、gain/BW/GBW/power、raw 文件 hash：`eda_result`；
- OA 结构/参数回读、Bridge probe、远端进程/session/目录清单：`bridge_readback`；
- preview shortlist、候选映射、约束判定、排序、耗时比例和 reference diff：
  `software_inference`；
- task、规格、top-k policy 与本次执行授权：`user_input`；
- 首次 topology mismatch：`system_event`。

尚未验证的边界是：未见拓扑上的 prospective winner retention、不同 PDK/PVT、noise、
linearity、完整 quality objective、任意 topology synthesis，以及人工 ADE handoff。本 Gate
只证明已知 nominal TSMC N28 共源/共栅九点域可以用 preview 把 OA 复核缩到三点，同时
保留原离散域 winner 和真实同源证据。
