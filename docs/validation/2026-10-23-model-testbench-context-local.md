# 局部模型固定仿真条件传递修复（2026-10-23）

## 为什么优先做这一步

沿现有 model→普通任务→post-run audit 检查，发现两处可以影响设计判断的实际缺口：

- 拟合已经要求源记录的 testbench override 保持不变，但生成 AtomicCandidate 时没有
  复制它。模型可能在固定输入偏置/AC 激励/负载下建立，新任务却退回基础 setup 的值。
- 原审计比较参数、指标和原子候选身份，却没有比较逐候选及最终选择的 testbench
  override；不同条件的记录可能在参数/指标恰好相同的情况下通过。

先用7个针对性用例重现，旧代码全部失败，再修原有接线；不是为了增加功能或验证数
而新建电路。开发分支 `codex/model-testbench-context-20261023`，基于 `12a59f3`。
不改动固定基线 tag `vda-baseline-20261023`，不推送。

## 具体修复

复用 `GenericTestbenchOverrides`、原局部模型/AtomicCandidate/compiler 和 validator：

1. 模型结果显式保存 `fixed_testbench_overrides`；每个候选独立复制相同条件，基础
   generic_simulation 不被改写。没有 override 时不序列化新字段，兼容旧语义模型。
2. 编译/审计检查模型内候选没有丢失固定条件。模板缺少 override 指向的源/负载时，
   原 TaskSpec typed 校验拒绝，不丢弃 override 或回退基础值。
3. post-run audit 比较 measured candidate 与任务的 override，并要求最终选择的
   selected_testbench_overrides 匹配被选中候选，不能仅凭参数/指标相同接受。
4. design-next 摘要展示固定源/负载条件。源模型仍须确定性重算；曾丢条件的旧模型应
   以原 run/policy 本地重算并审查新任务/token，不修改旧 run、audit 或 checkpoint。

没有新 operation、求解器、执行器或 Bridge API；原单项读图/改参/仿真与权限边界不变。
未建模的 override 变化仍拒绝拟合，本次没有扩展 testbench 数值优化。

## 本地验证

| 验证 | 实际结果 | 边界 |
| --- | --- | --- |
| 修复前针对性重现 | 7 failed | 确认旧行为丢条件、接受错配，不是新功能未接入的空测试 |
| 修复后模型/review/intake 回归 | 85 passed | 包含7个新风险用例，与全量重叠，不相加 |
| 主环境完整回归 | 1176 passed，1 skipped | 未安装 virtuoso_bridge，drawing Bridge 模块在收集时跳过 |
| Bridge Python 环境的本地新用例 | 7 passed | 不连接 Bridge、Virtuoso 或 Spectre；不冒充 live smoke |
| catalog / inverter demo plan | 通过；token 815e068f84bd532e | 仅列能力和计划，不执行 |
| 保存的共源语义模型本地 replay | local_model_draft_ready；6候选，token 9849a1d8b26d9d74 | 既有无 override 路径保持兼容；草案 compute/write/create/replace 均关闭 |

新用例复用原 synthetic fixtures，显式覆盖固定 DC/AC 幅相和负载的有效 typed 值、
候选或最终选择的条件错配（分别含/不含原 override）、模型内条件丢失，以及模板缺失
源的拒绝。fixture 中模拟的 eda_result/Bridge 字段只服务本地回归，不是本轮实测证据，
其具体数值也不是推荐设计值。新上下文正路径的真实 EDA 执行本轮未做。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest `
  tests/test_relinearization_testbench_context.py tests/test_numeric_cdf_relinearization.py `
  tests/test_op_relinearization.py tests/test_design_review.py tests/test_design_intake.py `
  --tb=short --basetemp artifacts/test-temp/context-targeted-fixed-20261023 `
  --junitxml=artifacts/model-context-20261023/pytest-targeted-fixed.xml

.\.venv\Scripts\python.exe -X utf8 -m pytest -o addopts='-p no:cacheprovider' -q -ra `
  --basetemp artifacts/test-temp/model-context-full-20261023 `
  --junitxml=artifacts/model-context-20261023/pytest-full.xml
```

本机证据目录 `artifacts/model-context-20261023/` 还保留修复前 pytest-repro.xml、
Bridge 环境 pytest-bridge-local.xml，以及 legacy-next.json/.md 和安全任务草案
legacy-next-task.json。忽略的 artifacts 不提交；未改动原来源文件。

旧记录 replay 复用 `artifacts/design-review-20261019/final-next.json` 所绑定的原共源
task/run、model-recomputed、2D policy/template 和 exact-byte audit。新报告保留每个
来源的原字节 SHA-256。原 audit 仍为 partial、预测精度门未通过；只用它对账历史身份
和测量，不继承质量通过、不伪造新审计，不把旧结果当当前 OA。

## 未验证边界与停止条件

这是声明/记录的条件一致性修复，不是实际 Spectre input 解析器；不能证明源/负载值
在真实 simulator input 中已生效，也不能证明 CDF callback、当前 OA 或新点性能。
模型/理由/摘要仍为 software_inference；旧测量保留原 eda_result，未新增 bridge_readback
或把工程判断改标 user_input。

本轮没有远端连接、OA 写入、仿真、UI 或新电路，没有修改 Bridge、系统/网络/PDK、
现有 cds.lib 或 Vault。已用本地失败重现、回归和旧记录确认本次风险，不追加无关 Gate。
以后真实任务确需新候选时，先列目标 library/cell/view、写入/计算次数、scratch 和风险，
另行申请授权；届时仍须原 OA→si/ADE 与实际输入/结果证据。完整 L5B 仍未闭合。
