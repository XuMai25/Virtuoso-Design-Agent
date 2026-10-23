# P1-a：本地接入与决策上下文

`design-intake` 引用已保存的接入意图、测量和可选历史记录，输出一份可审阅摘要及
原 `TaskSpec` 的安全草案。它是薄接线，不是新 operation、执行器、角色识别器或
优化器；旧单项操作和 Bridge 能力不受限制。

## 最短使用方式

示例直接引用已有 PMOS 有源负载共源任务和测量，不依赖被 Git 忽略的历史 artifacts：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent design-intake `
  examples/intake/pmos-loaded-cs.json `
  --output artifacts/my-intake/context.json `
  --markdown artifacts/my-intake/context.md `
  --task-output artifacts/my-intake/task.json
```

所有输出必须是不同的新文件；输入、配置和旧输出不覆盖。输入路径相对**请求文件
所在目录**解析。示例沿用原 3 点候选、规格、PDK 和 testbench，不推荐新值，也不会
运行这 3 点。agent 可先读 Markdown，需要细节再定位 JSON 或带哈希的原文件，减少
每次重新拼接角色、权限、测量和运行日志；摘要不替代原始证据。

## 请求只维护引用和理由

```json
{
  "source": {"kind": "task", "task": "../tasks/existing-task.json"},
  "measurements": "../measurements/existing-measurements.json",
  "history": {
    "task": "../tasks/original-history-task.json",
    "run": "../runs/original-run.json"
  },
  "rationale": [{
    "role": "load.active",
    "text": "先解释原 DC 偏置和摆幅约束，再判断增益/带宽权衡；本次不新增候选。",
    "source": "software_inference"
  }],
  "uncertainties": []
}
```

上例路径为格式示意；可运行实例见
[`examples/intake/pmos-loaded-cs.json`](../examples/intake/pmos-loaded-cs.json)。
角色、参数权限、冻结结构、搜索域、规格和预算从原配置导出，不在请求里维护第二份。
`measurements` 可省略，但 source task 须已有完整 measurement_plan；否则只输出待补全
摘要，不猜信号、单位或提取定义。`history` 可省略，没有记录不伪造。

若已有 onboarding 草稿与确认文件，source 可改为：

```json
{
  "kind": "onboarding",
  "draft": "../onboarding/draft.json",
  "resolution": "../onboarding/resolution.json"
}
```

直接复用原 resolver 的 exact-draft SHA、真实 inspect 来源、角色/端子、参数 inventory、
层级和 binding 校验。discovery/promotion 产生的普通 task 也可接入，但不替代这些专用
证据提升步骤。尚无 inspect 时仍须先申请只读接入，不能用摘要证明未读过的 OA。

## 理由、疑点与停止

- rationale 至少一条，可不指定目标表示整体理由，也可指定一个已声明 role、
  `instance`+`parameter`、`semantic_parameter` 或 metric；引用不存在则拒绝。
- 理由来源只接受 user_input/software_inference，不得伪装 EDA/Bridge 证据。入口能检查
  引用，**不能证明理由的物理正确性或充分性**；缺模型时说明缺量，不随机补数值。
- 任一疑点、测量错误或缺理由均阻止草案。省略 `--task-output` 可保存诊断；指定它而
  草案不完整则报错，不先写半份输出。新增分析须在原设计上下文中显式声明，不自动
  扩展权限。已有人工拓扑、参数和配置不被改写。

## 历史记录的边界

history 复用 design-review/next 的原 task/run 身份和 plan 绑定；可选 checkpoint、
binding_audit 继续使用原 exact-byte 校验。旧 token 不匹配时不得制造 audit 或改记录。

2026-10-24 补齐同一审计链的转接：history 还可声明 `completion`（已有收尾描述）和
`artifact_root`（保留原文件的本地目录），例如：

```json
{
  "task": "../runs/probe-task.json", "run": "../runs/probe-run.json",
  "completion": "../runs/completion.json", "artifact_root": "../runs"
}
```

这是 history 对象的格式示意，两项均可省略；不是新增 CLI 执行选项。路径相对 intake
请求文件解析，completion 内的引用则相对 completion 文件解析。目录本身不作为文件
计算 SHA；实际输入按原回执/manifest 验证。新 `history.input_evidence`、
`history.input_artifact_root` 和 `history.completion` 原样保留审计范围，关联 task/run
的精确文件引用也纳入 sources 与结束时的哈希复核。没有复制另一套校验器。

若已记录“探针 B 后恢复 A”，intake 保留 B 的历史指标和实际回读，但不自动沿 B 发出
新任务；issues 要求显式改用 baseline/final task/run 重新定锚，task_draft 为 null。
没有最终 testbench 证据也按未知处理。检查通过后显式引用 C 是新的本地输入选择，
不是自动恢复现场或继承 B 的目标。输入/恢复证据损坏或 pending checkpoint 仍先对账。
回执的 verified、recorded_not_replayed、not_recorded、incomplete 含义沿用
[复盘工作流](design-review-workflow.md)，正常 cleanup 不伪装成错误或重新验收通过。

- target/PDK 不同则拒绝混入，应分别复盘。
- 比较记录中的拓扑、固定参数、testbench、条件、分析/测量、搜索域及决策范围指纹；
  改候选、规格或权限也会标明，不只比较名义尺寸。
- 展示原已选候选；没有选择时最后候选仅作诊断，不自动成为 winner。逐指标保留原
  来源，demo 只证明软件逻辑。建议只针对原运行，不是修改后设计的新结论。
- 原运行失败/证据不完整，或 checkpoint 未完成/有 pending write，先对账，不输出
  可重放任务。不自动恢复、应用历史参数、提交计算或完成 OA 写回。
- 即使指纹相同，仍为 current_metrics_reused=false，未核对当前 OA、人工状态及模型
  文件，不能用旧指标补齐新设计。输入文件结束时复核哈希，变动则拒绝；这不是跨
  进程文件锁或远端并发保护。

## 输出与执行

JSON 保存完整配置摘要、理由、历史、源文件哈希和已有测量编译器的成本上界；
Markdown 面向审阅。task_draft.semantic_sha256 是规范化模型内容哈希，不是输出字节
哈希；sources 中的 SHA-256 才绑定实际输入字节。成本上界不含 OA/回读、winner
复核、重试和 agent 思考，不能称为端到端耗时。

草案沿用原 library/cell/view、PDK、任务及候选，固定关闭：allow_remote_compute、
allow_remote_write、create_if_missing、replace_existing；原白名单和前缀约束保留。
新测量绑定会改变 task/token，原 checkpoint 不可直接套用。真实执行仍须列明目标、
写入、计算、路径和风险，确认后走原 execution-scope/plan/--execute；摘要/token 不授权。

首版只覆盖有 design_context 的 existing_schematic simulation/tune/close_loop。
不读取/迁移 ADE L 或 Maestro 会话，不把配置保存当仿真；人工交接走原指南。未知
CDF 数值语义、自动角色识别和模型适用域仍未解决，不能据此宣称通用 L5B 闭环。

见[本地实现与旧记录重放验收](validation/2026-10-23-p1a-design-intake-local.md)。
输入/恢复链的跨入口接线及 A/B/A 离线重放见
[2026-10-24 收口记录](validation/2026-10-24-intake-iteration-closeout.md)。
