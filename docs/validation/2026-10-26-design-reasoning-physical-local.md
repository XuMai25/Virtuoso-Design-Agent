# 第一、第二批：设计理由与物理坐标本地验收

日期：2026-10-26。结论：**两批本地实现、已有记录离线回放和双环境软件验收通过。**
远端调用、Bridge smoke、OA 写入、网表生成、Spectre、GUI 操作、新电路均为 **0**。
这不是新设计性能、联合模型准确度或完整 L5B 的验收。

## 阻塞、复用和改动

1. 原 intake.rationale 只有作者文字和一个目标，不能逐项关联证据/调整预期。
   新 design_reasoning 使用原 task/run 绑定、精确文件 SHA、候选选择和原指标来源，
   分开保存观察、物理解释、可调字段、趋势、代价及证伪条件；接入 intake、next 和
   iterate。配置仍为声明，推理/对照为 software_inference，保留已有 eda_result 和
   bridge_readback 原始分类。混杂、条件变化、缺指标或 demo 不作实测确认。
2. 原局部模型有 CDF/偏置/负载联动，但没有显式物理坐标到完整候选的薄编译。
   新 physical_refinement 复用 characterization 的插值器、真实表加载器、manifest
   审计和原 Numeric*Binding。gm/ID 反查只接受精确长度、有效偏置域和单调分支；
   从 Id 或 gm 得到宽度，格点后核对范围，直接偏置/负载进入原 typed override。
3. 表侧归一化宽度与目标 CDF 宽度分别声明总有效宽度倍率；缺少任一倍率就拒绝。
   特别覆盖 nf 已计入表电流时不能再重复除/乘 target nf 的风险。格点后 Id/gm 只是
   假设偏置下的估算，不放进模块 predicted_metrics。
4. 新候选仍为原 AtomicCandidateSet/TaskSpec，源任务、请求、PDK profile、表/输入
   证据和编译输入契约均有哈希。保留目标、规格、测量、权限和原预算，关闭
   compute/write/create/replace，不增加 operation、worker、Bridge 或拟合器。

未改系统、PDK、网络、Vault 或第三方 Bridge。AGENTS.md 原有6行未提交改动未动。
没有自动收集新数据、拟合 PMOS 联合模型、实施第三批策略/经验功能或推送 Git。
使用入口、完整例子和限制见[工作流](../design-reasoning-and-physical-refinement.md)。

## 复用旧证据的结果

本地请求与输出位于 artifacts/reasoning-physical-20261026/，不入 Git。

| 回放 | 实际结果 | 不能据此宣称 |
| --- | --- | --- |
| 旧 A 与恢复后的 C，replay-C-report | BW、GBW 精确相等；固定上下文/已记录字段可比较，unchanged=consistent；无任务 | 新预测、因果验证、当前 OA 未变 |
| 旧 A 对 B，replay-B-report，附原 completion | objective 与联合输入不同，not_evaluated；保留 restored_baseline_review_or_stop，无任务 | 同一优化 campaign、独立灵敏度或沿 B 续跑 |
| 旧 N28 W=0.5 µm characterization run，physical-old-table-release | 用原表点做归一化回算得到 CDF W=0.5 µm、L=0.03 µm；缺原 input 且假设偏置不代表目标 OP，physical_design_needs_evidence，无任务 | 目标实际尺寸建议、PDK/金属栈迁移或电路改善 |
| 仓库 PMOS 理由例，example-reasoning-release | draft_ready；沿用原有限候选、零远端权限；无 baseline run，趋势 not_evaluated | 新尺寸选优或已测性能 |
| 仓库合成表例，example-physical-release | 计算可审阅，needs_definition；合成/缺温度/测量等分别保留，无任务 | 合成数值代表 TSMC 或可用于真实 OA |

A/C 原 GBW 均为18492508975.973553 Hz，BW 均为5721284078.756237 Hz；这里只重放
旧标量，不重新解析 PSF。A 的原 gm 指标和 inspect action 分别保留 eda_result 与
bridge_readback，不能把作者解释重标为它们。A/B/A 原记录/完成链的已知字节身份复核
匹配：A/C task、A/B/C run、restore run 和 completion 共7份；没有修改旧运行记录。

器件表回算采用旧表中的 |VDS|=0.3 V、VSB=0、gm/ID=14.33421835565327 V⁻¹ 与
Id=22.44542069997131 µA，仅验证该表归一化的算术往返；不把这个假设电位当当前共源
输出电位。旧原始 characterization input 未提供，因此不能核对其实际 include/section，
程序保留缺口，不为补材料重新仿真。

## 必要测试与完整本地验收

针对性回归：**151 passed**，记录于上述目录的 targeted-release.xml。覆盖：

- SHA/引用漂移、未知字段、唯一 action 与原来源；同一记录不算新检验，独立同输入
  可评价重复性；有反例保留 contradicted，不把作者机制当物理定律。
- 条件/负载混杂、未声明改动、缺指标、未完成记录和 demo 不支持实测确认；理由疑点
  与未解释的草案字段阻止提案，包括 design-next 外层组合 iteration 的路径。
- gm/ID 反查、缺偏置角点、域外/错长度、平坦与重复坐标；显式镜像/gm 目标和表侧/
  目标指宽双换算、格点后越界、余量、单位、重叠映射、权限和候选预算。
- 同名 profile 下实际模型 include 不同、输入字节与 manifest 不同、无 holdout 或
  仅独立自述表时不生成任务；intake 继续复用原测量编译器，失败执行先走原对账。
- 旧 intake/iteration/review/收益路径与原 A/B/A 证据门保持兼容，测试不构建远端 adapter。

最终统一入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent.local_validation `
  --output artifacts/local-validation/20261026-reasoning-physical-release
```

主环境 **1364 passed、1 declared import-only skip**；已安装 Bridge 的独立环境
**8 passed、0 skipped**，整体 passed。两阶段分别记录，不相加为电路证据。
summary.json、JUnit 和日志留在该目录；子进程已同步结束，没有启动 EDA/SSH/服务。

开发中曾遇普通 pytest 默认临时目录 WinError 5。本轮没有修改系统 ACL 或删除该目录，
使用原验证入口为每次运行创建独立 workspace 临时目录。早期测试/开发输出保留，
以上以最终完整验收为准。

## 示例可重复性与剩余边界

源 PMOS 配置工作副本原有 mixed EOL，而仓库已声明 eol=lf。为保证示例的精确 SHA
在 fresh checkout 仍成立，先把原字节备份到 template-before-lf.json，再仅规范为
仓库 LF；Git 内容无差异。前后计划 token 均为765ffdc5ac4ffe87；没有修改配置语义，
没有软化 SHA、计划 token、CAS 或历史证据校验。原 A/B/A 的真实 task/run 不受影响。

本轮没有验证新物理候选的真实 OP/性能、所有 PDK/多指约定、不同模型 include 组合、
跨 PVT、任意层级或新拓扑。表侧和目标宽度倍率仍是显式工程声明，真实使用前后要核对
master、CDF、nf/m、实际 OP 和 OA→si 网表；本地留存输入不能证明当前远端 PDK 文件
后来没有变化。源码没有自动 KCL/角色推断或绝对偏置求解，也不把单点 gm/gds 变成
尺寸导数。原 PMOS 数据仍不足以训练并独立留出联合响应模型，缺口未被硬拟合消除。

下一步应在真实设计需要时先给出明确目标/计算与写入预算，再授权最小同源验证；
不是为本轮软件功能再造电路或重跑全分析。第三批与完整 L5B 的质量/效率证明仍另行。
