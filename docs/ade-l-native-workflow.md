# ADE L 原生配置、保存与重开

这是可用于不同电路的 ADE L 基本操作说明。常规单测试优先采用用户熟悉的 ADE L；明确需要既有 Maestro setup 或更复杂结果组织时再选择相应环境。通用原理图、Print 和截图规则见 [Virtuoso 基本操作](virtuoso-basic-operations.md)。

本次调用在 IC6.1.8-64b.500.29 上验证，真实分析覆盖 DC。以下模板中的 library/cell、路径、变量、分析和表达式必须来自当前任务；不能据此宣称 AC、tran、其他版本或旧 state 自动迁移均已验证。历史证据集中在[验证记录](validation/2026-09-21-virtuoso-basic-operations.md)。

## 1. 会话身份

`sevStartSession` 返回 SEV session，`sevEnvironment` 返回 ASI session，`sev->window` 才是相关窗口。`sevGetSessionType` 应返回 `adel`。ViVA 标题中的 XL 不用于识别 ADE 类型。

```lisp
; libraryName/designCell 为当前任务已确认的字符串变量
sev = sevStartSession(?lib libraryName ?cell designCell ?view "schematic")
asi = sevEnvironment(sev)
list(sevGetSessionType(sev) sev->window)
```

这些是原生 SKILL 操作示意，通过已有 Bridge 公开执行入口使用，仍受任务写入/计算授权约束。不要硬编码历史 `sevSessionNN` 或 `window(NN)`；不要依赖另一会话的全局“当前对象”。

## 2. 配置与回读

| 内容 | 本次验证的原生入口 | 回读重点 |
|---|---|---|
| 模型与 section | `asiSetEnvOptionVal(asi 'modelFiles ...)` | `asiGetEnvOptionVal(asi 'modelFiles)` 的路径和 section 顺序 |
| 温度/仿真选项 | `asiSetSimOptionVal` | `asiGetSimOptionVal`，只设置当前任务所需项 |
| 变量 | `asiSetDesignVarList` | `asiGetDesignVarList`；整表设置前先合并未修改变量，避免漏掉已有项 |
| 分析 | 当前 ASI analysis 的 `asiSetAnalysisFieldVal` | 分析启用状态、轴变量、范围、步进及实际网表 |
| 输出 | `sevAddExpression` | `sevGetExpressions`，并逐项查看输出的 Plot/Save 标志 |
| 输出目录 | `asiSetProjectDir`、`asiSetResultsDir` | 指向本任务新的 scratch；保存 state 后加载时再次核对 |

`sevAddExpression` 在本次环境中默认未启用 Plot/Save，必须按需求设置，并保存后独立重开核对。已验证的是所需曲线输出；曾存在标量表达式保存但结果栏空白的情况，不能宣称所有 output 类型均已验证。

本次 ADE L 不接受直接沿用的 `includeFiles` 环境选项。辅助 Spectre 保存指令通过 `modelFiles` 中 `(filePath "")` 的无 section 项加载后成功，实际 input 中应再次核实 include 生效。保存哪种内部器件参数由本次电路和分析决定，不固定某个实例或参数列表。

## 3. 保存 state 与参数扫描

### 2026-10-19：窗口收拢的新默认

需要保留的配置优先保存到同一设计的 **ADE L state cellview**，之后通过 Load 接续，而不是
为保留不同配置长期打开多个 ADE L 窗口。使用新的 state view 名，不覆盖 schematic、已有
人工 state 或 Maestro view。保存前后及独立 Load 后核对同一 configuration fingerprint；
模型/变量/analysis/outputs 与 Plot/Save、project/results 路径均属于待核对配置。
只有保存和独立 Load 均确认、外层扫描文件及依赖也可恢复，且任务已确认完成时才关闭原窗口。

2026-10-19 已复用既有 TSMC18 IVGS 配置完成 **cellview 保存→关闭源临时会话→独立
Load→加载外层扫描文件→关闭验证窗口**，未运行电路。入口先按本机 IC618 官方文档核实，
再用原生 API 实测；八类配置完整比较一致，原有 56 个文件哈希不变，窗口清单恢复原样。
首次弹窗尝试受到用户手动点击 Save State OK 的影响，未算作无人值守成功；之后另用全新
view 完成不弹 Save State 窗口的自动保存及独立 Load。详见[本次验收](validation/2026-10-19-ade-l-cellview-save-load-close.md)。

```lisp
; asi 来自本任务已核实的 ADE L 会话；stateView 必须明确不存在
when(ddGetObj(libraryName designCell stateView) error("Refuse existing state view"))
asiSaveState(asi ?name stateView ?option 'cellview
             ?lib libraryName ?cell designCell)
; reopenedAsi 来自关闭源会话后独立启动的验证会话
asiLoadState(reopenedAsi ?name stateView ?option 'cellview
             ?lib libraryName ?cell designCell)
```

以上是 API 片段，不是省略授权、路径绑定、指纹比较和失败检查的可直接批量执行脚本。
验证过程只保留所需临时会话，结束后关闭本任务窗口，不终止原有 Virtuoso 或用户任务。
目录备份可额外保留，但不能用它冒充 cellview 交接。

窗口复用与关闭前检查入口见[设计复盘与窗口管理](design-review-workflow.md)。

真实交互式 SEV/ASI 会话中保存 state；OCEAN 脚本本身不等于已经保存可手动加载的 ADE state。

```lisp
; stateName 必须是新的名字；stateRoot 是当前任务目录
asiSaveState(asi ?name stateName ?option 'dir ?stateDir stateRoot)
```

目录式 state 还包含 library/cell/simulator 层级。交接记录应提供根目录及完整身份，不只给一个叶目录或 state 名。

保存结果路径时，不能只依赖 `asiSetProjectDir`：本次迁移中还需在保存前用 `envSetVal("asimenv.startup" "projectDir" 'string expectedPath)` 同步启动环境，再独立加载确认 state 内路径。无 section 的模型项 `(filePath "")` 可能规范化为 `(filePath)`；回读比较应按实际语义处理，不能把这种规范化判成模型丢失。

本次还确认：**裸 `asiLoadState` 不会自动恢复源 `projectDir`**，新会话可能继承其他任务的
全局路径。先读取可信源 state 的 `ADE_state.info` 与来源会话路径，不执行来源不明的脚本；
在 `unwindProtect` 中暂时绑定 `asimenv.startup/projectDir`，建立本任务会话并 Load，随后
立即恢复原全局值，再比较 project/results/PSF/netlist 四条路径。保存时同样临时绑定并恢复。
当前任务不运行仿真，因此保留旧路径用于一致性验收；以后要重跑，另经明确配置步骤指定新
scratch 并另存 state，不能直接写入旧结果目录。

有外层扫描时，通过 **Tools → Parametric Analysis** 配置，并在其 File 菜单另存参数文件。本次 state 不包含该配置，所以不能只保存 state。扫描值既可能是等间隔范围，也可能包含额外 Inclusion 值；加载后核对实际完整列表，不能把界面的计数字段直接解释为步长。

OCEAN `paramAnalysis()` 可以描述脚本扫描，但本次不会自动填好原生 Parametric Analysis 窗口。需要人工可打开、调整、运行时，必须验证原生窗口中的设置。

参数文件保存/加载曾通过观察到的 `_apaSaveParamFileCB`、`_apaLoadParamFileCB` 及原生对话框完成；Start All 曾使用窗口实际回调 `apaStartCB(... ?all t)`。这些属于版本相关 GUI 路径，不封装成稳定 VDA API。优先使用实际菜单；自动操作前核对对应控件和当前会话。

文件对话框可以由 `hiFileDialogSetSelection`/`hiFileDialogDone` 操作，但须先找到本次实际 handle。`hiGetCurrentForm()` 曾返回无关旧表单，不能据此给错窗口输入路径。

不要在同步 Bridge 请求中等待 Save/Load 模态窗口完成；本次这样调用曾导致超时与 IPC 忙。
state 本身优先用上述原生保存/加载 API；必须打开文件窗口时，将打开动作通过 `hiRegTimer`
提交后返回，再对现场识别的本任务 handle 单独操作。文件选择返回成功不足以确认扫描恢复：
本次 Parametric Analysis 的 `analysis` 字段为 nil，但原生参数表确实显示已加载的变量、范围、
Auto 设置和 Inclusion 列表，故以实际窗口截图与原参数文件核对作为证据；不能猜测内部字段
或为“验证加载”调用 Start All。

## 4. 独立加载与重跑

```lisp
; 新建独立会话，不在原会话中“读到刚写过的值”便算重开
reopened = sevStartSession(?lib libraryName ?cell designCell ?view "schematic")
reopenedAsi = sevEnvironment(reopened)
asiLoadState(reopenedAsi ?name stateName ?option 'dir ?stateDir stateRoot
             ?lib libraryName ?cell designCell ?simulator "spectre")
list(sevGetSessionType(reopened) sevGetExpressions(reopened)
     asiGetDesignVarList(reopenedAsi)
     asiGetEnvOptionVal(reopenedAsi 'modelFiles)
     asiGetSimOptionVal(reopenedAsi 'temp))
```

再核对 analyses、每项输出 Plot/Save、project/results 路径；有参数扫描时在 Parametric Analysis 加载已保存文件并回读。

- ADE L 主窗口 **Run** 执行当前固定变量下的分析。
- Parametric Analysis 的 **Start All** 执行外层参数组合。
- 启动后用实际 simulator input、PSF 和日志验证分析、变量和范围，不只看运行按钮或退出码。
- 重跑用新的结果目录，保留旧结果作为比较来源。

配置修复后另存新 state 名，并在最终交接记录中指明哪个是有效版本；保留失败版本，不静默覆盖，也不改写此前已批准任务的历史身份。

## 5. Print 与交付边界

人工 Print 入口：**Results → Print → DC Operating Points**，选择器件。多会话时明确绑定确切结果，再核对表中实际偏置。完整原生参数表可由 `OP("/instance" "??")` 配合 `awvPrintWaveform` 展示；应如实记录展示方式。

完整参数表、选定参数摘要、沿扫描 OP 曲线是不同形式，按用户要求交付。原生 Print 成功不等于菜单加鼠标选择的自动化已经可靠；本次后者仍未完整验证。

人工交接包括：原理图入口、state 根目录/身份、必要的参数扫描文件、模型/保存指令依赖、结果目录/日志/输入、输出表达式及重画方法。通过真实重开与读取结果验证可用性。

正式 `ade.*` operations 仍只覆盖现有 Bridge Maestro 接口。ADE L 采用已验证的原生步骤和任务级受控脚本；未新增 Bridge API。已验证的是一个既有目录式 state 非覆盖另存为 ADE L state cellview，不是任意旧 state 的通用迁移器，也不是 ADE L→Maestro 迁移；后者仍需原 state 备份、新目标和独立 live 验证。
