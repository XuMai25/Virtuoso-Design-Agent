# 设计理由与物理变量细化（2026-10-26，本地能力）

本轮完成升级计划第一、第二批：把可审阅假设接入原 intake/next/iterate，并把显式
电流/gm、gm/ID、长度、偏置和负载映射为原参数候选。没有新增 operation、求解器、
执行器、Bridge API、云端依赖或远端授权。不是自动电路识别、通用最优解或完整 L5B。

## 1. 最短入口

已有 PMOS 共源配置的结构化理由示例（不依赖本地历史产物）：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent design-intake `
  examples/intake/pmos-loaded-cs-reasoning.json `
  --output <new-context.json> --markdown <new-context.md> --task-output <new-safe-task.json>
```

这里只整理原有候选，不计算新尺寸或运行它们。没有 baseline run 的预期保持
not_evaluated，不能把任务内的 predicted_metrics 当实测。

物理换算的明确合成示例：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent design-intake `
  examples/intake/physical-refinement.synthetic.json `
  --output <new-physical-context.json> --markdown <new-physical-context.md>
```

此例复用原 cascode AC 任务，仅演示机制：假设 Id=20 µA、gm/ID=15 V⁻¹，合成插值
得到 Id/W=20 µA/µm，则总宽度为1 µm。这些不是 TSMC 器件数据或推荐值。
合成来源、缺 measurement_plan/明确任务温度等分别保留，task_draft=null；不得通过
改 source 标签绕过。给此例 --task-output 会拒绝，不留半份输出。

## 2. 理由文件：观察与解释分开

intake 请求新增可选 reasoning 路径；旧 rationale 原样兼容。完整示例见
[理由文件](../examples/reasoning/pmos-loaded-cs.json)。关键字段：

- task/task_sha256：理由所依据的原任务和精确文件字节哈希。
- 可选 run/run_sha256、candidate_index、binding_audit：原已绑定记录；多候选未选中时
  必须明确 candidate。历史 audit 沿用原加载器，不生成或修改旧 token。
- hypotheses：每项分别记录 observation、mechanism、evidence、controls、expectations、
  tradeoffs、falsification。解释和结果对照均为 software_inference。
- uncertainties：影响下一步的未决问题。存在时不生成相关调优/计算草案；必要的原只读
  对账建议仍可保留，不用疑点掩盖执行故障。

evidence 支持三种有界引用，不执行表达式或脚本：

| kind | selector | 证据分类 |
| --- | --- | --- |
| task | 标准 JSON pointer，如 /design_context/roles/0 | 保存的声明，标为 user_input；不证明实际 OA |
| metric | baseline candidate 中的精确指标名 | 保留原 metric source；demo 强制 software_inference |
| action | 另指定唯一 action 名，selector 指向其 details | 保留原 action source/status；可引用既有 OP、端子/网络回读 |

完整原值、状态和源文件哈希留在 JSON，Markdown 对集合只展示摘要，避免重复展开
整个原理图。缺字段、未知指标/可调字段、歧义 action 或错误哈希直接拒绝；不猜补。
controls 仅可指向原权限里的 CDF/semantic 字段，或原 testbench 已有 source_dc/load。
不会因此扩大设计上下文、删除冻结结构或允许 AC/连接变化。

expectations 明确指标、increase/decrease/unchanged 和该指标单位的 absolute_tolerance。
对照要求两份成功、完整、真实 Bridge 记录和逐指标 eda_result；PDK、目标、拓扑、
固定 testbench/AC 设置、条件、analysis/测量及规格上下文须可比较。已记录变化不得
超出该假设 controls。缺条件、缺数据、混杂或 demo 为 not_evaluated，不硬判成功。
独立记录的同输入重测可对照 unchanged；同一记录同一点不算新的检验。

结果只称 consistent/contradicted，不证明因果、独立灵敏度或事前盲预测。软件无法凭
新写文件证明作者此前未见结果；历史回放始终保留 retrospective 声明。只有已记录的
字段能够比较，未记录项不自动等于不变，更不能证明当前远端状态。

继续使用方式：

```powershell
.\.venv\Scripts\vda.exe design-next <task.json> <run.json> --reasoning <reasoning.json> `
  --output <new-next.json> --markdown <new-next.md>
```

design-iterate 的 rounds 可附 reasoning 路径（相对 iteration 请求解析）；每轮使用
同一校验器保存假设和对照。intake.history 的对照单独放在 reasoning_for_historical_run，
不补作当前指标。新草案有理由时还检查变动坐标是否被 controls 覆盖；未解释字段阻止
草案，不绕过原规格、预算、pending、恢复/重新定锚或安全检查。旧单项操作不强制写理由。

## 3. 物理变量到原参数契约

intake 请求可附 physical_refinement 路径，或在已绑定的固定基线运行后使用：

```powershell
.\.venv\Scripts\vda.exe design-next <fixed-task.json> <run.json> `
  --physical-refinement <physical-request.json> --reasoning <reasoning.json> `
  --output <new-next.json> --markdown <new-next.md> --task-output <new-safe-task.json>
```

与 --model/--policy、--data-policy、--prior-model 或 --iteration 不混用，防止把不同
提案依据拼成一个结论。没有 --reasoning 的旧路径仍可用 rationale/人工审阅，不限制
原 Bridge 或 parameters.apply。onboarding 可先导出普通任务，再作为显式 source 接入。

物理请求绑定源 task 字节 SHA 和当前 PdkProfile 规范化 SHA。源必须是已有效的固定
existing_schematic simulation/tuning 契约，不静默丢弃已有 candidate_set、搜索域、
theory_seed 或 topology_refinement。新 task id 必须不同；原候选预算不自动增加。

axes 声明单位和上下界；points 是1–3个完整显式物理坐标，不做笛卡尔铺点、选优或拟合。
四个以上候选继续使用原 preview/shortlist 工作流，不因此限制原有限搜索能力。
每个 MOS mapping 显式指定：

- characterization run、精确 SHA、model/polarity 和模型参数签名。
- characterization_input：原本地留存 mos_characterization.scs。复用原 manifest 校验，
  核对其大小/SHA 与表的源 manifest，并检查真实 include/section 与当前 PDK profile。
  同 profile 名但金属栈/模型路径不同不能通过；缺原输入不为此补跑或伪造。
  首版输入核对接受 profile 的单一默认 model_include/model_section；其他 include 组合
  不自动判等，保留为材料/接入缺口，不据此限制原仿真或 PVT 操作。
- 该表的精确长度平面、固定 |VDS|、|VSB|，gm/ID 目标和电流幅值，或 gm 目标。
- CDF 宽度/长度 binding、表归一化宽度到总有效宽度的倍率、目标总有效宽度/CDF 宽度倍率
  和文字定义、CDF 宽度范围/格点、
  该设计假设下的最小饱和余量。不能按参数名猜 total W、Wfg、nf、simM。

换算复用 characterization 的插值器，先在指定 L/VDS/VSB 的单调 VGS 分支反查 gm/ID：

```text
若给 gm：Id = gm / (gm/Id)
Wnormalized = Id / Jtable
Wtotal = Wnormalized * characterization_total_width_per_normalized_width
WCDF = Wtotal / total_width_per_cdf_width
WCDF → 显式格点 → 再检查上下界
```

严格拒绝跨长度平面、缺插值角点、非单调/平坦/重复坐标、域外 gm/ID 或偏置。查不到
不是最小尺寸 fallback。余量用假设端电压减表中 |VDSAT|，不是仅看 region；这仍是
局部估算，不是电路实际 OP 或宽 PVT 保证。格点后另报估计 Id/gm，不假装目标精确实现。
若表以 Wfg 归一化且表内电流已包含 nf/m，表侧倍率也必须声明；不能再把目标倍率重复
计入电流。两个倍率都没有默认值，未知时应留为待补全而不是猜1。

同一物理轴可用正比例 scale 表示明确镜像/支路倍率；长度可固定或选已表征平面。
复用 NumericInstanceParameterBinding / NumericTestbenchParameterBinding，把派生尺寸、
直接偏置和 R/C 负载展开为原 CDF updates/typed overrides；原等值/仿射联动也可用。
重复目标、错单位/器件类型、无权限、供电会被 operating_conditions 覆盖、不可表示
的成员格点和有损数值序列化均拒绝。AC 幅相、连接和未声明量保持原值。

不会从 |VGS| 自动写绝对栅偏置，因为 source/bulk 电位必须有依据。KCL、电压分配、
功能角色和变量范围仍由 agent 结合实际图/OP 组织；其他电路可以调整配置或已有分析
脚本，不增加任意代码执行器或声称本模块已自动求解所有拓扑。

## 4. 验收与停止边界

有合格旧表和完整材料时，输出普通 AtomicCandidateSet/TaskSpec，哈希绑定请求、源
任务、profile 和关联证据；不填写模块 predicted_metrics，不伪造性能预测。
默认 compute/write/create/replace 全部关闭，原白名单、cell 前缀、CDF callback/CAS、
checkpoint/回读、winner 和独立网表复核都保留。真实执行仍需另列范围并获授权。

合成/仅声明来源的独立表、缺留出、缺实际输入、未知温度、越界、余量不足或未决问题
保留逐点计算/问题，但不产半个候选任务。原局部响应模型的独立训练/留出门完全不变；
已有 PMOS A/B/A 仍不足以辨识尺寸、偏置和负载的独立灵敏度，不硬拟合、不自动补点。

表的 width scaling、模型参数和指数量对应属于显式假设/声明，仍须真实执行前后核对
目标 master/CDF/OP/网表。这里核对本地留存输入和已记录表/manifest，不重解 PSF、
重跑 characterization 或读取当前远端 PDK 内容，不能保证模型文件后来没有变化。
可重配置不等于任意拓扑已经验收；当前只完成本地接线，未增加电路性能证据。

第三批的选动作策略/经验整理、真实新点准确度和端到端成本验收不在本轮范围。
本轮记录见[本地验收](validation/2026-10-26-design-reasoning-physical-local.md)。
