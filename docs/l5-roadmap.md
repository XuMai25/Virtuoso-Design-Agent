# VDA 能力分级与路线

## 分级 v2.1（2026-10-23，结合外部调研）

本分级用于描述 **Codex agent + VDA + Bridge** 支持的设计工作流，不单独给 Python 库或
Bridge 评自主等级。Codex 负责开放式分析和决策，VDA 固化任务、边界和证据，Bridge 执行
EDA 操作；不要求为提高等级在 VDA 内置 LLM、更换架构或引入多智能体框架。

这里的 L0–L6 及 L5A/B/C 都是 VDA 内部定义，不宣称是行业标准，也不与其他资料的
同名等级直接等同。等级主要回答：**用户交代目标后，agent 能独立完成哪些设计决策？**
工具数量、电路复杂程度、仿真次数和权限大小不作为升级依据。
v2.1 保留 v2 的等级含义，补充可核查的能力画像、来源依据、现状与缺口；不是一次能力
升级。PPT、论文和开源项目都作为可批判的参考，不作为需要照搬的实现清单。

### 主等级

| 等级 | 定义 | 用户与 agent 的分工 |
| --- | --- | --- |
| L0 人工设计 | 人工制定和执行设计步骤 | EDA 工具完成计算；设计决策由人完成，作为分级参照 |
| L1 分析助手 | 解释电路、估算、阅读资料和结果 | 人决定并执行修改；文字建议不等于已实现或已验证 |
| L2 工具操作助手 | 根据明确指令完成单项操作 | 人指定改谁、改什么或跑什么；agent 执行建图、读回、参数应用、仿真、保存等并核对结果 |
| L3 流程执行代理 | 可靠执行预先规定的多步流程 | 人提供流程和判断规则；agent 串联操作、记录、续跑和清理，但不负责重新设计优化问题 |
| L4 反馈优化代理 | 在已定义的设计问题内迭代优化 | 人给定拓扑、测试、指标及可调域；agent 根据反馈选点、判规格、保护约束、恢复并停止，不止重放固定扫描 |
| L5 模块设计代理 | 将模块目标转成分析和设计细化任务 | agent 理解连接及物理机制、确定必要测试和调整策略，协调参数与允许的结构变化；具体自主范围按 A/B/C 区分 |
| L6 多模块协同设计代理 | 围绕系统目标协调多个模块 | agent 分解/协调模块指标和接口、处理级间负载与共同预算、安排联合验证并反馈修改，不止分别调用几个模块任务 |

主等级描述设计决策范围，不要求日常任务走完整流程。L5 工具也必须能只读图、只改参数
或只跑一个 analysis。用户仍可指定参数、算法或操作路径，人工介入不自动使能力降级。
等级不是质量或速度排行榜：能探索拓扑的系统不因此比专用尺寸优化器更准确/省时。
L6 的新增责任是系统指标分解与耦合决策，不是 cell、文件或 agent 数量更多。

### L5 的三个自主范围

| 范围 | 输入与自主能力 | 明确边界 |
| --- | --- | --- |
| L5A 已知设计域内的受限设计代理 | 用户给定模块规格和已接入的电路类型/模板；agent 利用已有领域知识选择初值、测试、参数范围和迭代策略，完成有界细化 | 能力依赖已支持的设计域；只把人预先写好的候选跑完属于 L3/L4，不能单独证明 L5A |
| **L5B 给定大致拓扑的可适配模块设计代理（近期产品目标）** | 用户提供图片、已有 OA 或连接描述及规格；agent 能对不同模块建立上下文，从理论和实际连接出发，调整参数与获准的局部拓扑细节，选择必要测量并跨轮次收敛 | 不要求从空白发明复杂拓扑；也不能只对反相器、单管或差分对的固定模板成立。新器件/PDK 映射、疑点和授权边界必须显式解决 |
| L5C 模块架构探索代理 | 用户主要提供模块功能、端口、规格和限制；agent 在声明范围与预算内形成、比较并选择不同总体拓扑，再进行尺寸与细节设计 | 比 L5B 多出模块架构选择权；不意味着任意电路、全局最优、无限搜索，或必须使用版图工具 |

L5B 的完整能力应当包括：

- 从实际器件连接建立可审阅的电气上下文；指定 PDK 的 master、端子、CDF/model 映射
  有真实依据。设计初值可以有理论估算并标明来源；按图片复现中缺失的参数或模糊连接
  不能静默补成事实。
- 根据偏置、KCL、工作区、小信号或适合该电路的模型解释瓶颈，再提出有理由的参数与
  局部结构调整；不是随意列数值取一个最大值。允许 agent 为具体电路调整配置或分析
  脚本，不要求每种拓扑增加专用核心 executor，也不承诺自动理解任意未知器件。
- 从规格选择分析、testbench、可调域和评价顺序；DC/AC/STB/noise/transient/PVT 按
  需求启用，不把所有分析强制为每次必跑项。采用低成本筛选和同源真值复核，报告工具
  耗时与 agent/对话耗时的测量边界。
- 根据结果决定下一轮，保护先前规格；参数、拓扑或条件改变时识别失效证据，做必要
  复验。识别不可行、预算耗尽和执行故障，保留基线/可行锚点与停止理由。
- 最终交付机器可核对、人工可理解和继续编辑的设计及结果；保存/重开 ADE 与实际
  仿真证据分别验收。不同任务的设计、会话和窗口保持归属边界。

设计达到声明规格和“在预算内未找到可行设计”是不同结论。后一种可以体现正确的诊断和
受控停止，但不得改写成该设计通过规格。L5B 的成熟依据是上述工作流可适配、可重复，
不是单个好看的结果或不断增加 circuit-specific smoke。

### 每项能力同时说明什么

等级之外，使用以下能力画像；本节是文档评价口径，不新增 TaskSpec 字段或执行入口：

| 维度 | 必须说清的内容 |
| --- | --- |
| 自主决策与人工介入 | 谁确定测试、变量、范围和下一轮；哪些仍需人提供工程判断；区分意图澄清、安全批准与反复代操作 |
| 适配与修改范围 | 已知模板/既有新拓扑、器件与 PDK、flat/hierarchy、可调参数、允许的局部 delta；可写某 CDF 不等于会优化其物理作用 |
| 设计质量与验证范围 | 实际规格、余量、analysis/条件、schematic/layout/post_layout、证据链与缺项；预测、nominal 和有限点各有边界 |
| 可靠性和可接管性 | 失败/不确定写入/状态漂移如何处置，能否恢复、回读，人工能否继续编辑/运行/保存；只在已验证范围内声明 |
| 成本与实用收益 | 可观察的总耗时、工具/仿真次数、agent 调用开销及人工返工；没有数据的项留未知，不从 action 耗时推算 agent 思考时间 |

画像可随真实使用补全，不要求先建评分平台、跑固定题库或形成一个总分。一个新模块
沿用相同执行契约、仅修改声明/模型配置，比多跑一批熟悉模板更能支持适配性判断；
但不为收集证据而另造模块。某范围没有证据就明确未验证，不限制已有 Bridge 能力。

### 设计范围、证据成熟度和实施顺序分开记录

**设计范围**独立注明 `schematic`、`layout` 或 `post_layout`（可组合）。版图生成、DRC、
LVS、PEX 和后仿是能力范围，不再把它们直接称为 L5C。L2 可以按明确指令执行 LVS；
L5B 可以先在 schematic 范围成立。是否声明物理实现质量，仍取决于对应真实证据。

**证据成熟度**按具体能力分别记录：已实现、本地验证、实机验收、真实使用中可重复。
每项同时注明 PDK/模型、拓扑或器件范围、分析和条件、版本、未验证项；这些是状态说明，
不是新权限或新的证据来源类型。继续使用 `eda_result`、`bridge_readback`、
`software_inference`、`user_input`。不靠改名重算旧成绩，不把 nominal 结果推广为 PVT
保证，也不要求为凑数量重跑所有电路。

**当前定位**：已有 L2–L4 执行/有限优化能力、已知模块的部分 L5A 实测切片，以及 L5B
需要的通用连接/参数/局部拓扑、理论辅助和跨轮次控制能力；完整 v2 L5B 仍是产品目标。
本次重定义没有增加功能或新增 EDA 验证，不追认 L5C/L6 已实现。当前具体证据以
[可用基线](current-baseline.md)和各验证记录为准。

**实施顺序**按真实设计价值、依赖、风险和成本安排。先复用现有能力解决实际任务，遇到
缺口再做最小补充；不要求每升一级立即增加一轮 Gate，也不把 L5C/L6 定义写成近期开发
承诺。分级不扩大授权，plan/token、compute/write、白名单、非覆盖和人工保护规则不变。

### 原始资料、开源实现与采用边界

以下是 2026-10-23 的只读资料/源码核查；未安装、运行或复现这些项目，不以作者的成功率、
提升倍数、星数或框架名称给项目排序，也不据此改变 VDA 的 PDK/仿真后端。

| 原始来源 | 核对到的机制 | VDA 的采用判断 |
| --- | --- | --- |
| [Levels of AGI §6.2](https://arxiv.org/html/2311.02462v3) | 能力、泛化范围与自主交互方式分别讨论 | 借鉴分维度描述，不把其 AGI 或 autonomy 编号等同本项目 L0–L6 |
| [AutoCkt 官方仓库](https://github.com/ksettaluri6/AutoCkt) | 预设网表、变量域、规格和 reward；区分公开实现与论文仿真条件 | 反馈优化不等于新拓扑设计；不引入 RL 训练作为升级前置 |
| [AnalogGym](https://github.com/CODA-Team/AnalogGym) | 电路、参数配置、testbench 与指标提取分离 | 复用现有 generic_simulation/measurement 契约；不把全部 benchmark 变成必跑 Gate，也不复制其 PDK 数值 |
| [ASTRA](https://github.com/IceLab-X/ASTRA)及[查表实现](https://github.com/IceLab-X/ASTRA/blob/main/Find_Initial_Design/bo_logic.py) | gm/ID 初值、LUT 与关键参数的分阶段优化 | 复用已有器件表、OP/MNA 和局部响应模型；BO/变量重要性方法只有在现有策略被真实任务证实不足时再考虑 |
| [AnalogCoder 检查代码](https://github.com/laiyao1/AnalogCoder/blob/main/gpt_run.py) | 执行、工作点和按任务类型分派的功能反馈 | 借鉴错误定位；特定任务 checker 或脚本执行成功不能替代 VDA 的 OA/网表/规格验收 |
| [AgenticSizing 仓库](https://github.com/aprilaihub/agentic-analog-sizing)与[论文](https://arxiv.org/html/2609.25873v1) | 连接/功能角色/变量归属、预测与仿真区分、可行锚点；论文同时分析推理成本及组件消融 | 与给定拓扑细化最接近；复用 design_context、design-next、design-iterate 和原 executor，不因多 agent/RAG 的存在就换框架；论文消融不支持“加组件必然更好” |
| [ALIGN](https://github.com/ALIGN-analoglayout/ALIGN-public) | 网表、约束、PDK 抽象及 primitive 共同支撑物理实现 | 物理能力独立声明；不因 Bridge 能调用某工具就承诺 TSMC18/N28 版图或后仿闭环 |

上表“采用判断”是本项目基于需求的工程推断，不是原作者对 VDA 的结论。论文报告、README
自述、实际可见源码和本项目实测是不同证据；代码可获取也不代表已经本地跑通或允许直接
复制。真正接入代码时另行核对接口、许可证和兼容性，不在本轮增加依赖。

### PPT 参考与旧定义迁移

两份用户提供的 PPT 仅作思路参考：`张一驰20261022.pptx` 第 1–5 页提供图片/需求输入、
电路表示、参数调整和仿真反馈的讨论；`AI4Chip_后续研究方向.pptx` 第 1、4–5、10、12 页
强调正确性、质量、分层评价及模型/EDA 的时间成本。这里借鉴这些讨论，不把其中引用
工作或方法视为本项目必须采用的实现，也不把本分级归为 PPT 原文或通用标准。
原文件只读保留于用户的 `G:/Downloads/`，不复制进仓库。

旧记录中的 L5A 表示固定模板/有限执行基础，旧 L5B 表示单模块目标，旧 L5C 表示物理
实现闭环。历史记录原义保留，不批量改写；**v2 的 L5C 改指模块架构探索**，旧物理
路线转为独立范围。下文实验顺序和历史状态不构成新的待办清单；同名旧章节以本节为准。

## 当前能力、实际缺口和升级优先级（2026-10-23）

### 已有实现，不重新开发

下表依据当前源码和已保存验收记录，不代表本轮新做实机验证。最近完整本地回归为
1129 passed、1 skipped，另在 Bridge 环境补跑 39 passed（有重叠，不累加）；来源见
[本地收口记录](validation/2026-10-23-baseline-closeout.md)。测试数量不是等级依据。

| 能力 | 直接复用的实现/入口 | 已有证据与真实边界 |
| --- | --- | --- |
| 安全执行、有限搜索与恢复 | TaskSpec、planner/executor、checkpoint、winner_verification、Bridge adapter | 有真实有限搜索/写回/恢复记录；不是任意并发 editor 的事务保证，不能消除所有 transport 故障 |
| 接手既有电路、参数与局部拓扑 | onboarding-draft/resolve/promote、design_context、CDF binding discovery、topology_delta | 受控 existing_schematic 和一层 hierarchy 有实测切片；初始角色候选主要来自 pin 名/方向，inventory 不是电路功能识别，语义角色仍需确认 |
| 理论分析和数值提案 | theory/characterization、small_signal/OP→MNA、op_relinearization、design-next | 器件表、小信号及局部响应有各自条件内的实测/留出记录；design-next 数值提案仍依赖已验证 semantic 局部模型，不是任意 raw CDF 优化器 |
| 低成本候选筛选 | netlist_preview、preview-shortlist、preview-oa-task 与原 OA executor | 有有限域 winner retention 与耗时记录；只有预计节省时使用，preview 绝对数值不替代最终 OA 真值 |
| 同源测量与规格判定 | OA→si、generic_simulation、measurement-compile、原 extractor | DC/AC/noise/transient 等有不同拓扑的实测切片；按实际任务选择分析，不代表所有 PDK/器件/测量已经通用验收 |
| 跨轮次摘要与保护 | design-review、design-next、design-iterate | 哈希绑定、规格退化/缺项、证据失效、可行锚点及预算/停滞已本地实现；不能将这些记录重放称为新模块多轮实机闭环 |
| STB 持久证据 | 正式 worker、stability-audit、同作业 native margin 对照 | 一次旧 N28 实测 + 正式代码本地回归/旧文件离线重放；修改后 worker 尚无新的正式远端验收，不是缺少另一套 STB 执行器 |
| 原生图面、人工 ADE 和窗口 | schematic_drawing、ADE L 操作路径、正式 ade.*、windows-plan/lifecycle | N28 图片图面/结构/网表、TSMC18 ADE L 保存/Load/DC 有限定证据；图片 fresh-create 单次全链、其他旧 state/分析待按需验证；windows-plan 只提建议 |

### 真正缺口及先后

这里的 P0–P3 是工作优先级，与 L0–L6 无数值对应。区分 **实现/接线缺口**、
**特定实机证据缺口** 和 **观测缺口**，不将三者统一改写为“新建功能”。

| 优先级 | 要解决的问题 | 复用方式和最小完成条件 |
| --- | --- | --- |
| P0 持续护栏，按故障触发 | 实际任务中的错误结果、越权、状态丢失、未知写入重放或未保存人工状态风险 | 先对账、回读、保全，再修复具体问题并加针对性回归；不是另开全量可靠性/网络审计。当前没有由本次分级产生的 P0 开发项 |
| P1-a 首要使用改进：新模块接入与决策上下文 | 功能角色和理论理由仍需 agent 判断；多文件重复拼接已补本地薄接线 | 已实现 design-intake：复用 onboarding + design_context + measurement 编译 + design-review/next，导出摘要和普通零权限草案；旧共源记录本地重放通过。物理解释和实际接入/返工节省仍待自然使用评价，不另建识别平台、不造电路。见[流程](design-intake-workflow.md) |
| P1-b 紧随实际瓶颈：理论驱动的可执行微调 | 原模型已接入显式 CDF/偏置/负载、联动和收益/失准反馈；未知物理语义与可靠新点建议不自动产生 | 复用 OP/MNA、原拟合/留出、普通任务和历史记录；可诊断不足数据、声明比例/偏移、判断收益/残差/余量/成本并显式重新定锚。既有 N28 电路的一次联合原子候选、实际输入回执和恢复已实机验收；未据此校准联合模型，保留 callback/CAS/回读，不造 solver/测试电路。见[说明](numeric-cdf-refinement.md) |
| P2 按任务规格补齐验收与人工交接 | 正式 STB 新路径、跨轮次组合运行、部分 ADE 保存/独立 Load/人工改动捕获等尚缺对应实机证据 | 下次真实任务需要哪项，就在同一设计上做该项最小验证；先复用既有 OA、setup 和结果。STB 不需要就不跑，旧 state 迁移不需要就继续延期，不把验收缺口当新实现 |
| P3 有需求再扩展 | 深层/shared-child override、更多分析/器件/PVT、其他优化器、跨模块或物理实现 | 只有目标设计确实越过现有边界、且不能用既有配置/脚本解决时才接入；先写清收益、范围和成本。L5C/L6、BO/RAG/多 agent/完整 benchmark 都不是近期必做清单 |

2026-10-23 按实际故障优先完成[模型固定仿真条件传递修复](validation/2026-10-23-model-testbench-context-local.md)：
保留固定源/负载 override，拒绝结果条件错配；复用原 compiler/audit 和旧记录，本地验收
不增加电路或远端 Gate，也不提升 L5B 的质量声明。后续顺序不因此扩大。

同日 P1-b 继续补齐[显式等值 CDF 联动](validation/2026-10-23-p1b-linked-cdf-local.md)：
多个受控字段共用一个已有模型变量，避免把匹配支路误列为独立自由度；本地展开为原
候选并审计，保留旧记录与单字段兼容。未知比例及真实写入仍按需。

随后补齐[DC 偏置/负载与尺寸联合映射](validation/2026-10-23-p1b-joint-testbench-local.md)，
仍用原局部模型，未声明条件继续固定；有代码接线不等于有可用校准数据。既有 PMOS
三点不足以训练并独立留出两变量，负载方向也没有变化，保留缺口而不新建/补跑电路。

本地完整验收现统一调度双环境；P1-b 同时修复历史点跨固定上下文/不完整证据/近格点
舍入的误判重。仍复用原模型与记录，不新增 EDA Gate；见[本次记录](validation/2026-10-23-dual-validation-p1b-local.md)。

继续补齐成组源 DC/同类负载的显式等值联动：共模偏置或对称负载可作为一个变量与
尺寸联合微调，各成员 AC 设置仍固定；原普通任务与完整元组审计复用。训练/holdout
除编号互斥外还核对实际坐标未重复。共源/共栅旧模型与上轮结果完整相等，PMOS 三点
数据不足仍保留，未新建/补跑电路。见[本地记录](validation/2026-10-23-p1b-linked-testbench-local.md)。

接着完成[本地微调反馈接线](validation/2026-10-23-p1b-refinement-completion-local.md)：
预测改善与历史残差、声明余量/预算比较后才出草案；数据检查明确缺失方向和可复用
切片；显式比例/偏移不新增自由度；旧预测误差重算后引导重新定锚。generic 最终上传
输入增加启动前哈希与同作业回执，只有本地程序和旧文件验收，live 仍需明确范围。
不把这些软件路径完成计为完整 L5B，不为补一个回执单独新建电路或重跑质量全套。

之后按用户批准复用既有 PMOS-loaded CS 完成[联合输入真实验收](validation/2026-10-23-p1b-joint-input-live.md)：
3次 OA→si、3个 DC＋AC，验证两个尺寸和 wrapper 偏置/负载同时生效、最终上传字节
回执及原值恢复。暴露并修复 Windows OA include 换行哈希和数值型 CDF 格式误拒绝；
首次开图超时经自然 resync、独立完整回读后从原 checkpoint 继续，失败仍保留。
最终全量回读/图面和 A/C 的25个数值指标、原始 si 哈希一致；临时进程与隧道已收拢。
只有一个新联合方向，不足以校准多轴模型或归因各项改善，不自动补点、不提高分级。

成本观测贯穿 P1/P2，而不是另建监控工程：现有 design-review 有 run/action 时长，
design-iterate 可用 action-time 预算；`agent_thinking_seconds` 当前明确为 None。
可靠的 agent/工具调用总数、上下文/模型开销、人工操作/返工和端到端交付时长仍不完整。
先在自然使用中记录可观察的起止/调用数据；只有已知缺口影响决策时才补轻量字段或日志。
不读取隐藏思维、不以 action 时长或工具数反推 token/agent 时间，不安装常驻遥测。

### 实际推进与停止规则

1. **有真实模块时**，先读当前 OA/图片描述和既有 task/run，用已有入口建立上下文及
   决策摘要：目标、实际连接、关键假设、瓶颈、最小拟议改动、必要分析、复用/失效证据、
   预算和执行范围。没有真实需求时可停在当前可用基线，不编造新电路来推动路线。
2. **先判断不开发能否完成。** 用户要求的单次改参可直接 parameters.apply；模型/字段
   已支持就改配置，只有真实阻塞或可解释的重复成本才排 P1 代码。适配脚本不等于新框架，
   不收窄 Bridge 原有能力，也不要求所有参数先进入优化模型才能手动修改。
3. **按信息价值做最少的新计算。** 理论与已有结果先解释方向；小候选域直接 OA，较大
   域只有预计省时才 preview。最终选优只服从声明条件下的同源结果；更改后不能拿旧性能
   补齐新设计，昂贵质量/PVT 按规格需要对 winner 复核。
4. **新增实现的完成条件**是解决具体阻塞、保留旧入口与安全边界，并针对改动风险验证。
   优先旧记录重放和少量本地故障用例；只有本地不能回答的问题才申请最小真实验收。
   不把“所有 topology/analysis 再跑一遍”或达到某个测试数作为完成条件。
5. **正确停止也是结果。** 达标且证据/交接完整则交付；不达标时区分离散域未找到、预算
   耗尽、证据不足和有依据的拓扑限制。停止迭代不等于放弃产品升级，但不得把停机报告成
   设计规格通过。同一已批准范围内不反复问机械步骤；新增副作用或意图歧义仍先确认。

排序可被真实任务的阻塞调整：例如已明确需要 STB 的模块，P2 的该项验收可先于其他
P1 改进。没有固定日期、最低模块数或全分析指标清单。效果看实际接入/返工成本、
可追溯修改理由、规格保护及最终可用性，不看新增 operation/Gate/测试数量。

### 已完成切片与证据边界（保留追溯，不是新待办）

[本地基线收口](validation/2026-10-23-baseline-closeout.md)已完成；接下来复用
[可用基线](current-baseline.md)，不为升级分级反复制造电路或跑同类扫描。

第一、第二批已完成本地纵向实现，不升级为完整 L5B closure：

1. 显式测量契约与普通任务编译；单位、真实信号及上下文绑定；UGB/GBW/环路裕量区分；
   optional min/nom/max 温度×电源取点；generic STB 的 typed wrapper、PSF 解析与证据记录。
2. 已存 task/run/checkpoint 的跨轮次重放；前级规格保护、历史最佳可行锚点、输入变化后的
   旧证据失效、检查/复验草案，以及预算/停滞/达标停止；接入既有 `design-next` 理论建议，
   不另造数值优化器、不随机列尺寸、不新建执行框架。

2026-10-22 的实现仅做本地测试与历史重放；2026-10-23 随后在获准范围内复用
`vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic`，以 N28/5X2Y2Z 完成一次
OA→si→DC＋STB。没有新建或写 OA；实际字段、单环探针/符号与同作业原生 margin
已对照，PM 差 0.00758°。临时隔离会话、helpers 和 tunnel 已关闭，原用户会话保留。
这不包括 ADE GUI 对照、其他 PDK/PVT、多环或新模块多轮实机闭环。

原始证据持久留存与 native-margin 对照随后已接入正式 STB 路径，新增纯本地
`stability-audit`；复盘和跨轮次控制检查绑定文件、candidate/winner 与条件覆盖。
已通过完整本地回归及旧 N28 实测文件离线重放，见[接入验收](validation/2026-10-23-stb-evidence-integration-local.md)。
接下来按正常设计需求验证正式集成执行、跨轮次规格保护及人工 ADE 交接，不再为同一
功能重跑一批电路；没有真实需求时不编造复杂拓扑或铺全域扫描。
阶段状态机仍未声称已完成新模块多轮实机闭环。PVT 按需可选，资源/游戏卡顿诊断
仍不属于本路线。

使用见[测量与迭代工作流](measurement-and-iteration-workflow.md)，实际本地验收见
[2026-10-22 记录](validation/2026-10-22-measurement-iteration-local.md)，真实单点结果及
未验证边界见[2026-10-23 STB 原生对照](validation/2026-10-23-n28-stb-native-acceptance.md)。

### 2026-10-19 既有能力与未闭合项（保留追溯）

已实现[图片→可审阅描述→显式导线 schematic](schematic-image-reproduction.md)入口，并在
用户提供的 N28 预放大器/锁存器图上完成一次非覆盖真实结构/网表验收：15 实例、13 nets、
8 pins、实体线覆盖/网络归属及 54 项网表参数一致。首次 guard 失败后仅对冻结的本任务
部分状态补完，保留失败记录；并非无故障单次 create 成功。没有性能仿真。
详见[原生验收](validation/2026-10-19-n28-image-latch-native.md)。

下一步先由用户对原生图逐块确认：两处反馈标签跨接是否接受，是否需要进一步整理局部文字。
若需整理，应对这个已创建目标另列仅图面变更计划，保留拓扑和参数、回读并核对网表等价；
不自动覆盖或重排。不为重证流程再造电路，修复后的 fresh-create 单次全链留到下一次
真实建图需求验证。只有用户提出功能规格并另行授权后，才配激励做 transient 等性能验收。

Windows TCP reset 按用户决定暂缓，保留
[遗留问题与使用边界](validation/2026-10-19-windows-tcp-reset-deferred.md)，不是已修复。
不再切换 TUN，不将网络排查或另一轮随机电路验收作为正常使用的前置。

本轮已增加本地 `design-review`、`design-next` 和 `windows-plan`，详见
[使用说明](design-review-workflow.md)。前两项通过历史记录重放和本地契约测试；数值建议
复用已有 semantic 局部响应模型，不宣称任意 CDF/拓扑自主优化。窗口入口只做保守计划，
ADE L cellview 保存/独立 Load/关闭随后已在既有 IVGS 配置验收，见
[对应记录](validation/2026-10-19-ade-l-cellview-save-load-close.md)；不能泛化到所有旧 state，
也不能凭目录式旧证据自动关闭窗口。

方向仍是**每轮设计的决策摘要与增量计划**，把已有 onboarding、理论分析、OP/MNA、
checkpoint、局部 topology refinement 和 winner verification 用起来，不再复制这些能力：

1. **先做纯本地、证据绑定的设计摘要。** 从原 task/run/checkpoint 与可选理论输出提取目标、
   PDK/条件、已测范围、规格值/余量、候选选择边界、OA 写回是否确认、未完成分析和原始证据链接。
   区分电路不达标、预算不足和执行/证据失败；没有测到不能写成不达标，有限候选最佳不能写成全局最优。
   同一份结构化摘要服务 agent 和中文人工报告，避免每轮重新通读大 JSON。
2. **再接理论驱动的下一步建议。** 复用现有分析器解释瓶颈与权衡，列出必要且有理由的参数或
   局部拓扑修改；不足以推断灵敏度时明确缺证据，不随机列尺寸。输出待审查的普通任务草案，
   不增加 executor、不自动打开权限、不把自然语言建议当执行指令。
3. **在真实设计中验证增量迭代。** 用前后指纹、条件和分析依赖说明哪些历史结果可用于对比，
   哪些必须重跑。尺寸/拓扑变化后的最终指标仍需新同源证据，不能把旧 winner 的结果搬给新设计。
   最终交付保留可人工打开的 OA/ADE 状态；ADE L 仍优先，未验证的保存/重开路径明确标注。

第一步只需复用历史成功、不可行、预算和中断记录做本地验证，不需要新建 cell 或远端仿真。
收益验收看生成摘要所需调用/时间、来源可追溯性和能否明确提出下一步，而不是增加 Gate 数。
工具处理时间与 agent 思考/对话时间分别计量；没有实测就不宣称节省多少。
没有真实应用需求时不为第三步编造复杂电路或跑全域；PVT/noise/linearity 按规格需要安排。
上述第三步留给真实使用；本段不构成新的远端执行授权。

## 旧 L5A：受限执行闭环与历史实现

本节保留 v1 的固定模板执行定义和实验记录。v2 L5A 还要求 agent 在已知设计域中承担
从规格到测试/调整策略的设计决策；仅执行冻结候选不因此升级。

输入是已知 PDK、固定电路模板、明确规格、参数搜索空间和最大迭代次数。系统可以：

1. 生成可审查计划。
2. 在明确授权后建图或读取已有图。
3. 运行有限候选点仿真。
4. 从波形提取指标并逐条判规格。
5. 选择当前搜索边界内的最佳候选。
6. 将参数应用回 OA，并结构化回读。
7. 保存证据和失败原因。
8. 在显式交接点捕获人工 ADE setup/history 与真实仿真结果，不覆盖人工状态。

此外，人工可以通过实例名、原始 CDF 参数名和值字符串要求一次独立参数写入；VDA 必须保留请求、写入前值、立即 OA 回读和独立再次回读。该能力不等于系统已经知道任意参数的物理含义，也不自动扩大优化搜索空间。

L5A 不允许自行发明无限搜索范围、修改 PDK、覆盖未知 cell、把 demo 模型当 EDA 结果，或在缺少证据时宣布 closure。

为降低新拓扑早期评估成本，L5A 现增加可选 `netlist_preview` 层：理论/KCL/gm-Id 先给出
少量具体候选，再由受校验结构直接生成 standalone foundry Spectre DC/AC deck。该层不
启动 Virtuoso、`si` 或 Maestro，不创建/修改 OA，只把 raw simulator 结果记为
`eda_result`、A/B 差值记为 `software_inference`。它只负责淘汰明显差的结构；最终候选
仍须进入 OA→`si`→Spectre 或 ADE。契约、planner、worker mock、空波形和无 OA executor
路径已本地通过；2026-07-27 nics4304 live smoke 又闭合两份 241 点 AC、DC OP、逐文件
hash manifest 和进程归零。级联/共源的 gain/BW/GBW A/B 方向与既有 OA→`si` 一致，
但绝对数值误差最高超过 20%，所以状态只升级为 standalone directional preview live，
不能用该层宣称 schematic-driven 性能或设计闭环。

2026-07-27 又补齐理论候选到该预评估层的本地交接：
`vda preview-task-from-candidates` 复用现有 `candidate_set`/`theory_seed`，按 policy 显式列出的
ID 和顺序把候选 semantic 参数映射到一个 typed graph placeholder。它拒绝来源/PDK
漂移、参数遗漏、固定值漂移、raw CDF 更新、重复目标和含糊的 placeholder objective，
并把 source/policy/template hash 与 `variant -> candidate ID` 写进输出。既有 9 点真实
cascode seed 已确定性编译成 1 个固定共源基线 + 9 个共栅候选并通过 plan；本 Gate 没有
连接 Bridge、运行远端计算或写 OA。获单独批准后，下一道 Gate 已完成 10/10 份真实
Spectre preview、逐 deck 重渲染与 manifest/候选身份审计，并用新增 `vda preview-select`
对照既有 OA→`si` 九点离散域。preview top-3=`009/007/003`，参考 top-3=`009/003/007`，
Spearman ρ=`0.9333`，两侧 winner 都是 `009`；gain/BW/GBW/power 最大误差分别为
`4.39%/11.04%/10.13%/19.45%`。因此该同拓扑预筛可把后续 OA 真值计算由 9 点缩到 3 点，
但 policy 明确标为 `retrospective_calibration`：下一未见拓扑仍需 prospective 验证，不能
把本次排序或误差门槛直接外推。全过程无 OA target/write，执行后 Spectre/si/Maestro
均归零，Bridge tunnel 已恢复为停止状态。

同日继续闭合 preview shortlist 到正常 OA 同源验证的交接。新增
`vda oa-task-from-preview-shortlist` 逐字节绑定 passed selection 与完整 OA candidate task，
保留 target/规格/objective/safety，只按 preview 排名抽取 009/007/003，并要求重新 plan
和独立授权。首次执行发现前一 round-trip Gate 已把目标还原为普通共源；失败发生在第一个
candidate stage，未形成候选结果，恢复后 OA 仍为普通共源。复用既有获验证 forward
topology-delta 后，3/3 OA→`si`→Spectre AC 完整通过，三点参数、网表 hash 和每点 40 项
指标与原九点 run 对应项精确一致，winner 仍为 009，最终独立回读也保持该点。3 点 wall
time=`229.924 s`，相对原 9 点 `811.503 s` 减少 `71.667%`；加上 preview 的
`71.628 s` 后仍减少 `62.840%`。新增 `expected_target_topology_variant` 令以后在任何候选
OA 写入前就拒绝此类拓扑漂移；拓扑修复仍是独立显式 operation，不由 handoff 静默执行。
该 Gate 仍是 retrospective known-domain replay，下一未见拓扑的 shortlist utility 必须
prospectively 预注册并验证。

2026-07-28 已完成这项 prospective Gate。新增 `vda preview-shortlist` 在接口层不接受
reference run，只用预注册 policy、standalone preview 和未来完整 OA task 冻结 top-3、
`frozen_at` 及全部 hash；`vda preview-shortlist-audit` 再要求真值 run 晚于冻结、task/token/
完整候选域一致。差分对八个新 tuple 与历史建模点零重合，preview 后先冻结
`op-local-001/002/003`，随后才在既有 `vda_diffpair_active_gate6_001` 完成 8/8
OA→`si`→Spectre DC/差模 AC/共模 AC。preview 与真值的功耗排名 1–8 完全一致，ρ、可行性
agreement、真值可行点 recall 均为 `1.0`，真实 winner 001 被保留并写回。候选 6 的下载
timeout/SSH reset 由 checkpoint 和独立 OA 回读恢复；最终 Spectre/si/Maestro 进程归零。
状态升级为 **prospectively frozen standalone preview ranking and winner retention verified for
one nominal TSMC N28 differential-pair local domain**。gain/BW/GBW/power 最大误差仍为
`3.05%/24.10%/27.71%/30.35%`，不能把 preview 数值用于最终 closure。冻结 top-3 已编译
为正常 OA task，但本轮没有重复执行；约 `354.176 s` 的两级时间是从完整 run 的前三点
动作重建的估算，不是独立三点实测。

本次之后不再安排“换一种拓扑再跑完整域”来重复证明这条能力，也不为已知八点域追加随机
点。已经验证的机制被提炼为项目默认快速工作流：4 个及以上显式候选先检查
`Tpreview < (N-k)*Toa_per_candidate`，成立时只运行一次批量 preview、在真值前冻结 top-3、
再执行一个 checkpointed OA shortlist task，最后只对真实 winner 补必要质量项。完整 OA 域
退到筛选代码/PDK 条件变化、shortlist 失效/near-boundary 或计划性周期审计；PVT 继续可选。
流程、跳过条件、授权点、失败恢复和最小证据链固定在
[`fast-preview-shortlist-workflow.md`](fast-preview-shortlist-workflow.md)，以后直接复用，不再靠
每轮对话重新推导。

PDK 路线默认按晶圆厂 CMOS 工艺推进：当前以 TSMC N28 LVT profile 为缺省；同工艺的 `nch_mac/pch_mac` 通过继承式 `nics4304_tsmc28_svt` 显式选择。NMOS 共源的 LVT/SVT OA/`si`/Spectre round-trip、standalone NMOS/PMOS 表征以及 PMOS 有源负载共源级 nominal onboarding Gate 已 live；其他 profile、工艺和拓扑仍需独立 Gate。后续优先通过独立 profile 接入 TSMC/SMIC 的实际晶体管 PDK。profile 继承只复用静态工艺配置，不复用性能证据。TSV、hybrid-bonding 等封装/3D PDK 不进入普通电路设计的默认路径；若未来需要，将作为显式选择和独立 Gate，而不是当前 profile 的替代品。

当前实现状态：`OA schematic -> si -> Spectre -> metrics`、供电能量积分、失败注入和候选级 checkpoint/resume 已通过本地测试。2026-07-19 live 结果覆盖 OA/`si` 参数一致性、非空 timing/current 波形、收紧规格、不可行 + 预算耗尽恢复，以及一个经历 3 次 tunnel 中断后仍完成 9/9 候选、最佳参数写回和独立 OA 回读的恢复任务。反相器 L5A 的同源有限闭环与显式恢复 Gate 已通过；Bridge 本地隔离补丁又通过强制断链只读 smoke，闭合 Windows stale state 与调用边界自动重建。运行中传输的随机 reset/timeout 仍是跨 Gate 的底层可靠性债务。

2026-07-24 又在隔离的 `vda_inv_ratio_calibration_001` 上固定 `Wn=0.6 µm`，完整比较 `Wp/Wn=1/1.25/1.5/2`，粗网格以 0.212 ps skew 选择 1.25。2026-07-25 follow-up 在相同 nominal `top_tt`、0.9 V、2 fF、5 ps input edge 下完整实测 `1.20/1.25/1.30/1.35`；1.25 的 hash/指标精确复现，1.30 skew 为 0.364 ps，而 1.20 以 0.0394 ps 成为新离散域最佳。checkpoint 还真实恢复了一次 raw 下载 timeout，最终 OA 独立回读为 `Wn/Wp=0.60/0.72 µm`。因此 profile 的简单可覆盖缺省更新为 `0.6 µm + 1.20`；多个 CL/input slew 和可选 PVT 仍待鲁棒性复核。显式任务值和已有 OA 值始终优先。

Gate 2A 又把相同执行语义扩展到电阻负载 NMOS 共源级：新 OA cellview 的 MN0/RD0 结构、W/L/R 回读和 `si` 网表一致性通过；显式保存的 Spectre DC OP 提供 Id/VGS/VDS/VDSAT/gm/gds，6 点 W/Vbias 搜索完成 3 个可行点、3 个线性区点、最佳 W 写回和最终独立紧规格复核。该结果只闭合 common-source nominal DC；当时 AC、source degeneration、noise 和 corner 均未验证。

2026-07-20 已按“微调已有 schematic、避免重建”的目标实现并真实验证源极退化原位 add：同一 cellview 只改 MN0.S 网名并新增 RS0/NSRC，前后审计保护 MN0/RD0、pins、位置和未点名参数；同一个 common-source adapter 随后读写 RS、自动解析退化网表、提取 NSRC DC 指标并完成有限搜索。2026-07-23 又补齐显式 remove：仅删除 RS0 与两条 VDA stub、恢复 MN0.S/VSS，并可把 add 前 placement SHA-256 作为 CAS 基线。新 cell 的 add→DC/AC→remove 一次通过，恢复后的 OA placement、nominal `si` 网表和连续 EDA 指标完全一致；该能力仍是已知拓扑的固定 delta，不是通用图重写。

同日已把共源 AC 做成正式能力，而不是一次性脚本：同一 OA→`si` 网表运行 DC OP 和复数 AC，并提取 gain、首个 −3 dB bandwidth、GBW、unity-gain 与相位。nominal/退化只读 smoke、各 6 点 bias/load 搜索、同参数只加 RS 的控制变量对比，以及 W/RD/RS 预算/不可行/完整 8 点搜索均已真实通过。完整搜索选择并回读 W=1.0 µm、RD=20 kΩ、RS=1 kΩ；多次 transport 中断都在 OA 恢复或独立 readback 后从 checkpoint 继续。状态升级为 **bounded common-source AC design-parameter tuning and recovery verified**。

设计质量分析已完成本地实现和单点 live Gate：实际 `VDD_SRC:p` DC 功耗与独立 KCL、相干 transient 幅度 sweep 的 gain/HD2/HD3/THD/P1dB/平均功耗，以及 ordinary noise 的输出/输入参考频带积分继续复用同一 OA→`si` worker。专用 cell 的 5 点 100 MHz sweep 解析出 `P1dB=88.32 mV peak` 和 150 mV 点 `THD=13.16%`；211 点 1 kHz–10 GHz PSF 得到输出/输入参考积分噪声 3.304/0.983 mV RMS。真实目录形状暴露的 DC info 覆盖和 transient 端点问题已在 VDA 层修正，没有改 Bridge。当前升级为单点 design-quality analysis execution verified，尚未升级为质量驱动的参数/规格闭环。

2026-07-21 已实现并真实验证固定 `analysis: quality` 组合：同一候选只生成一次经 OA/`si` 一致性核对的网表，再分别运行 AC、相干 transient 和 noise；三项指标共同进入原有 constraints/objective、候选预算和 checkpoint 语义。参数或共享 DC 指标不一致会硬失败，任一子分析不完整会拒绝整个候选。4 点 bias/load 搜索得到 2 个可行和 2 个 THD/功耗超限点，最高 GBW 点被质量约束正确拒绝；2/4 预算耗尽、全不可行和真实首候选 transport 中断后的 checkpoint 恢复也已通过，before/after OA 参数完全一致。状态升级为 **read-only multi-analysis common-source quality tuning and recovery verified**；设计参数质量写回和 corner 仍待验证。

随后同一专用 cell 完成 8 点 W/RD/RS 质量搜索：8/8 候选都完成 OA 写入回读、同源 netlist、AC、linearity 和 noise；候选 4 的真实 transport 中断在独立 OA 回读后从 4 恢复，没有重复 1–3。GBW objective 写回 `W=1 µm/RD=20 kΩ/RS=1 kΩ`；全不可行任务恢复该基线且 selection 为空。线性度优先的两点控制任务则自动写回 RS=2 kΩ，使 THD 降低 41.93%、P1dB 提升 37.26%、DC 功耗降低 17.27%，代价是 GBW 降低 29.30% 与输入参考噪声增加 15.03%。状态升级为 **bounded common-source multi-analysis design-parameter tuning, writeback, and recovery verified**。

实例参数面现已在任务契约、planner、demo、Bridge worker 和双重定向回读中实现。`existing_schematic` 不要求目标符合反相器或共源模板，可保留 Bridge 的完整结构读取并向任意已有实例透传 Bridge 字符串参数；空值或长值不再因通用摘要不可见而被 VDA 拒绝。固定模板还允许 semantic 与实例参数组合，并以最终 OA 同时满足两组请求为成功条件。通用只读 live smoke 在 Gate 2A cell 上读到 MN0 的 233 个 CDF 字段、RD0 的 2 个字段及完整 geometry/nets/pins；专用 `vda_param_surface_001` 又闭合 `fingers=2`、`r=22K` 的 callback、立即回读和独立 after 回读。`m=2` 被 PDK callback 恢复为 `1`，且多字段失败留下已保存前缀，证明该能力不能外推为任意字段可持久化或事务式写入。

2026-07-23 又把 raw CDF 参数作为显式、有限的 `instance_parameter_space` 接入原有 candidate/checkpoint 状态机。它不自动枚举完整 CDF，也不改变独立 `parameters.apply`：搜索必须使用未过滤 OA readback 中实际存在的字段名和值字符串，并与固定实例字段及 semantic space 形成一个受预算截断的笛卡尔积。真实共源 Gate 搜索 `MN0.fingers=["1","2"]`；两个点均完成 callback 后定向回读、OA→`si` 网表与 DC/AC，网表总宽度从 1 µm 变为 2 µm，最终按 GBW 写回 fingers=2。第一次 candidate 1 的 `si -batch` transport reset 在恢复初始 RD/fingers 后从 index 1 重试，未跳点。状态升级为 **bounded explicit-instance-parameter tuning, same-source evidence, writeback, and recovery verified**；多字段 callback 耦合、alias 搜索、PVT-aware raw 写回和自动参数物理语义仍未闭合。

同日进入并真实闭合 Gate 3 nominal 主线。新建不覆盖的 `vb_pdk_smoke/vda_diffpair_gate3_001/schematic` 完成 `MN0/MN1/RD0/RD1` 精确回读、OA→`si` 双管/双负载 geometry、单点 DC、只读尾电流/共模搜索、18 点 W/RD/尾电流设计搜索、最佳写回、全不可行、预算和 transport checkpoint；最终 OA 为 `W=2 µm/L=30 nm/RD=8 kΩ`。18 点搜索在两个不同候选发生 transport reset，均经独立 OA 回读后续跑并最终完成。状态升级为 **bounded differential-pair nominal DC writeback and recovery verified**。

同一最终 OA 随后完成差模 AC、有限尾源输出电阻下的差模/共模配对 AC、输入共模范围和相干 transient。无额外负载时差模增益 `2.992 V/V`、带宽 `29.18 GHz`、GBW `87.25 GHz`、unity `86.80 GHz`；显式 `1 MΩ` 外部尾源输出电阻得到低频 CMRR `58.59 dB` 和 CMRR 下降 3 dB 带宽 `306.38 MHz`。0.20–0.90 V 的 12 点共模采样在 transport reset 后从 9/10 checkpoint 恢复，当前 50 mV 余量门下通过点为 0.30–0.875 V。100 MHz/每端 1 fF 的 7 点差分 transient 得到输入 P1dB `110.9 mV peak` 和 250 mV 输入下 THD `16.54%`。四点尾电流×负载 AC 只读搜索也复用原有限搜索/选择状态机。所有动态任务的 `si` SHA 与最终 OA 一致。状态升级为 **differential-pair nominal same-source DC/AC/CMRR/linearity and read-only tuning verified**；这是 Gate 3 的明确边界，理想尾源能力继续保留。

Gate 4 随后在新 `vda_diffpair_tail_gate4_001` 上以 exact-delta transform 只增加 OA `MNTAIL/BIAS`。5 点 BIAS 搜索选中 0.40 V；三点尾管宽度 checkpoint 按最小功耗写回 0.8 µm。最终同一 OA/`si` 路径得到差模增益 `2.928 V/V`、带宽 `13.806 GHz`、GBW `40.418 GHz`、低频 CMRR `21.135 dB`、CMRR 带宽 `19.069 GHz`；10 点 ICMR、六点 transient 和 141 点 differential noise PSF 也全部完成。首轮 ICMR 状态误分类、transient 下载 timeout 和首版 noise 共模漂移均保留失败 record，并在修正/doctor 后成功重跑。状态升级为 **differential-pair real-tail multi-analysis same-source and bounded writeback verified**；ADE 人工交接、mismatch 和可选 PVT 仍待独立 Gate。

Gate 5 随后在新 `vda_diffpair_deg_gate5_001` 上验证对称源极退化。exact-delta add 只把 `MN0.S/MN1.S` 分到 `NSP/NSN` 并加入 `RS0/RS1` 后汇回 TAIL；500 Ω 的 OA 回读、`si` 网表、两支路电阻电流和 KCL 完全绑定。既有 DC/差模与共模 AC/10 点 ICMR/六点 transient/noise 全部无缝复用；相对同参数无退化基线，输入 P1dB 提升 29.19%、200 mV THD 降低 30.11%，代价是低频增益降低 17.89%、GBW 降低 22.22%、输入参考积分噪声增加 17.82%。250/500 Ω 两点有限搜索按 P1dB 选择并独立回读 500 Ω。两次 remove 都把 placement SHA 精确恢复到 add 前值，恢复网表和 DC metrics 逐项相同。状态升级为 **differential-pair reversible symmetric source-degeneration, full-analysis migration, and bounded RS writeback verified**；PVT 仍按任务可选，未作为默认成本。

同日继续完成 Gate 6 live。新 action 在全新 `vda_diffpair_active_gate6_001` 上把 `RD0/RD1` 精确替换为 `MP0/MP1` 电流镜，并按显式电阻值反向恢复；PMOS W/L 进入 semantic apply/search 面。OA/`si` parser 拒绝混合负载、单边缺失、错误 master/node 和几何不匹配；DC 增加 PMOS OP、支路 KCL、镜像误差、上下管工作区与联合摆幅；AC/CMRR/transient/noise 使用显式 `OUTN` 单端输出。真实 Gate 覆盖 10 点 ICMR、6 点 bias/load、6 点 Wn/Wp、预算耗尽、全不可行恢复、两次 transport timeout checkpoint/resume、最佳 OA 写回、精确 RD restore 和最终 active-load 重建。首版 `PM0/PM1` 因 Spectre `P` 前缀被解释为 port 而失败，保存日志、恢复基线并统一更名为 `MP0/MP1` 后闭合。状态升级为 **Gate 6 current-mirror-load same-source bounded closure verified at nominal TSMC N28**；可选 PVT、P1dB 包围、slew/settling、mismatch 和 ADE handoff 仍待后续 Gate。

随后启动 Gate 6Q 的第一项设计质量指标：差分对 `analysis: psrr`。每个候选只生成一次 OA/`si` 网表，并运行平衡差模、VDD 注入和 VSS 注入三次复数 AC；三次 DC 工作点、频率网格和网表 identity 必须一致。指标明确区分 `Avdd/Avss`、低频 PSRR+/PSRR−、扫频最差值、可选声明频带内最差值和首次下降 3 dB 频点；三份根 AC 文件在临时目录清理前分别绑定大小与 SHA-256。纯 `tail_bias_v/load_ff` 搜索沿用不写 OA 的有限搜索/选择状态机。2026-07-24 nominal 单点得到低频 `11.5156/13.4535 dB`；随后四点搜索在 `1 kHz–100 MHz` 得到组合最差 `11.4275–11.5125 dB`，四点均通过饱和、摆幅、增益、带宽和功耗护栏，但全未通过临时 `20 dB` 证伪门。`load_ff` 对带内值无改善，BIAS 提升仅约 `0.085 dB`；两次 transport 中断从同一 checkpoint 经独立 OA 回读恢复，最终 4/4 完整、零参数选择、OA 不变。状态升级为 **band-limited PSRR search and recovery verified; bias/load-only closure falsified on the declared grid**。因此后续 Gate 转向有边界的器件几何，不再密扫已证伪的两个 testbench 旋钮；该 OA 写入 Gate 的结果如下。

该 OA 几何 Gate 随后在同一 cell 上真实执行：`input/PMOS-load/tail L` 各取 `0.03/0.06 µm`，8 点逐一写入、回读、自动 `si`、三路 AC 和 checkpoint。8 个点全部通过饱和、摆幅、增益、带宽和功耗护栏，只有临时 `20 dB` PSRR 门失败；最佳观测点为三种 L 全部 `0.06 µm` 的 `19.7438 dB`，但没有被提交。更平衡的短尾管点 `Lin=Lp=0.06 µm/Ltail=0.03 µm` 为 `19.7123 dB`、带宽 `1.1186 GHz`、GBW `10.8095 GHz`；把尾管也加长只改善 `0.0315 dB`，却把带宽降至 `0.3305 GHz`、GBW 降至 `3.2940 GHz`。一次候选 3 写后回读 transport reset 先恢复初始 OA，再由同一 checkpoint 从 index 3 续跑；8/8 后因零可行点恢复三组 `L=0.03 µm`，最终运行内回读和额外独立 inspect 均一致。状态升级为 **OA length sensitivity and infeasible recovery verified; provisional PSRR closure short by 0.256 dB**。下一自动 Gate 固定尾管 30 nm，只对输入对/PMOS L 做小范围显式细化；任何过门点还必须复跑 CMRR、linearity、noise 和必要的可选 PVT，不能因单一 PSRR 指标升级 L5B。

2026-07-24 又加入 theory-first 本地 Gate，避免把少量人工枚举点中的最大值误称为最优。首版针对 Gate 6 固定拓扑，输入有限且带来源声明的 gm/Id characterization 表域，用 KCL、小信号和单极点关系反解每个表点组合满足 BW/GBW 的最小电流与 W，并输出功耗、面积、余量、约束裕量、限制项、寄生渐近上限和局部敏感性。程序会完整计数和穷尽声明的离散表域；现有 `design.tune` 也新增结构化 `search_audit`，预算截断只能称 `best_evaluated`。无论哪条路径，连续/全局最优声明固定为 false。随后 `vda theory-calibrate` 消费 Gate 6 六点真实 OA/`si`/Spectre 记录，在固定 `top_tt`、BIAS/VCM/VDD/CL 和 30 nm L 下拟合 topology-local 增益修正与等效电容；六点留一最大 gain/BW/GBW 误差为 `0.083%/0.373%/0.457%`。一次不写 OA 的新鲜同点执行逐值复现旧结果，模型误差为 `0.069%/0.320%/0.390%`。因此状态升级为 **topology-local one-pole calibration verified at nominal top_tt; standalone PDK gm/Id characterization pending**。该局部系数不自动注入 `vda theory`，也不授权 OA 写回。

同日进一步加入 topology-independent small-signal matrix core，避免把上述局部校准误当作通用理论能力。新的器件表按 model/polarity/L/偏置保存 width-normalized `Id/W、gm/Id、gds/Id、gmb/Id` 和端子电容，网络请求只描述 MOS/R/C 节点图、固定 AC 边界和输入/输出表达式，不接受 topology 分类。共源、源退化共源、NMOS 差分对和 PMOS 共源已用同一矩阵组装路径通过解析测试；浮空、偏置漂移和真实数据缺 hash 会拒绝。初始状态为 **topology-independent small-signal matrix core locally verified; live PDK characterization and OA/si graph binding pending**。

Gate 7A 随后闭合第一个待项：新增无 OA target 的 `device.characterize`，在一个独立 Spectre DC deck 中表征 `nch_lvt_mac/pch_lvt_mac`。最终 nominal `top_tt`/27 ℃、W=1 µm、L=30/60 nm 的 240 点表和 4 个真实 VGS 留出点全部通过；最坏归一化误差 `13.70% < 25%`，deck、`dcOp.dc`、`dcOpInfo.info`、log 和 `spectre.out` 共六项绑定 manifest SHA-256，NMOS/PMOS bias/IDS 符号逐点匹配，OA access/write 均为 false。原始 OP 是 `eda_result`，归一化点和插值审计是 `software_inference`。45 nm 留出几何的真实 PDK 失败和 96 点粗网格的 3/4 留出失败均保留并用于修正合法几何和采样/误差定义。状态升级为 **standalone TSMC N28 MOS characterization live verified at nominal top_tt; si graph binding and held-out circuit validation pending**。这仍不是任意拓扑综合或最终规格证据。

Gate 7B 已闭合首个纵向共源点。`vda small-signal-validate` 从 real Bridge run records 绑定只读 OA target、结构化 `si` 实例、真实 DC OP、原始 AC 网格和文件 hash；在 exact L 平面内插值 VGS/VDS/VSB，并拒绝外推。第一次 W=1 µm 表因物理宽度不匹配而拒绝；第二次 W=0.5 µm 但没有 OA 扩散/LDE 参数的表得到 gds `32.20%`、GBW `25.84%` 误差，按原门限保留 partial。实现随后把最多 64 项、禁止覆盖 `w/l/nf/m/multi` 的安全 numeric Spectre 参数签名加入 task/artifact，并要求与 `si` 集合和值完全相同。匹配 MN0 的 31 项签名表完成 60+1 点，留出最坏 `7.42% < 25%`；held-out circuit 的 Id/gm/gds/VDSAT、gain/phase/BW/GBW 全部通过原 policy。状态升级为 **nominal common-source same-geometry small-signal validation verified**。

Gate 7C 随后完成源极退化迁移。旧任务的 W=0.5 µm 与当前 OA W=1 µm 不一致时在 Spectre 前被拒绝；新增 `vda characterization-task-from-run` 从新鲜 real-si MN0 自动提取 exact model/W/L/31 参数签名，并把来源 run/netlist/signature hash 与用户审查的安全偏置网格一起写入 standalone 表征任务。1 µm 表完成 60+1 点，留出最坏 `15.61% < 25%`。同一 binder 从结构化图绑定 `MN0.S=NSRC` 和 `RS0(NSRC,VSS)=2 kΩ`，要求 DC source-current consistency matched；未增加源退化专用 AC 公式。held-out Id/gm/gds/VDSAT 误差为 `17.59%/17.69%/21.36%/2.82%`，gain/BW/GBW 误差为 `0.374 dB/13.52%/17.16%`，全部通过 Gate 7B 原固定门限。状态升级为 **source-degenerated common-source same-source small-signal migration verified at nominal top_tt**。下一步扩到多 MOS、不同 polarity/角色的差分对。自动表征任务生成当前只支持 nf=1/m=1；每种新签名仍需对应表，PVT 可选，理论结果不授权 OA 写回。

多 MOS 迁移的本地基础先完成后，Gate 7D 已执行三张真实 TSMC N28 表和 held-out OA→`si`→Spectre 差分对对照。输入 NMOS、PMOS 负载和尾 NMOS 表各自绑定 exact W/L、31 项 `si` 参数签名、来源实例、同一电路 run/netlist hash；missing/ambiguous/W/signature/source drift 和未使用表均拒绝。legacy 五电容模型的 BW 预测约 `5.27 GHz`，完整 signed 4×4 `dQi/dVj` 后仍为 `5.2356 GHz`；电路内实际 `cxx` 与三表最坏只差 `0.74%`，因而没有靠加密网格或抬高门限掩盖问题。补入与本征电荷矩阵分开的 `cjd/cjs` 后，预测/实际 BW 为 `3.0987/2.9756 GHz`，GBW 为 `11.6800/11.1351 GHz`，误差 `3.97%/4.67%`；增益误差 `0.063 dB`，相位及五个器件的 DC/电容检查全部通过原 policy。全过程只读 OA、Bridge 未修改。状态升级为 **differential-pair exact three-plane same-source small-signal validation verified at nominal top_tt**。多指/多重器件、其他 PVT、mismatch/noise 和不同输出表达式仍是后续边界。

Gate 8 随后完成 theory→EDA 的受控交接。新本地 derivation 入口把 Gate 7D validation hash、三张 characterization run/hash、实际 DC 偏置和 policy 绑定成 64 组合的真实 PDK theory request；12 个一阶可行点经“理论排名前缀 + log-distance maximin”生成 6 个完整原子 tuple，并在 plan 前按声明的 5 nm OA 网格量化。executor 没有把 Wn/Wp/Wtail 展开成笛卡尔积，而是在既有 active-load cell 上逐点 OA 写入/回读、自动 `si`、Spectre DC/差模 AC/共模 AC，遇到候选 6 transport reset 后恢复基线并从 checkpoint 续跑。4/6 真实可行，功耗 objective 选择理论第二名 `Wn/Wp/Wtail=1.315/1.180/0.605 µm`，得到 `10.981 µW`、`3.7277 V/V`、`2.6895 GHz` BW、`10.0258 GHz` GBW 和 `34.815 dB` CMRR；独立 OA readback 一致。只读 follow-up 又完成 PSRR、noise、三点 transient 和 10 点 ICMR/checkpoint，PVT 按本 Gate 授权未运行。状态升级为 **real-PDK theory shortlist to bounded same-source EDA selection verified at nominal top_tt**。

Gate 8 同时保留了理论边界：6 点中 4 点可行使候选生成门通过，但理论第一名并非 EDA 选择；功耗/增益/BW/GBW 的 24 个预先声明对照有 8 个超过 25%，故 prediction-accuracy Gate 为 partial。下一理论 Gate 不在这六点上过拟合，而是用第一遍真实 DC OP 对候选重线性化，再用未参与校正的 held-out tuple 复核；只有 EDA 指标可以决定最终写回。PSRR+ 仍只有 `11.4828 dB`，P1dB 未包围，故 Gate 8 也没有补齐完整设计质量闭环。

2026-07-25 已完成该下一步的本地能力 Gate，但尚未执行新候选。Task contract 新增通用 `candidate_set`：一个候选可同时携带 OA semantic、testbench 和原始 CDF 字段，固定 raw 字段按字段深合并；候选不会同逐维 space 组合或再展开成笛卡尔积。来源为 `software_inference` 时必须 hash 绑定，预测只进 provenance，最终选择仍由本次仿真证据决定。`vda op-relinearize` 则只消费成功 real-Bridge run 中的 `eda_result`，强制分离训练/留出 index、拒绝不可辨识扰动和未建模输入漂移，并要求每个指标同时通过训练及留出误差门，失败时不生成任务。

首轮真实记录回放没有连接 Bridge。共源 W/RD/RS 记录用 6 个训练点和 2 个留出点验证 3 个 OP + 7 个性能指标；最坏训练/留出误差为 `17.823%/11.450%`，均低于预声明 `15%` OP、`20%` performance 门。差分对 Gate 8 记录用 4 个训练点和 2 个留出点验证 8 个 OP + 7 个性能指标；最坏留出误差 `8.262%`。两者都从局部 27 点空间只编译出 6 个原子候选；task template 继续保留局部模型未覆盖的 saturation、THD、CMRR 等完整 EDA constraints。该本地状态是 **real-EDA-record local OP relinearization and atomic candidate compilation verified**，本身不代表新候选已跑 Spectre。

2026-07-26 共源六点进一步完成真实 OA→`si`→AC/transient/noise、候选 1 transport 失败后的基线恢复/checkpoint resume、6/6 全规格可行、最佳写回和独立 OA 回读。模型与 EDA 都选择 `W/RD/RS=1.1 µm/19 kΩ/0.75 kΩ`；GBW 为 `34.3965 GHz`，比 anchor 高 `13.561%`。新增 `vda op-relinearization-validate` 以 exact result/task/run 自动审计候选 provenance、完整域和原 held-out error limit：GBW 最大误差 `4.505%`，但候选 5 output swing 为 `22.479% > 20%`。因此状态升级为 **common-source atomic local-response shortlist to same-source EDA selection verified; live pointwise model accuracy partial**。下一轮共源必须在新 anchor 重新留出验证或缩小 trust region，不能放宽门。PVT 保持可选。

同日先完成不连接 Bridge 的新 anchor 刷新。实现新增 heldout 参数方向覆盖门：每个声明参数至少要在一个留出点变化。原三维 W/RD/RS 刷新因此拒绝，因为 candidates 5/6 的 RS 都与 anchor 相同；这避免把未验证的 RS 灵敏度包装成已校准。随后固定 `RS=750 Ω`，用 candidates `[2,3,4]` 训练 W/RD、`[5,6]` 留出，两维均有覆盖，10 个指标最坏历史留出误差为 `0.568%`。新小域 `W=1.05/1.10 µm × RD=18.5/19/19.5 kΩ` 的六点随后全部完成 OA→`si`→AC/transient/noise；6/6 可行，预测与 EDA 都选择 `1.1 µm/18.5 kΩ/750 Ω`，GBW=`34.65534 GHz`。exact validator 的 60/60 项比较全部通过，最坏新点误差为 `0.353712%`。两次 transport `system_event` 都安全恢复并从 checkpoint 续跑，最终 OA 独立回读一致。当前状态是 **held-out-covered common-source W/RD local response to same-source EDA selection verified at nominal top_tt**；继续 RS 调优前仍需要独立 RS 探针，PVT 保持可选且未运行。

差分对六点随后完成独立 live Gate。预检先回读 Gate 8 anchor，六个 `Wn/Wp/Wtail` tuple 再逐点完成 OA 暂存/回读、`si`、双支路 DC、差模 AC 和共模 AC/CMRR。一次 DNS/SCP 和两次 `WinError 10054` 都被分类为 `system_event`；执行器恢复并独立回读 anchor 后从未完成 index 续跑，最终 6/6 可行。局部模型与 EDA 都选择最小功耗点 `1.215/1.080/0.555 µm`：功耗 `10.2971 µW`、gain `3.71568 V/V`、BW `2.68269 GHz`、GBW `9.96800 GHz`、CMRR `34.8249 dB`。exact validator 对旧 result 使用其 canonical-hash-bound 原 policy 恢复已预声明的误差 floor，90/90 比较全过，最坏新点误差 `2.5693%`；最佳 OA 独立回读一致。当前状态是 **differential-pair held-out local-response shortlist to same-source EDA selection verified at nominal top_tt**。下一 Gate 不再重复尺寸六点，而是只读复核新最佳点的 PSRR、noise、linearity/P1dB 和 ICMR；PVT 继续按任务可选。

Bridge 隔离分支进一步加入幂等 SSH 有界退避和仅限 payload 发送前的 tunnel 自愈。新的 9 点压力任务仍在候选 8 发生一次本地端口拒绝，但 OA 恢复、候选前缀和续跑均正确，最终 9/9 与最佳写回成功；确定性同-client smoke 已覆盖 pre-send 自愈。payload 发送后的不确定错误仍不自动重放，这是保留的可靠性边界而不是跳过的工作。

2026-07-21 又完成 `ade.prepare` + `ade.capture` 的本地纵向实现。`prepare` 只在已有 design schematic 且目标 Maestro view 不存在时新建持久化 Spectre test，保存后重新打开核对；已有 view 一律拒绝，不配置 analysis/stimulus/sweep/output。人工补全并运行后，`capture` 只读核对聚焦的 `library/cell/maestro`，默认要求 setup 已保存，捕获 setup、指定/最新 history、Spectre netlist、PSF/log 和 ADE 逐 sweep 点 output/spec，并生成逐文件及聚合 SHA-256。两者都不把准备或捕获成功算作 VDA 规格 closure。Bridge 当前公开的持久化后端是 Maestro；旧 ADE L state 非破坏迁移、VDA-managed variable sweep/corner 和 live nics4304 prepare/capture 仍待 Gate，因此此项当前只能称为 **local bidirectional human-operated ADE handoff contract implemented**。

同日新增 `ade.run` 本地纵向能力：不打开或聚焦 GUI，以独立 background Maestro session 运行一个已保存 setup 中的原生 analysis/parametric sweep，并把 history 中逐 point 参数、output、spec 和 pass/fail 作为 `eda_result` 回收。随后补齐 exact-history + unique-runtime 产物门、显式 history/scratch 恢复、`/data/xum` 临时 session 目录定向与恢复，以及可选 OA→`input.scs` 一致性。2026-07-21 live Gate 在新 `vda_manual_ade_handoff_001/maestro` 上运行一次 transient；首轮 EDA 已完成但证据清单受 IC6.1.8 `csh` buffer 和本机 SCP DNS 影响失败，后续均按 `Interactive.0` 恢复而没有重算。最终 Detail 表返回 `VoutAvg=364 mV`、spec pass，清单含 65 个 simulator-input、1 个 RDB result 和 2 个 log，test design、OA instance/nodes 与 19 组 raw 参数映射一致。状态升级为 **live background Maestro same-source execution, evidence recovery, and raw-input consistency verified**；history 名唯一性、通用 constraint 映射、CDF `Wfg` 派生语义、变量 sweep/corner 仍未闭合，因此没有升级 L5B closure。

随后新增并扩展 `ade.variables.apply`：任务声明 exact tests、可选 enabled corners，以及每个 global/test/corner design variable 的 scope、旧值和新值；还可用独立 CAS 更新 global-variable enabled/disabled selection，并要求未声明名称在完整集合中保持。全部前置条件匹配后逐项写入，只保存一次 setup，再用全新 background session 逐 scope/selection 复核持久化值。`null` 可断言变量在该 scope 原先不存在，逗号字符串可请求该 scope 的原生 sweep。已有已配置 Maestro session 时保守拒绝；请求标为 `user_input`，三阶段回读标为 `bridge_readback`。单独执行该 Gate 不证明变量进入网表或仿真。2026-07-22 先在专用 TSMC N28 Maestro view 上真实闭合 global CL 三点，随后又闭合 test-scope `CL=1f,4f`、named-corner VDD=0.8/0.9、global VDD=0.9 以及 CL enabled→disabled；后续严格 corner Gate 证明这些状态实际进入结果链。

同日新增正交 `ade.corners.apply`：它只在 exact tests/旧 corner 顺序匹配时 add-only 新增命名 enabled corner，逐项回读、保存一次、独立重开；不删除、改名、替换或偷偷配置 model/temperature。live 从内建 `Nominal` 增加 `VDA_LOW_VDD/VDA_NOMINAL_VDD`。这两个名字在当前 Gate 只承载 VDD override，仍使用 nominal `top_tt`，因此不能冒充 process/temperature corner。

现在又新增正交的 `ade.setup.apply`：在 exact tests 下，对声明 analysis 的完整旧 enabled/options 做 CAS，并只新增明确不存在的命名 net/point output 与可选 lt/gt spec。worker 在首个 writer 前读完全部旧 analysis 和 output absence；随后复用 Bridge public analysis/output/spec writer，逐项立即回读，只保存一次，再独立重开核对。已有 output 不会被替换，未声明 setup 状态也不会被包装成已核验；analysis/output 配置仍是 `bridge_readback`，没有仿真就没有 `eda_result`。2026-07-21 nics4304 live Gate 已把默认 disabled transient 改为 `stop=300p/maxstep=1p`，新增 `/IN`、`/OUT` 和带 `>0.1` spec 的 `average(VT("/OUT"))`，并在独立重开中全部匹配；首次 expression 序列化失败发生在 save 前且重开证明零持久化。状态升级为 **live declared-analysis CAS and add-only output/spec persistence verified**；已有 output 替换、变量/corner scope 与完整 setup 指纹仍待验证。

2026-07-22 为 `ade.run` 增加可选 `sweep_verification` 严格门，并在 TSMC N28 上真实闭合。任务声明 exact tests/corners、各 scope 保存的逗号 sweep、连续 expected points 和 OA `instance.parameter` 绑定；worker 在运行前后回读同一 setup，RDB 每点必须有匹配参数和非空 scalar output。原先本地实现假设 exact history 会保留逐点 `input.scs`；live 运行证明 IC6.1.8 实际只保留共享 runtime `input.scs` + sibling `netlist` 和 exact-history RDB。VDA 因而保留两个互斥严格模式：出现任意逐点目录就要求全点 input/result 完整；完全没有时才允许共享符号输入束 + exact-history RDB/completion log，且必须同时核对 include 关系、两份输入 hash、OA/Spectre 变量引用、一个声明 retained point、RDB 全点值/非空 output、精确完成点数和零仿真错误。数据库模式明确保持 `exact_point_input_result_binding_verified=false`，不把共享 netlist 冒充逐点文件。最终 `Interactive.0` 给出 CL=`1f/2f/4f` 和 `VoutAvg=415.5/419.8/428.3 mV`，3 点完成、0 错误；证据失败后按固定 history/scratch 恢复，没有重算。最终本地回归 `288 passed`、`59/59` example plans。状态升级为 **native Maestro single-variable sweep same-source execution and evidence recovery verified**；二维 sweep、corner、多 test/multi-analysis 和通用 constraint 映射仍待 live。

同日继续闭合有物理意义的 ADE 质量 Gate：以 add-only 方式保存并独立重开核对 `Tphl/Tplh/Delay/Rise/Fall/RiseFallSkew/SupplyEnergyCycle`，再把 exact output、预期 calculator expression、SI-to-ps/fJ scale 与 VDA metric 显式绑定。worker 在 run 前后指纹化 output state；executor 只从严格 sweep 的有限 RDB scalar 构造候选，原始值保留为 `eda_result`，换算、constraints 和选优标为 `software_inference`。`Interactive.1` 的 CL=1/2/4 fF 分别得到 delay=2.882/3.766/5.503 ps、skew=1.223/2.112/3.939 ps、周期供电能量=1.659/2.497/4.147 fJ；三点都通过 6 ps/5 ps/5 fJ 约束，能量 objective 选中 1 fF。随后固定同一 history/scratch 的表达式指纹恢复没有重算或写 OA/setup。最终本地回归为 `314 passed`、`61/61` example plans。状态升级为 **native Maestro inverter scalar-to-constraint quality selection verified**；周期供电能量包含泄漏与短路电流，不称为纯动态能量。

下一步的二维 Gate 也已真实闭合：在同一 schematic 中把 `VDD0.vdc` 与 `VIN0.v2` 都改为 `VDD` 符号，global `VDD=0.8,0.9` 保存并独立重开，七个 VDD-aware outputs 以 add-only 方式持久化。`Interactive.2` 的 `VDD×CL` 六点全部进入同一个共享符号 `input.scs`+`netlist` 和 exact-history RDB；五点通过原 6 ps/5 ps/5 fJ 约束，能量 objective 选择 `0.8 V/1 fF`，得到 delay=3.254 ps、skew=1.425 ps、周期供电能量=1.309 fJ。第一次本地等待边界先于 worker 清理窗口终止，但远端已完成；孤立 background session 按旧 record 恢复 runtime path 后关闭，后续固定同一 history/scratch 恢复没有重算或写 OA/setup。旧固定阈值 `Rise/RiseFallSkew` 在三个 0.8 V 点产生的 6 个 `eval err` 被 exact test/output/point 契约、RDB 单元格和 log 数量双层核对，未解释错误为零；默认仍要求 0 error，mapped output 不得进入该声明。最终本地回归 `323 passed`、`65/65` example plans。状态升级为 **native Maestro two-variable VDD×CL same-source quality selection and recovery verified**；下一自动化门转为有限 corner/test-scope，L/VDD 联合设计搜索仍未闭合。

有限 scope/corner Gate 随后真实闭合。`CL=1f,4f` 位于 test scope，global CL selection 明确 disabled；global VDD=0.9，两个 named corner 覆盖 0.8/0.9 V。Bridge 0.7.0 的普通 Detail parser 只保留 Nominal 值，因此 VDA 通过其公开 `include_raw=True` 读取原始 CSV，按 2 个 Maestro parametric point × 3 个 corner 列构造 6 个可审计 case，并保留 raw hash。`Interactive.4/5` 的 4 个低压 legacy error 全部被精确解释，5 个 case 可行，仍选中 0.8 V/1 fF。状态升级为 **native Maestro test-scope × environmental-corner quality selection and bounded timeout recovery verified**；这里仍是 nominal model 下的环境 VDD corner，不等于真实 PVT。

2026-07-22 又在 direct common-source 路径闭合下一自动化 Gate。`L=[0.03,0.04] µm × VDD=[0.8,0.9] V` 四点三分析搜索全部可行，GBW objective 写回并复核 `L=0.03 µm`，VDD 保持 testbench 条件。随后同一 OA/同一 `si` 网表在 TT/25℃/0.90V、SS/125℃/0.81V、FF/−40℃/0.99V 下运行九项 Spectre 分析；每个条件都保留原始指标，全部满足规格，objective 按跨条件最坏值聚合。状态升级为 **bounded common-source L/VDD quality tuning and fixed-design PVT verification verified**。当前缺口收敛为 PVT-aware 设计候选调优、ADE 真实 PVT/multi-test 映射和差分对，而不是 L/VDD 或固定设计 process/temperature 执行能力。

2026-07-23 将 `operating_conditions` 作为显式可选项接入 `design.tune`/`design.close_loop`。不声明时仍按 nominal 单条件运行；声明后每个候选必须跨完整条件集，按最坏值选优。真实只读 Gate 对 `bias_v=[0.35,0.40] V` 跑完 18 项分析，`0.40 V` 因多角 THD、SS 摆幅和 FF 功耗失败，选择 `0.35 V`，OA 前后不变。状态升级为 **optional PVT-aware testbench tuning verified**。OA 设计变量的跨 PVT 最佳写回、全不可行、预算和 completed-prefix transport resume 已有本地测试，但 live 仍是可选加严 Gate，不作为所有调优的默认成本。

需要用户操作 Virtuoso/ADE 的验证已按用户决定延期，并集中记录在 [`deferred-manual-gates.md`](deferred-manual-gates.md)：包括人工修改/保存/重跑后的双向交接、旧 ADE L state 备份后迁移与重开，以及相同 history/output 的人工数值交叉检查。这些项目不阻塞后台自动化实现，但在真实完成前仍保留为未验证边界；延期记录本身不构成远端授权。

2026-07-25 又完成 Bridge/Spectre/ADE 资源生命周期 Gate。重复 10 次真实只读 worker 请求没有新增本地 Python/Spectre/SSH、句柄或 `vda_*` temp；合成超时先证伪 `taskkill /T`，随后 Windows Job Object 真实杀净 worker 的 120 秒后代进程。远端 direct Spectre 使用已哈希回读的 timeout guard。follow-up 再加入 cancel marker/父进程 watchdog，让 Python `finally` 在强杀前有 30 秒恢复 Maestro runtime、关闭 session/client；既有 Maestro view 的真实 timeout 故障注入后，独立 inventory 得到 Spectre/si/Maestro session 均为 0，空诊断 root 经逐层检查后精确删除。`vda resources [--remote]` 现可只读盘点本地 temp、持久 evidence、远端 EDA process/session、age/size 和 exact pin；默认不删除。状态升级为 **known VDA-owned process lifecycles bounded, Maestro cancellation live-verified, and retained evidence inventory available**。硬件/OS 崩溃与历史 evidence 的用户确认删除仍不是自动 GC。

2026-07-27 又把显式 Bridge 启停收进 `vda bridge start|status|stop`：短生命周期 launcher 在 Windows 使用隐藏窗口与同一 Job Object 所有权，进度和失败只回到当前终端；真实 `start/status` 得到 tunnel、CIW daemon 和 Spectre version healthy，命令退出后没有残留 VDA/Bridge launcher。该 Gate 没有访问 OA 或运行 Spectre analysis。审计同时发现第三方 Bridge 的 `%TEMP%/vb_tunnel_stderr_*.log` 历史保留 92 份，多数为 0–93 bytes；VDA 不越权删除或把它包装成已解决。若修复该 Bridge 磁盘卫生问题，必须先在现有私有分支建立新的修改前备份引用，记录逐文件差异并单独跑 Bridge/VDA 回归。

2026-07-26 先完成通用 topology-delta 的本地契约 Gate，随后在全新 `vda_generic_topology_delta_001` 上完成首个 live Gate。结构 snapshot 对实例 master/端子/位置属性、net 和 pin 做确定性 SHA-256；八类 operation 可序列化并自动生成逆向 patch。live contract 把普通共源级增量变为 `MN0.S→NSRC + RS0(NSRC,VSS)`，before/after SHA 分别为 `4471c939...d4dea9`/`68e0cbcd...a67ed`；RS0=`1K` 经过 OA 回读并进入 `si`，退化 DC 的 source current mismatch 为 `0.002723%`。inverse 后独立 readback 精确恢复 before SHA，普通共源 DC 再次成功。状态升级为 **bounded generic topology-delta OA execution, same-source netlisting, and exact inverse restoration verified**。

同日继续完成 post-save recovery 与 `replace_master + CDF` 本地 Gate，随后在新 `vda_master_migration_001` 做真实 round-trip。`MN0: nch_lvt_mac -> nch_mac` forward 的 before/after SHA 为 `4471c939...d4dea9`/`17ddebdb...8d5d1`；独立 OA 回读的 233 项 CDF 只有 `description/model` 随 flavor 改变，Wfg/l/fingers/m 保持。`si` 明确导出 `nch_mac`，SVT 的 271 点 AC 得到增益 `4.0184 V/V`、BW `4.5914 GHz`、GBW `18.4502 GHz`；inverse 后 topology、完整实例参数、LVT netlist SHA 和核心 DC/AC 标量与 before 精确一致。状态升级为 **controlled symbol-master/CDF migration, same-source simulation, and exact inverse restoration live verified**。post-save 自动 recovery 仍只有本地故障注入，不由正常 inverse 代替。

下一轮先在本地把通用 delta 的物理边界补到 pin 与完整 placement，随后已在两个新 cellview 完成 live。真实 PDK callback 把声明的 `MN0.m=2` 归一回 `1` 后，post-save 审计按预期失败，VDA 仅在新鲜 topology 匹配时执行 inverse；独立 readback 恢复原 topology、placement 和参数，原请求仍保留失败。共栅 Gate 则只增加 `NCAS/VCAS/MNCAS/VCAS pin` 并重连 `MN0.D`，真实 pin figure 的自动 OA 名用逻辑 pin 绑定得到稳定 placement SHA；部分恢复后缺失 terminal stub 的路径也以 exact instTerm/current-net CAS 和最小重建闭合。hash-bound OP 分析器生成的同一 9 个原子 tuple 先跑 DC、再跑 AC，对应自动 `si` 网表 9/9 匹配且两轮 9/9 可行；AC 离散域最佳点 gain=`6.4412 V/V`、BW=`3.6127 GHz`、GBW=`23.2705 GHz`。exact inverse 后普通共源 netlist、17 个 DC 指标和两次 28 项 AC/DC 指标一致。状态升级为 **recoverable common-source-to-cascode bounded same-source DC/AC selection live verified at nominal top_tt**；noise/linearity/PVT 与完整质量选拓扑仍待后续 Gate。

同日又在新 `vda_diffpair_active_deg_generic_001` 上把同一 contract 组合到 Gate 6 PMOS 电流镜负载：before/after SHA 为 `d3fe4b73...31b93`/`68c9d2e2...559a`，`RS0=RS1=500 ohm` 经过 OA 回读并进入七实例 `si` 网表；DC 的 PMOS/RS/尾管 KCL 全部 matched，随后差模 AC/CMRR、noise、transient/THD、十点 ICMR 和 nominal PSRR 均复用既有 worker。真实结果为增益 `3.5523 V/V`、BW `2.0576 GHz`、GBW `7.3093 GHz`、低频 CMRR `34.446 dB`、输入参考积分噪声 `817.84 uV RMS`；`50 mV_peak` 时 THD `1.411%` 且 P1dB 未包围，PSRR+/- 只有约 `11.06/13.08 dB`。inverse 后独立 readback 精确恢复 before SHA，恢复态 DC 再次成功。状态升级为 **active-load plus symmetric source-degeneration generic-delta and full nominal analysis migration live verified**。参数值继续走独立 CDF/semantic 契约；通用 wire/shape snapshot、pin 几何和并发 editor 尚未闭合，post-save recovery 仍待 live 故障注入。

## L5B：给定大致拓扑的可适配模块设计代理（产品目标）

面向用户给出大致拓扑或已有 schematic 的单模块，由规格驱动完成更完整的设计细化；
首版不要求 VDA 从空白发明复杂电路，也不把模块限制为反相器、单管或差分对：

- 理解用户声明或确认的端口、器件角色、冻结对象和可修改边界。
- 在用户起始拓扑周围提出、执行或拒绝受控的局部 topology-delta，而不是大范围重建。
- 自行计算和调整实际 schematic 暴露的参数，不要求每个新电路增加一套核心 executor。
- 自动建立 testbench、analysis、output 和 sweep。
- 处理 DC operating point、AC、tran、noise 和多 corner。
- 自动流程与可人工打开、调整、重跑的 ADE setup/history 双向交接。
- 使用更有效的优化策略，同时保留参数边界和预算。
- 识别不可行规格、模型异常和仿真失败，而不是无限重试。
- 生成可复核的设计报告和未闭合项目。

L5B 的完成标准是“给定大致拓扑后，模块设计细化与规格闭环可适配、可重复”，不是能
偶尔跑出一组好看的波形。上述 analysis、多 corner 等是按任务需要选择的能力面，不是
所有电路的固定必跑流程；完整定义以本文 v2 为准。

2026-07-28 已完成该方向的第一项本地基础：新增 hash-bound `design_context`，把任意
结构化 OA readback 绑定到用户/Agent 声明的角色、端子连接、冻结对象、实例/semantic
参数权限、analysis/metric 意图和局部 topology-delta envelope。planner 在所有后续远端
动作前显式加入只读绑定步骤，executor 将审计记为 `software_inference`；同一审计器已在
命名和结构不同的单端级、差分级本地图上通过。它先约束已有 inspect/parameter/
topology-delta 及专用仿真路径，现也作为下述通用 simulation/tune/close-loop 的共同前置；
这仍是 L5B 通用化基础而不是 L5B closure。

2026-07-31 又补上首次接入编译器：`vda onboarding-draft` 从 hash/plan-bound 的只读
real-Bridge inspect 任务与 run record 生成待确认草案。它保留完整 CDF inventory，默认冻结
全部已见结构、授予零参数权限，并将 testbench 固定为不可执行；显式一层 child 只建立
`TOP/CHILD` scope，不猜 `si` terminal order。保留的 flat 与 hierarchy 真实证据各本地重放
235 个 CDF 字段。这个结果降低手抄 JSON 和漏字段风险，但尚未把用户意图解析为可执行
TaskSpec，因此仍不是一次新的 live OA Gate。

同日继续闭合 resolution 编译：`vda onboarding-resolve` 只接受与 draft SHA-256 一致的显式
`user_input` intent，逐项证明角色、top node、CDF permission/binding 和一层 child terminal set
来自草案 inventory，再复用现有 `TaskSpec`/planner。真实历史 flat 草案已生成只读 AC 计划，
hierarchy 草案已生成两点 shared-netlist tuning 计划；两个输出的 remote compute/write 均为 false。
因此“inspect→草案→普通任务”的本地接入链已闭合，但没有产生新的 EDA 结果。

随后首个“用户没有现成 schematic”的 live Gate 把这条链用于新 PMOS 有源负载共源级。VDA 先
非覆盖创建最小 OA 基线，再用通用 delta 做局部负载替换；theory + standalone preview 从六个物理
候选冻结 top-3，普通 `existing_schematic design.tune` 再把两管 CDF 与 VBP/CL typed testbench
条件作为原子 tuple 执行同源 DC/AC。三点均可行，离散域 winner 的 gain/BW/GBW 为
`3.23223 V/V / 5.72128 GHz / 18.4925 GHz`，最终 W/L 写回与独立 OA 回读一致；VBP/CL 不写 OA。
这证明首次接入可到达真实选择与恢复，不证明遗漏三点的 OA winner retention、连续最优或 L5B
质量闭环。随后本地纵切已让 resolution 编译用户确认的可逆 topology envelope 和 winner-only
quality intent：最多三个 alternative 必须从 draft topology 完整 forward/inverse，新增实例只开放
声明的 fixed CDF/binding；所有候选仍只跑 nominal DC/AC，transient/noise 与可选 PVT 只交给真实
winner。alternative 未改变接口时继承 baseline testbench，不再复制整份契约。输出仍禁止远端
compute/write，复用原 `design.close_loop` executor。该能力先以本地 contract Gate 闭合；不为它再造
一个测试电路，首次 live integration 留给下一次真实用户模块使用。

紧接着的本地纵切补齐写后参数 promotion。`vda onboarding-promote` 以最终 stage-1 task SHA-256、
真实成功且穷尽声明域的 close-loop run、最终 Bridge topology readback 和更晚的独立 winner inspect
为入口；实际 selected variant 自动选择预声明 tuning 分支。只有 fresh inventory 中真实存在、同时有
显式 permission 与 OA→`si` binding 的字段才能进入第二阶段原子候选，因此新增器件不再永久停留在
fixed 初值。输出是普通固定拓扑 `design.tune`，quality/PVT 只对其真实 winner 运行，安全开关仍关闭；
两阶段分别复用原 checkpoint，promotion artifact 可幂等重建。当前 867 项本地回归通过，尚未用一个
新测试电路重复跑远端；首次 live 使用应绑定实际用户模块与实际规格。

同日第二个本地纵切开放 `existing_schematic simulation.run` 的受限通用 DC/AC 路径。任务以
typed `generic_simulation` 声明 voltage/current sources、R/C loads、single-ended 或
differential transfer、DC/source-current/MOS OP metrics 和 OA-CDF→`si` 参数绑定；任何 raw
Spectre/SKILL/shell 字段均拒绝。worker 在远端计算前再次绑定同一 `design_context`，并要求
OA 与 `si` 的 flat primitive instance/model/node 集合及声明参数一致，然后复用现有 process
guard、Spectre runner、PSF 解析和 SHA-256 manifest。首个 nominal TSMC N28 live Gate 已在
当前 `MN0+MNCAS+RD0` 共栅级联 OA 上完成只读 DC/AC：9 项 CDF 映射、拓扑与网表均一致，
通用路径的 17 项 OP/AC 指标与旧专用路径最坏相对差 `4.1e-16`。Gate 还真实捕获并修复了
worker 二次回读漏掉物理 pin geometry 导致的假 context hash 漂移，并在一次 `WinError 10054`
后恢复 tunnel、确认 `si=0/spectre=0` 再重试。该首个 Gate 当时只覆盖 flat DC/AC；后续纵切
已加入 staged transient/noise、winner-only PVT 和显式一层 hierarchy 本地契约，但仍不是
L5B closure。

同日第三个本地纵切完成通用有限实例参数闭环。`existing_schematic design.tune` 现在只接受
实际 CDF/OA 字段的 `instance_parameter_space`，或只含实例更新的原子 `candidate_set`；每个
被写字段必须同时在 `design_context` 的 search/fixed 权限和 OA→`si`
`netlist_parameter_bindings` 内，拒绝未知电路的 semantic alias、theory seed 或未绑定写入。
executor 没有新增电路分支，直接复用既有逐候选暂存、callback 后定向回读、同源仿真、
checkpoint、预算、最佳写回和无可行恢复。本地三点 fixture 已覆盖最佳点二次提交、全域不可行
恢复初值、`best_evaluated` 预算边界，以及候选 2 transport interruption 记为
`system_event`、恢复初值并从 index 2 重试。

2026-07-29 在同一 `MN0+MNCAS+RD0` TSMC N28 cell 上完成首个真实 OA-write Gate：
`MNCAS.Wfg=750.0n/1u/1.25u` 三点均完成 OA 定向回读、9 项 CDF→`si` binding、Spectre
AC 与完整 artifact manifest，三点拓扑指纹一致。候选 1、2 完成后，候选 3 前的真实
`WinError 10054` 触发基线恢复；checkpoint 保留 completed prefix，独立 OA/资源核验后从
index 3 续跑，未重复前两个候选。最终任务外 inspect 确认结构不变且
`MNCAS.Wfg=750.0n`。三点都可行且任务没有 objective，因此回写首个声明点只验证通用状态机，
不是设计性能最优。当前状态升级为 **generic existing-schematic finite raw-instance OA-write
tuning live verified for one flat nominal AC field**。

2026-07-31 完成第四个纵切：`existing_schematic design.close_loop` 可在一个 hash-bound
基线与一份预声明、可逆的局部 topology-delta 间运行相同的有限 raw-instance 候选域。
首版只允许一个 alternative，强制显式 objective 和覆盖完整 `2×N` 域的预算；候选可由人工、
理论或 OP 局部模型产生，但 controller 不随机扩点。两条路径分别做 context bind、OA 参数
写回/回读、OA→`si` binding 和 DC/AC；全部证据齐全后才提交 topology+parameters，完全同分
保留基线。全不可行恢复执行前基线；transport checkpoint 以全局候选 index 和 topology SHA
恢复，未知/部分结构拒绝自动覆盖。本地 fixture 已覆盖多字段原子 objective、alternative 与
baseline winner、全不可行和 alternative 中断恢复。fixture 中的 `eda_result` 只用于验证状态
机，不是电路性能证据。

同日已在 non-overwrite 的 `vda_existing_close_loop_gate_001` 完成真实同源 Gate。create 后从
实际 OA inspect 编译 fresh baseline/alternative hash；两个 `(MN0.Wfg,RD0.r)` tuple 在普通共源
和固定 `RS0=750 ohm` 的源退化变体上共完成四次 OA→`si`→Spectre AC。四点的拓扑/参数绑定、
271 点波形和 manifest 均完整；按 GBW 选择 baseline 的 `1.1 um/18.5 kohm`，随后 exact inverse
与独立 inspect 确认无 `RS0/NSRC` 且胜出参数已写回。baseline 两点之后的真实
`WinError 10054` 被记为 `system_event`，恢复 exact baseline 后从 checkpoint index 3 继续，
没有重算前缀。状态升级为 **single-delta existing-schematic topology-and-parameter same-source
close-loop live verified at nominal top_tt**。

2026-07-31 随后闭合 staged generic multi-analysis 的首个真实纵切。任务可把
DC/AC/transient/noise 声明成有序 stage，把每项 constraint 唯一分配给对应分析；只有完整
EDA 结果违反前级约束才提前淘汰，missing metric/空结果/仿真异常保持 incomplete。新建的
`vda_l5b_staged_gate_001` 用 3 个原子 `(MN0.Wfg,RD0.r)` tuple 实际执行 9 个 analysis：首点
在 DC 后拒绝，另两点完成 gain/BW、THD/功耗与 noise，按 GBW 选择并独立回读
`1u/5K`。显式 `shared_netlist` 又把每个候选合并成一次 worker/OA readback/`si`，executor
独立复算 gate 和 stage prefix；9 个 simulation action 从 739.195 s 降为 309.555 s，完整
成功命令从约 787 s 降为 358 s，三点全部指标与隔离模式逐项相等。首次 live 暴露“一项
metric 同时有上下界”的索引缺陷，修复后从 candidate-boundary checkpoint 成功续跑，失败前后
OA 恢复均有独立回读。

同日继续完成三个面向 L5B 通用性的纵切。第一，`winner_verification` 将便宜 nominal 搜索与昂贵
质量 Gate 分开：只把 provisional winner 暂存后，才按独立约束和显式 operating conditions 运行
DC/AC/transient/noise；失败或不完整会恢复搜索前 OA 并撤回推荐，不自动提升未复核 runner-up。
真实两点 TSMC N28 Gate 以 GBW 选择 `MN0.Wfg=1u/RD0.r=5K`，只对该点运行
TT/27 ℃/0.90 V 与 SS/125 ℃/0.81 V 的四分析；两条件全部通过，任务外 OA 回读一致。
一次候选 1 下载 `si.env` 的 DNS/SCP 中断被记为 `system_event`，恢复并从 exact candidate
boundary 完成。

第二，topology refinement 从一个 delta 泛化为最多七个共同基线、after 指纹各异的 independent
alternatives。controller 展平 topology×parameter domain，在每个 alternative 之间 exact inverse
回共同基线，允许 baseline、最后或更早 alternative 胜出；checkpoint 和恢复均绑定 exact topology
identity，未知/部分状态不写。该 controller 随后在 non-overwrite 新 cellview 上完成 common-source、固定
源极退化和 cascode 三 topology × 两参数 tuple 的真实 Gate：6/6 OA→`si` DC/AC 完整，三次 transport
中断均在任务外 exact readback 后从 index 2/5/6 恢复，最终写回唯一满足 gain/BW 门的 cascode
`MN0.Wfg=1.1u/MNCAS.Wfg=750n/RD0.r=18.5K`。选择范围仍明确为完整声明离散域，不宣称连续或全局最优。
第三，generic `si` 核对增加显式一层 hierarchy binding：top-level subcell call、terminal order、
child OA pin/primitive graph、canonical topology/placement 与 subckt body 必须逐项一致，nested 或
未绑定 hierarchy 在 Spectre 前拒绝。2026-07-31 已在两个 non-overwrite cellview 上完成 live Gate：
先从 child schematic 非覆盖生成并独立回读 sibling symbol，再把 flat top 增量替换为同库 child instance。
flat/hierarchical 两条 OA→`si`→Spectre DC/AC 的 output DC、供电电流、gain、BW、GBW、unity 最坏
相对差 `4.09e-15`。raw `si` netlist 保留为 `eda_result`，为无扩展名 include 增加 Spectre language
header 的 `.scs` envelope 单独标为 `software_inference`。

显式一层 child 参数路径随后也已接入既有 candidate/checkpoint 状态机。`XAMP/MN0.Wfg` 和
`XAMP/RD0.r` 的 scope 同时绑定 child target、topology 与 placement；三点 live Gate 逐点完成 child
CDF 写回/回读、`si` subckt 的 `Wfg→w`/`r→r` 一致性、shared-netlist DC/AC 和最终 winner 写回。
最高 GBW 候选因 gain 门失败而未被选中，两个可行点中写回 `1.1u/18.5K`。本地故障注入另覆盖
全不可行恢复和 child 写后 checkpoint resume。详见
[`validation/2026-07-31-existing-schematic-hierarchical-parameter-tuning-live.md`](validation/2026-07-31-existing-schematic-hierarchical-parameter-tuning-live.md)。

2026-07-31 先补上新器件接入时的通用 `parameters.binding.discover`：它不要求先知道 `si` 参数名，
而是对一个 context-authorized CDF 字段执行 baseline/probe/restore 三次自动 netlisting。任务以 exact
topology 和目标实例完整 CDF 表做 CAS；worker 在 probe netlisting 失败时仍从 `finally` 恢复，下一次
运行也只允许从声明 probe 中断态先恢复。三份 netlist/log 先落本地 hash manifest，再精确清理成功的
远端 `si` scratch。`binding-discovery-task` 再复用真实 inspect source 审计，自动继承完整 CDF、
topology/placement 和冻结对象，输出 source/intent/task/CDF hash handoff，且执行开关固定关闭；flat、
未知字段拒绝和显式一层 child 使用同一编译边界。

2026-08-01 已在 `vda_cs_cascode_gate_001/MNCAS` 完成真实
`Wfg 750.0n -> 800n -> 750.0n` 三阶段 probe。没有运行 Spectre，也没有创建/替换 cellview。主字段
`Wfg -> w` 两端工程单位等价；PDK callback 同时改变 `ad/as/nrd/nrs/pd/ps`，六项都限于同一实例并
在 OA/`si` 中逐名、逐值镜像。分类器因此增加
`direct_literal_binding_with_derived_callbacks`，但仍拒绝跨实例、结构变化、多个 literal 主候选或
未由同名 OA callback 解释的额外 netlist 字段。完整 233 项 CDF、topology、placement、baseline
canonical signature 和 raw netlist SHA 均恢复。

两次 baseline-only 失败还修复了真实生命周期缺口：silent csh cleanup 改用 Bridge 公开 SSH runner
的 return code；`simInitEnvWithArgs` 的 foreground log 在动态 SKILL 作用域内指向 `/dev/null`，不再让
长期 CIW 在 NFS scratch 留下 `.nfs*`，全局值退出即恢复。第三方 Bridge 未修改。纯本地
`vda binding-reclassify` 允许分类规则升级时重新验证保存的 EDA bytes/manifest/恢复签名，不重复写 OA
或再跑三次 `si`。原 live run 保留执行当时的 `partial/ambiguous` 记录；独立 reclassification 以
`software_inference` 提升主 binding，不改写原 `eda_result`。当前状态是 **flat TSMC N28 primary
OA-CDF to si binding with fully mirrored derived callback effects live verified**。其他字段、PMOS、
其他 PDK、深层 hierarchy 和并发人工 editor 仍未覆盖；合并前完整本地回归为 `895 passed`。详见
[`validation/2026-08-01-existing-schematic-parameter-binding-discovery-live.md`](validation/2026-08-01-existing-schematic-parameter-binding-discovery-live.md)。

同日继续完成 discovery→normal-task Gate。`onboarding-resolve-bindings` 输入 hash-bound
onboarding draft、未手填 mapping 的 resolution，以及一个或多个真实 discovery task/run；它复算
plan、重新校验三阶段 artifact/CDF/恢复，再把 primary、derived callback 和 source hashes 编译进
普通 `simulation.run` 或 `design.tune`。generic OA→`si` parser 运行时新增 callback 逐项等值核对；
旧 binding 缺省字段不进入 canonical JSON，因此旧 plan token 保持。实际 `MNCAS.Wfg` evidence 已
先生成关闭远端开关的 shared-netlist DC/AC TaskSpec，task SHA-256
`62564c46d99ba0a0212384ebbc30e861461ce026a9869a7cbf2bb2bc737a850e`，安全 plan token
`06b9987ea5b404df`；随后 compute-only token `abec578d3742d07a` 真实完成一次 OA readback、一次 `si`
和共享 DC/AC。`Wfg/w` 与六个 callback 全部匹配，netlist SHA 与旧独立 generic DC/AC 完全相同，
12 个共同指标最大相对差 `9.84e-16`。新增 `execution-scope` 确定性重建最小执行权限但仍要求用户确认；
`onboarding-binding-audit` 把 promotion/scope/task/run、stage manifest 和规格复算成 passed artifact。
完整回归为 `904 passed`。详见[本地编译记录](validation/2026-08-01-onboarding-discovered-binding-promotion-local.md)
和[真实执行记录](validation/2026-08-01-onboarding-discovered-binding-promotion-live.md)。

这仍不是 L5B closure。派生 CDF、深层 hierarchy、shared-child per-instance override、并发人工 editor、
mismatch/Monte Carlo 仍是边界；本次 PVT 也只是 winner 的两个声明条件，不是 foundry signoff corner
set。下一项优先工作仍是在用户首次提供的非夹具单模块上运行这条接入链：只读 inspect 后生成 draft，
由用户给出的拓扑/规格和 Agent 的电路分析形成 resolution，先编译并审查最小 DC/AC TaskSpec，再执行
真实 OA→`si` 仿真。只有 DC operating point 合格且任务确实需要时，才加入 tuning、AC 以外 analysis
或可选 PVT；只有真实模块暴露缺口时，才新增多个唯一 child scope、output/OP metric 映射、
per-instance override 或派生 CDF。不再在已知 fixture 上增加随机候选来重复证明控制器。

## 物理实现范围（旧 L5C，v2 独立记录）

在声明的自主程度之外，另行接入 layout、DRC、LVS、PEX 和 post-layout 迭代：

- 参数化或约束驱动的版图生成/修改。
- DRC/LVS 结果结构化解析与定位。
- PEX 后重新评估规格并回到电路或版图参数。
- 区分 block-level 规则、芯片级规则和无法自动修复的上下文。
- 保留每轮 schematic/layout/netlist/report 的可追溯关系。

现有 Bridge、`auCdl -> strmout -> Calibre` 和 Maestro/Spectre 已经触及物理实现所需的执行机制，但“机制可调用”不等于物理设计自主闭环已经成立。PEX 与自动修复仍需对应证据；不把这些工具的存在计为 v2 L5C 模块架构探索能力。

## 历史推进与验收记录（不作为 v2 必经顺序）

```text
反相器 L5A
  -> nominal 反相器驱动比例校准（粗网格与 1.20/1.25/1.30/1.35 细化均 live；1.20 为可覆盖缺省；多 CL/input slew/PVT 鲁棒性可选）
  -> 共源 nominal DC (Gate 2A 已通过)
  -> 显式实例参数面 (可持久化字段 live 双重回读已验证)
  -> 受控拓扑小变更（同一既有 cellview 原位增加 source degeneration）
  -> 各拓扑 AC gain/bandwidth（nominal/退化只读同源 smoke 已通过）
  -> 有限 AC trade-off 与失败/预算/恢复路径（已通过）
  -> 功耗 + transient 线性度 + noise（单点只读 live 已通过）
  -> 多 analysis 质量约束与受预算调优（W/RD/RS 写回、失败门与恢复已通过）
  -> ADE 双路径（prepare + analysis/output add-only patch + background run/resume/raw-input consistency + global CL/VDD sweep + test-scope CL × VDD environmental-corner + delay/skew/energy constraint mapping 已 live；capture 与人工交接待验证）
  -> 共源 L/VDD + 多 analysis + 固定设计有限真实 PVT（已通过）
  -> 可选 PVT-aware 候选调优（testbench bias 已 live；OA 设计变量写回待可选 live）
  -> 差分对 nominal DC + OA 写回/失败/预算/恢复（已 live）
  -> 差分对 AC/CMRR/输入共模范围/transient 线性度（已 live）
  -> 差分对真实尾管/偏置网络 + DC/AC/CMRR/ICMR/transient/noise（已 live；PVT 按任务可选）
  -> 差分对对称源极退化 + 全分析迁移 + RS 写回 + 精确 remove/restore（已 live）
  -> 差分对 active-load/current-mirror exact-template Gate（nominal OA/si/DC/AC/CMRR/ICMR/transient/noise/有限搜索/恢复已 live）
  -> theory-first gm/Id 尺寸估算（本地方程/离散域穷尽/最优性边界已实现；Gate 6 topology-local Spectre 校准与独立 TSMC N28 MOS 表已过）
  -> 通用 MOS 小信号网络（矩阵、stamp/MNA、独立器件表、nominal/源退化共源 held-out 已 live；Gate 7D 三表 signed cxx+cjd/cjs 差分对已通过）
  -> theory-seeded 差分对有限优化与 Spectre 复核（Gate 8 已 live；候选生成通过、逐点预测精度 partial，PVT 可选未跑）
  -> 通用原子 candidate_set + real-EDA OP 局部重线性化（共源首轮、新 anchor W/RD 和差分对 Wn/Wp/Wtail 六点均已 live、写回并完成 exact 审计；共源 RS 需独立探针）
  -> 差分对 PSRR+/PSRR- 三次同网表 AC（nominal、bias/load 只读与三种 L 的 OA 八点搜索已 live；临时 20 dB 门仍未闭合）
  -> 通用 topology-delta 契约（add/remove/reconnect/master/CDF/pin/placement 与 post-save exact recovery 均已在新 cellview live；仍是受控 contract，不是任意 OA editor）
  -> 共源→共栅原位微调（新 cellview 已完成同一 9 点 DC→AC、对应网表 9/9 匹配、checkpoint、exact inverse 和基线身份检查；完整 quality A/B 不再默认，未覆盖 residual 只在可能改变 objective 时补）
  -> OP 导数驱动的通用小信号预筛（既有共源/共栅真实 run 本地重放：增益误差 0.158%/0.646%；未来 gmb+dQi/dVj+cjd/cjs 保存面已实现、本地测试通过、live capture 待做）
  -> 结构化 standalone Spectre 轻量 A/B（共源/共栅级联已完成无 OA/si/Maestro 的 TSMC N28 live preview、完整 manifest 和进程归零；方向与 OA→si 一致，绝对值不作同源复现）
  -> preview shortlist → 普通 OA 同源复核（已知九点域 3/3 live；未见差分对八点域先冻结 top-3、后跑完整真值并以 ρ=1.0 保留 winner；机制已提炼为默认 fast path，不再安排独立应用 Gate）
  -> active-load + 对称源极退化组合拓扑（新 cellview forward/readback/七实例 si/DC/AC/CMRR/noise/transient/ICMR/PSRR/inverse/恢复态 DC 均已 live；质量闭环未过）
  -> 用户拓扑 onboarding + design_context（inspect→零权限草案→用户确认 resolution→普通 safe TaskSpec 的 flat/hierarchy 本地链已通过；首个非 fixture 模块 live Gate 待做）
  -> existing_schematic 通用 OA→si DC/AC testbench/结果契约（本地 + nominal 共栅级联 live Gate 已通过；未增加电路专用 executor）
  -> existing_schematic CDF→si binding 自动发现与 handoff（flat TSMC N28 Wfg→w 与同实例 derived callback 已 live；normal TaskSpec、最小 execution scope、共享 DC/AC 运行期 callback recheck 和事后 evidence audit 均已通过）
  -> 通用 instance-parameter candidate/checkpoint/writeback（本地可行/不可行/预算/中断恢复、单字段 OA-write 与多字段 objective live 已通过）
  -> 理论诊断 + topology/parameter refinement controller（单-delta nominal flat AC、staged DC/AC/transient/noise 与 shared-netlist live；winner-only 两条件 PVT live；三个 independent alternative 的 OA round-trip/checkpoint/winner writeback live；最多七个 alternative 本地通过；一层 primitive-child symbol/OA/si/DC/AC 与 scoped child 参数调优 live 已通过）
  -> L5B 单模块闭环
  -> layout/DRC/LVS/PEX Gate
```

上述链条记录历史实施，不是自主等级的晋级试题。具体真实能力须有对应回读、结果和
风险相关失败/恢复证据；本地能力不强迫跑 Bridge smoke，实机证据也只覆盖实际条件。

反相器可靠性 Gate 1R、共源 nominal/源退化/quality/PVT、显式实例字段，以及差分对 nominal、真实尾管、对称源退化和 PMOS 电流镜有源负载均已有 live 证据。Gate 7A–7D 又把独立 TSMC N28 器件表、通用 MOS/R/C 矩阵和 exact-signature held-out 验证接到共源及三表差分对；Gate 8 证明 theory seed 能进入正常同源 Spectre 搜索，但其逐点预测仍为 partial。通用 `candidate_set` 与 real-EDA OP 重线性化随后把共源局部 27 组合压缩为 6 个原子 tuple，并真实完成 6/6 quality、transport resume、EDA 最佳写回和自动事后预测审计：推荐一致且 GBW 提升 `13.561%`，但 output swing 的 `22.479%` 误差保留为 partial。新 anchor 刷新进一步拒绝没有 RS 留出覆盖的假三维校准，并在固定 RS 后把 W/RD 两维最坏历史留出误差降到 `0.568%`；该新六点现已同源执行，预测与 EDA 同选 `1.1 µm/18.5 kΩ/750 Ω`，60/60 比较通过且最坏新点误差为 `0.354%`。差分对六点也已同源执行，预测与 EDA 同选最小功耗点 `1.215/1.080/0.555 µm`，90/90 比较通过且最坏新点误差为 `2.5693%`；三次 transport 失败均安全恢复并完成最佳 OA 写回。通用 topology-delta 已在共源、“PMOS 电流镜负载 + 对称源极退化”和“共源 + 共栅管”三个新 cellview 完成增量写入、同源仿真和精确 inverse 恢复；新增共栅 Gate 还真实覆盖 pin/placement、post-save recovery、同一 9 点 DC→AC 和基线身份检查。2026-07-27 的只读本地复盘进一步证明，同一通用矩阵仅用已保存 `gm/gds` 就能在 `0.646%` 内解释共栅低频增益；故默认 Gate 改为“PDK 理论 seed → 必要 DC → OP 导数矩阵预筛 → 最小 Spectre residual validation”，不再把完整 A/B sweep 当作第一反应。旧 run 缺少 `gmb/dQi/dVj/cjd/cjs`，所以共栅 BW/GBW/noise/linearity 仍未由该路径闭合；只有当设计 objective 可能因这些残差改变拓扑选择时才值得追加对应仿真。组合 Gate 的 P1dB 仍未包围、PSRR 仍低，因此这里只升级能力面，不升级设计质量。继续共源 RS 必须补独立探针，不能从二维结果外推。公开的低层 stamping/MNA 接口继续允许 Agent 为具体电路增加局部方程而不复制求解器。PSRR 临时 `20 dB` 门、slew/settling、P1dB 包围、输出驱动、可选差分对 PVT、mismatch/Monte Carlo、ADE 真实 PVT/multi-test、人工打开/修改/重跑和旧 ADE L 迁移仍是独立 Gate。跨 PVT 不默认附加；这些完成前仍不能升级为可重复的 L5B 单模块规格闭环。

## 2026-09-03 传输安全 Gate

- 已完成：修改前 Bridge 备份引用与独立分支；RAMIC 默认回环；SSH 本地端显式回环；实际 bind 状态检查；VDA 设计动作前 fail-closed；远端旧 `0.0.0.0:65346` 精确清除；用户授权后把两个 7 月遗留 headless Virtuoso 正常替换为一个自动加载 setup 的会话；真实 `127.0.0.1:65346`、Windows tunnel `1+2=3` 和 VDA doctor 均通过。
- 功能验收暴露并闭合了 Windows OpenSSH half-close 丢回复：Bridge 私有分支在备份 `codex/backup-vda-framing-afd7346` 后提交 `b1194ca`，只对 Windows 远程 tunnel 使用完整 JSON framing；本地/非 Windows 保留旧 half-close，daemon 同时兼容 EOF。最终 Bridge `106 passed`、VDA `910 passed`。
- loopback 后同机用户认证与原生 Cadence listeners 是独立管理员/协议 Gate，不伪装成 L5B 电路能力缺口，也不默认扩展为新网络服务。
- 本项目现在回到 L5B 主线：首个由用户给出大致拓扑的真实单模块 onboarding → 最小 DC/AC → 有界参数/局部 topology refinement → winner 规格复核；不因本次安全修复增加随机电路或重复 sweep。

## 2026-09-04 Bridge 上游 0.8 升级 Gate

- 已从 GitHub 获取最新上游 main `c64461c` 与 `v0.8.0`，并在修改前建立 `codex/backup-pre-upstream-20260904-ebf7e50`。私有两父 merge 为 `106c61e`，managed restart 修正为 `40ff891`，说明 tip 为 `0156579`；未推送第三方 origin。
- 已接入上游 scoped Spectre pools、split-host、Paramiko/SOCKS5、strict PSF、schematic netlist/planner 与 Maestro 修复，同时保留回环、Windows 隐藏/framing、stale recovery、有限退避、pre-send-only recovery、身份/bind guard 和清理行为。
- 合并额外修复了短 timeout 被 recovery 等待超越、纯 split-host 被 legacy host 前置检查挡住、手工 forward 提示缺少显式回环，以及 Windows status/restart 裸 client 的 half-close 假阴性。status 返回码、幂等身份查询和 restart runner 清理均 fail closed。
- 最终 Bridge 私有差异文件集 `122 passed`；再加入 10 项 Spectre runtime/split-role 路径测试后的核心兼容集为 `132 passed`。完整集 `914 passed, 34 failed, 11 skipped`，剩余均是未修改上游文件中的 Windows/Unix fixture 边界，新增的一项 docs cache 断言已单独复现。VDA `910 passed`，catalog/plan 通过。
- 真实只读 Gate 已用 `restart` 把 0.8 runtime 限定部署并加载到 `/data/xum/virtuoso_bridge_xum/Aurora_s_Echo/virtuoso_bridge`，随后得到 daemon/tunnel user `xum`、`127.0.0.1:65346`、SKILL `3`、Virtuoso 6.1.8、Spectre 21.1.0；停止后本地 listener 与匹配 `ssh.exe` 均为 0。没有 OA 访问/写入或 Spectre analysis。
- 这是一道执行底座兼容 Gate，不把 GDS/docs/local-netlist 等未验收上游能力计入 L5B。下一步仍回到用户给定大致拓扑的真实单模块 onboarding、最小 DC/AC、局部参数/拓扑 refinement 与 winner 复核；只有真实模块需要时才为某个上游 0.8 API增加 VDA contract。
- 随后的工作流打磨只采用两个已有痛点能直接受益的 0.8 API：根 DC/OP/noise 单文件改用 strict PSF reader，AC/noise 频率轴要求有限、实数、严格递增；standalone preview 改用一次最多四路的 scoped `run_parallel`，并把一次 guard/一个 simulator/一次 inventory、提交顺序与远端 deck 双绑定写入证据。OA→`si` 没有改走通用 schematic export，因为该接口当前不能等价保留既有 `si.env`、远端环境和 OA/netlist 一致性证据。
- 同一十变体 live preview 的 action 从 `70.566828 s` 降到 `40.450857 s`（减少 `42.677%`），完整 run 从 `71.628403 s` 降到 `42.814626 s`（减少 `40.227%`）；新旧 893 个指标和十个 deck hash 完全一致，每点 manifest 仍为 8 项。该 Gate 无 OA/`si`/Maestro，结束后远端 Spectre/`si`/Maestro 进程和本地 tunnel/worker 均为 0；它提升筛选执行效率，不扩大 preview 的设计真值声明。

## 2026-09-15 TSMC18 profile Gate

- 远端只读 preflight 确认了 `tsmc18` OA 库、`nmos2v_mac/pmos2v_mac`、D/G/S/B terminal order、CDF 的 `w/wf/l/fingers/simM`、Spectre `nch_mac/pch_mac` 以及现有 ADE 使用的主 corner + `stat_noise` 配置。
- OA master 与 Spectre model 已在 profile 层显式解耦；MOS 宽度写入参数也改为 profile 选择，N28 继续使用 Bridge 的 `wf -> Wfg`，TSMC18 使用总宽度 `w` 并由 PDK callback 回填 `wf`。一个 corner 对应多个 include 的通用解析器已覆盖 preview、器件表征、反相器和通用 OA 仿真，N28 默认行为不变。
- live Gate 在唯一新对象 `Analog_VLSI/vda_tsmc18_inv_001/schematic` 上回读到 `Wn/Wp/L=2/4/0.18 µm`；自动 `si` 得到 `nch_mac/pch_mac` 和完全相同的 W/L。加载 `tt + stat_noise` 后，Spectre 21.1.0 nominal transient 返回 786 点非空波形；delay `20.7171 ps`、VOH `1.79350 V`、VOL `17.138 µV`，结果记录含 9 项输入/PSF/log SHA-256 manifest。
- 创建过程暴露并保留两次失败证据：错误工作目录的旧 headless CIW 看不到目标库；切换正确 work area 后结构已保存，但 Bridge 的 N28 型 `Wfg` 写法被 TSMC18 CDF 拒绝。随后用新的 profile 映射通过独立 `parameters.apply` 写回与重开回读恢复。由于 `replace_existing=false`，本轮没有删除对象再重复一次 fresh atomic create；这仍是明确的未验证边界。
- 人工 `cds.lib` 前后 SHA-256 均为 `377ad9f53db1c9ad07683cc8cab027899569b38f55eb8f1286fe45f1867bb6ed`。结束后本地 tunnel、headless Virtuoso、si、Spectre 和一次遗留只读 shell 链均已清理，只保留用户原有可见 Virtuoso 会话；三个运行目录共约 223 kB 留在批准的 TSMC18 scratch 作为复核证据。
- 当前状态升级为 **TSMC18 nominal OA-to-si-to-Spectre execution path verified; design-quality closure pending**。2:1 仍只是初始化值；本次没有规格门、PVT、AC、noise、参数搜索或质量选优。下一步只有在真实 TSMC18 设计需要时，才做 fresh-create 原子性复核或按给定拓扑增加最小 DC/AC/规格 Gate，不为重复证明工作流而盲目扩点。

## 2026-09-16 TSMC18 preliminary DC/AC Gate

- `device.characterize` 和 `netlist_preview` 直接使用 Bridge 的 standalone Spectre SSH role，不再要求 Virtuoso tunnel。split-host 时 guard/deck/计算跟随 Spectre host；timeout、非覆盖 scratch、manifest 和清理边界不变，Bridge 本身未修改。
- `tt/27 °C` 下完成 60 个器件网格点和 4 个留出点。raw OP 是 `eda_result`；归一化和插值审计是 `software_inference`。留出点只有 2/4 通过 35% 门，故不抬高阈值，也不把稀疏线性插值器称为已校准。
- 从实测 `VGS=0.60 V, VDS≈0.90 V` 反推负载，只运行三个共源 AC 条件。`L=0.18 µm, CL=10 fF` 为 `18.109 dB / 640.240 MHz / 5.150 GHz GBW / 61.622 µW`；`CL=100 fF` 带宽降至 `76.276 MHz`；`L=0.36 µm, CL=10 fF` 为 `19.946 dB / 215.793 MHz / 2.144 GHz GBW / 24.507 µW`。三点均饱和且输出约 `0.9 V`。
- 一阶 `gm(RD||ro)` 增益误差约 1.9%/0.9%；只计 10 fF 显式负载时带宽高估约 22%，反推有效输出电容约 12.2 fF。理论适合首值和方向，寄生及最终指标仍服从 PDK AC。
- 结束后远端 `spectre=0`、`si=0`，本地 `65347` 无 listener；没有 OA/`si`/Maestro 访问。当前状态为 **TSMC18 nominal execution plus preliminary DC/AC characterization verified; reusable interpolation and design-quality closure pending**。下一步是在用户给定的 TSMC18 实际拓扑上做最小 OA→`si` DC/AC；不继续扩随机网格。

## 2026-09-18 TSMC18 固定 DC/AC 规格尝试

- agent在仿真前提出练习门限：TT/27°C/1.8 V、CL=50 fF，gain≥20 dB、BW≥100 MHz、
  核心静态功耗≤100 µW、OUT=0.8–1.0 V及饱和/KCL护栏；不是用户真实应用需求。
- 从已测PDK OP和局部KCL确定W/L=8/0.36 µm、VGS=0.58 V、RD=21.2 kΩ；首值真实
  OA→si DC/AC全部合格。为增加薄增益余量，只改RD至23.5 kΩ，同一OA重新netlist得到
  20.8267 dB、125.8336 MHz、68.3224 µW、OUT=0.908013 V，九项原定约束全部通过。
- 外部si改为Bridge已有OA-host SSH runner执行，保留CIW环境初始化及严格exit/log/
  非空网表/实际参数一致性门，新增TERM/10 s kill-after限时及执行证据。Bridge本身未修改。
  完整本地回归929项通过；本轮live只覆盖TSMC18，不扩张为split-host或任意PDK证明。
- 创建后的独立读超时、CIW等待si超时和RD修改时的读超时均保留failed记录；重建本地隐藏
  tunnel并独立确认未写后才重试RD。没有盲目重放写入或把returncode/对象存在当作规格通过。
  本次全部自有后台进程/tunnel清理，原可见Virtuoso及cds.lib保留不变。
- 当前只升级为nominal共源固定DC/AC规格通过。common-source完整原始波形归档仍缺；
  稀疏插值器仍partial，fresh-create整体原子性和间歇transport超时未被包装成完全修复。
  实际设计下一门是在同一可行点按需求补noise/大信号和人工ADE交接；PVT显式可选，
  不默认加随机点、网格全域或新拓扑。详见[设计验证记录](validation/2026-09-18-tsmc18-common-source-spec-attempt.md)。

## 2026-10-18 Bridge 1.0 main 底座升级 Gate

- 修改前建立独立备份分支、Git bundles、源码与完整 venv 备份；在
  `codex/vda-upstream-main-20261018` 合入上游 `ecaf5a5c`，不向第三方 origin 推送私有历史。
- 采用上游 HMAC/签名 owner、IPC resync、Busy/recoverable requests、严格 schematic reader、
  stored/CDF evidence、Maestro job/history locks 与发布资源修复；保留 VDA 的回环检查、
  Windows 无窗口、stale PID、有限退避、发送前恢复和 cleanup。新 token 可放在工作区，
  Windows 完整签名响应不再等 EOF，认证没有关闭。
- VDA 929 项通过；Linux Bridge 2325 通过、89 跳过；Windows 平台既有失败与本次回归分开
  记录，不以 Linux 通过包装全部 Windows 功能已验证。完整结果见
  [升级验收](validation/2026-10-18-bridge-main-upgrade.md)。
- 真实 TSMC18 已有 OA 共源 DC/AC 的网表 hash 与全部 28 个原始指标保持一致；N28 的两点
  standalone AC preview 通过。原有两个 Virtuoso 会话保留，默认 daemon 已在原 PID 上完成
  新协议重载，两个 profile 的 doctor 通过；没有 OA 写入或人工 ADE state 覆盖。
- 这是兼容与安全底座 Gate，不另造一组随机电路测试。后续回到用户实际给定的模块/拓扑，
  按现有 onboarding→理论初值→最小 DC/AC→局部 refinement→真实 winner 复核工作流推进。
  shared-CIW receipts、丰富参数 evidence 或 Maestro job API 仅在具体任务需要时接入现有
  operation 并补契约/恢复测试；ADE L 仍保持可人工调整，PVT 仍是按需可选。

## 2026-10-18 Bridge 新功能与工作流 Gate

- 已将 stored/CDF 参数证据以显式选项接入 `schematic.inspect`，不扩大权限或改变旧 token；
  默认 compact/search 热路径保留。VDA 完整回归 937 项通过。
- 真实验证一次丢回执、一次等待预算耗尽，均仅查询原 receipt，原请求计数为一。Windows
  direct/standalone framing 已补齐，但更广本地 TCP 的三项 reset 仍是已记录的未闭合边界。
- 新 TSMC18 测试 cell 的 8→9→8 µm 可逆探针，三次 OA→si 确认主参数及六个派生字段，
  恢复态网表与基线 SHA 相同；没有重复 DC/AC 或新增性能结论。
- 同一 client 热读图约 0.13 s、完整 evidence 约 0.19 s；这是一次工具耗时，不是 agent
  思考或端到端提速声明。日常采用会话复用、首次按需取全、后续差异摘要和最小仿真；
  不为重复证明工作流再造拓扑测试。验收、失败与清理见
  [验证记录](validation/2026-10-18-bridge-feature-workflow.md)。
