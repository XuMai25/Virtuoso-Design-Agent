# 2026-07-20 共源 AC 控制变量与 W/RD/RS 真实调优

状态：**controlled source-degeneration A/B and bounded W/RD/RS AC tuning verified; broader design-quality closure pending**。

本轮在用户明确确认的专用范围内新建 `vb_pdk_smoke/vda_cs_ac_tradeoff_001/schematic`，先保存 nominal AC，再在同一 cellview 上只加入 RS，最后运行预算耗尽、不可行恢复和完整 8 点 W/RD/RS 搜索。现有 `vda_cs_gate2a_001` 与 `vda_param_surface_001` 未修改；`replace_existing=false`；第三方 `virtuoso-bridge-lite` 未修改。

## 专用 cell 与控制变量

创建并回读的 nominal 基线：

- `MN0`: W=0.5 µm、L=0.03 µm、fingers=1、m=1；
- `RD0`: 20 kΩ；
- bias=0.35 V、VDD=0.9 V、外部 load=1 fF；
- topology=`common_source`。

nominal AC 完成后，正式 `schematic.transform` 只执行：

- `MN0.S: VSS -> NSRC`；
- 新增 `RS0(NSRC,VSS)=2 kΩ`；
- 新增 NSRC net。

transform 前后 MN0/RD0 master、位置、pins、W/L/RD 和未点名参数保持。由此得到同一 W/L/RD/bias/VDD/load 下只改变 RS/topology 的控制变量结果：

| 指标 | nominal | 只加入 RS=2 kΩ | 变化 |
| --- | ---: | ---: | ---: |
| gain | 4.0225 V/V | 2.5349 V/V | −36.98% |
| −3 dB bandwidth | 10.1442 GHz | 8.1027 GHz | −20.12% |
| GBW | 40.8047 GHz | 20.5398 GHz | −49.66% |
| unity-gain frequency | 39.6204 GHz | 18.9116 GHz | −52.27% |
| drain current | 22.2913 µA | 15.5094 µA | −30.42% |
| output swing margin | 0.3498 V | 0.3102 V | −11.32% |

这些变化可以归因于在该固定偏置和负载下加入 RS 所造成的拓扑与工作点变化；不能外推到其他 bias、load、corner 或器件尺寸。它也不表示 source degeneration 在所有目标下都更差，因为本任务 objective 是 GBW，尚未评价线性度、失真、输入范围、噪声或反馈稳定性。

nominal 证据：

- netlist：`/data/xum/virtuoso_bridge_smoke/vda_common-source-ac-tradeoff-nominal_4229e935b209/netlist`；
- SHA-256：`13f7961610a470d4bf77726d3d0cdede9a376aad675069eae99a3c92dfbbda52`；
- run record：`artifacts/runs/common-source-ac-tradeoff-nominal/live-20260720.json`。

退化控制点证据：

- 首次 `si` transport 中断保留为失败记录；tunnel 重建后成功；
- netlist：`/data/xum/virtuoso_bridge_smoke/vda_common-source-ac-tradeoff-degenerated_275afd58b6af/netlist`；
- SHA-256：`90e662a710be77c4d29d27199a989ae49bf6b808caa4737da0e4e79715c277cd`；
- run record：`artifacts/runs/common-source-ac-tradeoff-degenerated/live-retry1-20260720.json`。

## 完整 8 点 W/RD/RS 搜索

固定条件：L=0.03 µm、bias=0.35 V、VDD=0.9 V、load=1 fF。搜索空间：

- W=`[0.5, 1.0] µm`；
- RD=`[20, 22] kΩ`；
- RS=`[1, 2] kΩ`。

约束：DC saturation、output swing margin≥0.1 V、两组 KCL mismatch≤1%、gain≥2.5 V/V、bandwidth≥3 GHz。objective：最大化 GBW。

| # | W | RD | RS | gain | bandwidth | GBW | swing margin | feasible |
| ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: | --- |
| 1 | 0.5 µm | 20 kΩ | 1 kΩ | 3.1220 | 8.7406 GHz | 27.2883 GHz | 0.3641 V | yes |
| 2 | 0.5 µm | 20 kΩ | 2 kΩ | 2.5349 | 8.1027 GHz | 20.5398 GHz | 0.3102 V | yes |
| 3 | 0.5 µm | 22 kΩ | 1 kΩ | 3.2870 | 8.1626 GHz | 26.8266 GHz | 0.3890 V | yes |
| 4 | 0.5 µm | 22 kΩ | 2 kΩ | 2.6982 | 7.5249 GHz | 20.3000 GHz | 0.3343 V | yes |
| 5 | 1.0 µm | 20 kΩ | 1 kΩ | 3.7006 | 8.1895 GHz | 30.3062 GHz | 0.2827 V | yes |
| 6 | 1.0 µm | 20 kΩ | 2 kΩ | 2.9670 | 7.2192 GHz | 21.4187 GHz | 0.3588 V | yes |
| 7 | 1.0 µm | 22 kΩ | 1 kΩ | 3.8327 | 7.7327 GHz | 29.6330 GHz | 0.2512 V | yes |
| 8 | 1.0 µm | 22 kΩ | 2 kΩ | 3.1336 | 6.7395 GHz | 21.1200 GHz | 0.3331 V | yes |

8/8 候选均 `analysis_complete=true` 并满足本任务约束。候选 5 在声明网格内具有最高 GBW，最终写回：

- W=1.0 µm；
- L=0.03 µm；
- RD=20 kΩ；
- RS=1 kΩ。

最终指标：gain=3.7006 V/V、bandwidth=8.1895 GHz、GBW=30.3062 GHz、unity=29.3847 GHz、output swing margin=0.2827 V、Id=24.8196 µA。`parameters.apply.best` 的立即回读和之后独立 `schematic.inspect.after` 完全一致，topology 仍为 `source_degenerated_common_source`。

最终候选网表：

- `/data/xum/virtuoso_bridge_smoke/vda_common-source-ac-design-tune_876805208aa8/netlist`；
- netlist SHA-256：`2c529799349ac466606367015a038e22c4ab7048b8b21e89092146515de7b232`；
- wrapper SHA-256：`98b5e5aeaf460513387b1fd243f83fe1bd565c2f3d437569e07623272c52fdf5`。

最终 run record：`artifacts/runs/common-source-ac-design-tune/live-resume2-20260720.json`；checkpoint 为 complete、`next_candidate_index=9`、8 个候选。

## 预算耗尽与不可行恢复

预算任务声明相同 8 点空间但 `max_iterations=3`：

- 只评估候选 1–3；
- 状态为 `partial`；
- note 明确写明 selection 只在 evaluated prefix 内；
- 写回前三点中 GBW 最高的 W=0.5 µm、RD=20 kΩ、RS=1 kΩ；
- checkpoint complete、3 个候选、`next_candidate_index=4`。

run record：`artifacts/runs/common-source-ac-design-tune-budget/live-20260720.json`。

不可行任务对 RS=[1,2] kΩ 人为要求 gain≥100 V/V、bandwidth≥100 GHz：

- 两个候选都有完整 EDA 指标且均 infeasible；
- 不提交“最接近”候选；
- 恢复任务初始 W=0.5 µm、RD=20 kΩ、RS=1 kΩ；
- 最终独立 OA inspect 与初始值一致；
- 状态为 `partial`，checkpoint complete、2 个候选。

run record：`artifacts/runs/common-source-ac-design-tune-infeasible/live-resume2-20260720.json`。

## Transport 与恢复证据

本轮出现多次真实外部中断：

- 退化控制点在 `si -batch` 期间 `WinError 10054`，单点任务另存失败记录后重跑；
- 不可行任务分别在 `si -batch` 和 raw download 阶段失败；两次都先执行 `parameters.restore.interrupted`，再从候选 1/2 恢复；
- 完整 8 点任务先在候选 1 raw download 失败，恢复后完成 1–6；又在候选 7 upload 失败，再从 7 恢复并完成 8/8 与最佳写回。

VDA 没有把 upload/download/transport failure 当成 EDA infeasible，也没有自动接受缺失 raw 的候选。每次 OA 写入后的中断都保留失败 action、恢复 action、expected OA 和独立 resume readback。该结果验证 VDA checkpoint 边界，但也再次证明底层 SSH 传输稳定性仍有真实债务。

## 证据分类与边界

- OA topology、参数写入和前后回读：`bridge_readback`；
- DC/AC 连续量、复数交点和 KCL：`eda_result`；
- task 的 W/RD/RS/bias/load 网格和规格：`user_input`；
- 饱和分类、GBW 公式、候选排序、完整性与预算判断：`software_inference`；
- transport failure：`system_event`。

本轮可以称为 **bounded common-source AC design-parameter tuning and recovery verified**。仍未验证：L/VDD 的真实搜索、bias/load 与 W/RD/RS 的联合大网格、线性度/失真、noise、corner、输入电容、功耗、面积、post-layout，以及不同规格下的重复最优性。因此不能称为完整 L5B 单模块闭环。
