# P1-b 显式 CDF 数值微调本地验收（2026-10-23）

## 范围与改动

本轮复用已有代码、原理图配置和保存记录；没有连接远端/Bridge、OA 写入、新建电路、
仿真或 UI 操作。未修改第三方 Bridge、PDK、系统、网络或 Vault；没有自动推送。
开发分支为 `codex/p1b-numeric-cdf-20261023`，保留原 P1-a 提交及基线 tag。

实际阻塞是原 OP 重线性化只接受 numeric semantic parameters，原始 CDF 随候选变化
时只能报“需声明数值映射”，但没有对应接线。本轮直接扩展原 policy/solver/compiler：

- 可选显式 instance_parameter：instance/field、模型单位、Spectre SI 字面量格式、
  物理含义和原有限范围。复用 spectre_scalar、原拟合、满秩/留出和排序，不新写 solver。
- 从原始字符串提取模型数值，再输出普通原子 CDF updates；别名不进入 executor。
  原锚点字符串保留，固定字段不变；检查序列化回转精度及旧参数权限。
- 拟合拒绝记录中未建模的 CDF/语义参数、拓扑、testbench 和条件变化；缺单位/格式、
  表达式、非正值、非有限值和来源不合要求均不能绕过已有门。
- 修复实际范围漏洞：原 proposal 先过滤浮点值再量化，可能把域内1.25舍入为域外1.3。
  现在量化后复查边界；测试将上限设为1.26，确认1.3不会进入 CDF 候选。
- design-next 增加无预测改善停止判断，不因模型通过就要求新计算；未证明全局最优。
  原语义 policy/result 的空映射不序列化，保持旧 policy 哈希和旧路径兼容。

## 本地验证

| 验证 | 实际结果 | 证据边界 |
| --- | --- | --- |
| 开发前原重线性化/review/intake | 62 passed | 既有本地基线 |
| 最终针对性回归 | 78 passed | 16个新增风险用例；不与完整回归相加 |
| 完整回归 | 1169 passed，1 skipped | 主环境无 virtuoso_bridge，跳过 drawing Bridge 模块 |
| Bridge 环境本地 drawing 回归 | 51 passed | 假客户端、真实安装 builder；不连接 EDA，包含主环境重叠项 |
| catalog、原 inverter demo plan | 通过；token 815e068f84bd532e | 不执行计划 |
| 原语义参数真实保存记录离线 design-next | local_model_draft_ready，仍为6候选、token 9849a1d8b26d9d74 | 使用原 exact-byte audit；无新仿真/任务执行 |

用例覆盖单位等价字面量、raw→模型→原子 task→design-next→原 post-run validator、
模型别名不冒充语义旋钮、精确锚点字符串、表达式/歧义后缀/非正/溢出拒绝、重复字段
别名拒绝、原权限保留、混杂偏置/拓扑/CDF/温度拒绝、条件内测量可变、留出失败无
草案、无预测改善停止、量化后不越界及旧语义序列化兼容。完整 task/run 正路径与
post-run 审计使用**显式 synthetic fixtures**，不是本轮真实 EDA 或新点性能证明。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest `
  tests/test_numeric_cdf_relinearization.py tests/test_op_relinearization.py `
  tests/test_design_review.py tests/test_design_intake.py `
  --basetemp artifacts/test-temp/p1b-targeted-final-20261023 `
  --junitxml=artifacts/numeric-cdf-20261023/pytest-targeted-final.xml

.\.venv\Scripts\python.exe -X utf8 -m pytest -o addopts='-p no:cacheprovider' -q -ra `
  --basetemp artifacts/test-temp/p1b-full-final-20261023 `
  --junitxml=artifacts/numeric-cdf-20261023/pytest-full-final.xml
```

Bridge 假客户端补验记录为同目录 pytest-bridge-local.xml，未运行 Bridge smoke。
忽略的 artifacts 不提交，不移动 tag，不修改原 run/配置/审计。

## 保存的 cascode CDF 记录复算：不是新真实验收

来源：`artifacts/runs/existing-schematic-generic-cascode-ac-tune/live-20260729-resumed.json`，
SHA-256 `e8b8c85d2f5ccd53309143dce67476571bc216e5011e4b7add2c6c129366056d`。
用[显式 policy](../../examples/theory/cascode-numeric-cdf-replay-policy.json)解析
MNCAS.Wfg=750.0n/1u/1.25u；原点1、2训练，点3留出。一阶模型的后验误差：

| 指标 | 留出误差 |
| --- | ---: |
| output_dc_v | 1.176864% |
| low_frequency_gain_v_per_v | 1.282640% |
| bandwidth_3db_hz | 0.476207% |
| gain_bandwidth_product_hz | 1.184748% |

原测量仍为 eda_result，数值映射/模型/建议为 software_inference。2%/5%门限和 GBW
最大化是本次后验复算的工程假设，**不是预注册验证或用户给定新规格**。原 task 无
objective；其旧 token 与当前 task/plan 绑定也不匹配，load_bound_run 正确拒绝。
没有伪造 token/audit、修改原 task 或用低层编译器绕过限制，**未输出下一轮真实任务**。

模型文件在 `artifacts/numeric-cdf-20261023/cascode-model.json`，新代码确定性复算一致。
仅生成模型内的 anchor 750.0n 和 prospective 0.875u 字符串；后者 GBW 预测反而低于
锚点。它不是推荐写入、更不是新仿真结果，也不是更宽/更窄尺寸的一般物理结论。
这些旧数据不能用于确认当前 PDK 文件、OA 状态或新点性能。

## 其他已有记录复用

- 原共源语义路径复用 `artifacts/design-review-20261019/final-next.json` 所绑定的
  task/run、已有 exact-byte audit、model-recomputed、原2D policy/template。确定性模型
  检查通过，仍输出同一六点安全任务及9849a1d8b26d9d74指纹；新停止判断识别5个
  预测可行未测点中2个预测改善点。只生成本地建议，没有新计算或沿用旧指标闭环。
  输出为本机 `artifacts/numeric-cdf-20261023/legacy-next.json/.md`。
- PMOS 三点 run 复用 P1-a 的
  `artifacts/runs/vda-pmos-loaded-cs-onboarding/13-onboarding-tune-resume2-20260731.json`，
  SHA-256 `5d838f937a0e19ea750e9dd5f68832121fac7ed7c2fd58139dc217f2be9fcd8a`。
  只声明 MP0.Wfg 而不建模 VBP 的变化时，拒绝原因为
  `unmodeled topology, testbench or operating conditions vary across source points`；
  未把偏置差异当宽度灵敏度，未拟合后自动补点。

## 仍保留的边界

这是 P1-b 的首个本地切片，不是通用理论优化器或 L5B 质量闭环。支持显式正值字面量，
不自动推断 CDF 单位/语义、表达式、未知器件/深层 hierarchy、testbench 数值映射或
工作区适用性。单点 OP/MNA 不变成尺寸导数；来源记录中看不见的变化不能据此排除。

新 CDF 候选实际写入、CDF callback 后值、实际 si 参数/模型一致性及新点指标本轮
未验收。后续只在真实模块需要微调时，结合当前 OA/PDK/目标和理论理由选择最小范围，
先列目标 library/cell/view、是否写 OA、计算次数、scratch 路径及风险，再申请确认。
不为这层接线新建电路、跑全域或补一套优化框架；没有有依据的改善/诊断需求就停。
