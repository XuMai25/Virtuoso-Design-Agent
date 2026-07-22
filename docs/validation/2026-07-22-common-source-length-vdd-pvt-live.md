# 2026-07-22 共源 L/VDD 质量调优与真实 PVT Gate

状态：**bounded common-source L/VDD quality tuning and fixed-design PVT verification verified; PVT-aware design tuning pending**。

本 Gate 在既有源极退化共源级
`vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic` 上完成两件事：

1. `L=[0.03,0.04] µm × VDD=[0.8,0.9] V` 四点
   `analysis: quality` 搜索，L 逐候选写 OA，VDD 只进入 testbench；
2. 对最终 OA 只读运行 TT/25℃/0.90V、SS/125℃/0.81V、
   FF/−40℃/0.99V 三个显式条件，每个条件执行 AC、相干 transient
   线性度和 ordinary noise。

这不是 ADE/Maestro corner，也不是完整 foundry signoff。它证明 direct
`OA -> si -> Spectre -> metrics -> constraints` 路径已经能在有限真实
process/temperature/supply 条件上保留逐条件证据并做最坏值判定。

## 范围与安全

- PDK profile：`nics4304_tsmc28`，TSMC N28/`tsmcN28`；
- L/VDD 任务允许远端计算和目标 common-source OA 参数写入；
- PVT 任务 `allow_remote_write: false`，只有远端计算；
- `replace_existing: false`，没有创建、删除或替换 cellview；
- 远端产物全部位于 `/data/xum/virtuoso_bridge_smoke`；
- 没有读取或记录密码、license 内容、`.env` 或模型文件正文；
- `virtuoso-bridge-lite` 第三方仓库没有修改、没有提交，也没有复制其
  CDF callback、`si`、Spectre、SSH 或文件传输实现。

任务与 token：

```text
examples/tasks/common-source-quality-length-vdd-tune.bridge.json
plan token = 5161070a31be676f

examples/tasks/common-source-quality-pvt-verify.bridge.json
plan token = 410a6f092fc7361a
```

## 新增契约

`operating_conditions` 当前是 common-source `simulation.run` 的有限验证
集合，每项要求唯一名称、profile 已映射的 `process_corner`、温度和可选
VDD。worker 只做一次 OA readback 和一次 `si` netlist，再跨条件复用。

executor 保留每个条件的完整指标、来源、analysis completion 和逐条约束：

- 任一条件缺失、顺序/值漂移、analysis 不完整或不满足规格，整个候选不通过；
- maximize objective 取各条件最小值，minimize objective 取最大值；
- 原始连续指标保持 `eda_result`；
- 跨条件 constraint/objective 聚合标为 `software_inference`；
- 当前拒绝把该字段与 `design.tune`/`design.close_loop` 组合，避免把固定
  设计验证提前包装成鲁棒 PVT 优化。

profile 对 TT/SS/FF 各显式映射四类 section：MOS/MOSCAP、
res/bip/dio/disres、MOM 和 metal resistor。每个 wrapper 的 evidence 现在
直接保存 profile、process corner、温度、四个 include path/section、来源
和 wrapper SHA-256，不能只凭角名猜测实际模型输入。

## L/VDD 四点真实搜索

固定参数是 W=1 µm、RD=20 kΩ、RS=2 kΩ、bias=0.35 V、load=1 fF。
四个候选都完成一份 OA/`si` 网表加三个 Spectre wrapper，均无
`analysis_issues` 且通过十条质量约束。

| 候选 | L (µm) | VDD (V) | gain (dB) | BW (GHz) | GBW (GHz) | THD (%) | P1dB (mV peak) | 输入噪声 (µV RMS) | DC 功耗 (µW) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| 1 | 0.03 | 0.8 | 9.085 | 7.246 | 20.622 | 9.579 | 109.14 | 1137.6 | 15.386 |
| 2 | 0.03 | 0.9 | 9.450 | 7.214 | 21.414 | 7.643 | 121.23 | 1130.8 | 18.479 |
| 3 | 0.04 | 0.8 | 8.826 | 6.526 | 18.030 | 11.923 | 118.44 | 1147.8 | 11.152 |
| 4 | 0.04 | 0.9 | 9.158 | 6.524 | 18.725 | 12.288 | 129.62 | 1134.4 | 13.320 |

GBW objective 选择候选 2。最终动作只把 OA 设计参数子集
`W=1 µm/L=0.03 µm/RD=20 kΩ/RS=2 kΩ` 写回并回读；VDD 没有被包装成
OA 属性。独立 `schematic.inspect.after` 再次得到相同参数和
`source_degenerated_common_source` topology。

同一 L 的两个 VDD 点具有相同结构 netlist SHA；两种 L 的 SHA 不同：

```text
L=0.03 µm: cdddb1f631d4baaec731c6c4567077ea2c330be5efdbc9156e6f02289e8d91f4
L=0.04 µm: e09c8bf0f84387af7408907dec7bb5077d913a0fc4721ae1bac4d6f2c5891015
```

四个候选分别位于：

```text
/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-length-vdd-tune_2e2b72f224c7
/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-length-vdd-tune_752e7073f872
/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-length-vdd-tune_16c2fad36d9e
/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-length-vdd-tune_57c199fa557d
```

每个目录都有三个唯一 wrapper SHA、完整 AC/transient/noise 结果和独立
noise PSF 路径。

## 首轮错误聚焦、恢复和旁路修复

首轮在 `parameters.stage.1` 以
`BridgeWorkerError: ValueError: instance not found: RD0` 停止，尚未运行候选
Spectre。根因不是目标 common-source 缺实例，而是 Bridge 0.7.0 的公共
`set_instance_params` 通过 `geGetEditCellView()` 选目标；公共
`open_window` 对已有窗口只执行 `hiRaiseWindow`，没有把它设为当前 edit
window。当时当前窗口仍是 `vda_ade_sweep_inv_001/schematic`：MN0 名称碰巧
存在，随后 RD0 不存在才暴露错目标。

VDA 的聚焦兼容层现在执行：

```text
Bridge open_window
-> hiSetCurrentWindow(window)
-> 立即核对 library/cell/view
-> Bridge public set_instance_params
```

没有复制第三方写入实现，也没有修改 Bridge。升级 Bridge 后需要回归这条
兼容依赖。

Bridge 的单实例写入会立即 `dbSave`，所以首轮错误在活动反相器
`vda_ade_sweep_inv_001/MN0.Wfg` 留下了 `500n -> 1u` 的旁路变化。该变化
随后通过两份独立证据确认：先前 exact-history OA/input comparison 保存
MN0 raw width `500n`，事故后只读 inspect 为 `1u`。VDA 使用一个显式
`parameters.apply` 修复任务只把 MN0 恢复到 `0.5 µm`，没有计算或替换
cellview。before/apply/after 记录为：

```text
before: MN0=1.0 µm, MP0=1.0 µm, L=0.03 µm
after:  MN0=0.5 µm, MP0=1.0 µm, L=0.03 µm
instances: CL0,GND0,MN0,MP0,VDD0,VIN0
nets: gnd!,IN,OUT,VDD,VSS
pins: IN,OUT,VDD,VSS
```

完整字段 diff 只有 MN0 的 `Wfg/w/multiwd/w_ov_l` 和由宽度 callback 派生
的 `ad/as/pd/ps/nrd/nrs` 十项；其他实例没有变化。审计与修复记录：

```text
artifacts/runs/inverter-active-window-audit-20260722.json
artifacts/runs/inverter-active-window-restore-20260722.json
```

common-source checkpoint 在连接正常后先独立 readback，确认目标仍处于声明
基线，再从 `next_candidate_index=1` 恢复。最终 checkpoint 包含 4 个完成
候选且 `complete=true`；历史失败 actions 仍保留，没有静默删除。

## 三条件 PVT 结果

PVT 任务没有 `parameters.*` action。三个条件共享 netlist SHA
`cdddb1f631d4...`，但有 9 个唯一 wrapper 路径和 SHA：

| 条件 | gain (dB) | BW (GHz) | GBW (GHz) | THD (%) | P1dB (mV peak) | 输入噪声 (µV RMS) | DC 功耗 (µW) | 摆幅余量 (V) |
|---|---:|---:|---:|---:|---:|---:|---:|---:|
| TT / 25℃ / 0.90 V | 9.457 | 7.206 | 21.404 | 7.627 | 121.64 | 1127.1 | 18.303 | 0.363 |
| SS / 125℃ / 0.81 V | 8.462 | 7.057 | 18.696 | 12.183 | 97.56 | 1348.7 | 18.180 | 0.196 |
| FF / −40℃ / 0.99 V | 10.063 | 7.833 | 24.952 | 6.440 | 132.34 | 993.7 | 22.276 | 0.422 |

SS 是增益、GBW、P1dB、输入参考噪声和摆幅余量的保守角；FF 是 DC 功耗
最大角。所有三角均处于饱和区，三项 analysis 完整且通过声明约束。顶层
selected metrics 对 maximize/minimize constraint 使用相应最坏角，全部标为
`software_inference`；逐条件指标没有被覆盖。

精确 section manifest：

```text
TT: ttmacro_mos_moscap, tt_res_bip_dio_disres, tt_mom, tt_r_metal
SS: ssmacro_mos_moscap, ss_res_bip_dio_disres, ss_mom, ss_r_metal
FF: ffmacro_mos_moscap, ff_res_bip_dio_disres, ff_mom, ff_r_metal
```

三组都引用 profile 中同一个
`cln28hpcp_1d8_elk_v1d0_2p2_shrink0d9_embedded_usage.scs`，并分别写入
`temp=25/125/-40`。远端根为：

```text
/data/xum/virtuoso_bridge_smoke/vda_common-source-quality-pvt-verify_1bc655792d86
```

Spectre log 保留 hierarchy/scalefactor warning 和 notices；每个 analysis 为
0 errors，响应提取没有 `analysis_warnings` 或 `analysis_issues`。这些 warning
没有被删除，但当前也没有证据表明它们改变了本 Gate 的标量/波形有效性。

## 证据与回归

主要记录：

```text
artifacts/runs/common-source-quality-length-vdd-tune/live-20260722.json
artifacts/runs/common-source-quality-length-vdd-tune/live-20260722.checkpoint.json
artifacts/runs/common-source-quality-length-vdd-tune/live-resume1-20260722.json
artifacts/runs/common-source-quality-pvt-verify/live-20260722.json
artifacts/runs/common-source-quality-pvt-verify/live-manifest-20260722.json
```

首次 PVT 和 manifest 增强后的第二次 PVT selected metrics 逐字段相同，说明
证据增强没有改变仿真输入语义。最终本地验证：

```text
366 passed
73/73 example plans
```

## 未验证边界与下一道 Gate

1. 当前 PVT 只有三个明确组合，不代表 PDK 全部 signoff corners；没有
   Monte Carlo、local mismatch、aging、PEX 或 statistical yield。
2. `operating_conditions` 目前只允许 fixed-design `simulation.run`，尚未让
   每个 W/L/RD/RS/bias/load 候选跨全部条件评估。
3. 本次三个条件全部可行，尚未 live 验证 PVT-aware 调优中的全不可行、预算
   耗尽、最佳 OA 写回和中途 transport checkpoint。
4. direct wrapper 的 PVT 不等于已保存 Maestro/ADE corner；人工打开、修改、
   重跑和旧 ADE L 迁移仍按延期记录处理。
5. 结果只适用于当前 `nics4304_tsmc28` profile、model 文件版本、服务器和
   目标 cell；切换 TSMC/SMIC 工艺、节点、section 或服务器必须重新过 Gate。
6. 尚未进入差分对、CMRR、多 test/multi-analysis ADE 或 L5B 单模块可重复闭环。

下一自动化 Gate 是把同一有限 `operating_conditions` 契约接入
`design.tune`：每个设计候选必须跨全部条件完成，然后再验证最佳 OA 写回、
全不可行恢复、预算耗尽和 transport resume。通过后才进入差分对。
