# 2026-10-19 图片复现显式图面入口：本地验证

结论：**local contract/compiler/readback comparison verified; native OA/netlist Gate pending**。
本轮只授权并进行了本地代码、文档和测试。没有用户待复现图片，没有远端连接、OA 写入、
Spectre、Bridge smoke、TUN 调整或第三方源码修改；没有修改 Vault。

## 实现及原路径兼容

- 新 `schematic_drawing.py`：电气/图面/疑点契约、真实 master 几何目录、8 种旋转/镜像、
  显式正交连线、分支与物理分量审计、独立 readback 比较和本地 SVG。
- 新 `adapters/schematic_drawing_bridge.py`：调用已安装 Bridge 的公开 symbol reader、
  schematic builders、batch/清理与结构读回，复用 VDA CDF setter；补充两段窄范围查询/
  检查脚本，分别核对 placed terminal 和导线 net/layer。没有复制 SSH/传输基础设施。
- `TaskSpec/planner/subprocess_bridge/bridge_worker/catalog` 接入新 circuit 的
  `schematic.create/inspect`；任务走原授权/token、library 白名单、cell 前缀及 run record。
  仅非覆盖创建，不附加仿真。旧模板和人工已有 schematic 没有批量重写。
- executor notes 明确成功范围；demo 不伪造这一新路径的 OA 回读。
- 离线入口 `schematic-compile` / `schematic-compare`，未知/未完成描述不输出执行任务；
  真实来源编译出的 task 也默认禁止写入。文档见[使用说明](../schematic-image-reproduction.md)。

## 实际通过的本地验证

| 验证 | 实际结果 | 证明范围 |
|---|---|---|
| VDA 完整 pytest | **1024 passed, 1 skipped，7.12 s** | 包含新增 43 项本地契约/几何/比较/CLI/执行边界测试及既有回归 |
| Bridge Python 单独运行新 adapter 测试 | **7 passed，0.28 s** | 真正安装的公开 builders/parsers + 假客户端；无连接/EDA |
| 本地示例 compile | 成功；没有 task.json | 不把 synthetic geometry 或无图片来源转换成执行任务 |
| 独立 fixture compare | topology/parameters、wire geometry、wire net binding 全过 | 输入和结论均为 software_inference，不是 OA 证据 |
| catalog | 新 circuit 只列 create/inspect，显示 live pending | 入口可发现，不声称生产验证完成 |
| 旧 inverter demo plan | token **815e068f84bd532e** 未变 | 新 optional 字段未改变该原有任务授权 token |

VDA 环境的 1 skipped 是未安装 Bridge 的适配器测试模块，已在 Bridge 自己的 Python
环境单独完成上述 7 项；不能把这一离线补测称为远端 smoke。

命令（重复执行时使用新的 basetemp，不删除或放宽旧目录权限）：

```powershell
.\.venv\Scripts\python.exe -m pytest -p no:cacheprovider --basetemp=C:\vbt\drawing-full-20261019c
& 'C:\Users\aknigsesl\tools\virtuoso-bridge-lite\.venv\Scripts\python.exe' -m pytest tests/test_schematic_drawing_bridge.py -p no:cacheprovider --basetemp=C:\vbt\drawing-bridge-offline-20261019e
.\.venv\Scripts\python.exe -m virtuoso_design_agent catalog
.\.venv\Scripts\python.exe -m virtuoso_design_agent plan examples\tasks\inverter-close-loop.demo.json
```

交付示例输出在本地 `artifacts/schematic-drawing-delivery/review.json` 和 `preview.svg`。
另已渲染并目视检查同一示例的 `artifacts/schematic-drawing-local-final/preview.png`：
左右镜像器件、旋转尾电阻、连续公共尾线和分支点可见，框与标题明确标注非原生符号。
不存在真实图片描摹成功的证据。

独立 fixture 结构 hash：
`772fbeb7ac1e406d36045c314bea405e78eecf6c9365f616873d5ef22fa16da7`。
导线 fixture hash：
`298f4629afed0ef917ed17896dedeb5d6c6e07884bb4906ae7d54f435548a1dd`。
这些 hash 仅绑定本地夹具，不能作为 OA 或 PDK 校准证据。

## 测试覆盖的真实风险

- 非对称端子在 8 个方向下的坐标；镜像差分支路与旋转电阻共享连续 tail。
- T 分支、同网四向交叉必须有显式 junction；跨网交叉、重叠触碰和碰到另一网络端子拒绝。
- off-grid、缺参、缺 master、未解图片疑点、浮空标签、信号网络标签孤岛拒绝。
- 独立回读发现 master、terminal→net、pins direction、参数、位置/方向、额外 net 和
  `nlAction` 漂移；分段不同但覆盖相同的真实线可以通过，少一段不能通过。
- 只看线段位置不够：错误 net、flight/annotation 图层、坏坐标/斜线都不能冒充导电连线。
- fresh master 几何漂移在写入前停止；CDF 字段被 setter 重映射在画线前停止。
- 目标已存在拒绝；参数阶段中断保留新目标，再次 create 拒绝，不自动重放/覆盖。
- 命令返回成功但结构错误时失败；既有 executor 使用正确 worker 动作，不生成 analysis，
  不以空仿真 metrics 宣称规格满足。任务序列化、token 变化和权限门保持有效。

## 待真实验收的边界与下一道 Gate

1. 实际 PDK 的 bbox、CDF 后 symbol 变化、`schCreateWire` 的 OA 几何/导电 net 绑定及
   分支端点行为尚未执行。新增 SKILL 检查/只读查询只在假客户端与真实 builder/parser 下测试。
2. 不支持无连接跨线、总线、数组、多页、自动避障/像素级描摹。不同网交叉必须显式绕线或
   经审阅改为 cross_region 标签；不会静默退回全端子标签。
3. 参数完整性仍需图片/用户与 PDK 字段核对；`parameters_complete` 不是自动 CDF 必填
   字段识别。原生视觉、连接、参数、真实网表和性能是不同验收项。
4. CDF setter 沿用原有 GUI 聚焦机制；新建流程的窗口复用与清理需要 live 观察。
   本轮没有开启/关闭任何远端原理图/ADE 窗口。
5. 新路径不支持阶段 checkpoint resume，也不回滚删除部分新建目标；已有 tuning
   checkpoint/resume 不变。失败必须保留现场并另行只读检查/制定恢复计划。

下一次只需一张真实清晰图片、一种指定 PDK、一个明确不存在的新目标。
先只读取得实际 master 几何并完成中间描述审阅；再列出 library/cell/view、OA 写入、
计算和产物路径、非覆盖与失败风险，获明确授权后创建一次。人工逐块对照原生图，
独立读取结构/参数/线网，随后另列 OA→si 网表验收；不为了验证入口跑一批电路或性能 sweep。
