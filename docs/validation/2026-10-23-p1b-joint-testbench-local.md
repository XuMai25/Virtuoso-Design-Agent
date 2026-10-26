# P1-b：偏置/负载与尺寸联合映射本地验收（2026-10-23）

## 范围与已有能力复用

原模型/solver 已能联合处理多个独立变量，原 semantic bias/load 路径也已存在；缺口
是 generic 原子候选的偏置与负载只支持固定 override，没有显式数值映射。本轮在
op_relinearization.py/design_next.py 接入这层薄映射，不新写模型、执行器或 Bridge。
分支 `codex/p1b-joint-testbench-20261023`，基于 `337803e`，不移动固定基线 tag、不推送。

- NumericTestbenchParameterBinding 对应显式 source_dc 或 load，声明 name/unit/meaning；
  与原 semantic/CDF/等值组变量复用一套拟合、满秩、留出、量化、排序与普通候选。
- 源 DC 支持 V/mV/A/uA，可显式零/负值；这只是源端子方向，不是器件 VGS/过驱。
  原语义/CDF/负载的正值范围不放宽；负载支持 F/pF/fF/ohm/kohm。
- 源/负载在原 GenericTestbenchOverrides 中仍用 SI。所有训练/留出点必须完整显式
  记录坐标，不从基础 setup 猜补。别名不能遮蔽语义字段或重复控制同一 testbench 字段。
- 只有声明的 DC/负载坐标可变化；其他 AC 幅相、固定 DC/负载、尺寸/拓扑/工况继续
  由原上下文门拒绝漂移。fixed_testbench_overrides 保存固定残余，完整候选保存全部值。
- 编译/审计核对实际 generic_simulation 的 voltage/current/capacitor/resistor 类型，
  不允许把工况将覆盖的供电源作为候选 DC 坐标；完整元组及最终选择仍按原审计对账。
- design-next 展示模型单位下的源/负载前后值，输出原普通安全 TaskSpec；旧 policy/result
  不新增空字段。原 planner/executor/worker、checkpoint、白名单和执行授权均未改动。

## 真实记录复核：明确不拟合

来源为既有 N28 PMOS-loaded CS onboarding run：
`artifacts/runs/vda-pmos-loaded-cs-onboarding/13-onboarding-tune-resume2-20260731.json`，
SHA-256 为 `5d838f937a0e19ea750e9dd5f68832121fac7ed7c2fd58139dc217f2be9fcd8a`。
只读复用原 task 的目标/指标，不修改 run 或补造测量：

| 原候选 | MP0.Wfg | VBP_SRC.dc_value | CL0 |
| --- | --- | --- | --- |
| 1 | 1.9u | 0.56 V | 2 fF |
| 2 | 1.7u | 0.57 V | 2 fF |
| 3 | 1.9u | 0.57 V | 2 fF |

两变量模型至少需要锚点加两个独立训练点，另留独立 holdout；这三点不能同时用于
完整训练与独立验证。只读评估用当前已选点作 anchor，另一个训练点、一个留出点，
原 policy 校验按预期拒绝：training set needs the anchor plus at least one independent
point per parameter。没有调用拟合、输出模型/任务或改变门限以通过。

点数下界不保证满秩或预测准确，不是要求立刻追加一次仿真的授权/计划。CL0 全部
固定，亦无独立负载方向，不能据此把负载灵敏度设为零或校准三变量模型。评估脚本中
范围/步长和误差限仅为 software_inference 的数据检查假设，非新用户规格或推荐参数。
原测量保留 eda_result；本次数据缺口判断不是新的电路性能证据。

本机 `artifacts/joint-testbench-20261023/inspect-retained-data.py` 为可重复的只读检查，
输出保存在同目录 pmos-data-gap.json；没有生成授权/执行任务。

## 本地验证

| 验证 | 实际结果 | 边界 |
| --- | --- | --- |
| 原相关模型/review 回归 | 68 passed | 改动前基线 |
| 新入口改动前检查 | 13 failed | 旧 schema 不支持该映射；不是13个既有产品故障 |
| 最终针对性回归 | 82 passed | 包含14个新增风险用例，与全量重叠不相加 |
| 主环境全量回归 | 1197 passed，1 skipped | 主环境无 virtuoso_bridge，跳过 drawing Bridge 模块 |
| Bridge Python 环境本地相关回归 | 44 passed | 新联合/单字段/联动/固定条件，不连接 Bridge 或 EDA |
| 原 cascode CDF 模型复算 | 与旧完整结果对象相等 | 原 policy 哈希、预测与候选保持；不增加真实联动或联合证据 |
| 原共源语义记录 design-next | local_model_draft_ready，6候选，token 9849a1d8b26d9d74 | 草案 compute/write/create/replace 均关闭；旧审计仍 partial |

新正向用例复用原 raw-CDF fixture，明确构造数学上的三独立轴与独立联合留出，仅用于
软件检查，不是实际电路：覆盖 CDF→源 DC→电容的单位/字段传递、零/负 DC、电流与
电阻单位、固定 AC/负载保留、缺坐标不得 fallback、源/负载类型错配、工况覆盖冲突、
候选漏坐标、别名冲突、数据不足/共线/留出失败，以及逐候选完整元组审计。

最终记录为同目录 pytest-targeted-corrected.xml、pytest-full.xml、pytest-bridge-local.xml。
一次测试草稿删除全部候选的负载坐标后，先触发已有的重复元组拒绝；已让故障样例保持
两个不同元组，隔离显式坐标缺失的拒绝。不是产品放过了错误元组，最终计数以上表为准。

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest `
  tests/test_joint_testbench_relinearization.py tests/test_numeric_cdf_relinearization.py `
  tests/test_op_relinearization.py tests/test_relinearization_testbench_context.py `
  tests/test_linked_cdf_relinearization.py tests/test_design_review.py --tb=short `
  --basetemp artifacts/test-temp/joint-testbench-targeted-corrected-20261023 `
  --junitxml=artifacts/joint-testbench-20261023/pytest-targeted-corrected.xml
```

旧 cascode 复算结果为同目录 cascode-model.json，共源兼容输出为 legacy-next.json/.md
与 legacy-next-task.json。源字节身份和旧审计边界沿用[前次记录](2026-10-23-p1b-linked-cdf-local.md)；
不伪造旧 token/审计、不把旧结果当当前 OA。忽略的 artifacts 不提交。

## 未验证边界与停止条件

联合仍是一阶局部响应，没有新增交叉项或全局优化器。留出合格也不证明任意组合、
全范围或工作区迁移都准确。CDF callback、真正的 simulator input、OA→si 一致性、
新点 DC/AC/噪声等规格与当前 PDK 状态仍分别验收；记录中的 tuple 不能替代实际输入。

本轮未远端连接、写 OA、跑仿真、创建电路或操作 UI，未修改 Bridge、系统/网络/PDK、
现有 cds.lib 或 Vault。常用直接改参、ADE 操作或原语义 bias/load 调优不被新模型限制。
新入口与语法见[数值微调说明](../numeric-cdf-refinement.md)。

当前停止于本地接入与明确数据缺口，不为验证造电路或自动补点。下次真实设计确需
联合微调时，先判断原理论/已有数据能否支持；必须实测再列原 library/cell/view、
写入/计算范围、scratch 路径与恢复风险，请用户确认。完整 L5B 仍未闭合。
