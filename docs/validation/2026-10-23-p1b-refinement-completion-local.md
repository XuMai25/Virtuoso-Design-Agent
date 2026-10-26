# P1-b 微调决策与反馈接线：本地完成记录

日期：2026-10-23。性质：本地软件实现、旧真实记录重放；不是新 EDA 验收。

## 范围与停止条件

用户要求逐项推进，常规实现自行处理，不反复请求机械步骤确认。本轮聚焦已有五个
实际缺口：收益是否值得、数据不足诊断、非等值联动、实际输入一致性、失准后刷新。
复用原 OP/MNA、局部拟合/留出、design-next、TaskSpec、executor 和 Bridge 公共 runner；
没有新 operation、求解器或架构。使用 karpathy-guidelines 约束为必要接线与风险测试。

没有新建或修改电路，没有远端连接、OA 写入、Spectre 计算、Bridge smoke、GUI 或
网络操作；未改系统、PDK、cds.lib、Vault 或第三方 Bridge。用户原有 AGENTS.md
未提交修改保留，不纳入本轮提交。固定基线标签仍为99408fb5，不移动。

## 五项实现与边界

| 缺口 | 已实现 | 不能据此宣称 |
| --- | --- | --- |
| 预测有提升就继续 | design-next 对照目标的历史留出绝对残差；可声明最低绝对/相对改善、建模约束余量及 action 预算 | 残差是误差界/置信区间、action 时间包括 agent/人工时间，或通过就值得远端执行 |
| 只有“不足”错误 | op-relinearize --check-only / design-next --data-policy 报缺方向、共线、重复留出、可固定轴和旧单轴切片 | 够数等于可校准；自动改 split、拼 run、生成补点或把无数据灵敏度设零 |
| 只能等值联动 | CDF linked_parameters / testbench linked_names 支持显式 scale/offset/成员 grid；一组仍为一个自由度，输出逐成员实际值 | 任意表达式、自动识别匹配/宽度比、跨 CDF/wrapper 的隐式关系，或底层跨实例事务 |
| 保存任务值不等于 simulator 输入 | 独立检查 VDA literal wrapper；已有 timeout launcher 核对 Bridge 最终上传副本及 OA include，下载后核对同作业非覆盖回执 | 通用 SPICE 解析、展开模型/PDK、独立证明全部 analysis 设置，或已完成新 guard 的 live 验收 |
| 模型失准只剩报告 | --prior-model/--prior-policy 重算新点误差，区分执行/精度/推荐，给出新锚点与显式刷新路径，可接已有合格的新模型 | 旧模型跨拓扑/PDK/工作区转移、自动放宽误差门、旧失败因 EDA 选优成功而消失 |

显式收益策略示例见 examples/theory/refinement-decision-policy.template.json。其中1%
是 software_inference 的演示值，不是用户规格或默认收益要求；不提供文件时原默认
绝对/相对改善门为零、历史残差倍率为1。输出仍是原零执行权限草案。
有 --task-output 而被停止门挡住时返回2、保存诊断、不写空任务或改掉已有任务文件。

## 旧记录重放

本机报告：artifacts/p1b-completion-20261023/results-r3/summary.json；原始 source hash
及逐项输出保存在该目录。保留文件不随 Git 提交，不在缺失时造假 run 代替。

1. **Cascode 与刷新共源模型保持完整一致。** 新结果与上一轮完整 typed 对象相等；
   按原 Windows CRLF 序列化后的完整字节也相等，不只比较候选数量：
   - cascode：8c4eb206d6c1aaa9125518228f6860cb0c56186770f5d19e91341998b1d84917，2候选。
   - common：5d80654c78866c6225492e54130b79d945049465aca91bbe3893d73c37de6bcb，6候选。
2. **真实旧预测失败没有被抹去。** 原共源六点执行完整且推荐一致，但
   output_swing_margin_v 的旧预测超限，新的 prior-audit 仍为
   local_model_refresh_required。历史身份仅引用原 exact-byte audit，全部60项误差
   重新计算，没有继承其精度判定或改 token。
3. **显式新模型可继续。** 引用已有 W/RD 二维刷新模型，固定 RS，留出门和默认收益门
   通过后产生原6候选零权限草案，token仍为891e81d48ffdcb4b。目标最大预测改善约
   250.665 MHz、历史目标留出绝对残差约36.144 MHz；action 粗估约585.85秒，只是旧
   action 均值乘候选数。不是新测收益或总耗时。换成示例1%门后，约0.729%预测改善
   被判 predicted_gain_not_resolved，不出任务。
4. **PMOS 数据不足仍不足。** 原3点的 Wp/VBP 有变而 CL0 固定2 fF。声明三个独立轴后
   缺少4个独立训练点的下界、负载训练/留出以及宽度留出方向；两点偏置切片也不足以
   带独立留出拟合。只生成诊断，无模型、候选或新计算。
5. **旧真实输入可以离线审查。** 先复核原 STB 持久证据清单和SHA，再检查其中原
   wrapper 的5个源、1个负载、端子/数值和 OA include，结果 matched；wrapper SHA
   为c0232acbbd644d1b45965f483883138ee8d905abd9087fd4fc01c360004c2b89。
   这是旧文件复核，不是新 STB，旧作业没有新的执行输入回执，不事后补造。

上述原 task/run/model/audit 字节的前后 SHA 均不变。最早 semantic-only policy 的
include_measured_anchor 后加默认曾使原 canonical hash 不匹配；已用 Git 旧源码确认
形状，验证器仅兼容这个已知遗漏、原 result 未存逐 metric floor 的情况。精确旧哈希
仍必须匹配，显式新选项或变更 floor 继续拒绝；不影响新拟合或重建旧候选域。

## 本地验证

完整入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent.local_validation `
  --output artifacts/local-validation/20261023-p1b-completion-final2
```

最终通过：主环境1268 passed，1个已声明的 Bridge import-only 模块留待补验；
Bridge 专用环境8 passed、0 skipped，总状态 passed。没有未知 skip。catalog 和原
inverter demo plan 通过；真实旧共源加1%示例门的 CLI 返回预期2，保存诊断但未生成
task-output。结果记在该目录 summary.json、两份 JUnit 和日志中。测试围绕：小收益不出任务、
预算/未知成本、训练独立性/真实坐标留出、比例/偏移/网格/AC保留、旧哈希/失准刷新，
以及实际启动脚本遇最终副本/依赖漂移/错 raw 目录/已有回执时先拒绝。
本地 shell 测试确实运行带 timeout 的脚本和替身程序，不调用真实 Spectre；Windows
隐藏启动，不增加窗口。所有结果仍属于 software_inference，不作为电路性能证据。

一次手工单环境定向测试因系统 Temp 目录权限在 fixture setup 失败；没有改权限或
系统配置，改用现有本地验收入口的工作区专用临时目录后通过。这不是产品功能失败
或修复系统权限的声明。

## 下一次真实任务的最小验收边界

本轮新 launcher/回执的实机证据、联动字段经过真实 callback 后的逐成员 OA→si
一致性、新点收益，以及其他电路/PDK 的模型适用性仍未完成。

优先在下一次实际需要的既有 generic 模块任务中合并验收，不为回执或分级另造电路、
重复 STB/全分析或扫描全域。执行前一次列明确切 library/cell/view、当前 PDK/模型、
参数范围、OA写入/恢复策略、计算上限、scratch 和不覆盖风险并获批准。发现未知当前
状态、PDK/拓扑漂移或不确定写入时先对账，不猜测或追加仿真。

独立 literal audit 属于 software_inference；实际启动前文件哈希回执属于
bridge_readback；真实波形/标量保持 eda_result；明确规格与授权是 user_input。
这些软件接线完成不等于五项已取得新实机证据，更不升级为完整 L5B。
