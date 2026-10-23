# Virtuoso 基本操作与人工交接

本文件沉淀 2026-09-20～21 实操中可跨电路复用的工具用法：原理图、ADE L、Maestro、Calculator、Print、ViVA 和结果交接。扫描数量、器件尺寸、特定偏置、单管计算公式和报告版式不作为 VDA 默认能力。历史实验只用于限定验证范围，见[操作验证记录](validation/2026-09-21-virtuoso-basic-operations.md)。

## 1. 选择入口与确认操作对象

| 需求 | 使用方式 | 注意事项 |
|---|---|---|
| 日常单测试，用户手动修改和重跑 | 优先 ADE L | 用户更熟悉；保存 state，有参数扫描时另存参数文件 |
| 已有 Maestro setup，或需要集中组织 tests/corners/history | 沿用 Maestro 对应 Explorer/Assembler | 复用正式 `ade.*` operations，不因自动化方便静默迁移 |
| 只读原理图、单次改参数、单次仿真 | 现有正交 operation | 不强制附加表征、调优或完整报告 |
| 直接查看某器件工作点 | 原生 Results → Print | 绑定实际结果和器件路径；曲线或 CSV 不能代替要求的窗口 |
| 用户要求 Virtuoso 原图 | ViVA 原生导出、原理图/ADE/Print 窗口截图 | 导出图像和保存可重开状态是两件事 |

Maestro 是保存 ADE setup 的 view 类型；Explorer/Assembler 是相关使用环境。ViVA 标题中的 XL 不足以证明使用了 ADE XL。界面选择不决定仿真精度；应核对实际模型、网表、分析、选项和偏置。

开始操作时绑定 `library/cell/view`、design、PDK profile、工具版本、当前会话与输出目录。通过 Bridge 的公开连接、执行、读图、文件传输和截图接口操作，不在 VDA 复制底层实现。正式接口未覆盖的原生操作仍按任务脚本管理，不能把一次成功包装成新增公共 API。

远端执行沿用现有任务授权、`--execute`、计划 token、library 白名单、cell 前缀和写入/计算权限。不把历史实验的授权、会话号、DISPLAY 或 scratch 路径当成本次默认值。默认不覆盖已有 cellview、state 或人工修改。超时后先读回实际状态，不盲目重放写入。

## 2. 原理图：画面、连接与仿真输入分别确认

按参考图片新建时使用[图片复现工作流](schematic-image-reproduction.md)：先审阅电气/图面/疑点
描述，再编译显式导线。不要用全端子短线标签替代用户要求的可追踪连接；本地 SVG 不是原生截图。

1. 从当前 PDK 读取实际 master、symbol 端子和参数，不用另一工艺的端子顺序或 CDF 字段猜测。OA master 名与 simulator model 名可以不同。
2. 使用 Bridge 原理图接口放置实例、连线或网络标签。需要供人操作的原理图应清楚显示信号方向、电源、地、反馈和关键节点；复杂连接优先用可追踪的导线，标签要附着于真实网络。
3. 参数更新只改声明字段，允许 PDK callback 生效；随后读取最终值。输入字段值、回调后的 OA 值、实际网表参数需分别核对。`w`、`Wfg`、指宽、总宽、倍数不能凭名称互换。
4. 执行检查并保存。通过 `client.schematic.read(..., include_positions=True, param_filters=None)` 等现有公开接口回读实例/master、参数、每个端子的 net、顶层 pins 和位置。截图证明可见性，不能代替电气连接检查。
5. 从该 cellview 生成实际 simulator input，核对实例/内部层次、端子顺序、模型、变量替换和 include sections。符号看起来正确不代表网表正确；同名标签是否连接以 OA 与网表共同确认。

对已有图做局部修改时保留未点名对象和参数；保存后独立重开，确认最终图可继续编辑。参数或回读失败时保留实际修改状态与失败原因，不直接从头建图覆盖。

## 3. ADE 配置：写入、保存、运行是不同阶段

配置顺序通常为：design/simulator → models/sections → 温度和必要选项 → design variables → analyses → outputs 与 Plot/Save → 结果目录 → 保存并回读。只设置任务所需项目，不照搬历史实验的收敛精度或额外分析。

- 外层参数扫描与分析自身的横轴分开声明。例如频率/时间/DC 变量是分析轴，另一个设计变量可生成曲线族；先写明每条曲线固定什么，再配置外层扫描。
- 指定线性步进后回读实际 analysis；本次曾出现期望线性步进但保存状态仍是 `Automatic`。界面输入成功不证明设置生效。
- 输出表达式存在，不代表 Plot/Save 已启用，也不代表求值成功。回读标志并查看真实结果；标量输出为空不能由曲线存在推定成功。
- 请求 operating-point 参数时在仿真前安排保存，随后从结果确认实际提供的字段、单位和 primitive 路径。顶层 PDK wrapper 不一定是参数所属器件。
- 模型文件、辅助保存指令文件、环境选项的支持情况以当前 ADE 版本为准；不能把 Maestro 的选项名直接搬到 ADE L。

### Maestro

优先使用现有 `ade.prepare/capture/variables.apply/setup.apply/corners.apply/run`。`prepare` 只负责新 Maestro view/test 及其设计绑定，不等于完成分析配置；setup/变量修改遵守旧状态匹配与独立重开回读，`run` 只消费已保存 setup。

交接对象包括 ADE 所属 `library/cell/maestro`、test 指向的 design、global/test/corner 有效变量、analysis、outputs、corners、返回的 exact history 和实际结果路径。ADE 所属 cell 可以与 design cell 不同，均需明确。

多个分析用途可以组织为同一 Maestro view 的多个 test，不必为每个用途复制原理图或 ADE owner cell。局部参数列表还需确认 test 变量已启用；本次通过 `maeSetEnableTestVar` 后才得到所需扫描点。Job control 模式与分发方式分别回读：`ICRP` 与分发 `Local` 是不同设置，不能把 `Local` 当作 job control 模式。最终是否生效仍以实际运行点和输入为准。

已有人工 session 或未保存修改不得被后台 patch 静默覆盖。结果必须绑定实际 input/result/log；单独的 `Interactive.N` 名称不能证明结果唯一或变量已进入网表。正式边界见 [AGENTS.md](../AGENTS.md) 的 ADE 人工交接约束。

### ADE L

详细入口见 [ADE L 原生操作](ade-l-native-workflow.md)。基本区别是：保存 ADE state 后，有外层 Parametric Analysis 时还要保存其参数文件；独立新会话加载两者并回读，才验证可重跑。ADE 主窗口 Run 与 Parametric Analysis 的 Start All 作用不同。

当前正式 `ade.*` 自动 operation 仍是 Maestro。ADE L 的已验证原生用法可以通过现有 Bridge 传输按任务执行，不因这一边界强迫用户换界面，也不声称已实现通用 ADE L adapter 或旧 state 自动迁移。

## 4. Calculator 与结果来源

先选定结果，再写表达式；多个 ADE 会话打开时不要依赖“当前结果大概正确”。记录 analysis/result 类型、实际 PSF 路径、test/corner/扫描点及器件路径，核对读到的实际偏置。

| 原生表达式形式 | 意义与检查 |
|---|---|
| `OP("/instance" "parameter")` | DC operating point 中的器件参数；参数及结果是否存在须实际确认 |
| `OS("/instance" "parameter")` | 已保存的 DC 扫描 operating-point 波形；不能用单次 OP 冒充整段扫描 |
| `deriv(wave)` | 对波形当前横轴求导；不是自动对外层参数求偏导 |
| `value(wave x)` | 在波形横轴位置读取/插值得值；需说明是否落在原始采样点 |
| `famValue(family parameterValue)` | 取一个外层参数切片；多维 family 需逐层确认 |

单个仿真点的 OP 与外层多个运行点组成的 OP family 要区分。不要只凭表达式名判断数据维度；读取原始 x/y、family 参数和值。

求导、积分和倒数等计算须检查单位、坐标、端点、缺失值与数值分辨率。不同函数/版本的差分方法可能不同，本次实际观察到 `deriv` 少一个端点；该观察不应变成所有 Cadence 版本的固定假设。零参考值不强算相对误差，接近零的分母不得隐去不可靠性。

电源端口电流有方向，不能为使曲线好看随意取绝对值。模型内部参数与整个电路端口测量也可能定义不同，例如串联元件、反馈和内部节点会改变端口响应。先核对物理量和边界条件，再解释误差；不能用更细步长或修改门限掩盖定义差异。

## 5. Print：绑定器件、结果与实际偏置

用户要求“直接 Print”时保留原生 Print 窗口。仅有 operating-point 曲线、Python 表格或 CSV 不满足这个要求。若需要单点完整参数表，也不能用只选几项参数的曲线族摘要表冒充。

1. 选择目标 run/history 及确切工作点 PSF，确认对应 design 与器件内部路径；读到的实际偏置比 ADE 当前变量栏更能说明 Print 的对象。
2. ADE L 的人工入口为 **Results → Print → DC Operating Points**，随后选择原理图器件；其他环境以实际菜单为准。
3. 查看新输出是否追加进旧 Print 窗口。只清理本任务拥有的内容/窗口，不能关闭用户无关窗口来消除歧义。
4. 核对表头、器件身份、参数、实际偏置和结果路径。需要时分多页截图，确保关键字段确实在画面中。
5. 记录是菜单选择，还是原生表达式展示。例如当前环境下 `awvPrintWaveform(OP("/instance" "??") ?numSigDigits 9)` 可展示完整参数；这是原生 Print 内容，但不证明鼠标菜单全链路自动化已稳定。

`openResults(exactPSF)` 影响表达式的结果上下文；ADE session 自身的结果选择也需对应。此次多会话操作曾串到另一组结果，应在 Print 后核对实际偏置，不能只相信菜单来自哪个 session。具体加载函数的签名要查当前版本文档，不照搬固定 session 字符串。

Print 有多种显示结构。某个 `printInfoDpl` 列表为空，不能推断窗口没数据；本次完整表的行在真实窗口 `form->body->choices` 中。检查实际 widget 和图像，避免根据一种元数据错误判断失败。直接调用未经验证的内部选择回调曾超时，不作为通用接口复用。

如果工具没有某参数，注明缺失；另行计算的值标为计算结果，不能改名冒充原生打印字段。无关扩展字段显示 NaN 时保留原表，按所需字段的有效性判断。

## 6. ViVA 原图与窗口截图

- 波形图优先用 ViVA 原生导出 `saveGraphImage`；原理图、ADE 设置、Print 用实际窗口截图。图像导出后必须检查实际文件和画面。
- ViVA 虚拟窗口不一定具有可供通用截图/缩放使用的普通窗口句柄。`hiGetSessionWindow` 返回 nil 或 Bridge 通用截图失败，不等于波形不存在；查实际窗口类型，选择已有的原生图像导出入口。
- 明确选择目标图窗口后导出，图例应展开到可辨认每条曲线的参数值。必要时用真实 family 切片及明确标签绘图；不能重新生成一套数值来冒充原图。
- 显示单位元数据缺失时可按已确认的物理量补标签，但必须与数值尺度一致。不要只改文字而忽略数值。
- Print 表格分页、焦点和窗口尺寸会影响可见内容。点击或 PageDown 后检查画面确实变化；窗口缩放可能重置滚动位置，最终截图必须再次查看。
- PNG 单独保留。要求原生图片时不以外部绘图代替；失败截图归入调试记录，不纳入最终截图索引。

## 7. 保存、重开和交付

交接按任务需要保留以下信息，不要求每次只读操作都生成整份报告：

| 对象 | 重开所需信息 | 成功判据 |
|---|---|---|
| 原理图 | library/cell/view、PDK/库依赖 | 独立打开，实例、连接、参数正确且可编辑 |
| Maestro | ADE view、design、tests、保存 setup、指定 history | 独立重开，配置匹配，能够选择真实结果 |
| ADE L | state 根目录/name/lib/cell/simulator；必要的参数文件 | 新会话加载并回读，有扫描时加载参数文件并运行正确范围 |
| 结果/波形 | simulator input、PSF、log、表达式/绘图设置、确切结果路径 | 数据可读取，坐标/数量正确，能重新显示目标图 |
| Print | 结果路径、器件路径、实际偏置、原生表与截图 | 与同一运行一致，必要字段可读 |

写入后回读证明的是配置；解析仿真数据证明运行输出；独立重开证明人工可用性。三者不能互相代替。导出 PNG 不代表波形窗口已可重建，后台仿真完成也不代表可操作的 ADE 窗口已交付。

要求报告时使用相对图片/数据链接，保留输入、结果、日志和 SHA-256 清单；正文区分原始仿真值与计算值。逐张看图、检查链接和数据一致性。结果未完成、数值不一致或交接失败要明确保留，不能为获得“完成”而删减用户已明确的范围。

## 8. 故障处理与通用化边界

| 现象 | 处理 |
|---|---|
| 启动弹出许可提示，但进程仍在 | 查看实际提示和可用按钮，按正常继续流程处理，再验证会话/仿真；不读取或输出 license 文件 |
| `sevStartSession` 返回值被当成窗口 | 区分 SEV session、ASI session 和窗口句柄，见 ADE L 操作说明 |
| 参数列表保存了，重新打开扫描却丢失 | 核对 ADE state 与 Parametric Analysis 文件是否都保存、加载 |
| 输出存在但不出图/不保存 | 回读 Plot/Save、表达式求值结果与所选结果集 |
| 表格显示了但脚本报告“空” | 查真实 table widget 与截图，不能只看单个 metadata 字段 |
| 图像存在却缺少关键字段 | 重新核对焦点、滚动位置、图例和缩放后的画面 |
| 写入/运行超时 | 检查实际 OA/结果/日志状态，再决定恢复；未经核实不重放非幂等动作 |

优先使用公开接口和已观察到的真实菜单；必要时查本机 SKILL 文档、`getd`/`arglist` 和控件信息，再小步执行并回读。内部 `_apa*` 回调、表格 widget 字段和动态窗口号依赖版本/会话，不能作为稳定公共 API 宣传。

历史 `tsmc18_*` 实验脚本已收入作业归档，位置见[验证记录](validation/2026-09-21-virtuoso-basic-operations.md)；其中含固定路径、设备名、窗口号和失败探索。以后做放大器、反相器或其他电路时复用本文件中的操作步骤与现有 VDA operations，不批量重放旧脚本，也不继承该实验的扫点、公式、门限或交付数量。
