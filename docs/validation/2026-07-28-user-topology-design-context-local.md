# 2026-07-28 用户拓扑 design-context 本地纵切

## 目标

把 L5B 的输入边界从“VDA 自行选择一张完整固定模板”改为“用户提供已有 schematic 或
大致拓扑，VDA 在明确边界内调整局部拓扑和参数”。本 Gate 只建立可复用的上下文、审计
和规划基础，不实现或宣称任意电路的远端 Spectre 闭环。

## 实现

- 新增 `DesignContext`：绑定用户拓扑角色、可选 expected topology SHA-256、冻结的
  instance/net/pin、实例及 semantic 参数的 `fixed/search` 权限、所需/可选 analysis、
  metric 面和局部 topology edit envelope。
- 新增通用 OA readback audit：复用已有 canonical topology snapshot，核对 role object、
  instance terminal→net、冻结对象和未过滤 readback 中真实存在的 CDF 字段。
- 对预声明 topology-delta 追加 context scope 审计：实际执行方向的每个 operation 都必须
  在 allowlist 中，目标必须属于对应 mutable object 集合，operation 数不超预算；master
  migration 的 CDF 写入还必须具备 fixed 参数权限。
- `TaskSpec` 对带上下文的固定/搜索 semantic 与 raw instance 字段、analysis 和 metric 做
  plan 前拒绝；没有上下文的独立 `parameters.apply` 不受新权限面限制。
- planner 在第一次只读 OA inspect 后、任何后续远端动作前插入
  `design.context.bind`。executor 保存完整审计 action，证据类型为
  `software_inference`；原始 schematic inspect 仍为 `bridge_readback`。带上下文的
  close-loop 必须使用已有 schematic，`create_if_missing=true` 会在 plan 前拒绝；拓扑修改
  也必须提供显式 `topology_delta`，旧专用 transform 不能绕过 scope audit。

## 本地验证

执行：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

结果：`745 passed`。

新增 11 项测试覆盖：

- 同一审计器绑定命名和结构不同的单端级与差分级；
- 端子连接漂移、CDF 字段缺失和越权参数写入拒绝；
- 允许的人工参数修改保持可用；
- topology-delta operation/target scope 拒绝；
- 带上下文的隐式专用 transform 与 close-loop 模板创建拒绝；
- plan 中 context audit 位于参数写入之前；
- executor 的 audit action 明确标为 `software_inference`；
- close-loop 的 analysis/metric 意图越界在任务验证阶段拒绝。

## 证据与边界

- 本 Gate 没有连接 Bridge、访问远端、写 OA、生成 `si` 网表或运行 Spectre。
- 测试中的结构和审计结果是 `software_inference`，不是电路性能证据。
- 现有反相器、共源和差分对真实 adapter 未被替换，旧任务行为保持，完整回归通过。
- `existing_schematic` 当前仍未开放 `simulation.run/design.tune/design.close_loop`；缺少的是
  通用 testbench/source/load/signal/OP-save 契约及 raw `si` 参数一致性解析。完成该 worker
  并在一个没有专用 circuit adapter 的新 cellview live smoke 前，不能称为跨电路 L5B。

## 下一道 Gate

为 `existing_schematic` 增加首个通用 OA→`si` DC/AC 纵切：用户声明安全的 voltage/current
source、load、输入/输出差分表达式、需要保存的 MOS OP 和 OA-CDF→netlist 参数绑定；worker
复用现有 `_generate_oa_netlist`、Spectre runner、进程 guard、manifest 和指标提取，不修改
Bridge。先本地验证空波形、缺 signal、参数漂移和中断边界，再单独列出目标 cellview 与
远端路径申请 live smoke 授权。
