# 2026-10-23 N28 单环 STB：既有 smoke 的 OA→si→Spectre 原生对照

> 后续代码状态：留存/原生对照已接入正式 STB 路径，完成本地集成与本页原始文件的离线
> 重放，见[接入验收](2026-10-23-stb-evidence-integration-local.md)。以下保留当时真实作业
> 的执行范围；后续离线工作没有新增远端作业，也不替代修改后正式 worker 的 live 验收。

## 结果与授权范围

**已复用原 smoke，完成一次真实 DC＋STB 作业和同作业原生 margin 对照；没有新建
cell、写 OA、调参或追加仿真。** 这是指定工作点的单环测量验收，不是完整 L5B
设计规格闭环、任意环路稳定性证明或 ADE GUI 验收。

用户在前次只读诊断后要求按原 5X2Y2Z PDK 继续：原 smoke 可用则复用，不可用才新建。
本次将口语中的“5x5y5z”明确解释为前面讨论的 **5X2Y2Z**，没有选择不存在或未经确认的
新工艺。原目标可只读打开，因此没有动用新建目标的权限。

| 项目 | 实际范围 |
| --- | --- |
| library/cell/view | `vb_pdk_smoke/vda_diffpair_active_gate6_001/schematic` |
| profile / 实际 PDK | `nics4304_tsmc28` / `/data/technique/tsmcn28_lo_0v9_1p10m_5X2Y2Z/tsmcN28` |
| 模型入口 / section | 同版本 `models/spectre/toplevel.scs` / `top_tt` |
| 目标 OA 写入、替换、新建 | 均为 0；只读结构及参数回读 |
| netlisting | 1 次 OA→`si`，未重放 |
| 实际 Spectre 引擎作业 | 1 次，包含 DC operating point＋STB |
| 启动尝试 | 2 次；首次在 Spectre 启动前返回 127，见下文 |
| 当前用户会话 | 不修改其库映射、PDK、OA、窗口或 ADE 状态 |

没有修改 Bridge 源码、系统文件、PDK 文件、已有 `cds.lib` 或 Obsidian Vault。
仅新增本任务 scratch、持久化证据及本地验收辅助脚本和文档。

## 隔离接入，而非改动当前会话

当前用户会话的同名 `tsmcN28` 指向 RF/7X2R，且没有 `vb_pdk_smoke`；原因见
[只读库上下文诊断](2026-10-23-stb-library-context-diagnosis.md)。因此本次创建短时隔离
Virtuoso 进程，私有 `cds.lib` 只 INCLUDE 原 smoke 库表，不覆盖任何已有库表。

- 本任务根：`/data/xum/virtuoso_bridge_smoke/stb_acceptance_20261023_01`。
- 远端 Bridge 仅监听 `127.0.0.1:65426`，独立本地转发为 `127.0.0.1:65427`。
  最初候选端口 65416 已占用，没有触碰其所有者，改用只读确认空闲的端口。
- 使用 Bridge 既有 SSH、传输、认证、managed tunnel、SKILL client 和 Spectre runner；
  没有复制基础设施，没有更改共享凭据或打印凭据内容。
- 任务身份、路径、权限与阶段计划 token 在脚本中绑定；先回读、后 netlist，再运行。
  独占提交标记阻止同一阶段盲目重复提交。
- 隔离会话启动初期两次只读连接重置发生在 daemon 就绪前；就绪后重新只读核对，
  没有重放写入，也不将这称为 Windows TCP reset 遗留问题已修复。

## 当前 OA 基线及同源性

隔离会话独立回读到 5 个 MOS、8 个顶层 pins，masters 为该 PDK 的真实器件。
当前尺寸与 7 月历史 selected point 不同；本次以**新鲜 OA 回读**为基线，不恢复旧尺寸，
不把历史 winner 的性能移植给当前目标。

| 实例 | 端子→net（D / G / S / B） | 当前尺寸 |
| --- | --- | --- |
| MN0 | OUTP / INP / TAIL / VSS | Wfg=1.165 µm，L=30 nm |
| MN1 | OUTN / INN / TAIL / VSS | Wfg=1.165 µm，L=30 nm |
| MP0 | OUTP / OUTP / VDD / VDD | Wfg=1.03 µm，L=30 nm |
| MP1 | OUTN / OUTP / VDD / VDD | Wfg=1.03 µm，L=30 nm |
| MNTAIL | TAIL / BIAS / VSS / VSS | Wfg=0.555 µm，L=30 nm |

五者 fingers=1、simM=1；pins 为 INP、INN、OUTP、OUTN、TAIL、VDD、VSS、BIAS。
调用既有 `_generate_oa_netlist`，从实际 `si` 网表解析器件参数，并以既有差分对
semantic/readback 一致性检查逐项核对。不是由共享参数另外手写一份 DUT。

执行前后结构化 OA digest 均为：
`e0bdbd8692d2a7491c4c10edf22a847e2d23bbf8f771e43bd091d8c5aae1d774`。

以下文件在本轮检查窗口内及清理后 SHA-256 不变：

| 文件 | SHA-256 |
| --- | --- |
| 目标 `schematic/sch.oa`（36644 bytes） | `f92b6a94e4661c355fda6da45a04cefb525256a6a5ddefff333ce5ca997a8129` |
| 当前工作目录 `/data/xum/Analog_VLSI/icfb/cds.lib` | `3402adc0189c6f012b8bd2c7567bc857683bcd063c1bf97699cd08c26bafbe43` |
| 原 smoke `cds.lib` | `674391325f18e0c46a6fcc9b9c9759a6a8ebce2a8300fd94f0adcb792e8f0a18` |
| 5X2Y2Z `models/spectre/toplevel.scs` | `bd8088e1ea620dcda229d1fd16d5d63a08b594a28e78d24c4db2906cf45a71d1` |

另对先前保存的四个用户原理图哈希核对，均未变化。上述模型入口哈希不是全部 PDK
文件的哈希证明；本任务没有对 PDK 执行写操作。既有锁文件未删除、未强制解锁。

## testbench 与测量定义

沿用 typed generic wrapper，仅在 testbench 连接顶层 pins，不重连 OA 内部器件：

- VDD=0.9 V、VSS=0 V、BIAS=0.32 V、INP=0.55 V；温度 27°C。
- OUTN 经 `VLOOP (OUTN INN) vsource dc=0` 接回 INN，构成单端单位负反馈。
- OUTN 对地负载 0.5 fF。
- DC operating point 与 `dcOpInfo`；`stb` 为 1 Hz–1 THz、40 points/dec、probe=VLOOP。
- `loop_scope=single_loop`、`open_loop_rhp_poles=0`，原始 `loopGain` 使用
  `feedback_transfer` 约定，取负号后归一到 `1+T`。

反馈选择和无开环 RHP 极点属于 agent 的 `software_inference` 假设，不冒充用户逐项
提供或程序已独立证明。配置用于验收测量通路，不是新制定的放大器优化规格。

## 同一作业：原生 margin 与 VDA 对照

实际 Spectre 为 `21.1.0.612.isr15`，原始 `stb.stb` 非空，DC 数值有限。
VDA 按既有符号归一、相位展开和 log-frequency 交点插值提取；原生标量取自同目录的
`stb.margin.stb`，不是另一轮仿真或另一份 ADE 结果。

| 量 | Spectre 原生 | VDA 提取 | 差异 |
| --- | --- | --- | --- |
| 相位裕量 PM | 85.82459515° | 85.83217331° | 0.00757816° |
| 环路单位增益频率 | 8.097762669 GHz | 8.097037259 GHz | 相对 0.00895816% |
| 增益裕量 GM | `nan`，无有限值 | 保留缺失并给出诊断 | 未伪造无穷值 |

原生原因是 `No gain margin. Phase does not reach 0 Deg`，原生 state 同时报告稳定。
这里的原生相位约定与归一后 `T` 的 -180° 交点对应；不能将无有限 GM 误写为 GM=∞，
也不能凭这句 state 声明任意多环或大信号稳定。若规格强制要求有限 GM，此次不能补齐。

DC 交叉检查：OUTN=0.56438741 V、TAIL=0.29266988 V、MNTAIL Id=11.4948541 µA；
供电电压源电流为 -11.4949937 µA（原生方向约定）。没有据此宣布全部工作区或性能规格通过。

原生日志为 **0 errors、3 warnings、3 notices**。三条告警均为 `SFE-1131`：
PDK embedded model 中 `scalefactor` 在同一 scope 重复指定，Spectre 使用最后指定值。
保留原日志；没有修改 PDK 或宣称告警无条件无影响。Bridge 返回的 warnings 列表有
5 行字符串，其中还包括告警段标题和最终汇总，**不是 5 条原生告警**。

这一次 Bridge 作业记录的 total 为 53.59 s，其中远端执行约 45.86 s；不包含隔离启动、
netlisting、agent 分析、失败处理和文档时间，不能当作端到端耗时或普遍性能承诺。

## 执行异常与恢复边界

1. `si` 已成功后，验收辅助脚本调用模型配置 helper 漏传 `operating_condition`。
   本地补为 `None` 后，核对唯一已存在的 run 目录和 native log，直接消费已生成网表；
   **没有再次执行 si**。原失败单独保留。
2. 第一次 Spectre launcher 因任务私有环境遗漏 Cadence 初始化入口而返回 127。
   核对该目录没有 `spectre.out`、raw 为空且 launcher 已结束，确认尚未进入 Spectre。
   仅在本任务私有配置声明现有 `/data/cshrc/.cshrc.cadence`；不修改该服务器文件。
   随后完成第一次、也是唯一一次实际引擎作业。两个 launcher 目录均保留。
3. 最终只读收尾脚本初次使用了远端 Python 3.6 不支持的 `capture_output` 关键字。
   仅调整该本地辅助脚本的兼容调用后重做只读检查，没有 EDA 重提或远端变更。

## 留存、解析与清理

本地证据根：`artifacts/stb-live-20261023/`（未纳入 Git；含原生日志，不整份提交或展示）。
重点索引：

- `04-isolated-inspect.json`：新鲜 OA 与实际 PDK；`05-netlist.json`：网表及参数一致性。
- `06-spectre-result.json`、`06-launcher-reconciliation.json`：引擎前失败及核对；
  `06-spectre-result-recovered.json`：唯一实际作业。
- `07-oa-after-engine.json`：作业后独立结构回读；`08-extracted-stability.json`：向量提取。
- `09-native-comparison.json`：原生对照、DC、大小/SHA 清单、远端作业计数和完整性。
- `10-cleanup.json`：首次关闭回执；`11-final-readonly-audit.json`：最终独立收尾核对。
- `retained-job/`：实际 `si.env`、si log、OA 网表、typed wrapper；其 `first-engine/`
  下保留 Spectre log、`dcOp.dc`、`dcOpInfo.info`、`stb.stb`、`stb.margin.stb` 等。

实际远端网表目录：
`/data/xum/virtuoso_bridge_smoke/stb_acceptance_20261023_01/vda_stb_acceptance_20261023_9e17fb9189fd`；
唯一实际 Spectre 作业为其子目录 `5b78260f`。

Bridge PSF reader 可正确读出此次有限的 native PM/frequency，但对 native margin 的
`nan` GM 和带空格字符串不能无损解析。验收脚本因此仅对该文件中明确的 `nan` 和
reason/state 元数据作窄范围读取，保留原文件哈希；没有复制通用 PSF parser 或修改 Bridge。

本次辅助执行明确 `keep_remote_files=True` 并保留独立本地工作目录。**不能据此声称
所有正式 STB CLI 路径都已自动持久留存 native margin 或自动完成这项对照**；现有正式
worker 的临时目录/清理策略仍需后续小范围接入，优先复用此次记录做本地回归，不再跑电路。
本次也没有验证 ADE L/Explorer GUI 的 STB state 保存、独立 Load 和重跑。

关闭前核对临时会话身份、工作目录及未保存 cellview 数量为 0；原生 exit 回执超时后，
只对本任务已核对 UID/SID 的 helpers 发送 TERM，没有批量杀同名进程或使用 KILL。
首次回执中两进程仍在退出，后续独立检查确认任务 SID 无活动进程，远端 65426 和
本地 65427 无监听。当前用户 PID 48417 及回环 65346、原本地 tunnel 65347 保留。
没有删除设计、scratch 或证据文件。

## 本地测试、证据分类及下一步

本轮执行 `tests/test_stability.py`：**25 passed**；未声称重跑完整测试集。
没有为这次验收修改 VDA 生产代码。karpathy-guidelines 的最小改动原则用于复用现有
Bridge/VDA 通路，并将接入问题约束在隔离配置和验收脚本，不引入新执行框架。

- `eda_result`：实际 OA→si 网表、Spectre PSF/log/native margin。
- `bridge_readback`：当前 OA、真实 masters/端子/参数及库路径。
- `software_inference`：反馈/RHP 假设、VDA 插值、对照误差和摘要判断。
- `system_event`：进程、监听、文件哈希与收尾；`user_input`：本轮范围授权。

尚未验证：其他 PDK/条件、PVT、多环/差分共模环路、大信号、全部 MOS 工作区、自动找探针、
正式 operation 的普遍 native-margin 留存对照、ADE GUI 交接以及新模块多轮设计闭环。
下一步有用的小改动是将此次原始证据留存及原生对照接入正式路径，先做本地记录回归；
随后按正常设计需求使用跨轮次保护，不为重复证明功能再跑一批电路。
