# P1-b 收口：intake/iterate 的输入证据与恢复链接线

日期：2026-10-24。结论：**本地接线、同批 A/B/A 离线重放与双环境回归通过，本切片收口。**
本轮远端调用、OA 写入、网表生成、Spectre、新电路、GUI 操作均为 **0**。

## 阻塞、实现与范围

上轮 design-review/next 已能消费实际输入和已记录恢复链，但 intake 的 history 与
独立 iterate 的 rounds 尚未转交该引用；iterate 的对账门也未消费新 generic 输入
损坏判定。本次只补这两条接线，复用原 TaskSpec/RunRecord、plan 绑定、
build_design_review/suggest_design_next 和 review_evidence，不另建审计器或 runner。

- 两个历史引用契约可选 completion/artifact_root，路径相对各自请求解析；目录本身
  不计算文件哈希，原回执/manifest 的文件核对、范围和缺失处理不变。
- 摘要保留 input_evidence、input_artifact_root、completion，及 baseline/restore/final
  的精确来源，结束时复核关联文件字节未改变；不是文件锁或当前 OA 检查。
- intake 遇已恢复的 B 停在诊断，不输出任务；要求显式选 baseline/final 重新定锚。
- iterate 遇输入/恢复证据不一致或 pending，仅走原只读 inspect 草案，不发调优/仿真
  草案。已恢复或最终 testbench 未知时为 recorded_state_requires_reanchor，
  next_action=reanchor，不读取 template、不自动恢复或替换成 C。历史 B 指标保留，
  但不继续提升为可用起点或沿用其阶段/停滞计数。
- 普通缺 analysis/objective 仍是 revalidation_required；正常 cleanup 为
  recorded_not_replayed，不能伪装成失败或新的字节验收。旧 STB、预算、规格退化、
  身份、白名单、前缀、冻结结构、授权和状态检查不放松。

未修改 CLI、TaskSpec、planner/executor、worker、Bridge、PDK、系统、Vault 或网络。
原有 AGENTS.md 未提交改动保留，不纳入本次提交。实际输入/恢复验证依然只认明确范围；
没有增加正式 operation、模型、分析或自动选点功能。

## 复用真实 A/B/A 的本地结果

原目标：vb_pdk_smoke/vda_pmos_loaded_cs_onboarding_001/schematic，TSMC N28
5X2Y2Z/top_tt。原授权和实测边界见[联合真实验收](2026-10-23-p1b-joint-input-live.md)。
本次直接消费 artifacts/p1b-joint-live-20261023 的 A-recovered/B-format-fixed/
restore/C 普通 task/run、B checkpoint，以及上轮 completion.json；不消费一次性
cleanup/helper 私有结构，不修改 token 或旧运行。

输入请求和输出保存在 artifacts/p1b-entry-wiring-20261024/（不入 Git）：

| 正式入口及请求 | 结果 | 草案/执行 |
| --- | --- | --- |
| design-intake intake-B.json | needs_definition；唯一 issue 为显式重新定锚，input verified，completion restored_to_baseline_in_record | task_draft=null，无执行 |
| design-iterate iteration-B.json | recorded_state_requires_reanchor / reanchor；B 的历史规格判定保留，但不作为后续起点 | task_draft=null，无执行 |
| design-intake intake-C.json | 显式改用既有 C：draft_ready，原 C 输入 verified | 只生成 C-safe-local-draft.json，compute/write/create/replace 均 false |
| design-iterate iteration-C.json | 原 C 声明规格重新判为 specifications_satisfied / stop | 无新任务、无执行、当前 OA 未检查 |

intake 复用仓库内原 PMOS-loaded CS 的六项测量定义；编译后的 DC/AC 测量上下文与
旧运行不同，报告标明 analysis 指纹变化，current_metrics_reused=false，不将旧结果
当作新配置结果。iterate 按原 generic AC 作业所含 DC OP＋AC 标量进行旧规格判定。
B 有 GBW 优化目标而 A/C 无目标，因此分别处理 B 和显式 C 请求，没有削弱原 campaign
目标一致性要求或将 A/B/C 强行拼成同一优化 campaign。

原八份 task/run 和原 completion 的精确 SHA 前后相同；B 的实际 475.00n 回读、三个
旧失败 action、历史指标与 A/C 恢复证据未更改。input_evidence=verified 仍只是原文件
回放；所有 execution_authorized=false，历史 current_remote_state_verified=false。
C 的零权限草案仍保留 vb_pdk_smoke 白名单、vda_ 前缀和原 design_context 编辑边界。

可在已有这些本地文件时复用以下命令；输出必须另选新文件：

```powershell
.\.venv\Scripts\vda.exe design-intake artifacts/p1b-entry-wiring-20261024/intake-B.json `
  --output <new-context.json> --markdown <new-context.md>
.\.venv\Scripts\vda.exe design-iterate artifacts/p1b-entry-wiring-20261024/iteration-B.json `
  --output <new-iteration.json> --markdown <new-iteration.md>
```

契约详见[接入工作流](../design-intake-workflow.md)和
[跨轮次工作流](../measurement-and-iteration-workflow.md)。

## 必要本地验证

复用原 test_review_evidence 的 A/B/restore/C 临时文件 fixture 和已有 measurement
定义，不新增电路测试平台。新增入口级测试覆盖：

- completion/artifact_root 相对请求解析，不把目录当文件；报告保留原回读、哈希和来源。
- 恢复 B 的重新定锚优先于预算/已达标，template 不被读取；组合 design-next --iteration
  也不能绕开；显式 C 可继续按原权限和预算生成普通零权限草案。
- 丢失回执、恢复状态漂移及 pending，即使指标成功也不能推进或形成新可行锚点。
- 正常 cleanup 不误判失败；哈希错误不留半份 CLI 输出；处理中关联文件改变会拒绝。
- 原旧路径回归继续保护缺指标/analysis 的复验分类、原目标/规格/编辑范围与停止门。

最终完整双环境入口：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m virtuoso_design_agent.local_validation `
  --output artifacts/local-validation/20261024-intake-iteration-closeout
```

主环境 **1322 passed**，1个已声明 Bridge import-only 模块交补验；Bridge 环境
**8 passed、0 skipped**，总状态 passed，JUnit/log/summary 保留在上述目录。
Bridge 环境仅执行原本地适配器模块；测试不启动服务、SSH 或 EDA，不把两环境数字
相加当作电路证据。针对性 intake/iteration/review 回归也通过，未为本次接线追加仿真。

## 剩余边界与收口

completion 是显式历史关联，不自动发现目录或追踪所有远端修改；不证明无人编辑、
当前归属、资源已关闭或 OA 二进制逐字节恢复。最终 testbench 缺失不按已通过处理。
原 PDK include 未展开，PSF 未重新解码，不将本地证据接线等同新性能或通用 L5B 闭环。
深层/shared-child、任意拓扑恢复及旧版本 completion token 仍沿上轮边界；联合模型数据
不足继续保留，不硬拟合、不补校准点。

本轮完成指定两项后停止，不新增远端 Gate 或功能。后续只有真实设计需求暴露出具体
阻塞时才另行判断；真实执行仍先列目标、范围、路径、风险并取得授权。
