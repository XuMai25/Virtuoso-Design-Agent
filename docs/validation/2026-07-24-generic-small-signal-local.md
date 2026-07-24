# 2026-07-24 通用 MOS 小信号网络本地 Gate

## 结论

VDA 已增加不依赖电路拓扑名称的 MOS/R/C 线性网络求解核心。当前状态是：

**topology-independent small-signal matrix core locally verified; live PDK characterization and OA/si graph binding pending**

这项实现把“适当通用性”限定在可验证范围：同一器件表和矩阵 stamping 核心可以
分析不同连接图，但不宣称任意模拟拓扑自动综合，也不绕过 Spectre 做最终规格判定。

本 Gate 只做本地计算，没有调用 Bridge、没有远端计算、没有读取或写入 OA，也没有
修改第三方仓库。

## 与此前固定拓扑理论层的区别

原有 `vda theory` 明确针对 Gate 6 电流镜负载差分对，使用该拓扑的解析方程反解
电流和尺寸；它仍保留为一个 topology-specific sizing adapter。

新增 `vda small-signal` 请求中没有 `topology` 字段，额外声明该字段会被 strict
schema 拒绝。输入只包含：

- 带来源、artifact hash、PDK/corner/temperature 和偏置条件的 MOS characterization；
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

这比 topology-local 拟合更可迁移：同一个器件点可以被不同电路图中的实例引用。
但当前还没有真实 TSMC N28 表，所以示例值不能作为设计证据。

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

本地入口：

```powershell
.\.venv\Scripts\vda.exe small-signal `
  examples\theory\common-source-small-signal.synthetic.json
```

## 验证结果

```text
python -m pytest
486 passed

vda small-signal examples\theory\common-source-small-signal.synthetic.json
exit 0；complex response 和 -3 dB bandwidth resolved
```

新增测试还覆盖真实 characterization 缺 hash/证据等级拒绝、偏置不匹配拒绝、
禁止伪造 topology 字段、hierarchical/bus-style net 名不被无谓收窄、低频参考不平坦
时保持 `partial` 且禁止作为尺寸种子，以及 CLI 输出与保存 JSON 一致。

## 未闭合边界

1. 当前求解器不求非线性 DC operating point；实例必须先绑定匹配的器件偏置点。
2. 当前没有 `si` 网表到通用节点图的自动转换，样例图仍由请求显式提供。
3. 当前只实现 normal-mode MOS、R、C 和固定电压边界，没有独立电流激励、BJT、
   通用受控源、inductor、transmission line、开关模型或 hierarchy flattening。
4. 没有真实 TSMC N28/SMIC characterization、插值、corner、noise、distortion、
   slew、settling、mismatch 或大信号预测。
5. 当前 W/multiplicity 采用线性缩放；`nf`、finger width、窄宽效应、扩散共享和
   layout-dependent effect 必须作为独立 characterization 维度，不能由本模型猜测。
6. 求解成功不等于设计完成；真实指标仍必须进入 OA→`si`→Spectre 证据链。

## 下一 Gate

下一步应先做只读 TSMC N28 独立 NMOS/PMOS characterization，覆盖显式的 L、VGS、
VDS、VSB 和可选 corner/temperature，保存原始 Spectre 文件清单与 SHA-256；随后把
现有 `si` 结构网表转换为本网络契约，并把一个完整拓扑留出、不参与建表或调参，
用于检验跨拓扑预测误差。只有这个 held-out topology Gate 通过后，通用理论结果才
可以作为 Spectre 搜索种子。

该下一 Gate 涉及真实远端计算，本地实现记录不构成新的远端授权。
