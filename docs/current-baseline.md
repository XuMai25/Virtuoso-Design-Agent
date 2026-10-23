# 当前可用基线（2026-10-23）

VDA 是 Bridge 之上的设计与验证编排层。按[分级 v2.1](l5-roadmap.md)，当前已有 L2–L4
执行/有限优化、部分 L5A 设计切片及 L5B 通用化能力；完整 L5B 质量闭环仍未成立。
不是任意电路自主设计器；重新定义不增加实测证据。包版本仍为 `0.1.0`；本地基线
标签 `vda-baseline-20261023` 用于版本回溯，不是发布到远程的 release。
该标签固定此前收口版本，不随本次分级文档更新移动；既有测试与实测结论不重新计分。

## 当前缺口与下一步

- **不是缺执行器。** 安全契约、既有电路接入、CDF/拓扑微调、同源仿真、理论分析、
  preview 和跨轮次保护均有现成实现，不重新搭建。
- **首要是少手工接线。** 新模块的语义角色、变量范围、测量和理论理由仍需 agent 整理；
  初始 pin/inventory 角色不是完整电路功能识别。P1-a 已有 design-intake，引用原 task/
  onboarding、measurement 和可选 history，导出上下文及零远端权限草案，不重抄角色、
  权限、规格和候选。物理解释仍需判断，不声称已测出端到端节省。
- **数值建议有适用域。** design-next 依赖通过校验的语义或显式 CDF 映射局部模型；任意 raw CDF
  可写不等于已知其单位、灵敏度或最优范围。优先复用器件表、OP/MNA、binding 和现有
  proposer，按实际变量补映射/最小诊断，不另造求解器或限制单次参数应用。
  P1-b 首个切片已支持[显式字面量 CDF 数值映射](numeric-cdf-refinement.md)，复用原
  拟合/留出与普通任务；混杂变化拒绝、量化不越界、无预测改善则停。真实新点及未知
  物理语义仍未自动闭合，不把本地数值映射称为通用理论优化器。
  固定源/负载 override 已从模型保留到候选，并纳入逐候选/最终选择审计；这是任务与
  保存记录的一致性修复，不是实际 simulator input 的独立复核。见[修复记录](validation/2026-10-23-model-testbench-context-local.md)。
- **部分缺的是实机证据。** 正式 STB 新路径、若干 ADE/跨轮次组合路径在需要时用原设计
  验收，不因此再写一套功能或强制重复全部分析。完整 agent/人工返工成本仍未可靠计量，
  不以 action 时长冒充。

完整的源码/证据对应表、P0 护栏、P1 接入/理论细化、P2 按需验收、P3 需求触发扩展见
[路线](l5-roadmap.md)。有真实设计先用现有工具；没有实际需求就可保持当前基线。

## 从真实需求选择最短路径

| 需求 | 已有入口 | 不能据此宣称 |
| --- | --- | --- |
| 读图、单次参数修改、单项仿真 | 普通 `schematic.inspect` / `parameters.apply` / `simulation.run` | 其他分析、工艺和全部规格已通过 |
| 已有原理图的受控微调 | existing_schematic、CDF binding、topology delta、checkpoint、winner verification | 任意 CDF 的物理含义或任意拓扑已经自动建模 |
| 图片复现 | electrical/drawing/uncertainties 描述 → `schematic-compile` → 非覆盖 create/inspect | 任意图片自动识别、N28 fresh-create 单次全链或性能已验收 |
| 已跑任务的摘要与下一轮 | `design-review` / `design-next` / `design-iterate` | 摘要等于新鲜 OA 回读，或旧结果可移植给已修改设计 |
| 接入与决策上下文整理 | [`design-intake`](design-intake-workflow.md)，复用 task/onboarding、测量和可选 run | 自动识别所有功能角色、理由已被物理验证或草案已有执行权限 |
| DC/AC/STB 等测量 | `measurement-compile` 编译到普通任务；`stability-audit` 离线核对 STB | 普通 AC 相位等于 PM，或证据自洽等于规格通过 |
| 人工 ADE 介入 | ADE L 原生操作指南；正式 `ade.*` 仍为 Maestro 接口 | 所有 ADE L 旧 state 或 STB GUI 保存/重开已打通 |
| 窗口收拢 | `windows-plan` 仅生成保守建议 | 自动保存、关闭已完成；跨任务窗口可以一并收走 |

开始设计时选定 PDK，不混用性能证据。默认 `nics4304_tsmc28`；TSMC18 用独立
`nics4304_tsmc18`。N28 金属栈/模型版本仍须与实际工作区匹配，不能只凭 profile 名判断。

先用用户给定拓扑、实际连接、理论/OP 和已有记录判断瓶颈，再声明有限的参数或局部拓扑
变化。没有可靠模型就输出缺失信息，不随机列几组尺寸后称“最优”。1–3 个候选直接走原
operation；4 个及以上才按[快速筛选流程](fast-preview-shortlist-workflow.md)核算 preview
是否省时，冻结 shortlist 后走普通 OA 真值任务。

DC、AC、noise、STB、transient 和 PVT 按电路及规格需要选择，不强制每次全跑。STB 需要
明确反馈环路和探针；PVT 可选；便宜筛选不能替代最终同源 OA→si/ADE 结果。有限点最佳
不是连续/全局最优。已达标且证据完整可以停止，不新增 Gate 维持工作量。

## 本地常用入口

以下命令均不执行远端；输出应使用新的文件名。带尖括号的输入是待替换占位路径。

```powershell
.\.venv\Scripts\vda.exe catalog
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-close-loop.demo.json
.\.venv\Scripts\vda.exe schematic-compile --help
.\.venv\Scripts\vda.exe design-review <task.json> <run.json> --output <new-review.json> --markdown <new-review.md>
.\.venv\Scripts\vda.exe design-next <task.json> <run.json> --output <new-next.json> --markdown <new-next.md>
.\.venv\Scripts\vda.exe stability-audit <stability-evidence.json> --expected-sha256 <原run中的SHA256> --output <new-audit.json>
```

测量/跨轮次示例见[测量与迭代](measurement-and-iteration-workflow.md)，图片描述示例见
[图片复现](schematic-image-reproduction.md)，摘要与窗口清单见
[复盘和窗口策略](design-review-workflow.md)，STB 文件与一致性检查见
[STB 证据工作流](stability-evidence-workflow.md)。工具处理耗时与 agent 思考/对话耗时分别
记录；没有测量就不宣称加速。

执行仍须按现有计划、token、compute/write 开关、白名单和非覆盖规则授权；上述本地命令
不授予远端权限。真实写入和计算先明确目标、范围、预计路径及风险。失败/部分状态先回读，
不自动重复提交。已有人工原理图、ADE 设置和其他对话的窗口不得静默改动或关闭。

## 验证边界与遗留事项

- 历史反相器、共源/源极退化、差分及受控既有拓扑已有真实 OA→si→Spectre 证据；
  不将一个 nominal 有限域推广到新 PDK、所有参数或完整多分析质量。
- N28 图片锁存器经一次明确授权的部分状态补完，通过结构、导线和网表；原生图面人工
  复核、修复后 fresh-create 单次全链及性能分别待验证，不为重证机制重复建图。
- STB 有一次 N28 单点 DC＋STB 与原生 margin 对照。正式 worker 的持久证据路径已做
  本地回归和旧文件离线重放，尚未新做该版本远端 smoke；离线 audit 使用保留的 Bridge
  解码样本，不是独立 PSF 波形解析器或新仿真。
- ADE L TSMC18 单管配置已验证保存、独立 Load、DC 和另一次 cellview 保存→Load→关闭；
  不代表任意旧 state 迁移、STB GUI 或 ADE L→Maestro 已验证。
- Windows TCP reset 仍按用户决定[暂缓排查](validation/2026-10-19-windows-tcp-reset-deferred.md)，
  不是已修复；不切换 TUN。Codex/游戏卡顿诊断不属于 VDA 升级范围。

以上证据分类继续区分 `eda_result`、`bridge_readback`、`software_inference` 和
`user_input`。本次收口没有新增电路性能证据，详情见[本地验收记录](validation/2026-10-23-baseline-closeout.md)。

## 本地版本与恢复

本次使用独立分支 `codex/vda-baseline-closeout-20261023`，代码/测试/示例作为一个可运行
提交，文档/约束作为另一个提交。基线标签 `vda-baseline-20261023` 绑定收口结果；未推送。
原分支 `codex/bridge-main-upgrade-20261018` 保留在 `e0aea2d`。

回看时可用 `git show vda-baseline-20261023`；需要恢复开发环境时先保全当前未提交修改，
再从标签建立新分支或单独 worktree，不用 hard reset、不覆盖解压历史文件。审查前的
61 个待提交文件另有仅本地快照，位置及哈希记录见验收记录。证据、快照、凭据和 PDK
内容不进入 Git；本次没有更改系统、Vault、第三方 Bridge 或远端文件。
