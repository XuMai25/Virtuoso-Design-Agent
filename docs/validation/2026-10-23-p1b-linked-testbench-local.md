# P1-b：成组偏置/负载与未见点留出检查（2026-10-23）

## 具体缺口与最小实现

本轮基于 `285e79f`，继续使用 `codex/local-validation-and-p1b-20261023`。
只修改本地 VDA 代码、测试和文档；没有远端连接、OA 写入、仿真、新建真实电路或 UI
操作。Bridge、系统、网络、PDK、已有 cds.lib、Vault 和历史 run/model/audit 均未修改。

上一切片已支持多个数值变量，因此不新增优化器/执行器。本轮解决两个具体问题：

1. generic 偏置/负载只允许单字段映射。多个等值源/负载同步变化时，只声明一项会被
   正确拒绝为未建模条件漂移，声明成独立轴又会共线。这会阻碍差分输入共模或对称
   负载与尺寸联合微调。现于 NumericTestbenchParameterBinding 增加显式 linked_names，
   一组占一个自由度，复用原 solver、量化、候选、TaskSpec 和完整元组 audit。
2. training/holdout 原只检查编号互斥，重复实参数组换个编号可让误差门看似通过。
   现拟合前比较实际声明坐标，包括等值 CDF 字面量与 testbench 单位换算；留出与训练
   同点则拒绝。未使用候选格点量化替代实值比较，也未放宽原满秩、覆盖或误差门。

成组成员每个源点都必须完整/等值，不自动补缺失值。仅联动 DC 或负载 value；
各源 AC 幅相、连接和其他固定字段独立保留。编译对每个成员检查类型、单位及工况
供电覆盖冲突；候选/最终选择仍按原完整记录元组对账。中文摘要显示全部目标。
没有组声明的旧 policy/result 不增加空字段，保持原 canonical hash。

这不是通用比例/偏移约束或自动匹配识别，也不把 wrapper 参数当 OA CDF。直接改参、
原 semantic 路径和 ADE 不受此数值映射限制。用法见[数值微调说明](../numeric-cdf-refinement.md)。

## 本地验证

| 检查 | 实际结果 | 证据范围 |
| --- | --- | --- |
| 改动前反例 | 16 failed，1 passed | 14项新组契约被旧 schema 拒绝；2项重复坐标留出未被拒绝；旧单字段序列化原就兼容 |
| 针对性回归 | 142 passed | 成组映射、单字段/CDF/语义、上下文、旧点复用与原 review/iteration；与全量重叠 |
| 统一入口主环境 | 1228 passed，1个导入模块 deferred | 主环境不安装第二份 Bridge |
| 统一入口 Bridge 环境 | 8 passed，0 skipped | 安装的真实 builder + 假客户端，未连接远端 |
| 整体双环境 | passed | 两阶段 JUnit、skip 清单及退出状态均已核对 |

新正向检查复用原 joint/raw-CDF **合成软件 fixture**，没有构建或仿真新电路，也不是
真实差分对性能证据。覆盖三自由度联合拟合到普通零权限任务、逐成员 DC/value 展开、
各源不同 AC 幅相保留、fF 差异不得被绝对容差吞掉、缺值/不等值/固定字段漂移、
别名重复控制、成员类型/存在性/供电覆盖和 post-run 完整元组不匹配拒绝。
重复留出反例分别覆盖原 semantic 和 SI 等值 CDF + typed 偏置/负载路径。

本地输出：

- `artifacts/p1b-linked-testbench-20261023/pytest-repro.xml`、`pytest-targeted.xml`。
- `artifacts/local-validation/20261023-p1b-linked-testbench/summary.json`，及 main/bridge
  各自 pytest.xml、pytest.log。新目录不覆盖旧验收。

完整入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent.local_validation `
  --output artifacts/local-validation/20261023-p1b-linked-testbench
```

以上目录已经存在，重跑需选新目录。忽略的 artifacts 不加入版本控制。

## 原模型与记录复用

以原 policy 重新运行本地 op-relinearize，没有修改输入或放宽门限：

- cascode：原 run `artifacts/runs/existing-schematic-generic-cascode-ac-tune/live-20260729-resumed.json`，
  SHA-256 `e8b8c85d2f5ccd53309143dce67476571bc216e5011e4b7add2c6c129366056d`。
  新 cascode-model.json 与上轮完整结果对象及文件字节完全相等，仍2候选。
- 共源2D：原 run `artifacts/runs/common-source-quality-op-relinearized-next/live-resume1-20260726.json`，
  SHA-256 `f6d5c98acb7b14f7d2c8407c5e8b3a6c281f3e3945e0e7a19eea0494d05bb635`。
  新 common-model.json 与上轮 common-model-recomputed.json 的完整对象及字节相等。
  design-next 仍为 local_model_draft_ready、6候选、token `891e81d48ffdcb4b`；
  compute/write/create/replace 全部关闭。原 exact-byte audit 仍 partial，不升级历史结论。

新输出位于 `artifacts/p1b-linked-testbench-20261023/`，包括 legacy-next.json/.md 和
legacy-next-task.json。源文件 SHA 与 policy 绑定及上轮记录一致；这些是历史复算，
不是 fresh OA/PDK 回读或真实联合微调证据。

## 数据不足及停止边界

复用旧只读检查脚本，PMOS onboarding 原三点及源 SHA 仍与
[上轮记录](2026-10-23-p1b-joint-testbench-local.md)一致：
`5d838f937a0e19ea750e9dd5f68832121fac7ed7c2fd58139dc217f2be9fcd8a`。

- Wfg/VBP 两变量训练加独立留出仍不够，状态为
  insufficient_independent_training_and_holdout。
- CL0 始终2 fF，没有独立负载扰动方向；不能将其灵敏度解释为零。
- fit_performed=false、task_produced=false；未硬拟合、补造数据或增加计算。

成组数值映射的新真实执行、实际 simulator input、CDF callback 后 OA/si、当前工况
及新点性能仍未验证；记录对账不能替代这些证据。一阶模型也没有自动获得交叉项、
非线性/工作区迁移保证或全局最优性。完整 L5B 仍未闭合。

本轮停止于可用本地实现与旧记录兼容性。下次真实模块确实需要这一组微调时，先按
物理含义与已有数据确定关系及范围，再另列目标 library/cell/view、写 OA/计算次数、
scratch 路径、状态前提和恢复风险，获得用户确认后执行；不为验证而另建测试电路。
