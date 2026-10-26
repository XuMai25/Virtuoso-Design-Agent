# 给接手此仓库的 AI agent

目标：让用户给定大致拓扑后，能够解释、微调、验证并交付可编辑设计；不要恢复其他产品或重搭工具框架。
VDA 没有内置 LLM，任何能读文件、运行 Python 并遵守授权边界的 agent 都可作为上层协作者。

## 首次接手，按此顺序

1. 读仓库 `AGENTS.md`、[README](../README.md)、[安装](getting-started.md)和[换机清单](environment-porting.md)。
   AGENTS 中含维护者服务器约束；它不是使用他人账号或工作目录的许可。有冲突先报告，不能静默改掉约束。
2. 核对 cwd、Git 分支/commit、已有改动和 Python；保留用户文件，不打印秘密，不照抄历史命令的绝对路径。
3. 运行离线 catalog/plan/demo，确认真正用的是 demo adapter；没有 EDA 也可以完成这一步。
4. 收集换机清单的环境表，区分 Bridge 原生配置与 VDA 本机绑定项。可读真实源码核对公开接口；
   不添加一套 SSH、SKILL 执行或 Spectre 基础设施。
5. 只读[当前基线](current-baseline.md)及本次必要的功能文档。历史验证是范围证据，不是新环境的既定事实，
   不必为了解所有历史把每个运行记录重新跑一遍。
6. 对实际设计，先确认拓扑、规格、可调/冻结范围与测量。进行物理/OP 分析，再决定是否需要有限候选，
   不随便列值后声称最优。

## 选择现有路径，不新增一套执行器

| 用户给了什么 | 先用什么 |
| --- | --- |
| 已有 OA schematic | 获准只读 inspect → onboarding-draft → 人工/agent 明确 resolution → onboarding-resolve |
| 清晰单页图片 | electrical/drawing/uncertainties 描述 → 真实 symbol 几何 → schematic-compile → 非覆盖 create/回读 |
| 已有任务/测量/历史 | design-intake 汇总；design-review/next/iterate 复用绑定证据 |
| 只改指定参数 | parameters.apply，真实旧值/范围/回读；不强制全闭环或模型拟合 |
| 需要设计微调 | 先用理论/OP/器件表；有适用域才用 physical_refinement 或局部模型，材料不足明确保留 |
| 明确多候选 | 1–3 点走普通任务；>=4 点先核算 preview 是否确实省时，再冻结 shortlist 做 OA 真值 |

精确 JSON schema／允许 operation 见 models、catalog 与子命令 help；不要编造 API。
`onboarding-resolve` 等输出的安全草案仍需另行执行授权；编译成功不是授权。

## 真正执行前必须写给用户

- 目标 library/cell/view，PDK/master/model、当前会话归属。
- 是否写 OA、是否部署/改变 daemon 或 tunnel、是否会写 scratch。
- 计算次数／预算、确切远端工作根、输出与保留范围。
- 是否可能覆盖、谁可能正在编辑、checkpoint/CAS、恢复与停止条件。

获得与当前范围一致的明确确认后才做真实动作。CLI token 与任务安全字段必须同时满足；
不代替用户自动同意真实计划。bootstrap、重启、断线后的未知写入、修改系统/PDK/网络均不由普通运行许可推导。

## 验收不能混在一起

- `user_input`：用户规格、拓扑意图、工程假设，不是假设已经成立。
- `software_inference`：demo、推理、插值、拟合、对照及计划，不能标为实测。
- `bridge_readback`：实际 OA、配置或符号读取；对象存在不代表连线正确或仿真生效。
- `eda_result`：实际绑定的网表／输入／PSF／日志／指标；return code 0 本身不够。

图面可读性、连接/网表一致性、参数完整性、仿真性能、PVT 和恢复分别报告。
发送后断线先查询原请求／作业并回读，不从头重放可能已生效的写入。
需要人工 ADE 时保留保存→独立 Load→关闭的交接，不堆窗口，不丢人工状态。

## 可直接发给另一个 agent 的 prompt

> 请在我自己的工作目录接手此 VDA checkout。先读 AGENTS.md、README.md、
> docs/agent-start-here.md 与 docs/environment-porting.md，核对实现，不照抄维护者账号、路径或历史结果。
> 先完成无需 Bridge/PDK 的离线入门，再列出我这台客户端及服务器缺少的非秘密配置。
> Bridge 使用原生安装、连接 profile 和部署机制；如需兼容修改，参考 references/bridge-compat，
> 在备份和独立分支上按实际上游核对，不整包覆盖或删除安全检查。
> VDA 硬编码目录、PDK facts、CDF/端子/网表绑定按移植清单适配，复用原任务与执行器。
> 本地代码和必要测试可先完成；真实连接/部署/OA/网表/计算前列清目标、范围、次数、路径与恢复方式，
> 等我确认。缺参数、缺证据或未知执行状态明确保留，不为证明能力重复造电路。

本提示不包含服务器权限、PDK 或凭据；用户应另行给出自己的环境和真实目标。
