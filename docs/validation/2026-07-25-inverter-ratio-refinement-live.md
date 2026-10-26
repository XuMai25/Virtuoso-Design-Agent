# 2026-07-25 反相器 Wp/Wn 细化 live Gate

## 结论

在 `nics4304_tsmc28`、nominal `top_tt`、`Wn=0.6 µm`、`L=0.03 µm`、`VDD=0.9 V`、`CL=2 fF` 和固定 5 ps input edge 下，完整实测 `Wp/Wn=[1.20,1.25,1.30,1.35]`。四点均满足预声明约束；以最小 rise/fall skew 为 objective，`1.20` 是声明离散域最佳：skew `0.0394 ps`。

因此 `nics4304_tsmc28` 的新建反相器 nominal 起点从 `Wp/Wn=1.25` 更新为简单的 `1.20`，即默认 `Wn/Wp=0.60/0.72 µm`。这不是连续空间、跨负载、跨 input slew 或跨 PVT 的全局最优，也不限制显式尺寸、已有 OA 尺寸、`parameters.apply` 或 Bridge 原有参数能力。

经验值 1.30 已在完全相同条件下实测，不再是插值：其 skew 为 `0.3639 ps`，比 1.25 的 `0.2119 ps` 更差；1.20 同时以很小的 delay 代价换来更好的对称性、更低能量和更小面积代理。

## 授权与恢复

- 目标：`vb_pdk_smoke/vda_inv_ratio_calibration_001/schematic`；
- OA 写入：逐候选暂存、回读、失败恢复和最佳点写回；
- 远端计算：四次 OA→`si`→Spectre transient；
- `replace_existing=false`，没有重建或覆盖 cellview；
- 计划 token：`87b00a1c841104ff`；
- Bridge 源码：未修改。

一次候选 1 的 Spectre raw 下载遇到 SSH connect timeout。该事件保留在最终 run notes，未产生候选指标；checkpoint 指向 candidate 1，OA 独立回读恢复为 600n/750n，远端 `spectre/si/Maestro=0` 后才使用 `--resume` 重试。恢复任务没有跳过或伪造 candidate 1，最终 run 同时保留失败 action 与成功 action。

## 结果

| `Wp/Wn` | Wp (µm) | rise (ps) | fall (ps) | skew (ps) | delay (ps) | energy/cycle (fJ) | power (µW) | area proxy (µm²) |
|---:|---:|---:|---:|---:|---:|---:|---:|---:|
| **1.20** | **0.72** | **5.3822** | **5.4216** | **0.0394** | 3.7758 | **2.3984** | **11.9920** | **0.0396** |
| 1.25 | 0.75 | 5.2295 | 5.4414 | 0.2119 | 3.7432 | 2.4147 | 12.0733 | 0.0405 |
| 1.30 | 0.78 | 5.0965 | 5.4604 | 0.3639 | 3.7117 | 2.4308 | 12.1542 | 0.0414 |
| 1.35 | 0.81 | 4.9647 | 5.4785 | 0.5139 | **3.6853** | 2.4470 | 12.2351 | 0.0423 |

四点的 overshoot/undershoot 均低于 26.0/24.2 mV，delay 均小于 3.9 ps，energy 均小于 2.8 fJ，skew 均小于 1 ps。selection audit 为：

- declared/attempted/completed：4/4/4；
- `domain_exhausted=true`；
- `selection_scope=best_in_declared_discrete_domain`；
- `continuous_optimum_claim=false`；
- `global_optimum_claim=false`。

相对 1.25，1.20 的 skew 降低约 81.4%，energy 降低约 0.67%，area proxy 降低约 2.22%，但平均 delay 增加约 0.033 ps。选择 1.20 是当前“优先对称、同时满足 delay/energy/overshoot 门”的结果；如果任务把最小 delay 设为 objective，结论会不同。

## 同源与最终状态

四个候选的 OA semantic W/L 与 `si` netlist 均为 `parameter_consistency=matched`，每次 transient 都解析到 786 个 `time/IN/OUT/VDD_SRC:p` 样本。网表 SHA-256：

1. 1.20：`967cc2a39775bd861eb9d1d6673993c9a02a9dc00874e88d10d2e631931c3b5f`；
2. 1.25：`79c32f7b8c9d42d0e4079ccb8fecdf6f7bd8266de61e6f278c8f3dffb7e517cd`；
3. 1.30：`4951510b67a4da931e3d4b1588ff611151f2dc6c24e53018a193227b6c5f0a53`；
4. 1.35：`c3986f85c9cff16d2f0d925acdfb776a0a97848e86750eac727afb62b6670954`。

1.25 的 netlist hash 和所有指标与 2026-07-24 粗扫完全一致，给本次相对比较提供了重复性锚点。最佳写回后的独立 OA inspect 为 `MN0 Wfg=600n`、`MP0 Wfg=720n`、两者 `L=30n`。结束后独立资源盘点为 `spectre=0`、`si=0`、Maestro session 0、本地 cancel marker/temp 0。

证据来源保持分离：OA 参数与结构是 `bridge_readback`；网表、波形和 timing/energy/overshoot 是 `eda_result`；area proxy、约束判定、百分比和离散选优是 `software_inference`；候选、门限、VDD、CL 与 objective 是 `user_input`；SSH timeout 与恢复是 `system_event`。

本地 run record：

- `artifacts/runs/inverter-ratio-calibration/refinement-20260725.json`，SHA-256 `4c74a9dce49014212c6dbca233b2902cf14624d737d00ff8028c5356e1c169f4`；
- `artifacts/runs/inverter-ratio-calibration/post-refinement-inspect-20260725.json`，SHA-256 `196477524d822c3183cb6a91922ce0b3eefc3bc4e584b021e0d474ba9f48bc48`。

## 未闭合边界与下一 Gate

- 只验证一个 CL、input slew、Wn/L/VDD 和 nominal model；1.20 仍只是可覆盖的 profile 初始化值。
- 当前 transient path 保存网表/testbench hash 和结构化波形指标，但 `tool_version` 仍为 null，尚未生成与新 AC Gate 等价的完整 raw transient artifact manifest。
- 若要检验 1.20 是否应作为更鲁棒的工艺经验起点，下一步只需在少量 CL/input-slew 组合上比较 1.20/1.25/1.30；PVT 保持可选，不应默认把成本加到每个创建任务。
- 不需要为了证明 1.188 或 1.207 一类插值点而把 profile 默认改成难记的小数；连续或自适应优化属于另一个 Gate。
