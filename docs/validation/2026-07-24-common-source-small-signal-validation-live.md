# 2026-07-24 Gate 7B 共源小信号真实验证

## 结论

VDA 已把独立 TSMC N28 MOS 表、只读 OA→`si` 图、真实 DC operating point 和
同源 Spectre AC 绑定到同一个通用 MOS/R/C small-signal kernel。当前状态是：

**nominal common-source same-geometry small-signal validation verified**

固定门限没有因失败而移动。最终 Id/gm/gds/VDSAT、低频 gain、低频 phase、−3 dB
处 phase、bandwidth 和 GBW 全部通过。本 Gate 只覆盖一个 nominal `top_tt` 共源点，
不是任意拓扑、任意几何、任意偏置或 PVT 的误差保证，也不授权理论结果写回 OA。

## 授权与副作用

- OA target：`vb_pdk_smoke/vda_cs_gate2a_001/schematic`；
- OA 行为：结构化只读；`oa_access_performed=true`，`oa_write_performed=false`；
- 远端计算：一个 standalone 61 点 DC characterization，以及一次目标 OA 的
  `si` + Spectre DC/AC；
- 条件：`nics4304_tsmc28`、`top_tt`、27 ℃、VDD=0.9 V；
- 电路参数：W=0.5 µm、L=0.03 µm、RD=20 kΩ、VBIAS=0.375 V、CL=3 fF；
- 所有远端根都位于 `/data/xum/virtuoso_bridge_smoke/`，使用 nonce 且运行前要求不存在；
- 没有新建、修改或覆盖 OA cellview，也没有修改 `virtuoso-bridge-lite`。

## 实现边界

新增 `vda small-signal-validate POLICY CHARACTERIZATION_RUN CIRCUIT_RUN`。它只消费
已有 JSON run records，不连接 Bridge、不运行仿真、不写 OA。输入必须来自 real
`virtuoso-bridge-subprocess` adapter；demo、OA write action、target/PVT 漂移、空 AC、
缺失文件 hash、非 `eda_result` OP/AC 或 OA/`si` 参数不一致都会拒绝。
characterization 侧还必须同时存在 raw `device.characterize=eda_result` 和 normalized
validation action；VDA 交叉核对 task/profile/corner/temperature/W/signature、Spectre
版本、完整 manifest、`/data/xum` 路径与 OA access/write=false。

binder 执行以下步骤：

1. 从结构化 `si` instances 取得 MN0、RD0 与节点；从 testbench 取得 CL；
2. 用 Spectre DC OP 的节点和器件值确定实际 VGS/VDS/VSB，不自行求非线性 DC；
3. 只在 exact L 平面内对 VGS/VDS/VSB 做 rectilinear linear interpolation，保存四个
   角点及权重，拒绝所有外推；
4. 要求 characterization W 与 `si` W 完全匹配；
5. 要求除 `w/l/nf/m/multi` 外的 `si` MOS 参数集合和值与 characterization artifact
   完全匹配，并保存参数数目和 canonical SHA-256；
6. 在原始 EDA frequency grid 上组装通用 `Y(f)`，计算 gain、phase、−3 dB bandwidth
   和 GBW，再按运行前固定 policy 对比 Spectre。

`DeviceCharacterizationSpec` 为此增加逐 polarity `model_parameters_by_polarity`。每个
signature 最多 64 项，只接受安全的 Spectre parameter name 和 numeric literal；
`w/l/nf/m/multi` 是保留控制量，不能从映射覆盖。artifact 保留相同 signature。
普通 Bridge-backed `simulation.run` 遇到未知参数 token 时会保留而不缩减原能力；只有
exact-signature validation policy 会拒绝未完整结构化的签名。

## 固定 policy

policy：`examples/theory/common-source-gate7b-validation-policy.json`

| 比较项 | 固定门限 |
| --- | ---: |
| Id/gm/gds/VDSAT 相对误差 | ≤ 25% |
| 低频增益绝对误差 | ≤ 0.5 dB |
| 低频与 −3 dB 处 wrapped phase 误差 | ≤ 5° |
| −3 dB bandwidth 相对误差 | ≤ 25% |
| GBW 相对误差 | ≤ 25% |

policy 还要求 exact characterization width 和 exact model-parameter signature。
target condition 另固定 `VDD=0.9 V`，并同时核对 operating-condition、仿真参数和
testbench 三处值。

## 失败、诊断与实现调整

没有把最接近点包装成通过：

1. 最初复用 W=1 µm Gate 7A 表时，虽然多个指标接近，物理宽度与 OA 的 0.5 µm
   不同。随后把 exact width 作为硬门；旧结果不再具备 Gate 7B 可比性。
2. W=0.5 µm、相同 L/偏置网格但只带 W/L 的独立表得到 `partial`：gds 误差
   32.20% > 25%，GBW 误差 25.84% > 25%。增益、相位和 BW 已通过，但门限没有放宽。
3. 对 retained `si` netlist 的只读诊断显示，MN0 还携带扩散面积/周长、NRD/NRS 和
   24 组 LDE/stress 参数。继续密扫 VGS/VDS 会把不同物理器件误当成插值问题。
4. VDA 因而加入可配置且受限的实例参数 signature，并从新的 `si` run record 自动
   提取。最终 MN0 signature 共 31 项，SHA-256 为
   `7a24cd99a2d5a5536808fc3f8b9cb4d85e47cde3a0a1bc947925d3fb42853f6b`。
5. 新 characterization 第一次尝试在任何远端计算前因沙箱 DNS 无法解析
   `nics4304-cad1` 失败，action 正确标为 `system_event`；失败 record 保留。获准的真实
   网络重试使用新 output 和新 nonce 成功，没有把 transport 失败归类为电路不可行。

## exact-geometry characterization

任务：`examples/tasks/mos-device-characterize-cs-mn0-top-tt.bridge.json`

```text
model: nch_lvt_mac
W: 0.5 um
L: 0.03 um
VGS: 0.35, 0.45, 0.55, 0.65 V
VDS: 0.15, 0.30, 0.45, 0.60, 0.75 V
VSB: 0, 0.075, 0.15 V
training: 60
holdout: VGS=0.40 V, VDS=0.375 V, VSB=0.075 V
```

31 项 signature 全部进入 standalone Spectre instance。留出点最坏归一化误差为
Id/W 的 `7.4169% < 25%`。run status、raw action 和 validation action 均为
`succeeded`；Spectre 为 `21.1.0.612.isr15`，OA access/write 为 false/false。

- run record：
  `artifacts/runs/mos-device-characterization-gate7b-cs-mn0/live-retry1-20260724.json`；
- remote root：
  `/data/xum/virtuoso_bridge_smoke/vda_mos_characterization_mos-device-characterize-cs-mn0-top-tt_88e44974663d/`；
- manifest SHA-256：
  `73e5865cf46a0a165d93726892d6ec0e6b45b381ecffff569ff1e2ee024f0a93`。

## 最终 held-out circuit 结果

电路 run：
`artifacts/runs/common-source-small-signal-heldout/live-signature-20260724.json`

validation：
`artifacts/runs/common-source-small-signal-heldout/gate7b-validation-signature-20260724.json`

实际 DC bias 为 VGS=0.375 V、VDS=0.3529275 V、VSB=0；插值来自 exact 30 nm
平面上的四个 VGS/VDS 角点。

| 量 | 理论预测 | Spectre | 误差 | 门限 | 结果 |
| --- | ---: | ---: | ---: | ---: | --- |
| Id | 29.5931 µA | 27.3535 µA | 7.57% | 25% | pass |
| gm | 396.629 µS | 357.835 µS | 9.78% | 25% | pass |
| gds | 43.3402 µS | 38.5570 µS | 11.04% | 25% | pass |
| VDSAT | 0.115346 V | 0.114443 V | 0.78% | 25% | pass |
| low-frequency gain | 12.5663 dB | 12.1019 dB | 0.4644 dB | 0.5 dB | pass |
| low-frequency phase | 179.999986° | 179.999984° | 0.000002° | 5° | pass |
| phase at bandwidth | 134.6647° | 134.6143° | 0.0504° | 5° | pass |
| −3 dB bandwidth | 4.81773 GHz | 4.31764 GHz | 10.38% | 25% | pass |
| GBW | 20.4719 GHz | 17.3917 GHz | 15.05% | 25% | pass |

最终 `status=succeeded`、`gate_passed=true`。核心 artifact bindings：

| 证据 | SHA-256 |
| --- | --- |
| policy | `74a70d658a00abccced1b22558eeba937333a39a6d768c5b80d062c883d012e0` |
| characterization run | `bad02370277d6157002026a6f1a1bb70dd53cc74d95fb83e3c9e8b91e80d2a42` |
| circuit run | `a7342092fbe549171eabeccafeca5023876913e7e964df618ef1ece49c7d6a5c` |
| characterization manifest | `73e5865cf46a0a165d93726892d6ec0e6b45b381ecffff569ff1e2ee024f0a93` |
| `si` netlist | `b7d8d9812f06cbae544d15266828934dcbb987978bd0cf175b5b39ca39ff97b6` |
| testbench wrapper | `cbe0074b5483fea08c9924d0b7456d500bb993e56e5a099972f1f99307b219df` |
| raw AC PSF | `4ccc01754bba00ddb9b46a844403c0c25e5b4cd3ed6a7c7114e39ba755e5b1c8` |

OA 结构是 `bridge_readback`；`si` netlist、OP、raw AC 与 Spectre metrics 是
`eda_result`；characterization 参数声明是 `user_input`；归一化、插值、signature
比较、矩阵预测和误差 Gate 是 `software_inference`。return code 没有单独用于闭合。

## 测试与失败注入

测试覆盖 rectilinear 角点、exact L、bias extrapolation、width/signature mismatch、
参数名/值注入和保留字段、deck 参数生成、真实 `si` signature parser、nested PVT 结果
选择、demo 拒绝、OA/`si`/target/write-action 破坏、空 raw AC、CLI 持久化和固定门失败
仍为 partial。最终完整回归为 `536 passed`。

## 未验证边界与下一 Gate

1. 只有一个 `top_tt`/27 ℃/0.9 V 共源点；没有建立统计或全域误差上界。
2. exact textual signature 只证明这一组 `si` 参数；其他 W、nf/multi、扩散共享或 LDE
   组合必须生成自己的 characterization，不按线性宽度缩放冒充相同物理器件。
3. binder 使用真实 EDA DC OP，不是一个通用 nonlinear DC solver。
4. 正式 binder 当前只提升 common-source 的已验证 schema；通用矩阵核心虽已本地覆盖
   源退化和差分对，但这些 topology 尚无同等级 live held-out 证据。
5. PVT 是可选后续，不默认增加成本；切换 PDK/corner/temperature 时旧证据失效。
6. Spectre 仍是最终规格证据；本 Gate 不授权自动 OA 写回。

下一道 Gate 7C 先复用同一 common-source parser/binder/solver 到已有源极退化 cell，
只增加 RS0/NSRC 图差异和对应 exact-geometry 表。通过后再扩到含多个 MOS 和不同
polarity/角色的差分对。
