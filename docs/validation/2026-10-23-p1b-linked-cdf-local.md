# P1-b：显式等值 CDF 联动本地验收（2026-10-23）

## 实际阻塞与改动范围

前一个切片支持单个 CDF 字段进入局部模型，但通用原理图中常有多个字段需要等值
联动。沿现有实现确认：只建模一个变化字段会因另一字段变化而拒绝；把一起变化的
字段建模成两个独立变量又会使训练矩阵不满秩。不能关闭这些正确护栏来绕过问题。

本轮扩展原 NumericInstanceParameterBinding，允许显式 linked_parameters，共用一个
模型变量、单位和有限范围。复用原 solver、训练/留出、量化、排序、AtomicCandidate、
TaskSpec、planner 及 post-run validator；没有新 operation 或新执行器。分支为
`codex/p1b-linked-cdf-20261023`，基于 `746d75d`；固定基线 tag 不移动，不推送。

- 每个训练/留出点读取全部成员；字段必须完整、为正值字面量且数值等值，只容许
  1e-12级相对表示误差。没有成员默认值、自动相等补全或比例猜测。
- 未建模的参数/拓扑/偏置/负载/条件仍固定，其他 CDF 漂移继续拒绝；相同字段不能
  重复声明或归属两个数值变量。
- 一个候选展开所有成员，锚点逐字段保留精确原始字符串；新点逐字段序列化。编译器
  和审计器拒绝组内缺字段/不等值，普通任务继续逐成员检查权限及实际执行证据。
- design-next 摘要展示完整关系与每个字段的前后值。未用 linked_parameters 的旧
  policy/result 不增加空字段，不改变旧模型/任务身份。

此等值声明是输入契约，不是器件物理匹配证明。特别不把 NMOS/PMOS 自动绑成1:1，
不支持任意比例/表达式或自动认定总宽/每指宽相同。跨实例更新仍由原 executor 执行，
不是底层 OA 多对象原子事务；原 checkpoint/部分失败处理不变。

## 本地验证结果

| 项目 | 结果 | 证据边界 |
| --- | --- | --- |
| 改动前相关模型/review 回归 | 61 passed | 旧路径基线 |
| 新联动用例的改动前检查 | 6 failed，1 passed | 新契约原来不支持；同一用例也确认单字段遗漏/双独立轴秩亏；单字段序列化兼容用例原已通过 |
| 最终针对性回归 | 68 passed | 包含7个新增风险用例，不与全量相加 |
| 主环境全量回归 | 1183 passed，1 skipped | 主环境无 virtuoso_bridge，跳过 drawing Bridge 模块 |
| Bridge Python 环境本地相关回归 | 30 passed | 新联动、原单字段、固定 testbench；没有 Bridge 连接或 EDA |
| 旧 cascode CDF 真实保存记录复算 | 与既有完整模型对象相等 | 原一字段模型、policy 哈希、预测及候选保持；不是新点性能验收 |
| 旧共源语义记录 design-next | local_model_draft_ready，6候选，token 9849a1d8b26d9d74 | 普通草案 compute/write/create/replace 均为false |

新组路径的正向测试复用原 synthetic raw-CDF fixture，扩展两个同数值字段，并保留
主/联动成员不同的原始 SI 字符串。验证同一个模型自由度生成整组更新、普通安全任务
与原 post-run audit；成员缺失、不等值、额外长度漂移、字段被两变量控制、成员无搜索
权限、编译结果丢成员及实测记录仅一成员变化均不能通过。synthetic 指标/Bridge 字段
只测试软件，不是本轮 eda_result 或新的电路；测试数值也不是推荐尺寸。

本机保留 `artifacts/linked-cdf-20261023/` 下的 pytest-baseline.xml、
pytest-repro-final.xml、pytest-targeted.xml、pytest-full.xml、pytest-bridge-local.xml，
以及 cascode-model.json、legacy-next.json/.md 和 legacy-next-task.json。
第一次测试草稿误将缺省字段未展开的原始 policy 与完整序列化对象相比，已改为检查
原 binding 字段；它不是产品故障，复现计数以 repro-final 为准。忽略 artifacts 不提交。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest `
  tests/test_linked_cdf_relinearization.py tests/test_numeric_cdf_relinearization.py `
  tests/test_op_relinearization.py tests/test_relinearization_testbench_context.py `
  tests/test_design_review.py --tb=short `
  --basetemp artifacts/test-temp/linked-cdf-targeted-20261023 `
  --junitxml=artifacts/linked-cdf-20261023/pytest-targeted.xml

.\.venv\Scripts\python.exe -X utf8 -m pytest -o addopts='-p no:cacheprovider' -q -ra `
  --basetemp artifacts/test-temp/linked-cdf-full-20261023 `
  --junitxml=artifacts/linked-cdf-20261023/pytest-full.xml
```

## 怎样复用了真实历史数据

- cascode：原 `artifacts/runs/existing-schematic-generic-cascode-ac-tune/live-20260729-resumed.json`，
  SHA-256 为 `e8b8c85d2f5ccd53309143dce67476571bc216e5011e4b7add2c6c129366056d`；
  沿用原 cascode-numeric-cdf-replay-policy 与旧 model，完整确定性复算一致。未增加 linked
  字段，也未把缺少 raw 关联字段的记录补造为联动实测。该记录仍只做后验复算，原 task
  身份/objective 边界见[前次记录](2026-10-23-p1b-numeric-cdf-local.md)，不输出新执行任务。
- 共源：复用 `artifacts/design-review-20261019/final-next.json` 所列原 task/run、已有
  exact-byte audit、model、policy/template。新报告逐一保留并核实原文件 SHA，输出
  同一个安全任务及计划指纹。原 audit 仍为 partial，不把本地兼容成功升级为质量通过。
- PMOS 三点：读取既有 onboarding run 的 MP0.Wfg 与 VBP_SRC.dc_value，确认宽度和
  偏置同时变化；3点不足以对两个独立变量既训练满秩又保留独立留出。本轮不关闭条件
  护栏、不编造点、不拟合成单一宽度效应。源/负载联合映射继续按实际需求处理。

## 未验证边界及下一次真实使用

本轮未连接远端、写 OA、跑仿真、创建真实电路或操作 UI；未修改 Bridge、PDK、
系统/网络、现有 cds.lib 或 Vault。真实联动字段的完整 CDF 回读、逐实例 callback、
OA→si 网表一致性及新点指标没有因本地回归而验收。完整 L5B 仍未闭合。

联动只按声明的 shared unit/Spectre SI 字面量解释；不推断相同字段名就有相同物理语义，
不把 callback 派生字段加入独立写入，也不扩大深层 hierarchy 支持。真实成员的可写性
与绑定仍先从该 PDK/设计确认。语法例和原入口见[使用说明](../numeric-cdf-refinement.md)。

不为这项接线再造电路。下一次真实模块需要整组微调时，在原目标内选最少的明确候选，
先列 library/cell/view、是否写 OA、计算次数、scratch 路径和恢复风险，由用户确认后
走原授权/回读/网表验收。普通单字段或人工指定修改仍走原正交 operation，不强迫建模。
