# 2026-07-24 差分对 PSRR 沟道长度八点 OA 搜索 live 验证

日期：2026-07-24（run record 使用 UTC 文件时间 `20260724T...Z`）

状态：**OA length sensitivity and infeasible recovery verified; provisional 20 dB PSRR closure short by 0.256 dB**。

## 目标与授权边界

- 目标：`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`
- PDK profile：`nics4304_tsmc28`
- OA 写入：是，只搜索输入对 `length_um`、PMOS 镜像 `pmos_load_length_um` 和尾管 `tail_length_um`
- 搜索空间：三条轴各为 `[0.03, 0.06] µm`，完整 `2×2×2=8` 点
- 固定 testbench：`BIAS=0.32 V`、`VCM=0.55 V`、`VDD=0.9 V`、`CL=0.5 fF`
- 远端计算：每点一份自动 `si` 网表，随后差模/VDD/VSS 三次 Spectre AC，共 24 次 AC
- 远端目录：`/data/xum/virtuoso_bridge_smoke/vda_differential-pair-current-mirror-psrr-length-tune-bridge_*`
- 覆盖：`replace_existing=false`，不替换或新建 cellview；有可行点才提交最佳 OA，否则恢复初始 OA

`1 kHz–100 MHz` 内 `minimum_psrr_db_in_band >= 20 dB` 继续沿用上一 Gate 的临时工程证伪门。它不是用户应用规格或行业通用指标；最终产品仍须显式给出频带、PSRR+/PSRR−、增益、摆幅、功耗和其他质量要求。

任务入口：

```text
examples/tasks/differential-pair-current-mirror-psrr-length-tune.bridge.json
plan token: 84e5509b67c38127
```

除临时 PSRR 门外，每点还要求全部信号器件饱和、联合输出摆幅余量至少 `0.1 V`、低频差模增益至少 `3 V/V`、差模带宽至少 `100 MHz`、DC 供电功耗不高于 `20 µW`。

## 执行与 transport 恢复

第一次运行完成候选 1–2。候选 3 写入后，立即 `read_schematic` 遇到 `WinError 10054`：

```text
artifacts/runs/differential-pair-current-mirror-psrr-length-tune-bridge/run-20260724T023700Z.json
artifacts/runs/differential-pair-current-mirror-psrr-length-tune-bridge/run-20260724T023700Z.checkpoint.json
```

该事件记为 `system_event`，没有生成候选 3，也没有被判成电路不可行。executor 随后成功执行 `parameters.restore.interrupted`，checkpoint 为两个完整候选、`next_candidate_index=3`、`pending_oa_parameters=null`，期望 OA 与初始六项语义几何一致。

Bridge doctor 恢复后，用同一 task、token 和 checkpoint 续跑。resume 先独立回读 OA，再只执行候选 3–8；已完成的候选 1–2 没有重跑。最终记录为：

```text
artifacts/runs/differential-pair-current-mirror-psrr-length-tune-bridge/run-20260724T024200Z.json
```

8 个候选均 `analysis_complete=true`、`analysis_issues=[]`、`analysis_warnings=[]`。零候选通过全部约束，因此 `status=partial`、`selected_parameters=null`；executor 没有把 objective 最大但不满足规格的候选 8 写回，而是执行 `parameters.restore` 和 `schematic.inspect.after`。checkpoint 最终 `complete=true`、`next_candidate_index=9`、`pending=null`。

随后另起一个只读任务再次检查最终 OA：

```text
artifacts/runs/differential-pair-current-mirror-inspect-bridge/run-20260724T025400Z.json
```

## 八点结果

| 点 | 输入对 L (µm) | PMOS L (µm) | 尾管 L (µm) | 1 kHz–100 MHz 最差 PSRR (dB) | 增益 (V/V) | BW (GHz) | GBW (GHz) | Pdc (µW) | 摆幅余量 (V) |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
| 1 | 0.03 | 0.03 | 0.03 | 11.512534 | 3.742105 | 2.975622 | 11.135090 | 14.292016 | 0.213103 |
| 2 | 0.03 | 0.03 | 0.06 | 11.007325 | 3.556334 | 0.830880 | 2.954887 | 3.427744 | 0.160219 |
| 3 | 0.03 | 0.06 | 0.03 | 15.825883 | 6.147422 | 1.928224 | 11.853607 | 14.091524 | 0.167782 |
| 4 | 0.03 | 0.06 | 0.06 | 15.471570 | 5.982338 | 0.534343 | 3.196619 | 3.400221 | 0.172496 |
| 5 | 0.06 | 0.03 | 0.03 | 13.690532 | 4.824254 | 2.110959 | 10.183806 | 12.239730 | 0.207276 |
| 6 | 0.06 | 0.03 | 0.06 | 13.749561 | 4.900770 | 0.627342 | 3.074457 | 3.129052 | 0.156915 |
| 7 | 0.06 | 0.06 | 0.03 | 19.712332 | 9.663619 | 1.118582 | 10.809547 | 12.146606 | 0.251580 |
| 8 | 0.06 | 0.06 | 0.06 | 19.743788 | 9.966331 | 0.330518 | 3.294047 | 3.116051 | 0.257258 |

所有点只失败 `minimum_psrr_db_in_band >= 20 dB`，其余五条护栏全部通过。最佳观测点 8 距临时门 `0.256212 dB`；点 7 距门 `0.287668 dB`。

## 敏感度与工程判断

两水平主效应平均值用于判断下一轮搜索方向；它只是当前 8 点上的 `software_inference`，不是连续模型：

| 长度轴 0.03→0.06 µm | 平均 PSRR 变化 | 平均 BW 变化 | 平均 GBW 变化 |
| --- | ---: | ---: | ---: |
| 输入对 L | `+3.270 dB` | `1.567→1.047 GHz` | `7.285→6.840 GHz` |
| PMOS 镜像 L | `+5.198 dB` | `1.636→0.978 GHz` | `6.837→7.288 GHz` |
| 尾管 L | `−0.192 dB` | `2.033→0.581 GHz` | `10.996→3.130 GHz` |

因此：

1. OA 几何不是无效旋钮；输入对和 PMOS L 对同源 PSRR 有显著影响，且两者同时加长存在明显联合增益。
2. 尾管 L 在该网格内不是有效 PSRR 旋钮。点 7→8 只增加 `0.031456 dB`，却把 BW 从 `1.1186 GHz` 降到 `0.3305 GHz`、GBW 从 `10.8095 GHz` 降到 `3.2940 GHz`。
3. 当前不能把点 8 写成“已闭合”或“最佳设计”：它没有通过声明门，而且只完成 PSRR/DC/差模 AC 护栏，尚未对新几何复跑 CMRR、linearity、noise、PVT 或 mismatch。
4. 下一自动 Gate 应固定 `tail_length_um=0.03 µm`，只对输入对与 PMOS L 做小范围显式二维细化。若完整护栏下仍无法跨门，再进入偏置参考或供电隔离结构；不能直接无限扩展 L。

## OA、网表和 raw AC 证据

每点 OA semantic/geometry 与自动 `si` 网表均为 `matched`；长度组合不同，因此八份网表 SHA-256 也不同。三次 AC 的 DC 工作点与频率网格均为 `matched`。每次带限评估使用 101 个采样点，实际终点为 `99,999,999.99999923 Hz`。

| 点 | netlist SHA-256 | 差模根 `ac.ac` | VDD 根 `ac.ac` | VSS 根 `ac.ac` |
| ---: | --- | --- | --- | --- |
| 1 | `bcd59efe00b8c3d5e51c90b0ae262714f2b22b659ad503d98e5b1ca625557535` | 137670 B, `84bf619a4a688673421c5039d974f3040e88ff4d486f000708fd363fe6f1e364` | 137072 B, `575b4088e018e7f84dfaa649b5f0336331051dade16e6aa1ef3f639bee2259ef` | 137169 B, `679a704dcbed52a101034fd73e0c17dcd480cb0e70a5ea8413f0a9d9c3345f24` |
| 2 | `ff02f5b17ffa5af8788ef00632ebf05de06e1d7cfe4629df77d37d7fb81a730a` | 137669 B, `5c0c225d2e8b398809cf6e5fd432f7cf906687ced5479a6d7f6f6f87aad8072e` | 137018 B, `bd3d01b8c421aa8a911df99ba15eb56f18ac0660b57b7805a4c8e0a719d6a9c5` | 137159 B, `ccb6dd009cd753e23c9021f862787dfc64ff141ccb2266e18c8522d65076383f` |
| 3 | `a732645c0f2dc613eeb0bc8229eeac86a024623148635380fa51bc33323aa252` | 137774 B, `79fcc00781f64ff8ce9173f1b4eaff38603791b2e0a6645f6efb344299042b7a` | 137068 B, `9828c12cd22f333678c3d735f0484b5520d5927b8ed9344d6a07f609afe60fda` | 137095 B, `b3571fa448be9ebfabeee97e800600ed2a194bb286806e691b4210961bffeeb4` |
| 4 | `f46f11b5292704f332a75effce1b04790765497b3e1cc4e525040dfe1f858548` | 137754 B, `c81e213eac509ed7d40777a14bb3bf914ee04b3ab3cfeb8de61a9745086acad2` | 137017 B, `b11513f5b3d99b84385e0317ba9a12f483c36f768ea232ee7510a4ec89ca9963` | 137090 B, `36a1b11f6e772414d4bcc8116443916a67171476fe0798fb8de93c7f493b9ce2` |
| 5 | `88bc52c18a7cafb86ba1403808d31deee258d82a1792db59781a29a1d92bb829` | 137644 B, `58c3fdf9ca50aef475a3709901482556de03d44fdc9f80c98fe6f563edc652ac` | 137021 B, `8de0375bc5e0056183107972951bdba8136d471f3964e32724ddbd0c2fe36b9b` | 137169 B, `9050761cf17de8ed5faa7ab4fa1011cae9fcdc0afaac05d403877bdba80b6498` |
| 6 | `95519a52dfcfc5ca07ca017256f906e2f26836d9c357828cd6c2a0cf55774548` | 137685 B, `02e9a5dd8bbb1e884b1af05ec789553235814be85e46559130262985613d4ca0` | 137018 B, `7b95915512ac75ba3c79e423ad49c082479a7307782fa1b937df8639f9bbfde8` | 137159 B, `5cc2ca9ac7c1c78417c8b6e567bab71408763ea2c46f3da3acded212d45d6a16` |
| 7 | `d57d1e428658e6178fe7b40a8cd9941fbc9f18b2d862df753f5a4a3c294f4cb5` | 137741 B, `0d7c7f2e76bd9a2a3a46e759edfd6d79c38676a1268e8d93bdd657399327b0ce` | 137021 B, `2f300f5e0379e2c0d0318f1922b120040736bd6729021b876a73227faebee2e5` | 137089 B, `3f7b22a9157906714687689717a6e5f692d0f6d2603dad78b661c5a517fd396a` |
| 8 | `a7a081d74d05982cc47c471c67bec7eab6c11fbe6fee6e172acfec57f3595367` | 137759 B, `b9e6b2b356aee5246ae30c3d096f700618e38e1520f7cc78207bd593e4f6ad27` | 137018 B, `454cd88db17b75126792166bd011dc3f4589962954cd20d6ae0ba446a0230203` | 137086 B, `5b6e2de4fc46a9e43a969f430a6404eb3c360210edc827f0f3d984c7b3b7d431` |

运行内 `schematic.inspect.before/after` 在 topology、instances、nets、pins、semantic parameters、输入对 geometry、尾管 geometry 和 PMOS mirror geometry 上全部相同。额外独立 inspect 再次确认：

```text
instances = MN0,MN1,MNTAIL,MP0,MP1
length_um = 0.03
pmos_load_length_um = 0.03
tail_length_um = 0.03
```

没有 cellview 替换或拓扑变化。

## 证据分类

- OA 写入、立即回读、恢复和独立最终 inspect：`bridge_readback`
- 八份自动 `si` 网表、器件 OP、差模/VDD/VSS AC 和 24 份根 raw 文件大小/SHA：`eda_result`
- PSRR 比值、带限最差值、主效应平均、约束判定和有限网格选优：`software_inference`
- task 中显式保存的目标、三条长度轴、偏置/供电/负载、sweep 和护栏在 run record 中标为 `user_input`；用户授权了该具体 Gate，但其中 `20 dB` 的来源仍是此前公开的 VDA 临时工程假设，不是用户产品规格
- `WinError 10054`：`system_event`

## 本地验收与结论

新增任务和 planner 安全测试后：

```text
458 passed in 0.99s
python -m compileall -q src tests passed
139/139 example task plans passed
vda catalog passed
git diff --check passed
```

`virtuoso-bridge-lite` 保持 `codex/vda-transport-recovery@e74379a`、工作树干净；本 Gate 没有修改 Bridge 或 Obsidian Vault。

本 Gate 能称为：

> **Gate 6Q OA length sensitivity and explicit infeasible recovery verified at nominal TSMC N28.**

不能称为 PSRR 产品规格闭合，也不能称为完成差分对设计质量闭环。下一道 Gate 是固定尾管 30 nm 的输入对/PMOS L 小网格细化；如出现可行点，再对该 OA 状态补做 CMRR、linearity、noise 和必要的可选 PVT，并保留失败恢复路径。
