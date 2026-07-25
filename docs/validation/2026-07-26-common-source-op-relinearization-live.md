# 2026-07-26 共源工作点重线性化六点真实 Gate

## 结论

本 Gate 把既有真实 W/RD/RS 工作点拟合出的 6 个原子候选送入正式
`design.tune`，完成了逐候选 OA 写入/回读、Cadence `si -batch` 自动网表、Spectre
AC + transient linearity + noise、完整规格判断、checkpoint/resume、EDA 选优、最佳
参数写回和独立 OA 回读。

6/6 候选均完成且通过 full-quality constraints。局部模型和真实 EDA 都把
`op-local-002` 排为 GBW objective 第一名；最终 OA 为：

```text
W = 1.1 µm
L = 0.03 µm
RD = 19 kΩ
RS = 0.75 kΩ
```

因此候选生成到真实同源选优链可以称为：

> **common-source atomic local-response shortlist to same-source EDA selection verified**

但逐点数值精度 Gate 为 `partial`：候选 5 的输出摆幅预测误差
`22.479% > 20%`。候选执行与最终选择成功不等于所有局部预测都过门，不能称为已完全
校准的连续优化器或连续/全局最优。

## 目标、授权与预检

- target：`vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic`
- profile：`nics4304_tsmc28`，model section `top_tt`
- OA write：只修改已有 `MN0/RD0/RS0` 参数；不新建或替换 cellview
- remote compute：每候选一份 OA/`si` 网表，随后运行 AC、4 幅度 transient 和 noise
- `replace_existing=false`
- plan token：`159e61466c22a6ed`
- task SHA-256：
  `ac9cfa0aa9f625db51c599dcaec0782892219d8bc80855f6f010404effbea8d4`
- 第三方 Bridge：`codex/vda-transport-recovery@e74379a`，工作树干净；本轮未修改

独立只读 preflight 没有假定历史 anchor 仍在 OA。实际回读为
`W=1 µm/L=30 nm/RD=20 kΩ/RS=2 kΩ`，这是本次失败恢复基线；模型 anchor
`1 µm/20 kΩ/1 kΩ` 只是候选 1，不得冒充搜索前 OA 状态。

## Transport 失败与恢复

首次候选 1 已写入并回读，但生成 `si.env` 后，本地 `scp` 在受限网络上下文中无法解析
`nics4304-cad1`。该 action 记录为 `system_event`，没有候选指标：

```text
simulation.candidate.1 = failed / system_event
```

执行器随后完成 `parameters.restore.interrupted`。checkpoint 为
`next_candidate_index=1`、`candidates=[]`、`pending_oa_parameters=null`，独立 OA inspect
再次确认恢复为 `1 µm/20 kΩ/2 kΩ`。在可用的网络上下文中用同一 task/token/checkpoint
恢复后，从候选 1 重跑，没有伪造或跳过该候选。最终 run 同时保留失败 action、恢复 action
和成功 action。

- 失败 scratch：
  `/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-op-relinearized-next_305ee6ba5722`
  （1182 bytes）
- 最终 run：
  `artifacts/runs/common-source-quality-op-relinearized-next/live-resume1-20260726.json`
- run SHA-256：
  `f6d5c98acb7b14f7d2c8407c5e8b3a6c281f3e3945e0e7a19eea0494d05bb635`
- complete checkpoint SHA-256：
  `441eb555b5c338284d65a6d4e3a0c8a2e11e7458d3257e794df8937d48c8096e`

## 六点真实结果

固定条件为 bias=0.35 V、VDD=0.9 V、CL=1 fF、L=30 nm。全部候选的 saturation、
摆幅、两项 KCL mismatch、gain、BW、P1dB、THD、输入参考积分噪声和 DC 功耗约束均
通过。

| # | ID | W (µm) | RD (kΩ) | RS (kΩ) | Gain | BW (GHz) | GBW (GHz) | P1dB (mVpk) | THD (%) | Noise (µVrms) | Power (µW) |
|---:|---|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | op-local-001 | 1.0 | 20 | 1.00 | 3.7006 | 8.1849 | 30.2890 | 88.318 | 13.161 | 983.071 | 22.337 |
| 2 | op-local-002 | 1.1 | 19 | 0.75 | 3.9132 | 8.7898 | **34.3965** | 78.080 | 15.425 | 913.064 | 25.071 |
| 3 | op-local-003 | 1.1 | 20 | 0.75 | 3.9731 | 8.5312 | 33.8952 | 74.715 | 16.540 | 911.385 | 24.576 |
| 4 | op-local-004 | 1.0 | 19 | 0.75 | 3.8560 | 8.8926 | 34.2899 | 82.445 | 14.084 | 948.011 | 24.052 |
| 5 | op-local-005 | 1.1 | 21 | 0.75 | 4.0235 | 8.3087 | 33.4300 | 71.757 | 17.623 | 910.036 | 24.098 |
| 6 | op-local-006 | 1.0 | 20 | 0.75 | 3.9233 | 8.6153 | 33.8001 | 78.678 | 15.135 | 945.856 | 23.595 |

相对已测 anchor，最终选择的 GBW 提高 `13.561%`，gain/BW 分别提高
`5.746%/7.390%`；代价是 P1dB 降低 `11.592%`、THD 增加 `17.201%`、DC 功耗增加
`12.238%`、摆幅余量降低 `11.106%`。输入参考积分噪声降低 `7.121%`。这些代价仍在
本任务明确规格内；不同 objective 或更紧 guard 可能选择其他点。

## OA、网表和 analysis 一致性

六个候选各有不同的 `si` netlist SHA-256；每点均为
`parameter_consistency=matched`、`shared_metric_consistency=matched`，AC/transient/noise
completion 全为 true，并在三项分析中复用同一份已核对网表。

| # | remote run suffix | netlist SHA-256 |
|---:|---|---|
| 1 | `f1b1db1cbb05` | `2c529799349ac466606367015a038e22c4ab7048b8b21e89092146515de7b232` |
| 2 | `9841866d396b` | `a9d1c7251ff53b56a7ee003f8d976efc24f2d4a81073e22f223259b18dd6df19` |
| 3 | `b7d1b48fb461` | `04350abddb001844f9275df7d4df1d968683870548266002056445e886e7d6ad` |
| 4 | `faf6c78a89bd` | `882a3612d37867910e538cf38daec58b2a0784468b15047fff12c4fbcbcee938` |
| 5 | `1d7a10ffdc2f` | `700b0f848b97e3a8c412182753c7bd22380b636800f4ca939e5ff1fe1dec5bdb` |
| 6 | `90c4fc4b5abc` | `482041be4115a8a2b24c66c3b36fe8b559287af39bc80ccf85cd3e62a4b078fa` |

`parameters.apply.best` 与独立的 post-success `schematic.inspect` 都回读
`1.1 µm/19 kΩ/0.75 kΩ`。后者 SHA-256 为
`ae6f52915f080a04ce31e91a578f618eebd27d10862be67521c33cea94b1fadc`。

## 自动事后预测审计

新增只读命令：

```powershell
.\.venv\Scripts\vda.exe op-relinearization-validate `
  artifacts\relinearization\common-source-op-relinearization.json `
  artifacts\relinearization\common-source-op-relinearized-task.json `
  artifacts\runs\common-source-quality-op-relinearized-next\live-resume1-20260726.json `
  --policy examples\theory\common-source-op-relinearization-policy.json `
  --output artifacts\relinearization\common-source-op-relinearization-live-validation.json
```

该历史 result 生成于 `relative_error_floor` 被逐 metric 序列化之前，因此新版 validator
要求 `--policy` 指向 result 已由 canonical SHA 绑定的原 policy；它不会用 schema 默认
floor 猜测旧运行的误差定义。该参数只恢复运行前已有的归一化定义，不改变 20% 门限。

它核对 result/task/run SHA、plan token、原子候选 source/ID/顺序/tuple/预测、完整离散域、
真实 Bridge adapter 和每项测量的 `eda_result` 来源；然后用原模型保存的 held-out error
limit 比较所有新点。当前结果：

- candidate execution Gate：passed，6/6 完整，6/6 EDA feasible
- recommendation agreement：passed，预测与 EDA 都选 `op-local-002`
- OP 指标最大误差：gds `5.156% < 15%`
- GBW 最大误差：`4.505% < 20%`
- 唯一失败：候选 5 output swing `22.479% > 20%`
- overall：`partial`，命令按设计返回 1
- validation SHA-256：
  `926c9a0b6490b7c129e05054e2ebb784ae8af43df9bd25d1d4bd9fa355c5ef0d`

这条命令不重新仿真、不写 OA。预测值是 `software_inference`，实际 OP/AC/transient/noise
指标是 `eda_result`，OA 写入和结构回读是 `bridge_readback`，传输失败是 `system_event`，
任务规格和授权是 `user_input`。

## 资源收尾

远端只读资源盘点前后为：

```text
spectre:  0 -> 0
si:       0 -> 0
virtuoso: 2 -> 2  (既有 Cadence/Bridge 基线)
VDA-managed Maestro sessions: 0 -> 0
```

本地没有遗留 VDA Python、Spectre 或 `si` 进程；两条执行前已存在的 SSH tunnel PID 保持
不变。新增 7 个远端目录是 1 份失败审计和 6 份保留的候选证据，不是仍在运行的临时
进程。post inventory 使用 Bridge public SSH，`direct_ssh_transport_error=null`，未执行
删除。

## 未闭合边界与下一 Gate

- 本 Gate 是 nominal `top_tt`；PVT 仍是显式可选项，没有默认增加本轮成本。
- 原模型对 GBW 排序有用且找到真实最佳点，但 output swing 已证明存在局部非线性。下一轮
  若继续共源细化，应以新 EDA 点重新定 anchor/heldout 或缩小对应 trust region，不能放宽
  20% 门来追求绿色结果。
- 本次没有跑全不可行的新 `candidate_set` 任务；既有 executor 已有真实全不可行恢复证据，
  但新原子来源的相同 live 组合尚未单独重复。
- semantic 原子 tuple 已 live；包含 raw CDF 的混合原子 tuple 仍只有本地执行测试。
- 差分对的 6 点工作点重线性化 task 仍未 live。其 target、OA 写入、远端计算、当前 OA
  基线和覆盖风险必须在执行前重新列出；不能从共源结果外推。

后续本地刷新已在不连接 Bridge 的条件下完成：三维 W/RD/RS 因 heldout 没有 RS 扰动而
拒绝，固定 RS 后的 W/RD 小域通过历史留出并生成待执行六点。见
[`2026-07-26-common-source-op-refresh-2d-local.md`](2026-07-26-common-source-op-refresh-2d-local.md)。
