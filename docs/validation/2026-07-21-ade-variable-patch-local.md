# 2026-07-21 Maestro 全局变量 CAS patch 本地实现

## 目标

让 VDA 能在不打开 GUI、不运行仿真、不修改 schematic 的前提下，对一个已有 Maestro setup 的全局 design variable 做可审计微调。每个变量都必须声明旧值前置条件；只有全部旧值匹配，才设置新值、保存一次 setup，并在全新后台 session 中再次回读。

## 实现

- 新增正交 operation `ade.variables.apply`，适用于 `existing_schematic`、反相器和共源任务。
- 任务显式声明 `expected_tests` 和变量 `name/expected_value/value`。`expected_value: null` 表示要求变量原先不存在；字符串按 Bridge 契约原样保留，不猜单位、表达式或物理含义。
- 变量名限制为安全 Cadence 标识符；值拒绝引号、反斜杠和控制字符，避免进入 Bridge `set_var` 的 SKILL 字符串边界。
- planner 将旧值/test 回读标为只读，将唯一一次 `save_setup` 标为 `remote_write`；因此真实执行仍需 token、`allow_remote_write: true`、library 白名单和 cell 前缀。
- worker 只复用 Bridge 公共 `find_open_session/open_session/get_var/set_var/save_setup/close_session`。任何已配置 Maestro session 已打开时保守拒绝，避免后台保存与人工状态并发。
- 写会话先读取全部旧值，再统一检查前置条件；任何不匹配都在 `set_var/save_setup` 前停止。每个 `set_var` 后立即 `get_var`，全部一致后只保存一次；关闭后重新打开，复核 tests 和所有持久化值。
- 对 tests + 声明变量值生成修改前后 targeted SHA-256。它不是完整 setup 指纹，不能证明未声明 analysis/output/corner 没有被外部并发修改。

## 证据语义

- 任务声明的 tests、旧值和新值：`user_input`
- 保存前、即时和独立重开后的变量回读：`bridge_readback`
- 逗号字符串被识别为“声明的全局 sweep 候选”：`software_inference`

成功只证明声明的全局变量完成 compare-and-swap 与持久化回读。它不证明同名 test/corner 局部变量没有覆盖全局值，也不证明新值真正进入 netlist 或 simulator；这些仍需 `ade.run` 的本次结果、netlist/PSF 证据和后续 scoped-variable Gate。

## 本地验证

- 模型测试覆盖缺失设置、错误 view、参数/搜索混入、覆盖请求、重复 test/变量和不安全字符串。
- planner/safety 测试覆盖旧值前置条件、单次 setup save、无 remote compute，以及完整 OA-write 授权边界。
- worker 模拟覆盖不存在变量、已有变量、多变量逗号 sweep、即时回读、一次保存、全新 session 持久化回读和 targeted 指纹。
- 失败测试覆盖旧值不一致时零写入、已有 session 拒绝，以及保存后持久化回读不一致。
- executor/subprocess 测试覆盖证据来源、精确请求映射、原始字符串保留和不可信 adapter 结果拒绝。

仓库根目录执行 `.\.venv\Scripts\python.exe -m pytest`：`203 passed in 0.67s`。

`.\.venv\Scripts\vda.exe catalog` 已列出三个 executable circuit 的 `ade.variables.apply`，并把 live 状态标为 pending。全部 `examples/tasks/*.json` 重新生成计划：`50/50 example plans passed`。新增示例计划只有 `remote_write`，没有 `remote_compute`；计划明确列出旧值前置条件、单次 setup save、独立重开回读和未检查局部 override。

## 未验证边界

- 尚未在 nics4304/Virtuoso 6.1.8 上执行真实变量 patch；`maeGetVar` 对逗号 sweep 的实际字符串规范化仍需 live smoke。
- `find_open_session` 公共 API 不能按目标 cell 过滤，因此当前会保守阻止任何已配置 Maestro session 并发存在；这可能阻挡无关 cell，但不会关闭或修改它。
- 当前只包装公共 getter 能精确回读的全局变量。Bridge 的 test/corner scoped set 能力没有被删除或禁止，但在具备等价 scoped readback 前不冒充 VDA 已闭合。
- setup save 成功而 close/独立回读或 transport 随后失败时，变量可能已经持久化；重试会因旧值前置条件不匹配而停止，不自动覆盖式回滚。
- 没有自动配置 analysis/output/spec、testbench stimulus 或 corner，也没有运行仿真。

本次没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`；第三方仓库仍保持在用户隔离分支且工作树干净。

后续同日扩展已加入 test/corner scoped CAS 与 exact enabled-corner 前置条件，见 [`2026-07-21-ade-scoped-variable-patch-local.md`](2026-07-21-ade-scoped-variable-patch-local.md)。本记录保留为首个 global-only Gate 的历史验证快照。
