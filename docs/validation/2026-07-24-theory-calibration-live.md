# 2026-07-24 Gate 6 theory 一阶模型真实校准

## 结论

VDA 已用真实 TSMC N28 OA→`si`→Spectre 数据校准并交叉检查 Gate 6
电流镜负载差分对的一阶增益/带宽模型。当前状态是：

**Gate 6 topology-local one-pole calibration verified at nominal top_tt; standalone PDK gm/Id characterization and broader-domain validation pending**

后续状态：同日已增加[通用 MOS 小信号网络本地 Gate](2026-07-24-generic-small-signal-local.md)，并完成[TSMC N28 独立 MOS characterization 真实 Gate](2026-07-24-tsmc28-mos-characterization-live.md)。本文件中的系数继续只作为 Gate 6 regression benchmark；跨拓扑能力由独立器件表和节点矩阵核心承载，不再尝试推广本页的 topology-local 系数。下一步是 `si` 图/真实 DC 偏置绑定与 held-out circuit 对照，而不是继续把本页局部系数外推。

这不是完整 PDK 器件模型，也不是连续或全局最优证明。校准只允许在已测的
`Wn=1.5–2.0 µm`、`Wp=1.5–2.5 µm` 区间内解释固定条件下的局部趋势；在真实
即使 nominal gm/Id characterization 表已建立，在完成角色/偏置绑定和 held-out circuit
Spectre 误差验证前，校准产物仍不会自动驱动 OA 写回。

## 执行边界

- 目标：`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`。
- OA 写入：无；本次只读 OA 回读、自动 `si` netlisting 和远端 Spectre AC。
- 远端计算：有，产物位于
  `/data/xum/virtuoso_bridge_smoke/vda_differential-pair-current-mirror-final-ac-bridge_486fc293bca5/`。
- 覆盖风险：无；远端 scratch 名唯一，没有修改既有 cellview。
- Bridge：只调用既有公开执行路径，没有修改第三方仓库。

## 输入证据

训练数据来自 2026-07-23 已完成的六点 Wn×Wp 同源搜索：

```text
artifacts/runs/differential-pair-current-mirror-gate6/19-geometry-tune-20260723.json
SHA-256 cc6785bd249e4946eeedb29596ad430656f7c800db07d2b41e37ef0b0809d273
```

六点完整覆盖 `Wn=[1.5,2.0] µm × Wp=[1.5,2.0,2.5] µm`。所有点固定：

- 输入/PMOS 负载/尾管 `L=0.03 µm`；尾管 `W=0.8 µm`；
- `BIAS=0.32 V`、`VCM=0.55 V`、`VDD=0.9 V`、`CL=0.5 fF`；
- profile `nics4304_tsmc28`，model section `top_tt`。

校准器逐点要求并核对：OA topology/geometry 为 `bridge_readback`；网表
`parameter_consistency=matched`；Spectre OP 与 AC 指标为 `eda_result`；输出支路
KCL 误差不超过 1%；GBW 等于增益与 −3 dB 带宽之积；netlist、wrapper、DC 和
OP 原始文件均有 SHA-256。缺少任一证据、混入 demo/`software_inference` 指标、
条件漂移、重复点或不完整矩形训练网格都会拒绝校准。

## 模型和校准结果

一阶模型没有直接拟合最终指标，而是先使用真实 OP：

```text
Ad_raw = gm_MN1 / (gds_MN1 + gds_MP1)
Ceff   = (gds_MN1 + gds_MP1) / (2*pi*BW_spectre)
Ad     = k_gain * Ad_raw
Ceff   = CL + Cn_eff*Wn + Cp_eff*Wp
BW     = (gds_MN1 + gds_MP1) / (2*pi*Ceff)
GBW    = Ad*BW
```

对六点的增益比取均值，并对 `Ceff-CL` 做两参数最小二乘，得到：

| 系数 | 校准值 |
|---|---:|
| `k_gain` | `0.9220508065` |
| `Cn_eff` | `0.4817661058 fF/µm` |
| `Cp_eff` | `0.7291574439 fF/µm` |
| `CL` | `0.5 fF` |

`Cn_eff/Cp_eff` 是该拓扑和偏置下的等效输出电容系数，不是 PDK 的独立
`Cgg/Cgd/Cdb` 参数。

六点逐一留出、用剩余五点重拟合后的最大绝对误差为：

| 指标 | 最大绝对误差 | RMS 误差 | 接受门槛 |
|---|---:|---:|---:|
| gain | `0.0834%` | `0.0583%` | `2%` |
| bandwidth | `0.3730%` | `0.2281%` | `2%` |
| GBW | `0.4567%` | `0.2698%` | `3%` |

因此这套线性局部模型通过了声明的表内几何交叉验证门槛。

## 新鲜只读复跑

随后使用正式 Bridge adapter 对 `Wn=Wp=1.5 µm` 做了一次新的只读执行：

```powershell
.\.venv\Scripts\vda.exe run examples\tasks\differential-pair-current-mirror-final-ac.bridge.json `
  --adapter bridge --execute --token e77eeb9e221dac14 `
  --output artifacts\runs\theory-calibration\fresh-ac-20260724.json
```

运行从 `2026-07-24T02:54:44Z` 到 `02:56:10Z`，状态 `succeeded`。自动 `si`
网表 SHA-256 为
`bcd59efe00b8c3d5e51c90b0ae262714f2b22b659ad503d98e5b1ca625557535`，OA
六组 semantic 参数与网表全部匹配。Spectre 得到：

- gain `3.7421045562 V/V`；
- bandwidth `2.9756224922 GHz`；
- GBW `11.1350904856 GHz`；
- DC supply power `14.2920160032 µW`。

这些数值与训练记录中的同一几何逐值一致。全训练模型对该新执行的预测误差为
gain `0.0695%`、bandwidth `0.3199%`、GBW `0.3896%`。因为它重复的是训练域中的
同一点，这一项证明的是远端执行与证据链重复性；几何泛化由上述留一法检查，
不能把该复跑描述成外推验证。

新运行记录 SHA-256：
`1218401bf2b7deb24b101cd71a03aad4462b145f9aaff6f79bfb69330a3752ba`。

## 可审计命令与产物

新增纯本地命令：

```powershell
.\.venv\Scripts\vda.exe theory-calibrate `
  artifacts\runs\differential-pair-current-mirror-gate6\19-geometry-tune-20260723.json `
  --validation-run artifacts\runs\theory-calibration\fresh-ac-20260724.json `
  --id gate6-one-pole-top-tt-20260724 `
  --output artifacts\theory\gate6-one-pole-top-tt-20260724.json
```

校准 JSON 绑定两个输入文件的完整 SHA-256、每点四类 EDA 文件哈希、拟合系数、
全部留一预测、独立复跑预测、误差门槛、适用域和证据来源。当前本地产物 SHA-256
为 `4ad4671df76216f0417bf0d53be86d8a7d3424340f1bb2a86126e4a56874cce2`；
`artifacts/` 按仓库策略不提交 Git，长期结论和关键哈希保留在本记录中。

## 测试

```text
python -m pytest
474 passed
```

新增测试覆盖：准确恢复已知系数、留一法和独立点验证、demo/非 EDA 指标拒绝、
不完整训练网格拒绝、适用域外推拒绝、固定条件漂移拒绝、误差门未过时保持
`partial`，以及 CLI 输出与保存产物一致。

## 未闭合边界与下一 Gate

1. 尚未建立独立晶体管 testbench 的 TSMC N28 `gm/Id`、`gds/Id`、`Id/W`、
   `VDSAT` 和电容随 L/VGS/VDS 的 characterization 表；现有 topology-local 系数
   不能取代它。
2. 校准只覆盖 nominal `top_tt`、一个偏置/负载和 30 nm 长度；PVT、L、BIAS、
   VCM、VDD、CL 外推均被禁止。PVT 仍保持任务可选，不默认附加。
3. 一阶模型不预测内部极点/零点、CMRR、PSRR、noise、distortion、slew、
   settling、mismatch 或 stability。
4. 当前校准命令验证局部模型，但还不会把该 topology-effective artifact 冒充
   `vda theory` 所需的真实器件 characterization 表，也不会据此写 OA。
5. 校准预测仍消费每个观测点由 Spectre 得到的 `gm/gds`；没有独立器件表和插值器
   时，它不能在 EDA 前预测一个从未仿真的新电路点。

下一道理论 Gate 是建立只读独立 MOS characterization 表并绑定原始 manifest/hash，
再用中心、边缘和未参与拟合的同源电路点验证。通过后，理论解只用于生成或缩小
Spectre 候选域；最终选优仍由真实 OP/AC 和完整规格判定完成。
