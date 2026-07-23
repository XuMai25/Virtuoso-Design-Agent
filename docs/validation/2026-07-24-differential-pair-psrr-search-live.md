# 2026-07-24 差分对带限 PSRR 四点搜索 live 验证

日期：2026-07-24（run record 使用 UTC 文件时间 `20260723T...Z`）
状态：**read-only band-limited PSRR characterization and transport recovery verified; provisional 20 dB quality gate infeasible**。

## 目标与授权边界

- 目标：`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`
- PDK profile：`nics4304_tsmc28`
- OA 写入：否；`allow_remote_write=false`
- 远端计算：是；四个候选各使用一份自动 `si` 网表和差模/VDD/VSS 三次 Spectre AC
- 覆盖已有对象：否；`replace_existing=false`
- 搜索：`tail_bias_v=[0.30,0.32] V × load_ff=[0.5,2.0] fF`
- 远端任务产物：只写 `/data/xum` 下的唯一 VDA/Bridge scratch，不写 `/home/xum`

本 Gate 的 `20 dB` 不是用户应用规格，也不作为行业通用指标。它是明确标注的临时工程证伪门：在 `1 kHz–100 MHz` 内要求 `|Ad/Asupply| >= 10`，用一个不宽松的数量级门判断现有两个 testbench 旋钮是否值得继续搜索。最终产品仍须由目标应用给出频带、PSRR+/PSRR−、功耗、增益和摆幅规格。

## 新增契约

`ac_sweep.evaluation_stop_hz` 只允许用于差分对 `analysis: psrr`，并且必须位于 AC sweep 内。本任务继续扫到 `1 THz` 以保留曲线形状和首次下降 3 dB 交点，但规格使用新的带限指标：

```text
positive_minimum_psrr_db_in_band
negative_minimum_psrr_db_in_band
minimum_psrr_db_in_band
```

评估频带从 sweep start 开始，包含最后一个不高于 `evaluation_stop_hz` 的实际采样点。真实运行得到 101 点，范围为 `1 kHz–99.99999999999923 MHz`；run record 同时保存请求 stop、有效 stop、点数和定义。未声明该字段时不会生成带限指标，其他 analysis 若声明它会在任务校验阶段拒绝，避免接受但静默忽略。

每次 PSRR 候选还在本地临时结果删除前，对三份 Bridge 已下载的根 `ac.ac` 分别记录相对路径、字节数和 SHA-256。浅层选择规则避免 nested sweep 的同名文件覆盖根 analysis。该清单把解析指标绑定到确切 raw 文件，但当前仍不把完整 AC 波形复制进长期 artifact，因此不能脱离远端/原始运行离线重放全部波形。

## 执行与恢复

第一次运行完成候选 1–3，在候选 4 下载 VDD 注入 raw result 时 SSH timeout：

```text
artifacts/runs/differential-pair-current-mirror-psrr-bias-load-tune-bridge/run-20260723T172635Z.json
artifacts/runs/differential-pair-current-mirror-psrr-bias-load-tune-bridge/run-20260723T172635Z.checkpoint.json
```

checkpoint 为 `next_candidate_index=4`、三点完整、`pending_oa_parameters=null`。Bridge doctor 随后正常。第一次 resume 在重新执行候选 4 的 `si -batch` 时发生 `WinError 10054`，保留远端 netlist scratch：

```text
artifacts/runs/differential-pair-current-mirror-psrr-bias-load-tune-bridge/run-20260723T173555Z.json
/data/xum/virtuoso_bridge_smoke/vda_differential-pair-current-mirror-psrr-bias-load-tune-bridge_f90fdb85ff40
```

第二次 resume 先独立 probe 和 OA 回读，再只重跑候选 4，最终完成 4/4：

```text
artifacts/runs/differential-pair-current-mirror-psrr-bias-load-tune-bridge/run-20260723T173728Z.json
```

最终 `status=partial` 的原因是零个候选满足全部约束；四个候选的 `analysis_complete=true`、`analysis_issues=[]`。两个 transport 事件作为 `system_event` 保留，没有被包装成电路不可行，也没有重跑已确认的前三点。

## 规格和结果

共同护栏为：全部信号器件饱和、联合输出摆幅余量至少 `0.1 V`、低频差模增益至少 `3 V/V`、差模带宽至少 `100 MHz`、DC 供电功耗不高于 `20 µW`。四点全部通过这些护栏，只有临时 `minimum_psrr_db_in_band >= 20 dB` 失败。

| 候选 | BIAS (V) | CL (fF) | 1 kHz–100 MHz 最差 PSRR (dB) | PSRR+ LF (dB) | PSRR− LF (dB) | Ad (V/V) | BW (GHz) | Pdc (µW) | 摆幅余量 (V) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.30 | 0.5 | 11.4275 | 11.4331 | 13.1059 | 3.7068 | 2.2051 | 10.1457 | 0.2002 |
| 2 | 0.32 | 0.5 | 11.5125 | 11.5156 | 13.4535 | 3.7421 | 2.9756 | 14.2920 | 0.2131 |
| 3 | 0.30 | 2.0 | 11.4275 | 11.4331 | 13.1059 | 3.7068 | 1.3321 | 10.1457 | 0.2002 |
| 4 | 0.32 | 2.0 | 11.5125 | 11.5156 | 13.4535 | 3.7421 | 1.8005 | 14.2920 | 0.2131 |

`load_ff` 从 `0.5 fF` 增到 `2 fF` 对带内最差 PSRR 在数值精度内没有改善，只显著降低带宽/GBW。BIAS 从 `0.30 V` 增到 `0.32 V` 只把最差 PSRR 提高约 `0.085 dB`，同时把功耗从 `10.15 µW` 增至 `14.29 µW`。最佳观测值仍比临时门低 `8.49 dB`。因此继续细扫这两个 testbench 旋钮没有足够依据；这不是预算不足造成的“可能漏掉一点”，而是该小网格已经证伪它们能带来数量级改善的假设。

executor 没有 `selected_parameters` 或 `selected_metrics`，并明确记录 `no feasible testbench condition was selected; OA parameters remained unchanged`。

## 同源与 raw 证据

四个候选的 OA topology/W/L 与自动网表均为 `matched`，网表 SHA-256 全部相同：

```text
bcd59efe00b8c3d5e51c90b0ae262714f2b22b659ad503d98e5b1ca625557535
```

每点三次 AC 的频率网格与 DC OP 均为 `matched`。12 份非空根 AC 文件清单如下：

| 候选 | differential `ac.ac` | VDD `ac.ac` | VSS `ac.ac` |
| ---: | --- | --- | --- |
| 1 | 137639 B, `fafe46077ed12e8fa70998fcf0069078364396e1889037e5810e6c8672a5510e` | 137021 B, `8c5a9d2b0493fece0355aa67bd0e93ff8529c2a12b4096fe64b738a779352002` | 137167 B, `ee16c9cdfa1ce82f870ecfe0f40df6c9b6acd404acaa4836d48c0c4fbccd43b8` |
| 2 | 137670 B, `f0e6e651a3428aaca1111e32bca468008a09686aa26e95f1ce59b4d40f4adf9a` | 137072 B, `2c44e78ed0e50571cedfb80a3a77535ac407e5d2c9b2a6450e41441a81423254` | 137169 B, `2b93f9098d27c974ee04a1a2ab29717dbe51fa87866507402895b5555215f3e9` |
| 3 | 137696 B, `9a475ab9a129586b5fe14a0cb1aacae5c4ccb41876bd6ff49f02f422720ac1c4` | 137021 B, `b72d166cbd973b41666159aa7e6292ed1395168254282d5af12982aa4e9fd760` | 137168 B, `6c4b0981191b8172fc26f978d3d4198d1e653c7e90553903ef98a1e62d02c3d7` |
| 4 | 137738 B, `6f01cc6d609a3f7d017ebb3bf7c8b1d338d38c0fd6f1332a1a1f2ea1f705ea14` | 137069 B, `a68bc894396b7bef9c1a5939977ef2a9b64256d1dfe896be1c2258c3f41ea569` | 137170 B, `787fcaa2320a48a647b94e8e2647dab98408a1fb7bc5a680287458c07cbed276` |

最终 `schematic.inspect.after` 与开始回读在 instances、nets、pins、semantic parameters、NMOS geometry、MNTAIL geometry、PMOS mirror geometry 和 topology variant 上逐项相同。没有 OA 写入，也没有覆盖 cellview。

## 证据分类

- OA 前后结构/参数：`bridge_readback`
- `si` 网表、DC OP、supply gain、三组 AC 文件大小与哈希：`eda_result`
- PSRR 比值、带限最差值、3 dB 交点、网格/DC/约束判断：`software_inference`
- 原任务已有的 analysis、全 sweep 和搜索网格：显式任务字段；run record 按 `user_input` 保留
- `1 kHz–100 MHz` 评估频带与临时 `20 dB` 门：本轮 VDA 选择并公开的 `software_inference`/工程假设，不是用户产品规格
- SSH timeout 与 `WinError 10054`：`system_event`

## 本地验证与结论

实现后完整回归：

```text
457 passed in 1.46s
python -m compileall -q src tests passed
138/138 example task plans passed
vda catalog passed
task plan token: 410e2c5888294a32
git diff --check passed
```

`virtuoso-bridge-lite` 没有修改。本 Gate 可以称为：

> **Gate 6Q read-only band-limited PSRR search and recovery verified; bias/load-only closure falsified on the declared grid.**

不能称为 PSRR 产品规格闭合，也不能把 `20 dB` 写成最终规格。下一道设计 Gate 应转向 OA 器件几何和偏置参考/供电隔离结构，而不是继续密扫 `load_ff`。任何真实 W/L 或拓扑写入仍需新的明确 OA 写授权、候选边界、checkpoint 和最终回读；PVT、mismatch/Monte Carlo、slew/settling 与 ADE/Maestro PSRR handoff 仍未验证。
