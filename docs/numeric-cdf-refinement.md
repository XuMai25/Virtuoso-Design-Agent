# P1-b 首个切片：显式 CDF 数值映射与局部微调

复用原 `op-relinearize` / `design-next`，让原始 CDF 字面量进入已有的局部响应模型，
再编译回普通原子 CDF 候选；没有新求解器、执行器或 Bridge 接口。它补的是“可写
字段与可计算变量之间的接线”，不等于任意字段已有物理模型或自动最优范围。

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

## 先解释，再拟合；模型不足就停

先用已有器件表、实际连接和 OP/MNA 解释偏置、gm/gds、负载和摆幅瓶颈。已有
`small-signal-from-run` / `design-next --small-signal-policy` 仍负责理论重算，不能把单点
小信号分析等同于尺寸导数。确需局部数值建议时，再使用原来的有限扰动/留出模型：

1. 原 run 字节 SHA、task ID、real-Bridge 成功状态及每个指标的 eda_result 来源必须
   匹配；明确且互不重叠的 training/holdout，保持满秩和各参数留出方向覆盖。
2. 只允许声明的变量变化。保存记录中可见的其他语义/CDF 参数、拓扑标识、候选 testbench override、
   corner/温度/电源条件必须保持一致；条件内的测量值可以不同。否则拒绝归因。
   当前没有 testbench 变量的数值映射，不把 VBP/CL 改动默认为尺寸效果。
3. 复用现有一阶局部拟合、误差规则和排序。独立留出/训练门未过则没有可执行候选；
   不用增大容差、混入 holdout 或随机补点把它变成通过。
4. 格点量化后再次检查上下界，不能因四舍五入把 CDF 候选推到声明域外。
5. 数值别名只存在于分析模型；原子任务输出 instance_parameter_updates，不把别名
   塞给普通 executor 当成新语义旋钮。原锚点保留原始 CDF 字符串，其余点明确输出
   工程字面量并核对数值回转精度。固定字段仍在原 task 中。

结果包含 parameter_bindings、灵敏度和误差。预测、理由和排序是 software_inference；
原测量保留 eda_result。局部拟合不是 MOS 物理定律、跨 PDK/拓扑保证或全局最优证明。
相同标识或“其他字段没出现在 candidate 里”也不能证明它们在当前 OA 中没有变化。

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

新增停止判断：没有“预测满足模型约束、且目标严格优于已选锚点”的未测点，就返回
no_predicted_improvement，不生成下一轮任务。它只排除数值舍入级差异，不证明预期
收益显著、模型外无更优点或电路已经全局最优。确有诊断需要时，先说明假设和缺量；
原 op-relinearize 结果仍可查看，不因此取消原单项操作或人工指定修改的能力。

通过时仍输出原 TaskSpec/planner，compute/write/replace 为false、不得 create；只有
重新列明目标、范围、路径与风险并获批准，才走原真实执行授权。直接候选编译器仍是
原低层接口，不独立证明当前 OA/PDK 与历史模型连续，不能拿它规避 design-next 的审查。

## 尚未闭合

新 CDF 数值建议真实执行、callback 后实际值/si 一致性、新点性能和新模块泛化本轮
均未验证；仅完成本地编译/防误归因，以及旧保存记录的复算。更多隐含单位、表达式、
testbench 数值变量和未知物理语义按真实需求接入，不新增一套优化框架或为此造电路。
详见[本地验证记录](validation/2026-10-23-p1b-numeric-cdf-local.md)。
