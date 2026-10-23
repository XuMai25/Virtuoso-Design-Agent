# Virtuoso 基本操作沉淀：验证范围与来源

日期：2026-09-21。本文记录基本工具用法的验证来源，不定义单管实验为后续任务模板，也不把实验的参数、扫描数量或比较门限升级为默认规则。

本次整理只读取已有产物及项目实现，更新通用操作文档和发现入口；没有启动远端计算、修改 OA 或改变 Bridge。未新增单管审计 CLI，也未新增通用 ADE L adapter。历史带日期任务和 `tsmc18_*` 脚本仍是实验记录，不能直接批量重放。

## 1. 哪些经验进入通用工作流

| 操作 | 本次可确认的经验 | 适用与限制 |
|---|---|---|
| 原理图 | 真实 master、参数 callback、带 pins/nets 的结构回读、网表核对、可见图交接 | 方法可跨电路；PDK 字段与连接必须每次读取，不能复用本次器件名和尺寸 |
| Maestro | 独立 ADE owner cell/test 指向 design；保存后独立读回；运行结果绑定 history/输入/日志 | 正式 `ade.*` 的已有契约保持不变；本次实验不证明任意 multi-test/PVT/人工双向编辑均闭合 |
| ADE L | 真实 SEV/ASI 会话、state 保存、新会话加载、参数文件另存与加载、重跑 | IC6.1.8-64b.500.29，真实 DC 已验证；其他版本/分析与旧 state 迁移需各自证据 |
| Calculator | 正确结果集、分析轴、OP/OS、family 切片、输出 Plot/Save 与原始坐标检查 | 本次 `deriv` 的端点行为只是版本/数据观察，不固化为所有场景规则 |
| Print | 原生菜单入口、完整参数表达式显示、实际偏置核对、表格分页截图 | 菜单入口已观察；最终单点表经原生表达式显示，纯鼠标全链路自动化未验证 |
| ViVA | 原生图像导出、展开图例、单位与坐标核对 | 虚拟图窗口和普通窗口截图路径不同；须检查实际输出图像 |
| 人工交接 | setup/state 与结果分别保存、独立重开、实际数据检查、图片与报告索引 | 配置存在、结果存在、可重开分别确认，不能互相替代 |

通用入口为 [Virtuoso 基本操作](../virtuoso-basic-operations.md) 和 [ADE L 操作](../ade-l-native-workflow.md)。原有 [人工 ADE Gate](../deferred-manual-gates.md) 的旧 state 迁移和更广双向编辑范围继续保留。

## 2. 历史实验身份，仅用于追溯

以下旧远端路径已在 2026-09-22 精简维护中退役；当前入口见第 6 节。ZIP 内历史记录保持原样。

- 模型/配置：`nics4304_tsmc18`、`tt + stat_noise`、27°C。它不是项目默认 PDK 的变更。
- 原理图来源：`Analog_VLSI/vda_t18_nmos_tb_20260920_003/schematic`。
- ADE L 远端根：`/data/xum/virtuoso_bridge_smoke/tsmc18/adel_20260921_001`。
- 最终 state：`vda_t18_nmos_ivgs_20260921_002`、`vda_t18_nmos_ivds_20260921_003`；根目录 `states`，各自另有 `ivgs_param.il`/`ivds_param.il`。
- 状态递增用于修正 Plot/Save 标志和表达式，旧 state 保留；最终绑定见 `evidence/final_state_binding.json`，不能只按原任务中的初始名字加载。
- 本地统一目录：`H:\学习资料\模拟大规模集成电路\作业2026\作业1`；[完整实验归档](<H:/学习资料/模拟大规模集成电路/作业2026/作业1/归档/试运行原始资料.zip>) 中的 `Desktop/TSMC180nm_IV_gm_ro_20260920_001/实验结果报告.md` 包含 NMOS/PMOS 的 Maestro 阶段扫描记录。
- [综合作业](<H:/学习资料/模拟大规模集成电路/作业2026/作业1/作业1.md>) 与其 PDF 位于统一目录根部；[常用补充资料](<H:/学习资料/模拟大规模集成电路/作业2026/作业1/补充资料/使用说明.md>) 中保留关键数据；原作业 11 行选定参数 Print 记录对应 10 个独立偏置点，完整 NMOS 简报在 ZIP 的 `Desktop/TSMC180nm_NMOS_Homework_20260921_001/` 中。
- [ADE L 重开与 Print 说明](<H:/学习资料/模拟大规模集成电路/作业2026/作业1/补充资料/使用说明.md>) 位于统一目录 `补充资料`；完整 ADE L 说明和证据位于 ZIP 的 `Desktop/TSMC180nm_ADE_L_20260921_001/`。`final_validation.json` 记录 11 条曲线、每条 1001 个电流点以及初跑/重跑 22 份完成日志；这是历史运行的验证记录，本次没有重跑。
- 后补的原生完整参数表为 1 个偏置点、121 行，不能与作业的摘要点数混为一谈。原完整范围、后续用户收窄的作业范围、进一步补充的 Print 形式分别记录，不自动继承为下次任务需求。

原桌面三份完整资料以及 VDA 本次专用源码、任务、步骤和调试文件已原样收入 [试运行原始资料.zip](<H:/学习资料/模拟大规模集成电路/作业2026/作业1/归档/试运行原始资料.zip>)。归档内部 `Desktop/` 保留旧文件夹名，`VDA/` 保留项目相对路径；迁移清单及整理记录也已收入 ZIP；`Maintenance/20260921_simplify/restore_layout.json` 逐文件映射维护前目录到归档成员，并保留 SHA-256。旧桌面位置不再作为当前入口。VDA 继续保留通用指南与正式工艺/仿真能力，远端 OA/ADE/PSF 路径未移动。

## 3. 需要保留的失败边界

1. 早期沿扫描 OP 曲线不能替代明确要求的直接 Print；之后确实补充了原生 Print。
2. ADE L 的辅助 include 选项、输出 Plot/Save、独立参数扫描文件需要实际回读；只保存表达式或 state 不足以证明可重跑。
3. 多会话上下文曾导致 Print 选到另一组结果。应显式选择结果并校验表中实际偏置。
4. 完整 Print 的 `printInfoDpl` 元数据为空时，真实表格仍有行；数据在实际表格 widget 中。不能用单个空属性判断失败。
5. 内部选择回调曾超时；最终使用原生完整参数表达式显示。不能声明菜单加鼠标自动化已通用打通。
6. 部分截图因滚动/缩放而缺字段或重复，已从最终索引排除。最终图由 `print_final_qa.json` 指定，不能按截图数量声称覆盖更多点。
7. 一次实验里的 session/window/对话框号以及 `_apa*` 回调属于版本和会话相关实现线索，不成为稳定 VDA 公共 API。

## 4. 本轮本地证据核验

以下历史核验文件现位于 ZIP 中 `Desktop/TSMC180nm_ADE_L_20260921_001/` 下，按表内相对路径读取；最终常用截图另展开在 `补充资料/screenshots`。本轮重新计算 SHA-256 并与该包已有 `SHA256SUMS.txt` 对照，全部一致。这只证明归档文件完整；原始 GUI 检查结论来自历史记录，不冒充本轮重新操作或逐图目测。

| 文件 | SHA-256 |
|---|---|
| `evidence/final_validation.json` | `b40ca927112309f88c35810b7991400f9df5aec983def4fcb0dd11c673a776ef` |
| `evidence/final_state_binding.json` | `15fa02ec0baba1a134464bd9f0da22d13d277b35f36e74d6c9f09c0533e74155` |
| `evidence/31_reopen_ivgs_il.json` | `dfe3ad381f8abcc73667e14da56366e90a6ec8c37aed3fbc0833055ebd8006b9` |
| `evidence/35_reopen_ivds_il.json` | `edccbe5e7fa694b950b43bfee807d90d460f16fa59f75705ea9c4487f55dd935` |
| `evidence/print_full_parameter_validation.json` | `961caaffc9259e9afca4d62c9cc2bb176d07ee55a9ec73e3ec066a6340cf48bf` |
| `evidence/print_final_qa.json` | `1c7dada23cd3d04bc37845eb38d2d78b0190cc1bb998c45a5455b0bdc6235cd2` |
| `screenshots/print_M0_gm_verified.png` | `58d2f069f178a2df12e73dbb6aa3a85d3694de1ad0a83116947841112dbf1ebf` |
| `screenshots/print_M0_ro_bias_final.png` | `0ba54664e27ca70135ff0c644f5087e8e43fa3ac44dae3e4872dc92b679f6ed6` |
| `reproduction/adel_runtime_bundle.tar.gz` | `e254c22d6842fc7e2383fd6753280433be3844dc5d094c240700ab020b8c2483` |

本轮检查 6 份新增/修改文档的 144 个本地链接，无缺失；`git diff --check` 通过。此次为工作流文档与项目规则更新，没有运行时代码变更，未运行仿真或代码回归。

## 5. 本地资料精简维护

统一作业目录仅展开主作业/配图、必要的 ADE L 与 Print 补充资料、一份 README 和校验清单，共 40 个文件（含一个 ZIP）。原归档成员全部保留；旧展开目录的重复内容按 SHA-256 复用归档成员，新增修改和整理记录进入 `Maintenance/20260921_simplify/`。同目录的 `restore_layout.json` 可以恢复维护前的每个文件；主作业 13 个文件未改写。此维护不改变通用 VDA 能力、远端原理图、ADE 设置或仿真结果。


## 6. 2026-09-22 远端合并与实际重跑

用户要求同时保留 ADE L 和 Maestro、合并重复版本、避免全量留档。前述“远端未改变”仅描述当时本地整理阶段。当前远端根为 `/data/xum/coursework/analog_vlsi_2026/homework1`，由 `nics4304-cad1` 进入；没有新增完整归档，也未修改 Bridge 公共 API。

- `Analog_VLSI/vda_t18_nmos_tb_20260920_003` 与 `Analog_VLSI/vda_t18_pmos_tb_20260920_001` 各保留 `schematic`、`maestro`；本实验从 11 个 cell / 14 个 view 合并为 2 个 cell / 4 个 view。每个 Maestro 包含 `IVGS`、`IVDS`、`OP` 三个 test。
- NMOS ADE L 保留目录式 state `IVGS`、`IVDS`，根为上述目录下 `ade_l/states_final`，另有 `ade_l/ivgs_param.il`、`ade_l/ivds_param.il`。现有 PMOS 使用 Maestro；本轮未新增 PMOS ADE L。
- NMOS Maestro 保留 `Interactive.3`，PMOS 保留 `Interactive.0`。结果在 `maestro/Analog_VLSI/<cell>/maestro/results/maestro/<history>`，OA 内对应 history 索引一并保留。
- 实际重跑 ADE L NMOS 11 条、Maestro NMOS 11 条、Maestro PMOS 11 条曲线，共 33,033 点。对原始 CSV 的最大绝对差：电流 `8.968622014753763e-16 A`，`gm`、`gds` 均为 0。数值记录为远端 `validation/verification.json`。
- 原理图实例、尺寸、pins/nets 和实际网表已核对；新 Virtuoso 进程独立加载两套 ADE L state/参数文件与两个 Maestro history，并重新显示原生完整 Print。56 个最终 ADE L state 文件在清理前后哈希一致。
- 删除 9 个重复 cell、两个旧试运行根目录、失效迁移 state 和多余 history；保留当前网表、PSF、日志、原理图和可运行设置。原用户无关 Virtuoso 进程未关闭。本次临时导出的 33 个比较 CSV 在逐一验证哈希及保留 PSF 后删除。

远端 `README.md` 和 `reopen.il` 为当前使用入口；本地 [补充资料使用说明](<H:/学习资料/模拟大规模集成电路/作业2026/作业1/补充资料/使用说明.md>) 已同步，Print 脚本改为当前 PSF 路径。主作业及配图共 13 个文件未改动，本地作业目录仍为 40 个文件。

证据分层：原理图及配置回读为 `bridge_readback`，实际网表/PSF/日志为 `eda_result`，数值比较与哈希检查为 `software_inference`。这验证了本次已有实验的受控整理，不等于新增通用旧 state 自动迁移能力，也不证明其他 PDK 或分析均已验证。
