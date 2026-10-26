# 2026-07-26 共源级新 anchor W/RD 二维刷新 live Gate

## 结论

已在 `vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic` 完成六个原子 W/RD tuple 的
OA→`si`→Spectre AC/transient/noise 同源执行。六点全部满足完整 quality constraints；
模型与真实 EDA 都选择：

- `W=1.1 µm`
- `RD=18.5 kΩ`
- `RS=750 Ω`（本轮固定）
- gain=`3.87961 V/V`
- bandwidth=`8.93269 GHz`
- GBW=`34.65534 GHz`
- P1dB=`79.9737 mV peak`
- THD=`14.8581%`
- input-referred integrated noise=`914.032 µV RMS`
- DC power=`25.3244 µW`
- output swing margin=`0.259725 V`

exact post-run validator 的三个 Gate 均通过：

- candidate execution：通过，6/6 完成且声明域穷尽
- recommendation agreement：通过，预测与 EDA 都是 `op-local-002`
- prediction accuracy：通过，60/60 个逐点指标比较均低于原门限

状态升级为：

**held-out-covered common-source W/RD local response to same-source EDA selection verified at nominal top_tt**

这仍只是固定 RS 的声明离散小域，不是连续或全局最优，也不证明 RS/PVT 方向已校准。

## 授权与预检

用户在列明以下范围后明确确认执行：

- target：`vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic`
- OA 写入：逐候选暂存，真实最佳点写回；失败恢复 preflight 基线
- 远端计算：逐候选自动 `si` + Spectre AC/transient/noise
- scratch：
  `/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-op-refresh-2d-next_<nonce>/`
- 覆盖：`replace_existing=false`，不替换 cellview
- PVT：不执行
- plan token：`ac7b19632251a835`

执行前独立 `schematic.inspect` 回读：

- `MN0.Wfg=1.1u`、`L=30n`
- `RD0.r=19K`
- `RS0.r=750`
- `MN0.S=NSRC`，`RS0(NSRC,VSS)`
- nets=`IN/NSRC/OUT/VDD/VSS`
- pins=`IN/OUT/VDD/VSS`

因此当前 OA 与生成 task 的 anchor 完全一致。执行前远端资源为
`Spectre=0/si=0/Virtuoso=2/VDA-managed Maestro=0`。

## 中断与恢复

本轮暴露了两次独立 transport failure，均保留为 `system_event`，没有归为电路不可行：

1. 首次 candidate 1 在下载 `si.env` 时本机 DNS/SCP 失败。没有候选指标；checkpoint 为
   `next_candidate_index=1`、`candidates=[]`、`pending=null`。执行器恢复 anchor，独立
   OA inspect 再次确认 `1.1 µm/19 kΩ/750 Ω` 后才从 index 1 重跑。
2. 第一次 resume 完成 candidates 1–3 后，candidate 4 的 `parameters.stage` 在
   `read_schematic` 遇到 `WinError 10054`。checkpoint 为 index 4、3 个完整候选、
   `pending=null`；恢复动作成功。独立 OA inspect 与资源审计确认 anchor、
   `Spectre=0/si=0` 后，第二次 resume 只执行 candidates 4–6。

最终 checkpoint 为 `complete=true`、`next_candidate_index=7`、6 个候选，expected OA 是
最终选择 `1.1 µm/18.5 kΩ/750 Ω`。任何不确定 payload 都没有自动重放，前三点也没有在
第二次 resume 中重算。

## 六点 EDA 结果

| ID | W (µm) | RD (kΩ) | gain | BW (GHz) | GBW (GHz) | P1dB (mV) | THD (%) | noise (µV) | power (µW) | swing (V) | 最大预测误差 (%) |
| --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| op-local-001 | 1.10 | 19.0 | 3.913232 | 8.789795 | 34.396502 | 78.080393 | 15.425102 | 913.063846 | 25.070720 | 0.251286 | 0.000000 |
| **op-local-002** | **1.10** | **18.5** | **3.879611** | **8.932686** | **34.655344** | **79.973686** | **14.858068** | **914.032321** | **25.324412** | **0.259725** | **0.263307** |
| op-local-003 | 1.05 | 18.5 | 3.850997 | 8.981926 | 34.589368 | 82.155235 | 14.208008 | 930.960985 | 24.815060 | 0.270847 | 0.255658 |
| op-local-004 | 1.05 | 19.0 | 3.886382 | 8.840040 | 34.355770 | 80.145880 | 14.757562 | 929.876463 | 24.570947 | 0.262485 | 0.145487 |
| op-local-005 | 1.10 | 19.5 | 3.944371 | 8.655512 | 34.140553 | 76.335719 | 15.985995 | 912.182389 | 24.821316 | 0.243031 | 0.081163 |
| op-local-006 | 1.05 | 19.5 | 3.919379 | 8.700988 | 34.102467 | 78.302797 | 15.303193 | 928.877599 | 24.330936 | 0.254300 | **0.353712** |

六点 `analysis_complete=true`、quality constraints 全部可行。EDA 在完整声明域中按 GBW
选择 candidate 2；`search_audit` 保存：

- declared/attempted/completed=`6/6/6`
- `domain_exhausted=true`
- `selection_scope=best_in_declared_discrete_domain`
- `continuous_optimum_claim=false`
- `global_optimum_claim=false`

相对已测 anchor，最终点的变化为：

- gain：`-0.858%`
- bandwidth：`+1.626%`
- GBW：`+0.752%`
- P1dB：`+2.425%`
- THD：`-3.676%`
- noise：`+0.106%`
- DC power：`+1.012%`
- output swing：`+3.358%`

提升幅度不大，但本 Gate 的主要价值是证明小域模型能正确排序且逐点数值通过新数据检验，
不是为了把 `0.752%` 包装成重大设计突破。

## 同源网表与分析证据

每个候选只生成并复用一份验证过的 OA `si` 网表，随后运行 AC、四档 coherent transient
linearity sweep 和 ordinary noise。六点均为：

- `parameter_consistency=matched`
- `shared_metric_consistency=matched`
- `oa_netlist_reuse=one_verified_netlist`
- AC/transient/noise completion=`true/true/true`
- Spectre=`21.1.0.612.isr15`

| candidate | remote suffix | netlist SHA-256 |
| --- | --- | --- |
| 1 | `395a5243bb8c` | `a9d1c7251ff53b56a7ee003f8d976efc24f2d4a81073e22f223259b18dd6df19` |
| 2 | `fb89fe7c6e2e` | `3fd2c3acc669b738ac608c25938042a8b306e4287067cdd76c4a2f7b78f568a0` |
| 3 | `a8a27bc9c2da` | `7c86cd5f921c6043ea88ef8f7bd995d345a1d093c11777f18f9098db0d5860f6` |
| 4 | `b12e6730df29` | `15ff81916b2e75c276b225bf2c0b990d2497076ad765cba7428ad0d75183b84e` |
| 5 | `781e07597582` | `0c1c2be4391d50d0759005539e30210c60d03f86f862ad59e2b490e3ef412054` |
| 6 | `52d394d85ce7` | `16400949aa8885a0ddfbe1c8d24a527fcfdb023d1ebdad6e3e3fc3b5d7d6131e` |

六份网表 hash 唯一，且 OA W/RD/RS 均真正进入相应网表；没有回到手写 deck 双源路径。

## 预测审计

执行：

```powershell
.\.venv\Scripts\vda.exe op-relinearization-validate `
  artifacts\relinearization\common-source-op-refresh-2d.json `
  artifacts\relinearization\common-source-op-refresh-2d-task.json `
  artifacts\runs\common-source-quality-op-refresh-2d-next\live-resume2-20260726.json `
  --output artifacts\relinearization\common-source-op-refresh-2d-live-validation.json
```

validator 核对 exact result/task/run SHA、plan token、候选顺序/tuple/预测、完整域、real
Bridge adapter 与每项指标来源。结果：

- predicted recommendation=`op-local-002`
- EDA selected=`op-local-002`
- 6/6 EDA feasible
- 60/60 prediction comparisons passed
- 新点最坏误差=`0.353712%`，candidate 6 的 P1dB
- candidate 2 的 GBW 预测误差=`0.023596%`
- candidate 2 的 output swing 预测误差=`0.105176%`

anchor candidate 1 是已测控制点，误差为零不计作新泛化证据；真正的新点精度来自
candidates 2–6。

## 最佳写回与证据源

run 完成后的独立 `schematic.inspect` 回读：

```text
W=1.1 µm, L=0.03 µm, RD=18.5 kΩ, RS=750 Ω
```

与 `selected_parameters` 和 checkpoint expected OA 完全一致。

- 用户授权、target、constraints 和 plan token：`user_input`
- OA 结构/参数写入与独立回读：`bridge_readback`
- `si` netlist、Spectre 波形/指标：`eda_result`
- candidate source、局部预测、参数覆盖、排序/误差审计：`software_inference`
- DNS/SCP 与 `WinError 10054`：`system_event`
- `saturation_region` 仍是由真实 OP 判定得到的 `software_inference`；其余每点 55 个指标
  为 `eda_result`

没有用 return code、OA 对象存在或命令成功单独宣称设计完成。

## 产物与资源

- final run SHA-256：
  `258e9deaeea2e4081a7e661d8a70544b60ad5afc8778bb2d21f7bc422e397a45`
- complete checkpoint SHA-256：
  `5404bfc80585222102932f991eb9da99ff12c6f4aae24f0117bcf5d77c6823f0`
- post-run validation SHA-256：
  `a793eb76e004f0b92986fd6519eab5adecb5424248a32f574913792549503730`
- final independent OA inspect SHA-256：
  `0f736018b0e2f91fda502fedd789e145c5b59e2e88c4b03f51d7716b95f4be9f`
- first failed run SHA-256：
  `226a2df6fea221d1699a0451d7085b93faacccaef92ad4abe7139fd2ddddd9f8`
- second failed/resumed run SHA-256：
  `6784bffe829f4860ce0c7bf965c8c94d53a0ff2553a11bb38541195413d5827f`

运行后远端仍为 `Spectre=0/si=0/Virtuoso=2/VDA-managed Maestro=0`，本地没有 VDA
Python、Spectre 或 `si` 进程。SSH tunnel 在 transport 自愈后 PID 更新，但始终保持两条，
没有数量增长。远端新增 7 个 retained evidence 目录：一次 1182-byte 失败 scratch 和六份
约 153 kB 的完整候选证据。未执行删除；它们是可审计产物，不是运行中的临时进程。

Bridge 仓库 `codex/vda-transport-recovery@e74379a` 保持干净，本轮没有修改第三方库；
Obsidian Vault 未修改。

## 本地回归

- Python 全量测试：`606 passed`
- `tests/test_cli.py`：`5 passed`
- `examples/tasks/*.json`：`155/155` plan passed
- `compileall`：通过
- `vda catalog`：通过，并报告
  `Gate 10 held-out-covered W/RD local-response EDA validation verified`
- `inverter-close-loop.demo.json`：plan 通过，token=`d952f73ecaa92e3b`
- exact `op-relinearization-validate`：返回 0，`gate_passed=true`
- `git diff --check`：通过

## 未闭合边界与下一 Gate

- 本轮是 nominal `top_tt`，PVT 按约定保持可选且未运行。
- 已验证的是固定 `RS=750 Ω` 的 W/RD 小域。继续调整 RS 前仍需一个独立 RS heldout
  探针；不能从二维通过外推三维精度。
- 该结果不是连续/全局最优，也没有补齐 mismatch/Monte Carlo、输出驱动、slew/settling
  或 ADE 人工路径。
- 共源局部模型在本域已经从 shortlist utility 升级为逐点 live prediction verified；下一道
  更有增量价值的 Gate 是差分对六点工作点重线性化 live，执行前仍需独立 OA 基线和明确
  写入范围。差分对 PVT 继续保持可选。
