# P1-a 接入与决策上下文本地验收（2026-10-23）

## 范围

按用户要求复用已有原理图配置、测量和运行记录，只修改本地代码、示例与文档。
没有连接服务器/Bridge，没有 OA 写入、新建电路、仿真、GUI 或 Bridge smoke；未修改
第三方库、系统、网络或 Vault。没有为测试新造电路或重新跑候选。

在 `codex/p1a-design-intake-20261023` 分支开发；原基线 tag 不移动，不自动推送。

## 实现

- 新 `design-intake` 引用普通 existing_schematic task，或直接复用 onboarding resolver
  解析 saved draft+resolution；从原契约导出角色、变量/冻结范围、候选、规格及预算。
- 测量仍由原编译器校验。只抽出无临时文件、无输入修改的组合函数；原 CLI 和契约
  保持兼容，不复制 executor、Bridge 或求解器。
- 请求额外记录有来源的设计理由/疑点；未知目标、缺测量、疑点或不完整执行不生成
  草案。历史身份、checkpoint 和建议沿用 review/next，不自动改参数或恢复。
- JSON/Markdown 绑定实际输入字节 SHA；结束时再次核对。草案的 compute/write/
  create/replace 全关闭，白名单及原 planner 保留。相同输入不表示当前 OA 已确认。

## 旧记录重放

使用原有目标配置 `vb_pdk_smoke/vda_pmos_loaded_cs_onboarding_001/schematic`、
`nics4304_tsmc28`，**只是读取本地保存文件，并未访问此远端目标**：

- task：`examples/tasks/pmos-loaded-common-source-onboarding-tune.bridge.json`；
  字节 SHA-256 `3c6f894ec232cd21572050a1d210d546ce6c2c352fe875e08b32f6be731cb48c`。
- measurements：`examples/measurements/pmos-loaded-cs.json`；
  SHA-256 `1bf2b0d81ac74b72bc497c40ccd4d610d80d61237c7b8d51dada8aa9cd36f145`。
- run：`artifacts/runs/vda-pmos-loaded-cs-onboarding/13-onboarding-tune-resume2-20260731.json`；
  SHA-256 `5d838f937a0e19ea750e9dd5f68832121fac7ed7c2fd58139dc217f2be9fcd8a`。

绑定通过原 current-plan-token 规则，未制造历史 audit。记录含 3 个候选、原选择为候选1，
原指标仍标 eda_result；此次整理/解释为 software_inference，没有新电路结果。
新增明确 measurement_plan 后 analysis 指纹不同，摘要显式记录该差异且
current_metrics_reused=false。原设计状态未确认，不能拿这些指标完成新的规格判定。

输出为普通安全 task，计划 token `73c8fc4ffeb5369d`，只是规划指纹，不是执行授权。
现有测量编译器给出至多6次主候选分析的上界；这是未来原域的规划计数，**本次实际
执行0次**，且该上界不包括 OA/回读、winner 复核、重试或 agent 时间。
原 history 的 design-next 判断为 review_or_stop；不因此扩域或追加搜索。

可移植示例为 `examples/intake/pmos-loaded-cs.json`，不依赖 Git 忽略的旧 run。
带历史的本机请求为 `artifacts/design-intake-20261023/pmos-loaded-cs-history.json`。
最终摘要/安全草案放在同目录的 `context.json`、`context.md`、`task.json`；运行资料
不提交。原始来源内容和历史记录未改写。

## 本地验证

| 验证 | 实际结果 | 边界 |
| --- | --- | --- |
| 开发前 onboarding/measurement/review 回归 | 109 passed | 原有本地基线，不是新 EDA |
| intake 与共享路径针对性回归 | 133 passed | 包含24个新增风险用例，不与完整回归相加 |
| 完整主环境回归 | 1153 passed，1 skipped | skip 为未安装 virtuoso_bridge 的 drawing 模块 |
| Bridge 环境 drawing 假客户端回归 | 51 passed | 包含与主回归重叠项；实际安装 builder，但不连接 EDA |
| catalog 与原 inverter demo plan | 通过；原 token 815e068f84bd532e | 计划未执行；原正交入口保留 |
| 旧共源配置/run 本地重放及普通 plan | draft_ready，来源/差异/零权限检查通过 | 历史指标不是本次测量 |

针对性测试覆盖：原测量编译器结果一致且不改输入；相对路径；已有 measurement_plan
复用；onboarding exact SHA；未声明分析拒绝；未知角色/参数/指标和理由来源伪装拒绝；
疑点/缺定义不出草案；无覆盖及多输出冲突；target/PDK/plan/checkpoint 不一致；
pending write/中断先对账；demo 来源不升级；搜索域变化被标记；调用期间源文件变化
拒绝；无真实 adapter 构造。示例直接使用已有3点域，不产生新的性能点。

本地测试命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest `
  tests/test_design_intake.py tests/test_measurement_iteration.py `
  tests/test_onboarding.py tests/test_design_review.py `
  --basetemp artifacts/test-temp/p1a-targeted-acceptance-20261023 `
  --junitxml=artifacts/design-intake-20261023/pytest-targeted-acceptance.xml

.\.venv\Scripts\python.exe -X utf8 -m pytest -o addopts='-p no:cacheprovider' -q -ra `
  --basetemp artifacts/test-temp/p1a-full-final-20261023 `
  --junitxml=artifacts/design-intake-20261023/pytest-full-final.xml
```

Bridge 环境本地补验只运行 `tests/test_schematic_drawing.py` 和
`tests/test_schematic_drawing_bridge.py`，记录为同目录 `pytest-bridge-local.xml`。
没有把本地测试数量或处理时间当作设计性能/自主等级证据。

## 未验证与下一步

P1-a 的本地最小闭环已完成；它不自动理解所有新拓扑，不证明设计理由正确，不解决
未知 CDF 的单位/数值语义，不迁移 ADE state，也不验证当前 OA 或 PDK 文件。
没有端到端 agent/人工返工成本测量，不能报告省时倍数或 L5B 质量闭环。

下一次实际模块可直接用此入口整理现成 task/测量/历史，先判断是否还需要新代码或
计算。若需要 fresh inspect/EDA，再列明 library/cell/view、读写范围、计算次数、scratch
路径、会话归属和风险申请确认；不为验收 P1-a 新建电路或重跑全域。
