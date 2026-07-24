# 2026-07-24 反相器驱动比例校准 live Gate

## 结论

在 TSMC N28 `nics4304_tsmc28`、nominal `top_tt`、`Wn=0.6 µm`、`L=0.03 µm`、`VDD=0.9 V`、`CL=2 fF` 和当前固定 transient 激励下，完整执行预先声明的四个简单 `Wp/Wn` 比例。以满足 delay、skew、能量和过冲/欠冲约束为前提最小化 rise/fall skew，`Wp/Wn=1.25` 是声明离散域内的最佳可行点。

因此 profile 的新建反相器缺省值采用：

- `Wn=0.6 µm`
- `Wp/Wn=1.25`，即 `Wp=0.75 µm`

这是方便记忆的 nominal 初始值，不是跨 PVT、跨负载、跨输入 slew 的连续或全局最优解。任务显式尺寸和已有 OA 回读尺寸均优先，VDA 不限制用户或 Bridge 可写入的参数范围。

## 授权与隔离范围

- 新目标：`vb_pdk_smoke/vda_inv_ratio_calibration_001/schematic`
- OA 写入：是；仅创建新 cell、逐候选暂存和最佳点写回
- 远端计算：是；四次 OA→`si`→Spectre transient
- 远端目录：`/data/xum/virtuoso_bridge_smoke/vda_inverter-ratio-calibration-*`
- 覆盖：`replace_existing=false`；没有覆盖已有 cellview
- 对照对象：`vb_pdk_smoke/vda_inv_l5a_001/schematic` 只读 inspect
- Bridge 源码：未修改

执行使用的计划 token 为：create `4148c4d1b717a725`、tune `5767de5ef66633f2`、新 cell 独立 inspect `79bba956436715f3`、旧 cell 独立 inspect `fca22615d9b9a983`。

## 预声明搜索与门限

任务固定 `Wn=0.6 µm`，只枚举 `Wp=[0.6, 0.75, 0.9, 1.2] µm`，对应比例 `[1, 1.25, 1.5, 2]`。门限在执行前固定为：

- `delay_ps <= 3.9`
- `rise_fall_skew_ps <= 1.0`
- `supply_energy_per_cycle_fj <= 2.8`
- `overshoot_v <= 0.04`
- `undershoot_v <= 0.04`
- objective：最小化 `rise_fall_skew_ps`

当前 wrapper 使用 0→VDD pulse，rise/fall 各 5 ps，width 100 ps，period 200 ps；transient stop 380 ps、maxstep 0.5 ps。四点都返回 786 个 `time/IN/OUT/VDD_SRC:p` 样本。

## 结果

| `Wp/Wn` | `Wp` (µm) | rise (ps) | fall (ps) | `rise-fall` (ps) | skew (ps) | delay (ps) | energy/cycle (fJ) | 可行 |
|---:|---:|---:|---:|---:|---:|---:|---:|:---:|
| 1.00 | 0.60 | 6.108 | 5.334 | +0.774 | 0.774 | 3.956 | 2.333 | 否：delay |
| 1.25 | 0.75 | 5.229 | 5.441 | -0.212 | 0.212 | 3.743 | 2.415 | 是，选中 |
| 1.50 | 0.90 | 4.635 | 5.529 | -0.894 | 0.894 | 3.617 | 2.495 | 是 |
| 2.00 | 1.20 | 3.912 | 5.757 | -1.845 | 1.845 | 3.497 | 2.654 | 否：skew |

选中点的 `tphl/tplh=3.791/3.696 ps`，VOH/VOL=`0.899986 V/31.17 µV`，overshoot/undershoot=`25.35/23.81 mV`，平均供电功耗 `12.073 µW`。四个候选全部完成，所以 selection scope 是 `best_in_declared_discrete_domain`；run record 明确将 `continuous_optimum_claim` 和 `global_optimum_claim` 设为 false。

## 同源和回读证据

每个候选都按以下链路执行：

```text
OA 参数暂存并回读
  -> Cadence si -batch 自动网表
  -> OA 与 netlist 的 MN0/MP0 W/L 一致性
  -> 只含模型、激励、负载和 analysis 的 wrapper
  -> Spectre 波形
  -> timing/energy/overshoot 指标
```

四份 `si` 网表 SHA-256 分别为：

1. ratio 1.00：`f899ea09d88562e6ea04e15808a73b90268a75cb7f0e772e0e468372e1e4513c`
2. ratio 1.25：`79c32f7b8c9d42d0e4079ccb8fecdf6f7bd8266de61e6f278c8f3dffb7e517cd`
3. ratio 1.50：`bfbf9d7e707bb5c576f2f55dccaac9ad8727d1147da2c51295dea2f1f87b60e9`
4. ratio 2.00：`c943cd8c0d24bcddffd87e57412a730eef75d52dd15c4ff01ad693e712eea214`

逐候选 `parameters.stage.N` 和最终 OA inspect 是 `bridge_readback`；transient 波形及其 timing、能量、过冲指标是 `eda_result`；`gate_area_proxy_um2=(Wn+Wp)L`、约束判定和离散域选优是 `software_inference`；固定尺寸、VDD、CL、搜索点与门限是 `user_input`。

最佳点写回后，独立只读 inspect 得到 `Wn/Wp/L=0.6/0.75/0.03 µm`。对原 `vda_inv_l5a_001` 的独立 inspect 仍得到 `0.6/0.8/0.03 µm`，证明隔离 cell 的校准没有改动原对象。

本地未提交 run records 的 SHA-256 为：

- `tune-20260724.json`：`d9d13ed1020d6048f1e55d727f21cb2c5210bd6fd4c883063e77fe3405882172`
- `inspect-after-20260724.json`：`3c95d08e324564dd668ba1f14d5b54ca5f6ea354a1451bbb47c5984735e8f310`
- `original-inspect-after-20260724.json`：`ae5d6f6a9711a3e4c990aaee2feb8740f33bfd7c7d78b2051f9d2f0b363d180c`

## 未闭合边界

- 只验证 nominal `top_tt`；没有显式 temperature sweep、TT/SS/FF、mismatch 或 Monte Carlo。
- 只固定一个 Wn、L、VDD、CL 和输入 slew/周期；默认比例应被视为良好起点，不是所有负载条件的最终尺寸。
- run record 当前保存网表与 wrapper SHA，但本条旧反相器路径的 `tool_version` 为 null，且没有像较新的 AC Gate 一样保存原始 transient 文件 manifest/hash 和显式 `analysis_complete` 字段。Spectre 日志保留 `0 errors, 3 warnings, 8 notices`，其中三条是 `scalefactor` scope warning。这是证据完整度债务，不影响本次四点的相对比较，但下一次升级该路径时应补齐。
- 比例只作为 profile 缺省：已有 OA 尺寸、`parameters.apply`、semantic 搜索和原始实例参数写入能力保持开放。

## 下一道 Gate

如需把 1.25 从 nominal 初始值升级成更强的工艺默认，应对少量 CL/input-slew 和可选 TT/SS/FF 条件做鲁棒性复核，并补齐 transient artifact manifest。它不阻塞继续推进多 MOS、多 polarity 的差分对 small-signal 绑定 Gate。
