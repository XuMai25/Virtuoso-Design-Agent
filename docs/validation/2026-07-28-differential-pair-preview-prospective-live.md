# 2026-07-28 差分对未见候选域 prospective preview shortlist live Gate

## 结论

首个严格的 prospective preview Gate 已在 TSMC N28 nominal 差分对局部候选域完成。
VDA 在读取本轮任何 OA 真值前，先把 preview policy、完整 OA reference task、top-3 和
冻结时间写入 hash-bound shortlist；随后才执行完整 8 点 OA→`si`→Spectre 真值域，并由
独立 audit 检查执行先后、候选身份、域穷尽、可行性、排序和 winner retention。

preview 与 OA 真值按功耗的 1–8 名完全一致，Spearman ρ=`1.0`，可行性 agreement=`1.0`，
真值可行点 recall=`1.0`；真实 winner `op-local-001` 位于预先冻结的 top-3，并最终写回
OA。当前状态可表述为：

**prospectively frozen standalone preview ranking and winner retention verified for one
nominal TSMC N28 differential-pair local domain; absolute-value substitution and general
cross-topology/PVT utility remain unverified**

这不是连续或全局最优证明。gain/BW/GBW/power 最大绝对误差仍为
`3.05%/24.10%/27.71%/30.35%`，所以 preview 只能排序和缩小候选域，最终接受仍服从
OA→`si`→Spectre 的 `eda_result`。

## 授权与执行边界

- 用户明确批准本次 prospective Gate 和完整八点真值 reference；
- target：`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`；
- OA write：是，只暂存八个声明 tuple 并写回真实最优点；
- remote compute：是，standalone preview 8 点以及 OA→`si` 的 8 点 DC/差模 AC/共模 AC；
- preview token：`9bbc38ddd3efbd75`；
- OA reference token：`33602f4214786c04`；
- `create_if_missing=false`、`replace_existing=false`，未新建或替换 cellview；
- PDK/profile：`nics4304_tsmc28`，TSMC N28 `top_tt`；
- 远端产物位于 `/data/xum/virtuoso_bridge_smoke`，未写 `/home/xum`；
- 未执行 PVT、noise、linearity、mismatch 或 ADE；
- 未修改 `virtuoso-bridge-lite` 或 Obsidian Vault。

## 为什么这次不是事后挑选

旧 `preview-select` 同时读取 preview 和 reference run，适合 known-domain retrospective
校准，但无法单独证明“在看见真值前已经固定名单”。本 Gate 新增两阶段契约：

1. `vda preview-shortlist` 的参数中没有 reference run；它只接受预先写好的完整 reference
   task，并冻结 policy hash、reference task hash、preview run hash、top-k 和 `frozen_at`；
2. 冻结 shortlist 可立即由 `vda oa-task-from-preview-shortlist` 编译成普通 OA task；该任务
   仍需重新 plan 和单独执行授权；
3. `vda preview-shortlist-audit` 只接受 `started_at > frozen_at` 的 reference run，并要求
   reference task SHA、plan token、候选来源、完整顺序和域穷尽全部一致；
4. 冻结后改名单、替换 task、使用旧真值 run 或给出未穷尽域都会硬拒绝。

本轮 shortlist 冻结于 `2026-07-27T20:42:23.080158Z`，完整 OA reference 开始于
`2026-07-27T20:42:54.291822Z`。audit 自动复核了这个时间顺序，不依赖人工陈述。

## 未见候选域

候选由既有成功 real-Bridge 差分对 run 的局部响应生成，但 policy 设置
`include_measured_anchor=false`。输出的八个 tuple 均排除 anchor 和所有历史实测 tuple；
新旧候选交集为零。只改变输入 NMOS 与 PMOS 电流镜负载宽度，尾管和工作条件固定：

| candidate | Wn (µm) | Wp (µm) |
|---|---:|---:|
| op-local-001 | 1.165 | 1.030 |
| op-local-002 | 1.165 | 1.080 |
| op-local-003 | 1.165 | 1.130 |
| op-local-004 | 1.215 | 1.030 |
| op-local-005 | 1.215 | 1.130 |
| op-local-006 | 1.265 | 1.030 |
| op-local-007 | 1.265 | 1.080 |
| op-local-008 | 1.265 | 1.130 |

固定条件为 `L=30 nm`、`Wtail=0.555 µm`、`Ltail=30 nm`、`VDD=0.9 V`、
`VCM=0.55 V`、`VBIAS=0.32 V` 和 `CL=0.5 fF`。它是 preview 尚未校准过的差分对拓扑，
不是对 2026-07-27 共源/共栅九点结果的重放。

## 预注册对象与哈希

| object | SHA-256 |
|---|---|
| source real run | `cd0d211256157eed41117a022ebe2a85cdc7f431a24f7fe238f3f60e2f09acfe` |
| relinearization result / candidate source | `2936e7d401aa4efcc2ab24cc499e7fc4055bf4d9ba0969afd58812322f9848e1` |
| complete OA reference task | `68058022939ca3f78901299a75376588b77f54ae1283769180817b1a51798f5d` |
| preview task | `8458d14b105b03bddc90db0ba4d1784c5d5ff85285cb9ccc451bfc4e225dd878` |
| prospective policy canonical hash | `3b9b7e40ff09628e7d473a60da9e864acee1c3eeb1b190bfa9884163ff5ec8a0` |
| preview live run | `3e106454b9f6e3661d60fe6650c9787f8554ff3ed3862ed98b7e0878d42ee3f8` |
| frozen shortlist | `f1c873643ee5c86097dba0c621e00ce9bed6e00c15f75d7855448f158a884064` |
| compiled three-point OA task | `4419f8fd9ed0b25f03707fb5ee3c3ad7e48db32299429ca419dc4ca912a93988` |
| completed OA reference run | `b4a12a5326f97b9865126625e3ef8fb245352e9d62d421555a172c463a5a0857` |
| completed checkpoint | `ffc4693e9624a4f42f376cf7237b1eb0102c24fd37e427c24ee574e82b7b431b` |
| prospective audit | `10a63142be8ea05e6d6f1ace1f9973bf3e4d3a91031db8dd93145500fd94ea17` |

冻结的 top-3 为 `op-local-001/002/003`。编译后的普通 OA shortlist task token 为
`071204dde5367bc9`，并绑定 frozen shortlist SHA；本 Gate 没有重复执行该三点任务。

## Standalone preview

preview task 使用真实 foundry model Spectre，但不访问 OA、`si` 或 Maestro。八个 variant
全部 `analysis_complete=true`，每点都有非空 AC 和完整 deck/PSF/log/process manifest。
预先固定的粗约束为：所有 MOS saturation、gain≥2、BW≥1.5 GHz、GBW≥6 GHz、
power≤25 µW；objective 为最小功耗，shortlist size=3。八点全部通过粗约束。

preview run record 内 wall time 为 `81.181 s`；外层 CLI 观察值为 `81.544 s`。冻结前没有
读取本轮 OA reference run。

## 完整 OA 真值与恢复

完整 reference 逐点暂存 OA 参数、结构化回读、自动 `si`、运行 Spectre DC/差模 AC/共模
AC，并核对 OA semantic 与网表参数。前五点完成后，candidate 6 的 common-mode raw 下载
遇到 SSH timeout，随后恢复动作又遇到 `[WinError 10054]`。首份 run 因此明确失败、无
recommendation，且把 OA 状态标为 unverified；没有把传输失败记成电路不可行。

Bridge tunnel 隐藏重启后，同一 task/token 从 checkpoint 恢复。executor 先独立回读 OA，
确认当前状态与 candidate 6 一致，再从 index 6 继续；最终 8/8 完成，checkpoint
`complete=true`、`next_candidate_index=9`。reference run 从首次开始到恢复完成的记录 wall
time 为 `944.249 s`。初次 CLI 为 `580.896 s`，恢复 CLI 为 `287.300 s`；其余时间包含故障
诊断和 tunnel 重启边界。

最终独立 `schematic.inspect.after` 回读：

- `MN0/MN1 Wfg=1.165u, L=30n`；
- `MP0/MP1 Wfg=1.03u, L=30n`；
- `MNTAIL Wfg=555n, L=30n`；
- topology 为 `pmos_current_mirror_load_nmos_differential_pair_with_tail_device`；
- nets/pins 为 `BIAS/INN/INP/OUTN/OUTP/TAIL/VDD/VSS`。

每个成功候选的 OA readback 与 `si` netlist semantic parameters 都为 matched。

## 排序、winner 与真实指标

| candidate | preview power (µW) | preview rank | OA power (µW) | OA rank |
|---|---:|---:|---:|---:|
| op-local-001 | 13.373009 | 1 | 10.265282 | 1 |
| op-local-002 | 13.377914 | 2 | 10.268358 | 2 |
| op-local-003 | 13.382574 | 3 | 10.271278 | 3 |
| op-local-004 | 13.413577 | 4 | 10.294012 | 4 |
| op-local-005 | 13.423152 | 5 | 10.300014 | 5 |
| op-local-006 | 13.452321 | 6 | 10.321392 | 6 |
| op-local-007 | 13.457237 | 7 | 10.324475 | 7 |
| op-local-008 | 13.461907 | 8 | 10.327400 | 8 |

真实 winner `op-local-001` 的主要 `eda_result`：

| metric | value |
|---|---:|
| DC supply power | 10.265282 µW |
| differential low-frequency gain | 3.712421 V/V / 11.393144 dB |
| differential −3 dB bandwidth | 2.753089 GHz |
| differential GBW | 10.220625 GHz |
| differential unity-gain frequency | 8.962243 GHz |
| low-frequency CMRR | 34.755901 dB |
| minimum output swing margin | 0.214448 V |
| all signal devices saturation | true |

完整离散域 8/8 可行，selection scope 为 `best_in_declared_discrete_domain`；continuous/global
optimum claim 均为 false。

## Prospective audit

所有预注册 utility Gate 通过：

- artifact integrity：通过；
- preview/reference winner agreement：`true`；
- reference winner in frozen top-3：`true`；
- Spearman rank correlation：`1.0 ≥ 0.5`；
- feasibility agreement：`1.0 ≥ 0.75`；
- reference feasible recall：`1.0 ≥ 1.0`。

绝对数值误差仍呈系统偏差：

| metric | mean absolute error | maximum absolute error | max-error candidate |
|---|---:|---:|---|
| gain | 2.8990% | 3.0536% | op-local-003 |
| bandwidth | 23.9211% | 24.1015% | op-local-006 |
| GBW | 27.5135% | 27.7140% | op-local-008 |
| power | 30.3129% | 30.3514% | op-local-008 |

因此通过的是排名与 shortlist utility，不是绝对 metric accuracy。粗约束若靠近上述误差带，
下一任务必须使用显式 guard band 或直接进入 OA，不能沿用 preview 数值作最终规格判断。

## 耗时解释

冻结 top-3 已自动编译，但没有再次运行三点 OA，因为完整八点 reference 已包含相同前三点，
重复执行不会增加设计证据。可以从同一 run 的 action timestamps 做可审计估算：

| component | seconds |
|---|---:|
| preview CLI measured | 81.544 |
| first three successful OA simulations | 251.108 |
| first three OA parameter stages | 14.505 |
| one probe + before inspect + best writeback + after inspect | 7.019 |
| estimated three-point OA path | 272.632 |
| estimated preview + three-point OA path | 354.176 |

同样按八次成功 simulation、每个候选一次 stage 和固定前后动作重建的无中断八点 OA 基线
为 `733.694 s`，所以估算两级路径减少 `51.727%`。若同本次包含真实断线/恢复的
`944.249 s` workflow wall 比较，则减少 `62.491%`。这些是 action-derived estimate，
不是独立三点实测；2026-07-27 已知域 Gate 的三点路径才是实际计时。

Agent 阅读、编写 policy、诊断网络和决定恢复策略的思考时间没有独立机器计时器，因此不
伪造精确值。run-record wall 已把实际故障与恢复等待计入，新增 prospective 命令则把未来
最关键的决策边界变成一次本地、确定性调用，减少每轮人工核对和事后解释成本。

## 资源与进程清理

最终只读资源记录：

```text
artifacts/probes/differential-pair-preview-prospective-resources-after-20260728.json
SHA-256 440646450e498732b14a0a1b254d78e5d9bc3786c92cdccafa2b43db85e2a77c
```

- 本地 transient entry：0；
- 远端 `spectre=0`、`si=0`；
- Maestro session=0，VDA-managed=0；
- 既有 `virtuoso=2`，本 Gate 未新增 Maestro；
- 本 Gate 1 个 preview root 加 9 个 OA roots 共 `2,136,779 B`；
- `deletion_performed=false`，远端证据按保留策略未删除；
- Bridge tunnel 已显式停止；
- 本地 `ssh/scp/tar/vda/WindowsTerminal/OpenConsole` 均为 0；
- Bridge 仓库为干净的 `codex/vda-transport-recovery`，本 Gate 未修改第三方代码。

所有 tunnel/worker 都使用隐藏启动，本轮未再弹出空白 PowerShell 窗口。

## 自动化测试

- 全量 Python：`734 passed`；
- `examples/tasks/*.json`：`196/196` 可 plan；
- `python -m compileall -q src tests`：通过；
- `vda catalog --json`：通过；
- `git diff --check`：通过。

新增负向测试覆盖：reference-free freeze、reference chronology、shortlist/content drift、
reference task/token/hash drift、未穷尽 reference、prospective shortlist 到普通 OA task 的
确定性交接，以及 `include_measured_anchor=false` 时排除所有历史实测候选。既有
retrospective `preview-select` 默认行为和所有正交 task operation 保持兼容。

## 证据分类与边界

- standalone/OA Spectre DC/OP/AC、gain/BW/GBW/power 和 raw manifests：`eda_result`；
- OA 结构/参数回读、`si` 参数一致性、Bridge/process/session inventory：`bridge_readback`；
- candidate generation、variant mapping、frozen shortlist、rank、Spearman、误差和耗时重建：
  `software_inference`；
- policy、阈值、task、top-k 和本次执行授权：`user_input`；
- candidate 6 timeout、SSH reset、失败恢复：`system_event`。

本 Gate 没有把 return code 0、OA 对象存在或 shortlist winner 单独表述为设计完成。当前只
证明一个 nominal TSMC N28 差分对局部域的 prospective 排名 utility。下一道有产品价值的
Gate 应把这套两阶段流程用于尚未校准的 active-load + source-degeneration 等结构，并在
正常设计中默认只执行冻结 shortlist 的 OA 真值复核；完整域改为周期性审计或 near-boundary
复核，而不是继续在本八点域增加随机测试。PVT 仍为可选项，不默认附加。
