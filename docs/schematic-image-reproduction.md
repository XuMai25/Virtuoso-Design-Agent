# 从参考图片到可追踪导线的原生 schematic

状态：**本地实现已验证；N28 实图经显式部分状态补完后，结构/实体线与 OA→si 网表通过**。
2026-10-19 获得图片、目标/占位参数和隔离会话授权后执行了首个真实 Gate；没有性能仿真。
原生图已导出，人工图面确认仍待完成；两处反馈标签跨接和局部 PDK 文字拥挤没有隐藏。
完整失败、恢复与范围见[真实验收](validation/2026-10-19-n28-image-latch-native.md)。
下文 `differential-pair.local.json` 仍只是虚构几何的软件示例，不是这次图片或 TSMC18 证据。

## 1. 入口与范围

Agent 先理解图片，整理可审阅 JSON；确定性代码检查电气意图和图面路径，再使用已有
`schematic.create` / `schematic.inspect`，新增 circuit 选择 `schematic_drawing`。
没有 OCR、自动电路识别、自动布线器、云端模型、Web UI 或新执行框架。
原有固定模板、已有图的参数修改、拓扑 delta、仿真和 checkpoint 操作不变；
不自动重画现有人工原理图，也不把所有模板立即切换成新路径。

首版限定单页、最多 64 个实例、128 个网络、64 个标量顶层 pin、256 条显式路径、
合计最多 1024 段正交导线。坐标为 Virtuoso user units，x 向右、y 向上，不是图片像素。
标量名字只接受字母/数字/下划线及 `$!`，暂不处理总线、数组、斜线和非正交旋转。
顶层 pin 名须等于其 net 名。间距可为实际 PDK 符号调整，功能分组、支路次序和信号方向需人工审阅。

## 2. 三部分中间描述，加一份 PDK 几何目录

完整可审阅示例：

- [differential-pair.local.json](../examples/schematic-drawing/differential-pair.local.json)：电气意图、图面和疑点。
- [symbols.local.json](../examples/schematic-drawing/symbols.local.json)：**虚构** master 和几何；不是 PDK 测量。
- [readback.local.json](../examples/schematic-drawing/readback.local.json)：独立手写的回读测试夹具；不是 OA 输出。

| 描述部分 | 字段 | 审阅重点 |
|---|---|---|
| `source` | 图片路径/来源、SHA-256、解释备注 | 真实图片须有哈希，避免换图后沿用旧计划 |
| `electrical` | instances/master key、命名 terminal→net、parameters、nets、pins | 连到了谁，而不只是画在哪里 |
| `drawing` | placements/orient/group、pin placements、routes、junctions、labels、grid | 实体导线怎样走，哪些交点是分支 |
| `uncertainties` | category、question、resolution | 模糊连接、参数、master 和图面疑点是否确实解决 |
| 独立 `symbols` 文件 | profile、证据来源、真实 master、命名端子中心、selection boxes | 不按器件常见画法猜顺序或坐标 |

例如示例的公共尾节点以如下显式路径表示，而不是三个同名短线标签：

```json
{
  "routes": [
    {"id": "tail", "net": "TAIL", "points": [
      {"instance": "M0", "terminal": "S"}, [0, 0],
      {"instance": "M1", "terminal": "S"}
    ]},
    {"id": "tail_branch", "net": "TAIL", "points": [
      [0, 0], {"instance": "RTAIL", "terminal": "PLUS"}
    ]}
  ],
  "junctions": [{"net": "TAIL", "at": [0, 0]}]
}
```

这是完整 JSON 中图面字段的摘录，不能单独作为任务。`M1` 使用 `MY`、`RTAIL` 使用 `R90`；
端点由命名端子和实际方向变换求出。示例的尺寸、电阻、假 NMOS/电阻 master 只用于软件测试，
不得作为设计初值或改名后冒充真实工艺证据。

缺失 master/terminal/net/参数可用 `null`，并写入 `uncertainties`。每个器件必须显式
`parameters_complete: true` 且提供 `parameter_review` 后才允许编译；这是审查者的完整性声明，
**不是自动掌握所有 PDK 必填 CDF 字段**。缺失尺寸、偏置、模型选择不得用默认值补齐。
parameter review 应列清要求的字段；确实无参数的器件也要说明原因。
任何未解决疑点都会阻止任务输出，不仅限于拓扑疑点。参数值使用字符串，保留单位/表达式。

## 3. 本地编译和独立比较

以下命令全部离线，不初始化 Bridge，也不访问 OA：

```powershell
.\.venv\Scripts\python.exe -m virtuoso_design_agent schematic-compile `
  examples\schematic-drawing\differential-pair.local.json `
  --symbols examples\schematic-drawing\symbols.local.json `
  --output artifacts\schematic-drawing-my-review

.\.venv\Scripts\python.exe -m virtuoso_design_agent schematic-compare `
  examples\schematic-drawing\differential-pair.local.json `
  --symbols examples\schematic-drawing\symbols.local.json `
  --readback examples\schematic-drawing\readback.local.json
```

输出目录必须不存在。成功输出 `review.json`（路径解析、连通分量、哈希、验收状态）
和 `preview.svg`。SVG 的虚线框是 symbol selection box，不是原生 PDK 符号，更不能替代
Virtuoso 图面验收。语义检查不通过则输出 blocked review，不生成执行任务；JSON schema
本身错误会直接报错。这里的示例即使提供 `--library/--cell` 也不能输出 `task.json`。

只有真实 image 来源、Bridge 几何读回、疑点已清除且给出 `--library LIB --cell vda_NAME`
时，才输出普通 `TaskSpec`。初始 `allow_remote_write=false`、`allow_remote_compute=false`、
`replace_existing=false`。它是待批准的计划输入，不是执行授权。
随后仍用原有 `vda plan task.json` 和 `vda run task.json --adapter bridge --execute --token ...`；
批准后改变安全字段必须重新 plan，旧 token 不再有效。此文中的占位命令不得直接运行远端。

比较输入包括独立 `schematic`、`wire_polylines` 和带 layer/purpose/net 的 `wire_records`。
只逻辑连接正确、只有线的位置，或者缺少导线网络绑定，都不能得到完整比较通过。
允许一条长线与多段同网短线具有相同几何覆盖，不要求 OA 对象分段与编译器完全相同。

## 4. 连线语义与真实 Bridge 接入

本地检查：命名端子必须与真实目录一致；8 种 OA 旋转/镜像后端点落在声明网格；
导线正交、非零长；同网三路以上分支必须显式声明 junction；禁止悬空末端、孤立导线、
悬空标签和跨网端子触碰。主要 `signal` net 必须是一个连续实体导线分量。
只有明确的 power/ground/cross_region net，附有 `label_connection_reason` 且每个分量有
连接标签或同名 pin，才允许分散连接。

**首版拒绝不同网络的任何几何交叉、重叠和相切**，不猜 Cadence 的跨线语义。
图片中确实不相接的交叉应显式绕开，或审阅后改为 cross_region 标签；不能静默补点。
相同网络交叉且四向连通时必须声明 junction，再拆分为共端点线段。
这不是通用自动布线，不会自动保证线避开所有符号笔画；原生图的遮挡、标签密度和可读性仍要人工检查。

复用当前本机 Bridge 的实际公开能力：

| 能力 | 复用位置及边界 |
|---|---|
| PDK 端子几何 | `client.symbol.read_ports(lib, cell, view=...)`，读命名 scalar terminal bbox 中心和 selection boxes |
| 实例/pin/线/标签 | `virtuoso.schematic.ops` builders；线用 `route_style="draw"` 保留顶点；pin 保留 direction |
| 批处理与清理 | `client.execute_operations`、现有 editor cellview 生命周期；不增加 SSH/传输实现 |
| CDF 回调 | VDA 既有 `_set_target_instance_params`；`param_filters=None`，最终独立比较要求的参数 |
| 结构回读 | `client.schematic.read(... include_positions=True, include_param_evidence=True, param_filters=None)` |
| 图面回读 | 公开 `read_placement`，并补充只读 net/layer/purpose 绑定查询 |

Bridge 已有 manifest importer/solver，但其流程可重路由 terminal escapes、使用逻辑端子
绑定，且该版本 manifest pin helper 固定 `inputOutput`。本入口优先复用更底层的公开 builders，
保留已审阅路线和 pin 方向；不复制 importer/布局 solver，更不修改 Bridge。

两个具体公开接口缺口以最小受控 SKILL 接入：

1. `read_ports` 是 master 几何，不是 CDF callback 后的 placed instance 几何。在画线前用
   `dbTransformPoint` 核对真实实例端子中心；变化即停止，不能猜偏移继续连线。
2. `read_placement` 缺少导线 net/layer/purpose。通过现有 `execute_skill` 只读取得这些字段，
   `unwindProtect` 关闭本次引用；只接受 wire/drawing 的 line/path，label 不算导线，
   原生 `schCheck` 产生的 netless ellipse junction 圆点不计为导线，未知形状仍拒绝。
   避免“装饰线 + 标签/圆点”冒充实体电气连线；覆盖和网络绑定只使用真实 line/path。

新建步骤：重新读取 master 几何并匹配 → 同一批次检查目标不存在后用 append 模式创建实例
→ 现有 CDF setter → 实例真实端子几何检查 → pins/实体线/明确标签 → check/save/close
→ 独立结构与线网比较。没有 destructive mode w、没有删除目标、没有 `dbCreateInstTerm`
兜底伪造预期连接。读取参数优先完整 CDF evidence，数值只作严格单位归一比较，表达式须匹配。

新操作不实现多阶段自动 resume；原有调优 checkpoint/resume 不变。中途失败可能留下只含
实例或部分参数的**新** cellview，必须保留并只读检查；重跑 create 会因目标已存在而拒绝。
修复需另列变更计划或另选新目标，不能超时后删除/覆盖/自动重放。CDF setter 沿用既有
目标窗口聚焦方式，本次 N28 Gate 使用隔离会话内的目标窗口并在结束后清理自有会话；
不关闭已有人工窗口，不把这个结果推广为所有多用户/多会话场景均已验证。

## 5. 分开的验收与下一次真实 Gate

| 验收 | 当前可证明 | 尚不能证明 |
|---|---|---|
| 图面 | 本地坐标检查；本次 N28 原生截图和实际导线已取得 | 两处跨接仍标签，局部文字拥挤，人工逐块对照待确认 |
| 电气连接 | 本次 N28 15 实例/13 nets/8 pins、线网绑定及实际 si 网表一致 | 其他图/PDK，以及修复后的 fresh-create 单次全链不据此宣称全通过 |
| 参数 | 本次批准的 W/L/fingers/simM/Cp 共 54 项进入实际网表 | 这是占位参数，不是原图尺寸、所有 CDF 参数证明或性能设计 |
| 仿真性能 | 无 | DC/AC/transient 或任何规格，不附带自动仿真 |

证据分类：图片及用户明确尺寸/接线确认是 `user_input`；agent 解读、预览和比较结论是
`software_inference`；真实 symbol/OA 读回是 `bridge_readback`；未来实际 simulator input/results
是 `eda_result`。JSON 的 evidence 字段不是签名认证：不能靠手改字段把夹具变成真实证据。
真实写入前再次读 PDK 几何可发现错 master/漂移，仍必须保留原始来源及审阅记录。

用户提供图片后按以下次序做一次小而完整的验收，不扩展随机电路测试：

1. 确定图片、PDK profile、library/新 `vda_` cell/`schematic`；列清模糊交叉、器件映射与缺失参数。
2. 经允许只读访问后，用 `capture_symbol_catalog(client, request, provenance=...)` 读取所列 master。
   初稿可仅列 master、空 terminals；它不能编译。捕获函数只读 Bridge，不自动连服务器，
   不猜映射。保存返回的 `SymbolCatalog`，再完善图面与端子引用、确认参数。
3. 离线编译并一起审阅 JSON/SVG，给出精确目标、是否写 OA、是否计算、远端 scratch/产物
   路径、非覆盖检查及部分失败风险。未取得本次明确授权，不运行 Bridge。
4. 批准后仅在新目标创建，独立比较 master、terminal→net、pins、参数及实体线网；保留 JSON
   和真实 Virtuoso 图，用户按输入支路/公共节点/镜像支路/反馈逐块对照。
5. 单独列出该目标 OA→si 的只读网表计划及 scratch 写入范围，获得相应执行/计算授权后核对
   实际模型、端子顺序、参数和 include。可复用既有 `existing_schematic` onboarding/网表流程，
   不把任意新拓扑硬套为旧固定模板。不需要性能验证时不运行 Spectre。

本次状态为 **N28 image schematic structure and OA-netlist consistency verified after guarded partial-state continuation; human readability acceptance and performance pending**。

## 6. 隔离会话与失败经验

同名 `tsmcN28` 可能绑定不同版本。真实 Gate 发现当前人工会话是 RF/7X2R，而批准 profile
是 5X2Y2Z；先停止，经用户批准另起仅回环、独立端口和独立 `/data/xum` 根的会话。
`VDA_BRIDGE_ENV_FILE` 可显式选择 Bridge 配置的绝对路径，传给 worker 的 Bridge runtime
env 接口；错误路径直接拒绝，禁止自动退回默认会话。不修改仓库/用户默认 `.env`。

首次执行在 15 个器件参数已保存、尚未建 pin/线时遇到 SKILL 数字括号错误。
先只读保存部分状态，修正坐标表达式并真实只读检查 56 个端点；随后单独冻结状态指纹，
仅补 pin/线/标签，没有重放 create、删除/替换目标或重新设置器件。
这不是新增通用 resume operation；以后部分失败仍须逐案盘点和另列补完计划。
本次没有为修复验证额外新建第二个 cell，故单次 fresh-create 全链仍留待实际需求验证。
