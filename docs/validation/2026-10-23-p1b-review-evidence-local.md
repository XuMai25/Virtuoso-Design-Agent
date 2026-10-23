# P1-b：数值格式、实际输入与最终恢复状态的本地接线

日期：2026-10-23。结论：**本地实现、旧 A/B/A 文件重放及双环境回归通过**。
本轮远端调用、OA 写入、网表生成、Spectre、新电路均为 **0**；不是又一次真实验收。

## 具体阻塞与复用

既有联合探针将 `475n` 写入数值 CDF 后，真实 callback/OA/si 均为 `475.00n`；worker
已正确接受，但 design-review 仍按字符串误报。此外，新输入回执和独立恢复/C 记录未
进入普通 review/next，必须依赖一次性 audit 才能知道 B 已不在现场。

本次仅扩展原 `design_review.py` / `design_next.py` / CLI，新增只读
`review_evidence.py`；复用原 TaskSpec/RunRecord、plan 绑定、完整结构 snapshot、严格
Spectre 标量、wrapper audit 和输入 receipt 校验。没有新执行器/优化器、Bridge API 或
第三方改动；没有修改 planner、executor、权限、CDF setter、状态 CAS 或旧记录。
收益是后续直接读正式报告，不再手拼一次性恢复摘要；未实测 agent 总耗时节省。

## 实现与入口

1. **局部数值格式匹配。** `writeback.parameter_checks` 保留 requested/actual/basis。
   仅成功真实 action、同目标及匹配的 OA→si binding 支持普通有限标量格式等值。
   未声明字段、字符串/表达式和实际差值不通过；全部恢复状态仍逐字段精确比较。
2. **实际输入审计。** 自动消费已声明 receipt 与 artifact manifest；有效 settings 从原
   task、候选 typed overrides 和工况重新形成。核对 wrapper/原始及转换后 OA include
   SHA、清单文件大小/SHA、同作业输入回执、源/负载连接及 DC/AC 字面量，拒绝目录
   逃逸、同 receipt 冲突、来源不符、条件漂移与仅一部分文件丢失。
3. **显式收尾。** `--completion` 固定被复盘 task/run 的 SHA，并引用 baseline、
   restoration、final 三组普通 task/run 的路径及 SHA。检查四份不同 run、目标/PDK、
   顺序、真实成功来源、精确恢复请求与确认值，以及前后完整实例/master/端子/pins、
   参数和 placement。final 可用已有 simulation.run 或独立 schematic.inspect；不要求
   为收尾新增仿真，inspect 不补造最终性能或 testbench。
4. **下一步保护。** 缺失/冲突证据及 pending checkpoint 优先对账、不出任务；B 已被
   恢复时要求显式 baseline/final 重新定锚，不自动沿 B 的 model/iteration 继续。
   旧失败 actions/notes 保留；草案仍经原零权限、token、白名单与非覆盖流程。

`--artifact-root` 默认是 run 所在目录。默认目录之外不跟随读取；整份作业已正常清理
或位于范围外，标为 `recorded_not_replayed`，不误判执行失败，也不称为新的字节验收。
旧记录未提供回执为 `not_recorded`；有损坏/部分丢失则 `incomplete`。显式指定 root 或
completion 时必须完整回放，文件不足不能仅凭元数据通过。不会自动搜文件、改路径、
联网取回、恢复状态、重跑任务或扩张授权。

契约、命令及填写说明见[设计复盘工作流](../design-review-workflow.md)。

## 本次 A/B/A 的正式入口重放

输入全部来自[上轮批准的联合真实验收](2026-10-23-p1b-joint-input-live.md)：
`artifacts/p1b-joint-live-20261023/`。读取 A-recovered、B-format-fixed、restore、C
普通 run 及 tasks-current 中对应任务，不依赖一次性 `10-final-readback`、cleanup 或
`12-acceptance-audit` 的私有结构。源 task/run 精确 SHA 与原记录相同，未重写旧 token。

| 记录 | run SHA-256 |
| --- | --- |
| A | `0ccf247fee0ac88fd4f361b7ff35e30fa535989f4cb0b4989f444c7418db280a` |
| B | `df3aa1792d9a56c7b83ea41d4cfb30f3872834431acc36d985ff84f43e64927e` |
| restore | `19f4c5c9cd0a68bf074e6d3b119312eb2f738091a27b30a75b5c478a6647ee91` |
| C | `d8ef111eda1a7c055655b908900e713750dc115632058a3c2b2c53d7e3834839` |

复盘目标仍为 `vb_pdk_smoke/vda_pmos_loaded_cs_onboarding_001/schematic`，N28
5X2Y2Z/top_tt。用 `design-review` 与 `design-next`，提供 B 的既有 checkpoint 及
本地 `completion.json`；得到：

- B：`selected_parameter_readback_matched_in_record`，明确保留请求 `475n` 与实际
  `475.00n`，依据为 `declared_oa_si_numeric_binding`，不是用请求覆盖实际值。
- A/B/C：各16项原有文件清单与回执/有效 wrapper 通过本地重核；B 的 input_evidence
  为 verified，completion 内 baseline/final 也为 verified。未重新解码 PSF。
- 收尾：`restored_to_baseline_in_record`；最后关联 OA 观察时间为
  `2026-10-23T11:13:06.705164+00:00`，是历史时间，不是本轮现场检查时间。
- B 的 OA/testbench 元组不是最后状态，`selected_point_is_final_state=false`；A/C 的
  原25个 selected_metrics 相等。报告仍保留 B 的历史候选/指标及3条失败 action。
- `design-next.status=restored_baseline_review_or_stop`，`task_draft=null`；不误用 B
  继续调参。`current_remote_state_verified=false`、`execution_authorized=false`。

输出位于 `artifacts/p1b-review-wiring-20261023/`：`completion.json`、
`review-final.json/md`、`next-final.json/md`。原文件、产物和运行配置不提交 Git。

## 必要测试与软件验收

新增测试复用已有 generic task/inspection fixture，在临时目录构造文件协议和状态
反例；fixture 文本明确不是物理波形，不生成或验证新电路。覆盖格式与真实数值差异、
未声明字符串字段、回执/网表/清单损坏或缺失、有效条件/来源漂移、重复 receipt 冲突、
默认目录范围与正常 cleanup、精确哈希/顺序/状态/图面恢复、pending、已有 inspect
收尾且不补造 testbench、重新定锚、CLI 输出非覆盖和旧路径兼容。

完整入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent.local_validation `
  --output artifacts/local-validation/20261023-review-evidence-complete
```

主环境 **1313 passed**，1个已声明的 Bridge import-only 模块转入补验；Bridge 环境
**8 passed、0 skipped**，总状态 passed。结果为上述目录的 `summary.json` 与两份
JUnit/log。所有测试仅本地软件；Bridge 环境只跑原有本地适配器模块，不会启动
Bridge 服务、SSH 或 EDA，不以两环境测试数量相加宣称更多电路证据。
`catalog` 与原 inverter demo `plan` 入口也通过；只生成计划，没有执行它。

## 剩余边界与停止条件

- 回执证明记录内最终上传的 wrapper/OA include 字节，未展开或归档 PDK/model include；
  literal audit 不是通用 SPICE 解释器。manifest 哈希自洽不等于独立重新求取全部指标。
- 本轮不重解码 PSF，不能由输入重核替代性能/工作区/角落验收；状态恢复也不等于
  OA 二进制逐字节恢复。历史 A 的保留 si 恢复与 B/C 普通 worker 路径区别仍保留。
- completion 当前支持明确参数恢复及其完整平面回读；不宣称任意层级/共享依赖/拓扑
  变更的恢复全部闭合。引用要求现版本 task/run token，不伪造历史版本绑定。
- 只凭文件路径/哈希与时间不能证明当前 OA 归属、无人编辑或资源已关闭；本次收尾
  状态不包含一次性 cleanup helper 的结论。下一次真实执行仍需新鲜状态和明确授权。
- 旧点仍不足以可靠校准偏置/负载/尺寸的多个独立灵敏度与未见留出，不硬拟合，也不
  追加电路/仿真补数据。本次是证据到决策接线完成，不是完整 L5B 质量闭环。

以上风险路径本地实现与旧文件重放通过即停止，不为该接线追加远端 Gate。
