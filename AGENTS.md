# Virtuoso Design Agent 协作约束

## 产品边界

本项目是位于 `virtuoso-bridge-lite` 之上的设计与验证编排层，不复制 Bridge 的 SSH、SKILL、Spectre 或文件传输实现。Bridge 负责可靠执行；本项目负责任务契约、规划、受控写入、有限搜索、结果判定和证据记录。

能力分级采用 `docs/l5-roadmap.md` 的 v2.1：L5B 的目标是“给定大致拓扑、可适配不同电路的模块设计细化”，L5C 指模块架构探索，L6 指多模块协同；版图/PEX 是独立范围，不以调用工具数量或测试数量升级。当前已有受控执行/有限优化和部分设计能力，完整 L5B 仍未闭合。分级、适配范围、验证质量、可靠性/人工介入和实际成本分别说明；实施顺序按真实价值安排。PPT、论文和开源项目都不是强制功能清单。只在已接入 PDK、明确拓扑/参数契约和显式搜索边界内执行；分级不扩大权限。不得把一次命令成功、OA 对象存在或仿真退出码为 0 单独表述成“设计完成”。

## PDK 默认策略

- 普通晶体管级设计默认使用晶圆厂 CMOS PDK；当前默认 profile 是 `nics4304_tsmc28`，对应 TSMC N28/`tsmcN28`。
- 后续 TSMC、SMIC 等工艺以独立 profile 显式接入和验证。切换晶圆厂、节点、版本、model section 或服务器时不得沿用旧性能证据。
- TSV、hybrid-bonding 等封装/3D PDK 不得成为默认或自动 fallback；只有任务显式选择并具备专门模板与验证 Gate 时才使用。

## 远端安全

- 默认只生成计划；真实执行必须同时提供 `--execute` 和计划 token。
- OA 写入还必须在任务文件中显式设置 `allow_remote_write: true`，并通过 library 白名单和 cell 前缀检查。
- 仿真会写远端 scratch；必须显式设置 `allow_remote_compute: true`。
- 默认不得覆盖已有 cellview。`replace_existing` 保持 `false`；需要覆盖时必须在任务中显式声明。
- 不读取、打印、提交 SSH 密码、API key、license 内容或 `.env` 全文。
- 远端 EDA 运行遵守 nics4304 规则：通过 `nics4304-cad1` 进入 cad 执行上下文，产物位于 `/data/xum`，不写 `/home/xum`。

## 证据规则

- 区分 `eda_result`、`bridge_readback`、`software_inference` 和 `user_input`。
- demo adapter 只能证明编排与搜索逻辑，不得作为电路性能证据。
- 原理图创建后必须结构化回读实例、网络和 pins。
- 参数写入后必须回读；仿真必须解析波形或标量指标，不能只看 return code。
- 失败和未验证边界保留在 run record 中，不得静默降级。

## 实现原则

- Python 代码放在 `src/virtuoso_design_agent/`；测试放在 `tests/`。
- 任务能力保持正交：建图、读图、应用参数、ADE 准备/人工捕获/变量或 setup 微调/后台运行、仿真、调优、完整闭环均可单独调用。
- 复用已有反相器、共源/源极退化、差分对和受控 existing_schematic 路径；不把某个模板的验收推广到任意拓扑。新增 PDK、器件映射或分析仍须明确验证范围，不写空壳执行器。
- 不引入 Web UI、数据库、多智能体框架或云端 LLM 依赖，除非真实工作流证明有必要。
- 升级前先核对已有实现和记录，区分实现缺口、特定实机证据缺口和观测缺口；优先复用 onboarding、理论/OP、design-review/next/iterate、原 executor 和 Bridge。没有真实需求可以停止开发，不为分级新增电路、operation 或重复 Gate。
- 新改动先说明具体阻塞、复用位置、收益/成本和停止条件；测试只覆盖改动风险，优先历史记录和本地回归。需要真实 EDA 才能回答的问题另列最小授权范围；不按最低测试数、固定拓扑数或全分析重跑判断升级。

## 默认快速筛选工作流

- 对 4 个及以上的显式有限候选，默认先用一次 standalone Spectre preview 批处理，真值前冻结 top-3，再把 shortlist 编译为一个普通 checkpointed OA→`si` 任务；不逐候选启动 Bridge。
- 1–3 个候选、人工指定的单次参数修改、单项建图/读图/仿真应直接走原正交 operation，不强迫使用 preview。
- 完整 OA 候选域不是日常必经步骤，只在筛选实现或 PDK/模型条件改变、shortlist 失效/近边界，或计划性周期审计时运行。不得为了重复证明工作流而立即换一个拓扑再跑全域。
- preview 只做排序和明显工作区证伪；最终规格、winner、写回与质量复核只服从 OA→`si`/ADE 的真实结果。PVT、noise、linearity 等昂贵分析默认只对真实 winner 且按任务需要运行。
- 执行前先用最近同路径 action time 检查 `Tpreview < (N-k)*Toa_per_candidate`；无法证明节省时直接走 OA。详细步骤见 `docs/fast-preview-shortlist-workflow.md`。

## 温度与电源验证取点

- 只有温度、电源范围要求而未指定采样密度时，默认温度取最低、标称、最高，电源取最低、标称、最高，交叉组合为 3×3 共 9 点。标称值按当前 PDK 和任务确定，不把 27°C、1.8 V 固化为所有工艺的默认值；单变量验证优先取标称值和两个边界。
- 这 9 点以外的中间点可不取或少取；仅在接近指标、工作区异常、怀疑内部存在更差条件，或需要解释趋势时，有依据地少量补点。不默认铺密温度×电源网格，也不自动逐级加密；任务明确要求完整扫描时按要求执行。
- 报告列明实际验证点及最差采样结果，区分边界／有限点检查与连续范围的严格保证，不用扩大采样数量代替物理分析。

## ADE 人工交接

- VDA 的自动规划、搜索和选优不得取消人工打开、调整、运行和保存 ADE setup/history 的能力。
- 自动路径与人工 ADE 路径必须通过显式 operation、目标 view 和状态/结果指纹交接；不得静默覆盖人工改动或把两个仿真状态混成同一真源。
- `ade.prepare` 只能创建明确不存在的新 Maestro view；已有 view 一律拒绝，后续修改必须使用带前置指纹和逐项回读的显式 patch。
- `ade.run` 只能消费已保存的 Maestro setup：使用后台 session，不改变 GUI 焦点、不保存 setup、不写 OA；默认还必须把本次返回的 exact-history 核心 simulator input/result/log 绑定到大小与 SHA-256 清单。缺少结构化 output 或完整清单时不得仅凭 history/回调成功宣称仿真有效；显式放宽要求也只能记为 partial。精确路径绑定不得包装成 history 名称唯一或变量已按预期进入网表。
- `ade.variables.apply` 只能在声明的 tests、可选 enabled corners 和逐 scope 变量旧值都匹配时保存；已有 Maestro session 时拒绝，保存后必须独立重开逐 scope 回读。不得把声明 scope 的写回包装成未声明 override 已排除或仿真生效。
- `ade.setup.apply` 只能对声明 analysis 做旧状态 CAS，并新增明确不存在的命名 output/spec；已有 output、并发 Maestro session 或任一旧状态不匹配时拒绝。全部目标写后立即回读，只保存一次，再独立重开复核；不得把配置持久化表述成 analysis 已运行或 output 已产生结果。
- ADE setup 属于 `bridge_readback`，实际 Spectre 输入/结果属于 `eda_result`；捕获成功不等于规格闭环。
- 用户熟悉 ADE L：常规单测试原理图仿真优先使用 ADE L。确需 Maestro/Explorer/Assembler 时说明具体原因，不静默更换界面。ViVA 的窗口标题含 XL 不代表 ADE 会话是 ADE XL。
- ADE L 原生会话、保存、独立重开和 DC 重跑已通过 TSMC18 NMOS 实例验证，见 `docs/ade-l-native-workflow.md`。ADE state 与 Parametric Analysis 参数文件须分别保存、回读和绑定；仿真输入、PSF、日志及数据另存证据。不得据此声称所有 PDK、分析或旧 state 迁移均已验证。
- 正式 `ade.*` operations 仍仅覆盖 Bridge 已公开的 Maestro 接口；本次 ADE L 使用现有公开 Bridge 传输执行受控任务脚本，不扩展 Bridge 公共 API。旧 ADE L state 的自动迁移在完成备份、非覆盖迁移和 live smoke 前仍不得宣称打通。

## Virtuoso 基本工具用法

- 按图片复现采用 `docs/schematic-image-reproduction.md` 的 electrical/drawing/uncertainties 契约；agent 解图，代码确定性编译。真实 PDK 端子几何必须读回，疑点/缺参未解决不得生成执行任务。主要 signal net 使用连续导线；首版不同网络交叉必须显式绕开或审阅为 cross_region 标签，不静默全标签降级。新 `schematic_drawing` 仅支持非覆盖 create/inspect；部分失败保留、先回读，不自动重放。N28 图片锁存器经显式补完已通过结构/导线与 OA→si 验收，见 `docs/validation/2026-10-19-n28-image-latch-native.md`；原生图面人工确认、修复后的 fresh-create 单次全链及性能仍分别列为未验证。

- 窗口生命周期默认“先盘点、优先复用、完成后收拢”：新建 ADE/原理图/ViVA/Print/参数窗口前，核对已有窗口的 library/cell/view、PDK、会话归属与当前用途；同一任务可复用时不重复启动。
- 需要保留的 ADE L 配置优先另存为同一设计的非覆盖 state cellview；保存后核对模型、变量、analysis、outputs/Plot/Save、结果路径和指纹，并验证独立 Load。Parametric Analysis 文件与依赖单独保存/加载，不能假定 state 包含它们。保存/回读/Load 失败时保持窗口，不自动 discard。
- IC618 的 ADE L state cellview 保存→独立 Load→关闭已在既有 TSMC18 IVGS 配置上验收（`docs/validation/2026-10-19-ade-l-cellview-save-load-close.md`）。使用现场确认的 `asiSaveState`/`asiLoadState`、`?option 'cellview`，不依赖保存弹窗；保存/Load 前绑定源 projectDir，短暂全局设置必须恢复。该范围不代表所有旧 state 或 ADE L→Maestro 迁移已验证；其他配置仍须逐项回读。
- 对仍在计算、用户明确保留、归属不明或任务状态不明的窗口不关闭；先询问该任务是否完成。任务已确认完成后，只关闭已妥善保存或明确不需保留的本任务窗口，不终止用户 Virtuoso 会话。无用“参数窗口”可关闭，不代表允许删除设计变量/CDF 参数。
- `vda windows-plan` 可从已观察的清单给出复用/保存/询问/关闭前复核建议；它不操作 UI、不证明保存已经发生。实际关闭前必须再次核对现场身份和未保存状态，不能照搬历史窗口号。

- 原理图、ADE、Calculator、Print、ViVA 和人工交接按 `docs/virtuoso-basic-operations.md` 的通用操作经验执行；ADE L 保存/重开细节见 `docs/ade-l-native-workflow.md`。这些是操作指南，不增加新的正式 operation。
- 沉淀跨电路工具用法，不把某次单管实验的器件、尺寸、扫描点、计算公式、误差门限或报告数量固化成默认任务。历史 `tsmc18_*` 实验脚本已随作业资料归档（位置见 `docs/validation/2026-09-21-virtuoso-basic-operations.md`），仅作追溯，不直接批量重放其固定路径和窗口号。
- 原理图写入核对真实 master、CDF callback 后参数、pins/nets 和实际网表；参数名、端子顺序及内部模型路径不跨 PDK 猜测。
- ADE 配置保存、实际仿真结果、独立重开可用性分别验证。表达式存在不等于 Plot/Save 开启或求值成功；外层参数扫描与分析横轴分别确认。
- Print 绑定确切结果、器件和实际偏置；原生参数表、参数摘要、OP 曲线分别注明。用户要求 Print 时不能只交 OP 曲线；多会话不得依赖未核实的“当前结果”。
- 用户要求 Virtuoso 原图时，采用原生图导出或实际窗口截图，检查图例、单位、偏置与关键字段可读。内部 GUI 回调和临时句柄须现场识别，失败及未验证边界保留。

## 验证入口

完整本地软件验收统一使用双环境入口；主环境全量与已安装 Bridge 的本地适配器补验
必须一起通过。缺环境、未知 skip 或 Bridge 补验仍 skip 时不算完整通过；不据此安装
第二份 Bridge 或启动真实 smoke。tests/ 保持离线，真实 EDA 验收须在明确授权范围内单独做。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent.local_validation
```

单环境 pytest 仍可用于针对性开发，但不能单独替代上述完整验收。输出/配置见
`docs/local-validation.md`。以下原诊断与计划入口保留：

```powershell
python -m pytest
python -m virtuoso_design_agent catalog
python -m virtuoso_design_agent plan examples/tasks/inverter-close-loop.demo.json
```

真实 Bridge smoke 必须由用户或当前任务明确授权后执行。
