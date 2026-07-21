# 2026-07-21 Maestro scoped 变量 CAS 扩展本地实现

## 目标

把 `ade.variables.apply` 从 global-only 扩展到 global、test 和 corner 三种 Maestro design-variable scope，同时继续满足“先读全部旧状态、任何不一致零保存、写后独立重开回读”的证据门槛。该能力用于自动微调已有 setup，不创建 test/corner，不运行仿真，也不改变 schematic、analysis 或 output。

## 接口依据与第三方边界

- 本机 Bridge 0.7.0 的 public `set_var` 已支持 `type_name/type_value`，其注释明确列出 test/corner 写法；public `get_var` 仍只包装 global getter。
- Cadence 官方社区给出的后台读法是 `maeGetVar(name ?typeName "test" ?typeValue testName)`，并说明 `maeGetVar/maeSetVar` 可用于 global、test 和 corner。相关说明见 [Getting variable names from schematic view](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/19344/getting-variable-names-from-schematic-view/1365398) 和 [Design variables in ADE](https://community.cadence.com/cadence_technology_forums/f/custom-ic-skill/36321/design-variables-in-ade---skill-functions)。
- VDA 的 scoped 写入复用 Bridge public `set_var`；scoped 读取通过 Bridge 的 SKILL channel 调用 Cadence public `maeGetVar`。没有复制 SSH、会话或文件传输实现，也没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`。

## 契约

- 每个 update 显式声明 `name`、`expected_value`、`value`，以及 `scope: global|test|corner`；test/corner 还必须声明一个 `scope_name`。
- 任务必须声明 exact `expected_tests`。出现 corner update 时还必须声明 exact enabled `expected_corners`，且 scope name 必须属于相应列表。
- 同名变量可分别出现在 global、test 和 corner；同一 `scope + scope_name + name` 不能重复。证据 key 分别为 `name`、`test:<test>:<name>` 和 `corner:<corner>:<name>`。
- Maestro test/corner 名只拒绝引号、反斜杠、控制字符和超长字符串，不再强行限制为简化标识符，因此不会无故屏蔽 Bridge 可接受的冒号、空格等名称。
- worker 拒绝与任何已配置的开放 Maestro session 并发保存。写会话先精确核对 tests、可选 enabled corners 和全部声明 scope 旧值；任何不一致都在首个 `set_var` 前失败。
- 每项写入后立即以相同 scope 回读。全部一致后只调用一次 `save_setup`；关闭后用全新 background session 再次核对 tests/corners/所有目标值。
- 请求字段标为 `user_input`；旧值、即时值和持久化值标为 `bridge_readback`；逗号字符串被识别为 sweep 声明仍只属于 `software_inference`。

## 本地验证

- 模型测试覆盖 global/test/corner 混合更新、同名跨 scope、缺少 scope name、scope 不属于 expected tests/corners、重复 scoped identity、不安全 selector，以及带空格/冒号的合法名称。
- planner 测试覆盖 exact enabled-corner 前置条件、scope identity 披露、单次 setup save、无 remote compute 和未声明 override/仿真生效边界。
- worker 模拟覆盖 global public getter、test/corner scoped getter、Bridge public scoped setter 参数、全部旧值先读后写、一次保存、全新 session 持久化回读和 scoped sweep 记录。
- 失败测试覆盖 corner membership 不一致时零变量 I/O/零保存、旧值不一致时零写入、已有 session 拒绝，以及保存后持久化回读不一致。
- executor/subprocess 测试覆盖 exact scope 映射、三阶段 readback、默认 global 字段序列化和伪造/不一致证据拒绝。

最终本地回归：

- `.\.venv\Scripts\python.exe -m pytest`：`216 passed in 0.56s`。
- `.\.venv\Scripts\vda.exe catalog`：三种 executable circuit 均列出 `CAS declared-scope variable patch (live pending)`。
- `.\.venv\Scripts\vda.exe plan examples\tasks\existing-maestro-scoped-variables-apply.bridge.json`：计划只要求 remote write，不要求 remote compute，并明确 tests/corners/逐 scope CAS、单次保存及未验证边界。
- `examples/tasks/*.json`：`51/51` 个任务均可生成计划。

## 未验证边界

- 尚未在 nics4304/Virtuoso 6.1.8 上执行真实 test/corner scoped patch；尤其是 corner `maeGetVar` 的 `typeValue` 形式和缺失变量返回值仍需 live smoke。
- Cadence 资料指出从 history 读取带 sweep 的 test 变量可能只返回一个值；本 operation 读取当前保存 setup，不读取 history，但逗号 sweep 的实际持久化字符串与逐 point 展开仍必须由 live `ade.run`/产物证明。
- exact `expected_corners` 只核对 enabled corner membership；不会创建、删除或启停 corner，也不会检查未声明变量和 corner model-file 内容。
- 成功只证明声明 scope 的配置值被持久化。未声明的 test/corner/global override、变量优先级、netlist 中的最终值以及 simulator 实际使用值均未验证。
- setup 保存成功后若 close、重开或 transport 失败，远端可能已经持久化；旧值前置条件会阻止盲目重放，但当前没有自动回滚。
- analysis CAS 与 output/spec add-only patch 已在后续本地 Gate 实现，见 [`2026-07-21-ade-setup-patch-local.md`](2026-07-21-ade-setup-patch-local.md)；两类 patch 的 nics4304 live、background netlist/PSF 捕获和有限 corner 规格闭环仍待验证。
