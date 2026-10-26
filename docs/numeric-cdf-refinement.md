# P1-b：显式数值映射与局部微调

复用原 `op-relinearize` / `design-next`，让原始 CDF 字面量进入已有的局部响应模型，
再编译回普通原子 CDF 候选；没有新求解器、执行器或 Bridge 接口。它补的是“可写
字段与可计算变量之间的接线”，不等于任意字段已有物理模型或自动最优范围。
现也支持将已有 generic testbench 的 DC 源/电阻或电容负载显式映射为同一模型的变量，
与尺寸联合处理；其他激励和条件仍固定，不增加求解器、仿真器或自动补点。

## 映射由 agent/用户声明，不能按名字猜

原 policy 的参数可增加 instance_parameter：

```json
{
  "name": "cascode_width_um",
  "minimum": 0.75,
  "maximum": 1.25,
  "proposal_step": 0.125,
  "quantization_grid": 0.005,
  "instance_parameter": {
    "instance": "MNCAS",
    "parameter": "Wfg",
    "unit": "um",
    "literal_format": "spectre_si",
    "meaning": "每指宽度，不是总宽；nf/simM、其他几何与偏置须保持固定。"
  }
}
```

这些值只是[旧 cascode 记录复算示例](../examples/theory/cascode-numeric-cdf-replay-policy.json)，
不是所有电路/PDK 的默认值。上下界、步长和网格都用声明的模型单位。

- spectre_si 明确表示原 CDF 字符串可按 Spectre SI 字面量解释：`750n`、`0.75u`、
  `7.5e-7` 均可对应0.75 um。裸数字按 SI，不推断某个 PDK 编辑框的隐含单位。
  缺少格式/单位/含义则拒绝，不回退猜测；表达式、变量、向量、未知后缀、非有限值、
  零或负值不进入这一正值局部模型。不能用通用解释替代真实 CDF/网表核对。
- 支持模型单位 m/um/nm、ohm/kohm、F/pF/fF、V/mV、A/uA 和1。单位1不自动赋予整数
  语义；nf/m/开关或离散器件选项仍须正确的显式量化/合法性说明，不能当连续变量乱插值。
- meaning 为物理解释，不是 EDA 证明；policy 可标 software_inference。不得把 agent
  选择的范围、误差门限或优化目标标成用户已确认。原语义 policy 默认来源保持不变。
- 同一 CDF 字段不能映射成两个独立变量，别名不能遮蔽记录中的语义参数。无映射的
  原语义参数继续使用原路径，其 policy/result 不新增空字段或改变旧 policy 哈希。

这个映射**不是** OA→si parameter binding discovery。实际 task 仍需原 generic_simulation
中的 master/端子/parameter binding、权限及 callback 规则；这些检查不被绕过。
原 parameters.apply 和其他原始 CDF/Bridge 能力不受此数值模型的格式/单位限制。

## 一个变量联动多个等值字段

通用 existing_schematic 的匹配输入对/镜像支路不能依赖某个固定模板代替联动：若两只
器件的宽度始终一起变化，把它们声明为两个独立模型变量会使扰动矩阵不满秩；只声明
一只又会正确触发“未建模的另一只字段发生变化”。可在原 instance_parameter 下显式加
`linked_parameters`，以**一个自由度**生成多个普通 CDF updates。例如这个本地语法片段：

```json
{
  "name": "matched_width_um",
  "minimum": 0.8,
  "maximum": 1.3,
  "proposal_step": 0.05,
  "quantization_grid": 0.005,
  "instance_parameter": {
    "instance": "MN0", "parameter": "w", "unit": "um",
    "literal_format": "spectre_si",
    "meaning": "本地示例中的两个等值宽度字段；不代表真实 PDK、匹配质量或推荐尺寸。",
    "linked_parameters": [{"instance": "MN1", "parameter": "w"}]
  }
}
```

这是 policy.parameters 的一个条目，不是完整可执行任务。实例、字段、单位/含义、
范围及权限必须按当前真实设计确认；例中的 w 不自动替代其他 PDK 的 Wfg 或总宽含义。

- 主字段和所有 linked 字段共同使用明确声明的 unit、literal_format、范围、网格和含义。
  训练/留出各点必须完整出现且 SI 数值相等（只容许1e-12级相对表示误差）；不同字面量
  如1.1u与1.1e-6可等值。成员缺失、不等值、表达式或其他未建模字段漂移均拒绝。
- 锚点分别保留每个成员的原字符串；新点逐成员输出相同数值，每个字段的原权限、
  binding、CDF callback、OA/si 回读与 checkpoint 规则照常适用。不得漏掉某一成员。
  一个字段不能重复出现或被两个模型变量控制；所有成员都必须可在声明范围内搜索。
- 摘要显示 MN0.w = MN1.w，并列出各成员的修改；post-run audit 仍逐字段对账，不能
  用主器件值代替其他器件回读。单字段旧 policy 不增加空 linked 字段，哈希保持兼容。
- 默认是声明的等值关系，不是自动识别器件匹配或全局1:1规则；显式比例/偏移见下文，
  尤其不把 NMOS/PMOS 默认绑成相同宽度。不要把 callback 派生字段当独立可写成员。
  同一候选的联动更新不是跨实例 OA 写入的底层原子事务，部分失败仍按原恢复规则对账。

沿用下面的 op-relinearize / design-next / op-relinearization-validate 入口，没有新增操作
或 solver。已完成本地模型→候选→安全任务→审计回归，见
[本地验收](validation/2026-10-23-p1b-linked-cdf-local.md)。另有一次既有 N28 电路的
两尺寸＋wrapper 偏置/负载原子候选真实执行；它不是校准模型→linked_parameters
编译→真实执行的全路径证明，见[真实范围](validation/2026-10-23-p1b-joint-input-live.md)。

## 偏置、负载与尺寸联合微调

原 policy.parameters 的每个条目选择原语义变量、instance_parameter 或 testbench_parameter
中的一种；不能同时映射 CDF 与 testbench。同一模型可混合这些变量，成组 CDF 仍只占
一个自由度。以下两个条目可与前面的尺寸条目放入同一 parameters 列表：

```json
[
  {
    "name": "bias_mv", "minimum": 350, "maximum": 450, "proposal_step": 10,
    "testbench_parameter": {
      "target": "source_dc", "name": "VIN_SRC", "unit": "mV",
      "meaning": "本地合成示例的输入偏置源 DC，不是器件 VGS 或推荐偏置。"
    }
  },
  {
    "name": "load_ff", "minimum": 3, "maximum": 7, "proposal_step": 0.5,
    "testbench_parameter": {
      "target": "load", "name": "CL0", "unit": "fF",
      "meaning": "本地合成示例的显式电容负载，不是任意设计的默认负载。"
    }
  }
]
```

这只是经过本地回归的语法片段，不是完整任务、真实校准或新规格。字段/含义、范围和
步长仍由当前设计的理论与已有证据决定；不能照抄这些示例值执行。

- source_dc 只映射已记录源的 dc_value：支持 V/mV 或 A/uA。零/负 DC 可显式声明，
  表示源端子方向下的数值，不自动解释成器件 VGS、过驱或安全电压。原语义/CDF/负载
  模型的正值范围要求不放宽。load 映射已记录负载的 value：支持 F/pF/fF 或 ohm/kohm。
- GenericTestbenchOverrides 内仍用 SI 数值，模型按声明单位换算；建议选择 mV/uA/fF/
  kohm 等适当量级，原量化/范围规则不变。编译时还会与 generic_simulation 的实际
  voltage/current/capacitor/resistor 类型核对；名字不存在或单位维度不匹配则拒绝。
- 每个训练/留出点必须显式记录全部变量；缺 dc_value 或 load value 时不能从基础
  setup 猜补。source 的 AC 幅相、未建模的 DC/负载、其他参数、拓扑和工况仍固定。
  会被 operating_conditions 覆盖的供电源不允许作为这一模型的 DC 坐标。
- 候选从锚点复制完整 typed override，只替换明确建模的值，不改写基础 setup/OA。
  结果的 testbench_parameter_bindings 记录映射；fixed_testbench_overrides 只记录
  剩余固定字段。没有 testbench 映射时，该字段仍按旧路径表示完整固定 override。
- design-next 展示尺寸和源/负载各自的前后值及模型单位，输出普通零权限 TaskSpec；
  原编译器/审计检查变量没有丢失、其余字段固定，并对账逐候选与最终选择的完整元组。
  这是记录/任务一致性，不是对实际 simulator input 的独立解析。
- 只覆盖原 generic_simulation 中的源和负载，不自动修改 OA 中的偏置源 CDF、ADE
  变量或模型 include；这些仍走各自已有显式路径，不受此数值模型限制。

“联合”仍是局部一阶响应的多个独立项，不新增交叉项、全局最优或任意拓扑物理模型。
d 个独立变量至少需要锚点加 d 个独立训练点，并保留互不重叠的 holdout；点数够还不
等于满秩、误差合格或适用整个范围。偏置总随尺寸一起变化，或负载从未变化时，不能
硬归因、把无数据的灵敏度设为零，或放宽原误差门来生成候选。

现有 PMOS 三点仅有尺寸/偏置变化、负载固定，不足以建立带独立留出的联合模型；本轮
没有拟合它、没有新任务或远端补点。实现/数据缺口详见
[本地验收](validation/2026-10-23-p1b-joint-testbench-local.md)。

### 等值偏置或负载组共用一个变量

差分输入共模偏置、对称输出负载等需要多项同步变化时，可在原 testbench_parameter
内显式追加 `linked_names`。它们与尺寸一起进入原局部模型，但一组只占一个自由度：

```json
{
  "target": "source_dc", "name": "VINP_SRC", "linked_names": ["VINN_SRC"],
  "unit": "mV",
  "meaning": "语法示例：两个已声明电压源的 DC 等值联动；各自 AC 幅相仍固定。"
}
```

对负载可相应声明 `target: "load"`、`name: "CLP"`、`linked_names: ["CLN"]` 和
`unit: "fF"`。数值表示**每个成员**的值，不是两侧总电容。这只是映射语法，不是
完整任务、默认接线/尺寸或新的真实差分电路验收。

- primary 与 linked 成员须在每个训练/留出点完整显式记录，单位换算后等值；缺失、
  不等值或一个成员被另一模型变量控制均拒绝。等值比较不使用会吞掉 fF 差异的绝对
  容差。同一组只能是同类型 DC 源或同类型负载；不自动识别关系，比例/偏移须按下文显式声明。
- 仅 DC/负载 value 参与联动。不同成员的 AC 幅值、相位、端子连接和其他固定字段
  各自保留；例如两输入反相 AC 不会被“同一个偏置变量”改成同相。源 DC 仍可显式
  为零或负值，这不赋予它器件 VGS 等物理含义。未声明的条件变化继续拒绝。
- 编译逐成员检查源/负载存在性、类型与单位；任一成员被 operating_conditions
  当作供电源覆盖时拒绝。候选展开为原 typed override，中文摘要与 JSON 列出全部
  目标；写回/仿真权限不扩大，后续审计仍比较每个候选及最终选择的完整元组。
- 不加 linked_names 的旧 policy/result 不新增空字段，保留原哈希与单字段行为。
  已有差分对 semantic common_mode/load、直接改参和 ADE 路径继续可用，不强制改用
  此映射。也不把 CDF 字段与 wrapper 源混成一个隐含联动关系。

源码、旧模型复算与未验证边界见
[本地记录](validation/2026-10-23-p1b-linked-testbench-local.md)。没有新建或仿真真实电路。

## 比例和固定偏移：仍是一个自由度

联动成员现可显式声明 `member = scale × primary + offset`。scale 无量纲，offset 和
可选的成员 quantization_grid 都使用该组声明的模型单位，不是自动按 SI 猜测。
例如以下只是本地语法示例，不是推荐尺寸、真实 PDK 映射或完整执行任务：

```json
{
  "instance": "MN0", "parameter": "w", "unit": "um",
  "literal_format": "spectre_si", "meaning": "显式声明的局部宽度关系，仅作语法示例。",
  "linked_parameters": [
    {"instance": "MN1", "parameter": "w", "scale": 2, "offset": 0.1, "quantization_grid": 0.005}
  ]
}
```

testbench 的 linked_names 既保留原字符串等值写法，也可用对象，例如
`[{"name":"VINN_SRC","scale":-1,"offset":900}]`，组单位 mV 时表示两源 DC 之和
为900 mV；各源原 AC 幅相不变。这不是自动确定差模或共模激励。

- 比例不能为零；常量继续放固定条件，不伪装成自由度。允许负比例，但整个声明范围
  内的 CDF/负载仍须为正；源 DC 可以有符号。成员缺失或实际值不符合关系时拒绝。
- 依赖成员可单独声明实际 PDK 网格。提案若不满足该网格就剔除并记录原因，绝不把
  成员各自取整而破坏比例。已有训练点也必须符合声明的关系/成员网格。
- 编译逐成员写实际派生值并保留原锚点字面量；回读/审计不能只看 primary。旧等值
  声明不新增默认 scale/offset/grid 字段，保留旧哈希。没有引入任意表达式求值器。
- 不跨 CDF 与 wrapper 源建立隐式关系，不自动确认匹配质量；未知字段仍走原单次
  parameters.apply/Bridge 能力，不强迫进入数值模型。

## 数据不足时，先给可操作的缺口

原 policy 可以原样交给只读检查入口，允许诊断尚不足以拟合的数据：

```powershell
.\.venv\Scripts\vda.exe op-relinearize <policy.json> <run.json> --check-only --output <new-data-check.json>
.\.venv\Scripts\vda.exe design-next <task.json> <run.json> --data-policy <policy.json> --output <new-next.json> --markdown <new-next.md>
```

检查给出缺失/非 EDA/混杂点、独立训练方向、实际重复的 holdout、逐轴扰动覆盖、
从未变化的可固定轴，以及“其余坐标固定在锚点”的历史单轴切片。d 个独立变量的
点数下界仍是 d+1 个训练点加至少一个未见留出点；够数不等于满秩或误差合格。
它不自动重分 training/holdout、拼接不同 run、拟合缺失灵敏度或生成补点任务。
check-only 的 CLI 数据可用返回0，不足返回2；严格 fitter 仍拒绝数据不足的声明。

现有 PMOS 三点在三变量声明下明确缺少负载训练/留出方向、宽度留出方向以及独立
训练点；也没有足够的单轴旧点可以带留出校准。可明确把负载固定为2 fF，但剩余两轴
仍需自己的独立证据。2 fF 是这份旧记录，不是默认负载。诊断本身是 software_inference。

## 有预测改善，还要判断是否值得继续

design-next 在原模型门、已选锚点和规格连续性检查后，增加轻量收益判断：默认预测
目标改善必须超过该模型目标指标的最大历史留出**绝对残差**。可选 --decision-policy
进一步声明绝对改善、相对改善百分比、建模约束的最小预测余量和 action 时间上限。

[策略模板](../examples/theory/refinement-decision-policy.template.json)中的1%仅是
software_inference 语法示例，不是用户规格或全电路默认值；应按当前目的审阅修改。
不提供文件时相对/绝对改善门为零、残差倍率为1，不要求默认追加分析。

```powershell
.\.venv\Scripts\vda.exe design-next <task.json> <run.json> `
  --model <model.json> --policy <policy.json> --task-template <template.json> `
  --decision-policy <reviewed-benefit-policy.json> --output <new-next.json> --markdown <new-next.md> --task-output <new-task.json>
```

预测增益未超过门限、余量不足，或显式成本上限无法估计/被超过时，保留诊断而不出
草案。约束余量使用各指标本身单位；未被模型覆盖的约束不能填一个伪预测余量。
即使同时给了 --task-output，停止时也保留新诊断文件并返回2，不写空任务、不删除或
覆盖该路径已有草案；自动化调用应检查退出状态和本次 report，不能捡起旧任务执行。
成本仅由旧 action 总时长/旧候选数乘本次编译候选数粗估，包含旧流程开销，既不是
增量模拟器成本也不是 agent/人工总时长；缺时间就是未知。历史残差不是误差上界、
统计置信区间或全局最优证明。允许出草案不等于应执行，更不等于实测收益。

## 上轮失准后，显式刷新而非继续外推

`design-next <completed-task> <run> --prior-model <previous-model> --prior-policy <previous-policy>`
复用原 op-relinearization-validate，重新比较本次每个候选实际值与旧预测，分开报告
执行完整性、模型精度和推荐一致性。失准则输出 local_model_refresh_required，记录
新选点的输入指纹及刷新步骤，不撤销真实 EDA 选优、不自动放宽误差门限。

已有足够的同上下文数据时，可同时传入显式重拟合后的 --model/--policy/--task-template，
以新选点为锚再过原留出、收益和安全编译门；数据不足则用 --data-policy 解释缺口。
拓扑、PDK、模型或工况发生变化时不能只换锚点沿用旧导数，需要新证据。

历史 planner 版本仅可通过已有 exact-byte --binding-audit 绑定原 task/run。该身份
审计不替代重算预测误差；不改旧 token/记录。验证器只为最早 semantic-only schema
提供一个已知兼容投影：原 policy 未声明 include_measured_anchor、旧模型未存逐项
relative_error_floor 时，允许核对该选项出现前的原 canonical hash。仍须精确哈希、
原误差门限和归一化定义，不以当前默认值猜补，更不重新生成旧候选域。

## 实际输入字节检查：保留真实未验证边界

generic existing_schematic worker 现独立解析 VDA 自己输出的 literal wrapper，核对
源/负载集合、端子、DC/AC 幅相和值，以及确切 OA include。它不是通用 SPICE parser，
不展开/读取 PDK 模型文件，也不独立证明全部 sweep、模型选项或电路性能。

Bridge 会把 wrapper 再传到自己的 UUID 子目录。已有有界 Spectre launcher 现于这个
**最终上传副本**启动前核对 wrapper 与原始/转换后 OA include 的 SHA-256，并在同
作业 raw 目录非覆盖写 vda-input-manifest.sha256；下载后必须核对回执。输入漂移或
回执缺失会失败，不允许仅凭模拟器成功继续。检查仍在原 timeout 内，不增加逐候选
SSH 往返，不复制或修改 Bridge 的 runner/传输实现，产物只在原批准 scratch 内。

本地真实 shell＋替身程序已覆盖匹配、上传后变化、依赖漂移、错 raw 路径和回执冲突；
旧 STB 原始输入束也已离线核对。随后既有 N28 PMOS-loaded CS 的3个 DC＋AC 作业均
通过新 launcher 的实际输入回执；旧作业仍不能事后补造回执。实机暴露的转换后 OA
include 也已固定 LF，并按上传文件实际字节计算哈希，不只固定 wrapper。见
[本地记录](validation/2026-10-23-p1b-refinement-completion-local.md)和
[真实验收](validation/2026-10-23-p1b-joint-input-live.md)。

数值型 CDF 的 callback 可能把475n显示为475.00n。targeted verifier 现独立读取实际
值及 parseAsNumber，仅数值型、有限普通标量接受工程字面量等值；实际格式保留在
confirmed/readback，不伪造请求回显。表达式/标识符/普通字符串仍须严格匹配，请求
echo 和 checkpoint 状态比较也不放宽。此规则不替代真实 PDK 合法范围、网格或 si binding。

## 先解释，再拟合；模型不足就停

先用已有器件表、实际连接和 OP/MNA 解释偏置、gm/gds、负载和摆幅瓶颈。已有
`small-signal-from-run` / `design-next --small-signal-policy` 仍负责理论重算，不能把单点
小信号分析等同于尺寸导数。确需局部数值建议时，再使用原来的有限扰动/留出模型：

1. 原 run 字节 SHA、task ID、real-Bridge 成功状态及每个指标的 eda_result 来源必须
   匹配；明确且互不重叠的 training/holdout，保持满秩和各参数留出方向覆盖。
   不仅 candidate index 必须互斥，留出点的实际参数元组也不得与训练点相同；CDF 的
   1u/1e-6 等值拼法仍是同一点。不按候选网格舍入后判断，避免把不同点混为一谈。
   同点重跑仍可保留作重复性证据，但不能据此验证未见参数点的局部预测。
2. 只允许声明的变量变化。保存记录中可见的其他语义/CDF 参数、拓扑标识、候选 testbench override、
   corner/温度/电源条件必须保持一致；条件内的测量值可以不同。否则拒绝归因。
   只有明确映射的 testbench DC/负载可变化，不把未声明的 VBP/CL 改动默认为尺寸效果。
3. 复用现有一阶局部拟合、误差规则和排序。独立留出/训练门未过则没有可执行候选；
   不用增大容差、混入 holdout 或随机补点把它变成通过。
4. 格点量化后再次检查上下界，不能因四舍五入把 CDF 候选推到声明域外。
5. 数值别名只存在于分析模型；原子任务输出 instance_parameter_updates，不把别名
   塞给普通 executor 当成新语义旋钮。原锚点保留原始 CDF 字符串，其余点明确输出
   工程字面量并核对数值回转精度。固定字段仍在原 task 中。

结果包含 parameter_bindings、灵敏度和误差。预测、理由和排序是 software_inference；
原测量保留 eda_result。局部拟合不是 MOS 物理定律、跨 PDK/拓扑保证或全局最优证明。
相同标识或“其他字段没出现在 candidate 里”也不能证明它们在当前 OA 中没有变化。

## 旧点何时可以减少新候选

模型只使用显式 training/holdout；原 run 中其余点可以用于避免重复计算，但必须再次
核对完整 EDA 证据、全部模型指标的来源、相同固定参数/CDF/拓扑/testbench/工况，
以及实际数值与当前候选格点一致。其他长度、源极电阻或固定偏置下的点，未完成的点，
或只有软件指标的点，都不能替当前候选充当已测值。附近1.149不能因舍入成1.15而证明
1.15已经测过；真正同上下文的精确格点仍会排除，不因此重复执行。

不适合判重的历史点仍保留，原因写进 model.notes 并在 design-next 摘要展示；这不是
把它们加入训练、删除旧记录或放松数据门。若整个 run 仍有未完成/待对账状态，原
reconcile_execution_first 会继续阻止下一任务，不因“不算已测”就自动重试。
新说明可能改变模型文件指纹：旧模型被确定性重算拒绝时，另存重算结果/草案/新 token，
不能修改旧 run、audit 或 checkpoint。见[本地记录](validation/2026-10-23-dual-validation-p1b-local.md)。

## 固定偏置和负载不能在生成候选时丢失

源记录各点完全相同的 typed testbench override（例如源 DC/AC 值、负载值）不是新的
模型变量，但仍是拟合适用条件。未映射时，结果在 `fixed_testbench_overrides` 保存完整
条件，并把它复制到每个新候选；使用 testbench 映射时只剔除明确建模的字段，保留
其余固定条件，完整候选仍在 `AtomicCandidate.testbench_overrides`，不改基础 setup/OA。
`design-next` 的 JSON/中文摘要展示固定条件；模板缺少相应源或负载时由原 typed
校验拒绝，不能静默退回基础值。没有 override 的旧路径不新增该空字段，保持原任务兼容。

原 `op-relinearization-validate` 除参数/原子候选身份/指标外，现在还比较逐候选和最终
选择的 override；相同参数和指标不能掩盖不同的偏置或负载。编译器也检查模型内候选
与声明固定条件一致。未建模的 override 变化仍拒绝拟合，须先显式声明对应的数值映射。
这些条件来自保存记录，不是新鲜 Bridge 回读；对记录值的匹配也不是实际 simulator
input 已应用的独立证明。真实执行仍须原 OA→si/ADE 输入与结果证据。

修复前若模型在源记录含固定 override 时丢失了它，`design-next` 的确定性重算将拒绝旧
模型；应以原 run/policy 本地重算到新文件，再审阅普通任务和新 plan。不要补改原记录、
旧 token 或 checkpoint；低层候选编译器不能替代这一步源模型重算和上下文审查。
本地重现与旧记录兼容检查见[修复验收](validation/2026-10-23-model-testbench-context-local.md)。

## 使用原入口，不增加一次远端调用

只复算旧记录：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent op-relinearize `
  examples/theory/cascode-numeric-cdf-replay-policy.json `
  artifacts/runs/existing-schematic-generic-cascode-ac-tune/live-20260729-resumed.json `
  --output artifacts/my-numeric-cdf/model.json
```

旧 artifacts 只保留在本机，缺少时不要造一份 run 替代。此示例只做后验数学复算，
不证明它与当前 task/PDK 一致：旧 cascode 的现版本 task/run 绑定不通过，原 task
也未定义优化 objective。示例中的 GBW 最大化和2%/5%门限是 software_inference 的
复算假设，不是预注册的新设计验收要求；本次未据此创建下一轮任务。

实际需要任务草案时，优先 `design-next <task> <run> --model <model> --policy <policy>
--task-template <template>`，再加新路径的 --output/--markdown/--task-output。它沿用
原历史身份/audit、源模型确定性复算、已选锚点、目标/PDK/上下文/规格/目标一致性
检查，并展示原 CDF 值→建议字符串和模型单位。不能伪造历史 token/audit 绕过绑定。

没有“预测满足模型约束、且目标严格优于已选锚点”的未测点，就返回
no_predicted_improvement，不生成下一轮任务；有改善仍须经过上述收益/误差/余量/成本门。
这些都不证明模型外无更优点或电路已经全局最优。确有诊断需要时，先说明假设和缺量；
原 op-relinearize 结果仍可查看，不因此取消原单项操作或人工指定修改的能力。

通过时仍输出原 TaskSpec/planner，compute/write/replace 为false、不得 create；只有
重新列明目标、范围、路径与风险并获批准，才走原真实执行授权。直接候选编译器仍是
原低层接口，不独立证明当前 OA/PDK 与历史模型连续，不能拿它规避 design-next 的审查。

## 尚未闭合

已有一份普通原子候选的双 Wfg＋wrapper DC/CL 真实执行、callback/si/实际输入一致性
与精确原值恢复通过；不是联合模型建议有效性或完整 linked_names 泛化。该 A/B/A
只有一个新联合方向，不能消除 PMOS 历史点缺独立训练/留出的事实，没有据此拟合。
新模块泛化、更多隐含单位/表达式/testbench量与未知物理语义仍按真实需求接入，
不新增优化框架或为补模型而立即造电路/追加计算。
详见[本地验证记录](validation/2026-10-23-p1b-numeric-cdf-local.md)。
