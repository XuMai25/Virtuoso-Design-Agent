# 双环境验收与 P1-b 旧点复用修复（2026-10-23）

## 改动与边界

分支 `codex/local-validation-and-p1b-20261023`，基于 `f6b9d2c`。没有连接远端、OA 写入、
仿真或新电路，也未修改 Bridge、PDK、系统/网络、现有 cds.lib 或 Vault。固定基线 tag
不移动，不推送；以下均为本地软件验证/旧记录重放，不升级 L5B 质量声明。

新增 local_validation.py 开发入口，复用原 Bridge Python 配置与现有 pytest：主环境全量
加已安装 Bridge 的必需绘图模块，独立 JUnit/日志与统一 summary。缺环境、失败、未知
skip 或补验未完成均不能整体通过；不安装第二份 Bridge 或运行真实 smoke。用法和
明确清单规则见[双环境入口](../local-validation.md)，不新增设计 operation。

P1-b 沿原候选判重发现：observed 只比较部分上下文并将历史输入量化后视为“已测”。
其他固定 CDF/语义参数的点、未完成/指标非 EDA 的点，以及只在量化后接近新点的记录，
都可能压掉未测候选，让 design-next 错误停在 no_predicted_improvement。

现复用原 _validate_source_candidate / _constant_inputs 核对 EDA 完整性、全部固定
上下文，再确认实际值确实位于当前格点；不适合的旧点不参与判重，原因保留在 notes
和中文建议摘要。训练/holdout 不变，同上下文的真实格点仍排除。原不完整运行的
reconcile_execution_first 不绕过，绝不因此自动重试不确定操作。

## 本地验证

| 项目 | 实际结果 | 边界 |
| --- | --- | --- |
| 验收控制器故障回归 | 9 passed | 假子进程，覆盖缺环境/skip/无报告/失败报告/输出保护/非零退出/保留虚拟环境启动路径，不递归启动 pytest |
| P1-b 修复前反例 | 4 failed，1 passed | 不同长度、不完整、非EDA指标、近格点均误排除；精确有效点原就正确排除 |
| 最终针对性回归 | 96 passed | 与全量重叠，不相加 |
| 统一入口主阶段 | 1211 passed，1个导入模块 deferred | 主环境不直接安装 Bridge，非功能失败 |
| 统一入口 Bridge 阶段 | 8 passed，0 skipped | 覆盖该 deferred 模块，真实 builder + 假客户端，无远端连接 |
| 整体 | passed | 两阶段及 JUnit 同时通过，不只看退出码 |

反例复用原 raw-CDF 合成 fixture，不是新电路/实测。未完成反例的模型不将该点计为
已测，但 design-next 仍返回 reconcile_execution_first，没有草案。一次测试初稿误期待
此时也出草案，已改为明确验证原对账门；不是实现放松了安全边界。

控制器初始回归为 artifacts/local-validation-controller-20261023/pytest.xml；最终控制器
与 P1-b 回归为 artifacts/local-validation-p1b-20261023/pytest-targeted-verified.xml，
原反例为同目录 pytest-observed-repro.xml。最终统一入口的 summary/JUnit/日志在
artifacts/local-validation/20261023-dual-env-p1b-verified/。
这些忽略的 artifacts 不提交。完整入口命令不运行任何新电路。

## 旧共源记录确有不同固定条件，但本次数值未变

原 run：artifacts/runs/common-source-quality-op-relinearized-next/live-resume1-20260726.json，
SHA-256 为 f6d5c98acb7b14f7d2c8407c5e8b3a6c281f3e3945e0e7a19eea0494d05bb635。
原2D policy 的 anchor=2、training=[2,3,4]、holdout=[5,6]；这些点 RS 都为750 ohm。
同一 run 的候选1为 W=1.0 um、RD=20 kohm、RS=1000 ohm，不是当前750 ohm切片的
同上下文点。新模型明确不把它用作已测格点；没有删除或否认其原始 eda_result。

这条旧记录中，该点不在当前建议格点内，所以本次新旧 sensitivities/models、holdout
比较、proposals 与候选完全一致，**变化只有说明 notes**，不声称以前选错了设计。
旧 model-recomputed.json 因说明不同被 design-next 的确定性重算正确拒绝；没有手改
旧模型/源记录来通过。另存 common-model-recomputed.json 后再生成安全草案：

- 仍为6点，数值相同，compute/write/create/replace 均关闭。
- 旧草案 token 9849a1d8b26d9d74 不修改；新模型字节 SHA 变了，绑定该模型的新任务
  token 为891e81d48ffdcb4b。它不是执行授权，不接续旧 checkpoint。
- 原 exact-byte audit 只用于历史身份绑定，仍为 partial，不继承预测精度门通过。

本机 artifacts/local-validation-p1b-20261023/ 下保留重算模型与
legacy-next-recomputed.json/.md、legacy-next-task-recomputed.json；旧文件未改动。
原 cascode 单字段记录复算仍与旧完整模型对象相等，输出为同目录 cascode-model.json。

## 数据不足继续保留

原 PMOS onboarding run 的3点仍不足以训练并独立留出“宽度＋栅极偏置”两个自由度，
CL0 始终2 fF，无可校准的负载扰动方向。复用前次只读检查，pmos-data-gap.json 仍为
insufficient_independent_training_and_holdout，fit_performed/task_produced 都为false。
源 SHA 与具体点见[联合映射记录](2026-10-23-p1b-joint-testbench-local.md)，未补点或改门限。

不把附近格点视为已测，不等于当前模型在附近一定准确；仍是有限一阶响应和原留出
边界。实际输入、CDF callback、OA→si、新点性能和当前 PDK/会话状态分别验收。
没有实际设计需求就停止在当前本地能力，不为了这项修复追加远端 Gate；确需运行时，
先另列目标 library/cell/view、写入/计算范围、scratch 与恢复风险，由用户确认。
