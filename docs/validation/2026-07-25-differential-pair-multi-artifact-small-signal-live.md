# 2026-07-25 差分对三表同源小信号 live Gate

## 结论

Gate 7D 已在既有 TSMC N28 电流镜负载差分对上闭合：三张独立 MOS
characterization artifact 能按输入 NMOS、PMOS 负载和 NMOS 尾管的 exact W/L、31 项
`si` 参数签名和实际 DC 偏置绑定到同一份 OA→`si`→Spectre held-out 电路记录；通用
小信号矩阵在不使用拓扑专用 AC 公式的情况下通过 DC、signed 端子电荷导数、结电容、
gain、phase、−3 dB bandwidth 和 GBW 的预声明门限。

当前可称为：

> **differential-pair exact three-plane same-source small-signal validation verified at nominal top_tt**

这不是任意差分对、任意器件签名、PVT 或完整设计质量闭环。

## 授权与执行范围

- OA 目标：`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`
- OA 行为：只读；没有创建、修改、暂存或覆盖 cellview
- 远端计算：是；一次 held-out OA→`si`→Spectre DC/AC，及三张 standalone 器件表
- 远端产物：全部位于 `/data/xum/virtuoso_bridge_smoke/...`，没有写 `/home/xum`
- PDK：`nics4304_tsmc28`，`top_tt`，27 ℃，VDD=0.9 V
- Bridge：复用既有 `virtuoso-bridge-lite` 连接、Cadence 环境和文件传输；源码未修改
- Spectre：`21.1.0.612.isr15`

最终执行 token 为：电路 `0543a6740aa38432`、输入管表 `b63762396aa4486d`、
PMOS 负载表 `48314e2c804e9c02`、尾管表 `d7331e0b8199a270`。

## 为什么前两版没有闭合

本 Gate 保留失败证据并按因果逐层排除，没有提高原来的 25% DC/BW/GBW、0.5 dB gain
和 5° phase 门限。

| 模型版本 | 预测 BW | 实际 BW | BW 误差 | 预测 GBW | 实际 GBW | GBW 误差 | 结论 |
|---|---:|---:|---:|---:|---:|---:|---|
| legacy 五类等效电容 | 5.2731 GHz | 2.9756 GHz | 43.57% | 19.8764 GHz | 11.1351 GHz | 43.98% | partial |
| 完整 signed 4×4 `dQi/dVj` | 5.2356 GHz | 2.9756 GHz | 43.17% | 19.7348 GHz | 11.1351 GHz | 43.58% | partial |
| `dQi/dVj` + 独立 `cjd/cjs` | 3.0987 GHz | 2.9756 GHz | 3.97% | 11.6800 GHz | 11.1351 GHz | 4.67% | pass |

Spectre 的 MOS OP 中 `cgs=dQg/dVs`、`csg=dQs/dVg` 等量是有方向、有符号且不必互易
的偏导；因此实现保存完整 `cgg...cbb`，直接 stamp `jω dQi/dVj`，不再把交叉项取绝对值
或强制成成对电容。Cadence 对这些定义和非互易性有公开说明：
[端子电荷偏导定义](https://community.cadence.com/cadence_technology_forums/f/rf-design/28791/cgs-and-cgs-vs-vgs)、
[非互易及有符号电容](https://community.cadence.com/cadence_technology_forums/f/custom-ic-design/42825/spectre-captab-1-negative-capacitance-2-not-reciprocal-values/1363831)。

但第二版仍失败。为区分“表/插值错误”和“电路模型漏项”，worker 又在 held-out 电路
本身保存五个 MOS 的 16 项 `cxx`。三张表对实际电路矩阵的最坏 normalized error 只有：

- `MN0/MN1`：0.206%（`cds`）
- `MP0/MP1`：0.735%（`csd`）
- `MNTAIL`：0.553%（`cbg`）

这排除了 bias interpolation、characterization 角色选择或 signed matrix 方向作为 43% 误差
的主因。Spectre OP 同时把 `cjd/cjs` 作为与本征 `cxx` 分开的结耗尽电容报告；Cadence 的
BSIM3 示例也明确同时列出 intrinsic `cxx` 与 separate `cjd/cjs`：
[Spectre DC OP 电容示例](https://community.cadence.com/cadence_technology_forums/f/rf-design/11810/parasitic-capacitances-from-printed-dc-operating-point-of-spectre-with-bsim3v3)。
把这两项分别表征、插值、逐器件验证并作为 drain/body、source/body 二端电容 stamp 后，
误差才收敛到原 policy 内。

## 最终同源绑定

held-out 电路 run：

- 本地记录：`artifacts/runs/differential-pair-current-mirror-characterization-heldout-bridge/run-20260724T204307Z.json`
- run SHA-256：`a03e1f51458be938af899673036a76876bab26cbf001212e3498369545b4b046`
- `si` netlist SHA-256：`bcd59efe00b8c3d5e51c90b0ae262714f2b22b659ad503d98e5b1ca625557535`
- raw AC SHA-256：`945a278c4c10a2282330c3d09a1b1fb1f6f869fd05ad519cfb8223da1962b887`
- AC 网格：181 点，1 kHz–1 THz；五个 MOS 全部处于 saturation

三张最终 standalone 表：

| 角色 | W/L | run record | run SHA-256 | artifact manifest SHA-256 |
|---|---:|---|---|---|
| `MN0/MN1` 输入 NMOS | 1.5/0.03 µm | `run-20260724T204823Z.json` | `746b03ed57c5b32238b38e8da06aa902ea784742efe9de9b553f04eeeebe1224` | `8ebe3e6d5017a85979c766f89bf623c14ba53d5240ad11ff07a39943041d677b` |
| `MP0/MP1` PMOS 负载 | 1.5/0.03 µm | `run-20260724T204820Z.json` | `005389d0bb87dbd5180fb9d1c35a1d8ce9bda0ec943ae1640200220f7f3ed012` | `af3e6753f0b28321e3811f98647cd802ff577e6a1c816af86b14af1c92a2b67a` |
| `MNTAIL` 尾 NMOS | 0.8/0.03 µm | `run-20260724T204818Z.json` | `5811b2d585fae96f898709b177813867003c0f1c662116c29ad5002fe3b7f65a` | `b0d0eea248dbff0941e743cf5600961e8d849e82fc2941eebbe65239b682effb` |

三张表声明的 `source_run_sha256` 都等于上述 held-out 电路 run，来源 netlist、实例、model、
W/L、参数 count/hash、profile/corner/temperature 也逐项一致。最终 validator 把三个
`characterization_source_run_is_current_circuit_run` 都记录为 true。

## 最终结果

固定 policy SHA-256：
`d769e81bdc3655f2690e8b98b47174bef71175fc2796580d79733515e58a896c`。

| 指标 | 矩阵预测 | Spectre | 误差 | 门限 | 结果 |
|---|---:|---:|---:|---:|:---:|
| 低频增益 | 11.5254 dB | 11.4623 dB | 0.0631 dB | 0.5 dB | 通过 |
| 低频相位 | −0.0000257° | −0.0000267° | 0.0000010° | 5° | 通过 |
| BW 处相位 | −50.8584° | −50.8276° | 0.0309° | 5° | 通过 |
| −3 dB BW | 3.0987 GHz | 2.9756 GHz | 3.97% | 25% | 通过 |
| GBW | 11.6800 GHz | 11.1351 GHz | 4.67% | 25% | 通过 |

五个器件的 Id/gm/gds/VDSAT、16 项 `cxx` 和 `cjd/cjs` 全部通过。各角色最坏的 DC
相对误差为输入 NMOS `gds=4.83%`、PMOS 负载 `Id=3.33%`、尾 NMOS `gds=2.54%`；
远低于 25% 门限。最终 validation record：

- `gate7d-final-validation-20260725.json`
- SHA-256：`6495473d18b1c32da356d295f0d28d498598b7d3a8db6cad0fbe775d7581cf03`

中间失败/诊断记录也保留：

- legacy 五电容：`13a7e2b8825ab66b8a50e8f3ffb7c77392b1ca32a5b66d185140bba2922be75c`
- 完整电荷矩阵：`959085995bd179d238e2e3930e86d1fe76ae9e1c028ccc56f092ccfad140a636`
- 电路内矩阵诊断：`6169725dec3afa728e11cd3de52014f06dbb83b41509878a9c32b4f670e7f6f1`
- 加入结电容但尚未刷新 exact-source 绑定：`c141f6cb051b01808ace534b225fbb67128a579f6bea99b7acfa062cec21829d`

## 证据分类

- `eda_result`：`si` netlist、Spectre DC OP、181 点 raw AC、五管 `cxx/cjd/cjs`、三张 standalone 表的 raw OP 和所有文件 manifest/hash。
- `bridge_readback`：目标 OA schematic 的 instances/nets/pins 及只读 target identity。
- `software_inference`：width normalization、偏置插值、signed matrix 与结电容 stamp、graph/artifact 选择、预测指标、误差及 pass/fail。
- `user_input`：目标、PDK/corner/temperature/VDD、三张安全 bias grid 和执行前固定的 validation policy。
- `system_event`：若存在 transport/worker 异常则单独记录；本次四份最终 run 均 succeeded，不能仅由这一状态推导电路通过。

## 未闭合边界与下一 Gate

- 只验证 nominal `top_tt`；PVT 是可选增强，不作为默认成本，mismatch/Monte Carlo 未做。
- 当前自动表征任务生成仍只支持 `nf=1、m=1`；multi-finger/multiplicity 不能沿用本 Gate 误差保证。
- 只验证当前五管 PMOS 电流镜负载拓扑、单端 `OUTN` 和该偏置/负载点；没有验证 noise、linearity、CMRR/PSRR 或差分输出预测。
- 当前用 Spectre OP 的准静态导数构造小信号网络；没有用端口 Y 参数在全频逐点拟合。若后续高频拓扑出现超门误差，应先加多端口 Y(f) 诊断，而不是调整固定阈值。
- 本 Gate 没有授权或执行 OA 写回，理论结果仍不能直接提交为设计参数。

下一自动 Gate 是用已闭合的三角色器件表/矩阵做 theory-seeded 差分对有限候选生成，
再由真实 OA→`si`→Spectre 复核与选优。候选搜索仍必须报告完整声明域、预算状态和
`best_evaluated`/`best_in_declared_discrete_domain`，不能把有限点最大值称为全局最优。
