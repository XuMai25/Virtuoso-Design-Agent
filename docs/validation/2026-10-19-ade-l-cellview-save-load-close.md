# 2026-10-19 ADE L cellview 保存、独立 Load 与窗口收拢

## 结论与范围

通过：复用既有 TSMC18 IVGS 配置，自动另存到全新 ADE L state cellview，关闭来源临时会话，
在独立 ADE L 会话 Load，核对配置，另外加载原 Parametric Analysis 文件，最后关闭本任务
窗口。未运行 si、Spectre、ADE Run 或 Start All，不产生新的电路性能证据。

用户要求复用已有配置、不覆盖或丢失原设置；随后确认曾手动点击 Save State 的 OK。
因此首次保存/模态恢复不计入无人值守证据。之后另用全新目标、直接保存 API 完成独立对照，
没有打开 Save State 弹窗，也不依赖用户点击 OK。

证据类别：配置、文件、会话和窗口实测为 `bridge_readback`；哈希/配置比较为
`software_inference`；用户手动按钮说明为 `user_input`。本轮无新增 `eda_result`。

## 身份及非覆盖目标

- 入口 `nics4304-cad1`，OA 主机 cad52，用户 xum，Virtuoso PID 94710，IC6.1.8。
- 工作区 `/data/xum/Analog_VLSI/icfb`，设计 `Analog_VLSI/vda_t18_nmos_tb_20260920_003/schematic`。
- 原 state：`/data/xum/coursework/analog_vlsi_2026/homework1/ade_l/states_final/Analog_VLSI/vda_t18_nmos_tb_20260920_003/spectre/IVGS`。
- 原外层扫描文件：`/data/xum/coursework/analog_vlsi_2026/homework1/ade_l/ivgs_param.il`。
- 首次尝试新 view：`vda_adel_IVGS_20261019`，保留用于追溯，但不以其证明全自动保存。
- **自动路径验收 view：`vda_adel_IVGS_auto_20261019`**，同一 library/cell，保存前检查不存在。
- 两个新 state 均未覆盖 schematic、原 IVGS/IVDS 目录 state 或其他现存 view。
- 远端任务产物仅位于 `/data/xum/virtuoso_bridge_smoke/ade_l_cellview_20261019`；未修改 Bridge/Vault、代理/TUN或防火墙。

## 实际验收

1. 盘点原窗口和活跃会话。原有 ADE L 27–30 属于另一个设计，全部保留，不将其当作可复用的本任务会话。
2. 读取本机 IC618 `maeSKILLref/chap7.html` 的 `asiSaveState`/`asiLoadState`：确认 `?option 'cellview`、`?name`、`?lib`、`?cell`；不猜测参数。
3. 从原目录 state 恢复临时来源会话，采集完整配置；发现默认 Load 会继承全局 projectDir 后，用来源已保存路径建立正确的任务会话，再比较并保存。
4. 使用直接 `asiSaveState` 保存第二个全新 view。执行前配置比较一致；返回 t，任务 receipt 为 completed，保存后配置与全局路径仍一致。
5. 关闭来源临时会话，在新会话 34 独立 Load 自动保存的 view。原生 `equal` 对完整配置返回 t，八组分别为 t：design、paths、models、variables、analyses、simulatorOptions、outputs、subckts。
6. 独立打开本任务 Parametric Analysis，现场识别 Load 文件窗口，加载原 `ivgs_param.il`。截图显示加载路径及 VD：From/To 0→1、Auto、Step Value 2、Inclusion `0.1 0.2 0.5`、Sweep 勾选；未运行扫描，不将 Auto 下的显示字段擅自解释为固定步长。
7. 再次确认核心配置未漂移，关闭本任务 Parametric Analysis 及 ADE L 会话34。`sevEnvironment` 确认该临时 ASI 不存在；窗口清单与起始完全相同：24 项（含 CIW，即其余23个原窗口），原4个活跃 ADE L 会话身份不变。
8. 停止本任务创建的本地65347 tunnel并确认无监听；远端daemon仍仅监听 `127.0.0.1:65346`。原Virtuoso进程94710/126060和既有Spectre进程11005/25503保留。

八组配置包含所有分析的启用状态、fields/options，而不只启用的DC；路径包含project、results、PSF、netlist；输出包含表达式和Plot/Save等属性。本例VG=0.8、VD=0.5，DC扫描VG，5个输出Save开启、其中3个Plot开启。它们只是本配置的验收值，不是以后电路的默认值。

## 文件完整性

- 原目录 IVGS/IVDS state、两个扫描文件及 schematic，共 **56个原文件SHA-256全部不变**。
- 自动保存的state与源state的23个组件逐一对应：20个字节完全相同。
- spList/sprobeList只差首行保存时间注释，实际内容均为nil。
- convergence是tar容器；其中sigList只有保存时间及三个恢复项顺序不同，initConds、forceNodes、nodeSets的值均为nil。不能将它误判为字节相同；本例内容逐项检查后语义一致。
- `ADE_state.info`包含新描述，source designInfo/projectDir保持；cellview使用`.state`及`master.tag`是原生存储形式，不假定目录文件名直接相同。
- 原model、save_op.scs及参数文件存在并记录哈希；既有PSF的dc.dc仍存在。本轮未重新求值或验证性能。

## 发现的问题与可复用修正

- **模态弹窗**：同步调用`sevLoadState`打开Loading State并等待会阻塞Bridge请求，曾发生30秒超时/IPC忙。首次恢复期间有人工操作，保留失败记录，不声称自动恢复。常规保存/Load改用官方原生API；必须用文件对话框时先timer异步打开，再分请求核对handle并选择文件，不执行未经核实的“当前窗口”操作。
- **projectDir**：裸`asiLoadState`不能证明路径已恢复。以可信源state/会话为依据，保存前及新会话创建前暂时绑定startup projectDir，并用`unwindProtect`恢复原全局值。验收只保留原路径，不在其中重跑；日后仿真另设新scratch并显式保存。
- **参数扫描分离**：state中的paramSetup为空，不能将保存state等同扫描文件已保存。本次原参数文件独立Load；内部`analysis`字段为nil但真实参数表已恢复，故使用原生窗口截图核对，不靠内部字段猜测，更不运行仿真来触发恢复。
- **窗口归属**：只关闭本任务临时窗口；其他任务即使数量多也不批量清理。没有丢弃人工改动或终止整个Virtuoso。

## 证据与边界

任务脚本/JSON/原生截图保存在本地`artifacts/ade-l-cellview-20261019/`（运行产物，不作为源码提交）：

- `automatic-save.json`：request `01a150415a3b6c3d045e541e6ec7e567`；直接保存与配置未变。
- `automatic-independent-load.json`：request `01a15042c64ddd3ab78a920c64646710`；八组配置一致。
- `snapshot-after-manual-confirm.json`与`snapshot_source.il`：比较基线及字段定义。
- `param-loaded-window.png`：原生Parametric Analysis回读截图，非重绘图。
- `save-document-location.json`、`final-file-audit.json`：56个原文件前后哈希和新state组件比较。
- `session-inventory.json`、`final-session-inventory.json`、`final-window-check.json`：原窗口清单一致，临时会话已关闭，全局路径恢复。
- `final-process-check.json`：原进程和远端loopback监听保留。

本次只验证该既有配置及IC618环境，不是任意旧state迁移器，不证明AC/tran等全部分析均可无损迁移，也不关闭ADE L→Maestro迁移Gate。正式`ade.*` API仍为Bridge已有Maestro接口；ADE L流程是任务级受控脚本，没有新建公共API或新框架。
