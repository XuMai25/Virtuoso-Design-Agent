# 2026-07-24 通用 MOS 小信号网络本地 Gate

## 结论

VDA 已增加不依赖电路拓扑名称的 MOS/R/C 线性网络求解核心。当前状态是：

**topology-independent small-signal matrix core locally verified; nominal/source-degenerated common-source live-bound; multi-artifact differential-pair binder locally verified**

后续状态：同日已完成[TSMC N28 独立 MOS characterization 真实 Gate](2026-07-24-tsmc28-mos-characterization-live.md)。240 点 nominal `top_tt` NMOS/PMOS artifact 与四个真实留出点已通过，并能直接进入本页的 schema/kernel。故当前剩余边界已收窄为 OA/`si` 图与 DC 偏置自动绑定、正式表内插值接口和完整 held-out circuit 的同源 Spectre 对照；本页以下 synthetic 解析测试仍作为数值层基线。

进一步的[Gate 7B 共源真实验证](2026-07-24-common-source-small-signal-validation-live.md)
已完成上述首个纵向点：exact-geometry 表、只读 OA/`si` 图、EDA DC bias 与原始 AC
网格全部绑定，固定 DC/gain/phase/BW/GBW 门通过。[Gate 7C](2026-07-24-source-degenerated-small-signal-migration-live.md)
又以同一 binder/kernel 通过源极退化共源 held-out Gate，没有增加专用增益公式。

本次继续补齐多 artifact 契约和电流镜负载真实尾管差分对 policy。本地 synthetic Gate
使用三张独立表分别绑定输入 NMOS、PMOS 负载和 NMOS 尾管，并覆盖非 1:1 W；缺失
PMOS 表、重复匹配、宽度漂移、模型参数签名漂移和未使用表均拒绝。该结果只提升
schema/binder/policy，本轮没有调用 Bridge 或 Spectre，不能外推为 live 差分对精度。

这项实现把“适当通用性”限定在可验证范围：同一器件表和矩阵 stamping 核心可以
分析不同连接图，但不宣称任意模拟拓扑自动综合，也不绕过 Spectre 做最终规格判定。

本 Gate 只做本地计算，没有调用 Bridge、没有远端计算、没有读取或写入 OA，也没有
修改第三方仓库。

## 与此前固定拓扑理论层的区别

原有 `vda theory` 明确针对 Gate 6 电流镜负载差分对，使用该拓扑的解析方程反解
电流和尺寸；它仍保留为一个 topology-specific sizing adapter。

新增 `vda small-signal` 请求中没有 `topology` 字段，额外声明该字段会被 strict
schema 拒绝。输入只包含：

- 一张或多张带来源、artifact hash、PDK/corner/temperature 和偏置条件的 MOS characterization；
- MOS 实例的 model、D/G/S/B 节点、W/L/multiplicity 和所选 characterization 点；
- 电阻、电容、固定 AC 边界节点；
- 输入和输出的节点线性表达式；
- 显式频率点。

因此，共源、源极退化、差分连接或电流镜等区别由实例/节点图表达，而不是在求解器
中选择不同的拓扑公式。

## 器件 characterization 契约

每个 MOS 点独立于其电路角色，保存：

```text
model, polarity, L
|VGS|, |VDS|, |VSB|, |VDSAT|
Id/W, gm/Id, gds/Id, gmb/Id
Cgs/W, Cgd/W, Cgb/W, Cdb/W, Csb/W
```

实例由 `Id=(Id/W)*W*multiplicity` 以及各归一化比值生成 `gm/gds/gmb` 和电容。
实例 model、L、VGS、VDS、VSB 必须与所选点在声明容差内匹配。synthetic 点只能标为
`user_input` 且不能声明 artifact hash；`pdk_characterization` 或
`eda_operating_point` 必须把原始数据绑定为带 SHA-256 的 `eda_result`，归一化点值
另标为 `software_inference`。

每个 MOS 实例保存 `characterization_id`；多张表的 ID 必须唯一、各自保留 raw artifact
hash，且属于同一 PVT 和证据边界，每张表都必须被至少一个实例使用。自动 run-record binder 再按 model、
polarity、W/L、完整 `si` 参数签名和可选来源实例选择唯一 artifact，不能以列表顺序
猜测器件角色。

这比 topology-local 拟合更可迁移：同一个器件点可以被不同电路图中的实例引用。
Gate 7A 现已有真实 TSMC N28 表；本页 synthetic 示例值仍不能作为设计证据，而真实
artifact 也必须先绑定实际电路 DC 偏置并通过 held-out Spectre 对照。

## 通用网络求解

求解器对所有电路使用同一过程：

```text
1. 由 MOS OP 比值和 W/m 生成小信号参数
2. 对 MOS、R、C 按物理端子连接组装复数 Y(f) 矩阵
3. 把输入、VDD、bias 等声明节点作为固定 AC 边界
4. 解 Yuu*Vu = -Yuk*Vk
5. 用任意节点线性表达式计算输入、输出和 transfer
6. 由复数响应提取低频增益、相位和首个 -3 dB 带宽
```

矩阵奇异/病态、浮空节点、零输入表达式、重复实例、未使用边界、未知器件点、model/L
或偏置不匹配都会显式拒绝。所有矩阵推导和指标均为 `software_inference`。

## 跨拓扑测试

同一个 `analyze_small_signal_network()` 已覆盖：

1. NMOS 共源：解析低频增益 `9.0909 V/V`，一阶极点约 `17.5 MHz`；
2. NMOS 源极退化共源：`gm=1 mS`、`RD=10 kΩ`、`RS=1 kΩ` 得到
   transfer `-5 V/V`；
3. 对称 NMOS 差分对：`±0.5 V` 差分激励、`RD=10 kΩ` 得到差分增益
   `10 V/V`，TAIL 小信号电压为零；
4. PMOS 共源：同一物理端子 stamping 得到正确反相增益；
5. 浮空输出导致矩阵奇异时拒绝，而不是返回一个伪指标。

这些测试没有调用 topology-specific 方程或分支。

## 电路脚本扩展接缝

严格 `vda small-signal` 请求继续只接受已验证的 MOS/R/C element。其下的数值层现已
拆成两个公开入口：

- `ComplexNodalSystem`：提供 KCL coefficient、RHS、二端 admittance 和独立电流
  injection；具体电路脚本可以据此加入局部受控源或频率相关 stamp；
- `solve_complex_linear_system`：直接求有限复数方程组，允许脚本在普通节点之外
  增加支路电流等 MNA auxiliary unknown。

新增测试在不修改正式 element schema 的情况下，由测试侧脚本实现一个 VCCS stamp，
得到 `gm=1 mS`、负载导纳 `100 µS` 时的 `-10 V/V`；另用 `1 mA` 电流探针和
`10 kΩ` 端口得到 `10 V`，并用三未知量 MNA 解出跨两个节点的理想 `1 V` 电压源。
未知节点、非有限系数、非零 ground 和非法 pivot tolerance 均拒绝。

这是 Agent 为具体电路编写薄脚本的受控接缝，不是任意代码进入 VDA 任务的插件入口。
新 stamp 的结果仍是 `software_inference`；只有在语义重复、加入 strict schema、失败
测试并完成 Spectre 数值对照后，才可把它写入 capability catalog。当前 dense Python
solver 也不宣称适用于大型或强病态网络。

本地入口：

```powershell
.\.venv\Scripts\vda.exe small-signal `
  examples\theory\common-source-small-signal.synthetic.json
```

## 验证结果

```text
python -m pytest
556 passed

vda small-signal examples\theory\common-source-small-signal.synthetic.json
exit 0；complex response 和 -3 dB bandwidth resolved
```

新增测试还覆盖真实 characterization 缺 hash/证据等级拒绝、偏置不匹配拒绝、
禁止伪造 topology 字段、hierarchical/bus-style net 名不被无谓收窄、低频参考不平坦
时保持 `partial` 且禁止作为尺寸种子，以及 CLI 输出与保存 JSON 一致。

## 未闭合边界

1. 当前求解器不求非线性 DC operating point；实例必须先绑定匹配的器件偏置点。
2. run-record binder 已能把 nominal/源退化共源和当前 active-load 差分对 policy 的
   结构化 `si` 图转换成 MOS/R/C 网络；这仍不是任意 `si` hierarchy/element 的自动转换。
3. 正式 JSON 只实现 normal-mode MOS、R、C 和固定电压边界；低层脚本 API 已能表达
   电流注入、自定义受控源 stamp 和 raw MNA，但这些还不是带 schema/evidence record
   的正式 BJT、inductor、transmission line、开关或 hierarchy flattening 能力。
4. 已有 nominal TSMC N28 characterization 和 operation 内局部留出审计，但没有 SMIC、
   PVT/corner、正式任意偏置插值、noise、distortion、slew、settling、mismatch 或大信号预测。
5. 当前 W/multiplicity 采用线性缩放；`nf`、finger width、窄宽效应、扩散共享和
   layout-dependent effect 必须作为独立 characterization 维度，不能由本模型猜测。
6. 求解成功不等于设计完成；真实指标仍必须进入 OA→`si`→Spectre 证据链。

## 下一 Gate

Gate 7B/7C 已完成 nominal 与源极退化共源。下一步生成输入 NMOS、PMOS 负载和
NMOS 尾管各自的 real-si exact-signature characterization，再对同一个 held-out 电流镜
负载差分对运行只读 OA→`si`→Spectre 对照。该 live Gate 必须继续绑定全部三个 run、
netlist 和 artifact hash，并按目标、OA/compute 副作用及远端路径单独授权。
