# 2026-07-23 共源可选 PVT-aware 调优 live Gate

状态：**optional PVT-aware common-source testbench tuning verified; live OA-design-variable PVT writeback pending**。

## 目的与边界

本 Gate 验证 `operating_conditions` 可以显式附加到 common-source
`design.tune`，但不会成为普通调优的默认成本。未声明该字段时，既有单条件
任务、计划 token 和执行路径保持不变。

本次真实任务只搜索 testbench 偏置，不写 OA：

- target：`vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic`；
- 固定 OA：W=1 µm、L=0.03 µm、RD=20 kΩ、RS=2 kΩ；
- 固定负载：1 fF；
- 搜索：`bias_v=[0.35,0.40] V`；
- 条件：TT/25℃/0.90V、SS/125℃/0.81V、FF/−40℃/0.99V；
- 每个候选：AC + coherent transient linearity + ordinary noise；
- OA write：false；remote compute：true；replace existing：false。

因此本记录证明可选跨 PVT 候选评估和保守选优，不把本地测试中的 OA
设计变量写回包装成 live 证据，也不代表完整 foundry signoff corner set。

## 契约改动

`operating_conditions` 现在允许用于 common-source `simulation.run`、
`design.tune` 和 `design.close_loop`：

1. 每个候选最多跨五个显式条件；
2. 含 OA 设计变量时，每候选只暂存一次 OA，然后生成一份已核对 `si`
   网表并跨条件复用；
3. 只有完整 condition bundle 才能进入 candidate/checkpoint；
4. 每个条件都必须 analysis complete 且满足全部 constraints；
5. maximize objective 取各条件最小值，minimize 取最大值；
6. 逐条件原始指标保持 `eda_result`，跨条件聚合保持
   `software_inference`；
7. 逐条件 VDD 与 task/搜索空间 `vdd_v` 互斥；若条件都省略 VDD，则共同
   继承当前候选 VDD；
8. 不声明 `operating_conditions` 时不自动增加任何 PVT 执行。

本地确定性测试另外覆盖了：鲁棒最优不同于 nominal 最优时的 OA 最佳
写回、全不可行恢复初始 OA、预算只在 evaluated prefix 内选优、完整候选
checkpoint 续跑，以及逐条件设计参数漂移拒绝。这些测试证明编排语义，
不构成真实 EDA/OA 写回证据。

最终本地回归：`373 passed`；全部 `74/74` example plans 可生成。

## 计划与恢复

- plan token：`b82c02acc2a51fba`；
- 首次记录：
  `artifacts/runs/common-source-quality-pvt-bias-tune/live-20260723.json`；
- checkpoint：
  `artifacts/runs/common-source-quality-pvt-bias-tune/live-20260723.checkpoint.json`；
- 完成记录：
  `artifacts/runs/common-source-quality-pvt-bias-tune/live-resume1-20260723.json`。

首次受限进程在候选 1 的 `si.env` 下载处失败：本机 `scp` 无法解析
`nics4304-cad1`。Bridge doctor 和同次 OA readback 均成功，错误发生在
Spectre 之前，因此标为 `system_event`，不能解释成候选不可行。使用正常
用户 SSH 配置从同一 checkpoint 恢复后完成；最终 run 保留该失败 action
和 note。恢复前尚无完整候选，所以这是边界恢复证据，不是
completed-prefix 不重算的 live 证明。

## 真实结果

两个候选各完成 3 个 condition × 3 个 analysis，共 18 项；所有 analysis
均 complete，`analysis_issues=[]`、`analysis_warnings=[]`。

| bias | 三条件可行 | 最坏 GBW | 最坏 gain | 最坏 THD | 最坏 P1dB | 最坏输入噪声 | 最大 DC 功耗 |
|---:|:---:|---:|---:|---:|---:|---:|---:|
| 0.35 V | 是 | 18.696 GHz | 2.649 V/V | 12.183% | 97.56 mV peak | 1348.73 µV RMS | 22.276 µW |
| 0.40 V | 否 | 19.113 GHz | 2.292 V/V | 24.222% | 63.92 mV peak | 1372.22 µV RMS | 30.229 µW |

`0.40 V` 虽有更高最坏 GBW，但被完整规格门拒绝：

- TT：THD 16.681% > 15%；
- SS：摆幅余量 0.04342 V < 0.05 V，THD 24.222% > 15%；
- FF：THD 15.604% > 15%，DC 功耗 30.229 µW > 30 µW。

因此最终选择 `bias_v=0.35 V`，不是选择单一目标最大的不可行点。

## 同源与无写入证据

候选 1 远端根：
`/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-pvt-bias-tune_d889a0bc5a12/`。

候选 2 远端根：
`/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-pvt-bias-tune_488f1ee0fade/`。

每个候选的三个条件只出现一个 netlist path，且两个候选的设计未改变，
所以网表 SHA-256 都是：
`cdddb1f631d4baaec731c6c4567077ea2c330be5efdbc9156e6f02289e8d91f4`。
每个 condition 的 wrapper 仍分别绑定角名、温度、VDD、profile model
sections 和独立 SHA。

run record 中 `parameters.*` action 数为 0。独立前后 OA semantic readback
均为：

```text
W=1.0 µm, L=0.03 µm, RD=20000 Ω, RS=2000 Ω
```

所以本次选择只记录 testbench bias，没有把偏置伪装成 OA 属性，也没有
覆盖现有 cellview。

## 证据来源

- `user_input`：两个 bias 候选、三组角/温度/VDD、sweep、constraints 和
  objective；
- `bridge_readback`：OA 实例、网络、pins、W/L/RD/RS 以及最终独立回读；
- `eda_result`：逐条件 Spectre DC/AC/transient/noise 波形与标量、wrapper、
  PSF 和网表文件哈希；
- `software_inference`：analysis bundle 完整性、约束计算、跨条件最坏值和
  最终候选排序；
- `system_event`：首次受限身份的 `scp`/DNS 失败。

## 尚未验证

1. 真实 OA 设计变量 W/L/RD/RS 的跨 PVT 最佳写回；
2. 跨 PVT 全不可行后的 live OA 恢复；
3. 跨 PVT 预算耗尽和 completed-prefix transport resume 的 live 路径；
4. foundry 全部 signoff corners、Monte Carlo/mismatch、PEX/layout；
5. Maestro/ADE 真实 process/temperature corner 和人工打开/修改/重跑。

以上 1–3 已有本地编排测试，但是否运行 live 应由任务显式选择；它们不再
阻塞默认 nominal 工作流进入下一拓扑。
