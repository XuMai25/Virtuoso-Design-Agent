# 2026-10-23 本地基线收口

## 范围与版本

本轮完成的是已有待提交实现的审查、必要修复、入口/证据边界统一和本地版本固定，
不是新增功能 Gate。远端连接、OA 写入、仿真、Bridge smoke、GUI 操作均为 **0**。
没有修改系统、PDK、Vault 或第三方 Bridge，没有推送 Git。

- 起点：`codex/bridge-main-upgrade-20261018`，`e0aea2d57bff3e6cefe73deea3518eba7c8e63a2`。
- 收口分支：`codex/vda-baseline-closeout-20261023`；原分支保留不动。
- 代码/测试/示例提交：`7022dc6bd6d9f9dc84148a1565c966481da41033`，34 个文件。
- 文档另提交；本地标签 `vda-baseline-20261023` 绑定最终文档提交。不是远程 release，
  包版本仍为 `0.1.0`。代码共用 models、CLI 和 planner/executor，保留为一个可运行的
  集成提交，没有强行拆成缺少依赖模块的中间版本。

审查前已保存 61 个待提交文件的逐文件大小/SHA-256 和原始字节，均仅存于忽略目录：

```text
artifacts/baseline-closeout-20261023/snapshot.json
artifacts/baseline-closeout-20261023/pending-before-review.zip
artifacts/baseline-closeout-20261023/tracked-before-review.patch
```

zip SHA-256：`020b1573a83dce75fdd50194af96d6649b585fb5fddecac2407f3b0478c6d9f4`。
patch SHA-256：`b0087e7c44062609831a2e94a35fd58fe90e254fc3bfc47c229ddd5cf06503d7`。
zip 同时保留当时未跟踪文件；patch 只覆盖当时 tracked diff。它们不是可直接覆盖当前目录
的恢复指令；需要历史版本时先保存新工作，再从基线标签建立新分支或独立 worktree。

## 审查与实际修复

复核图片描述/端子几何/导线与结构比较、测量和跨轮次状态、已有理论建议复用、STB 持久
证据及离线审计、窗口建议、CLI 与原 operation 的接入。保持原授权、白名单、非覆盖、
checkpoint 和证据分类，不另造执行器或优化器。

按 karpathy-guidelines 收紧范围：只为复现的具体问题修改实现，不做邻近重构。

1. **窗口建议不得越过任务归属。** 原 planner 对另一任务已完成且保存/Load 完整的窗口
   可能给出 close 建议，对状态不明的另一任务也可能给出 completion 询问。本轮将不同
   `owner_task_id` 固定为 keep，不由当前任务安排保存、复用或关闭。它始终只是本地计划，
   没有实际关闭过窗口。新增 active/completed/unknown 三个回归情形；修复前 2 failed、
   19 passed，修复后窗口模块 21 passed。
2. **STB 清单先拒绝越界路径，再接触文件系统。** 原代码先 resolve 再判断绝对/越界路径，
   Windows UNC 可能在拒绝前触发外部解析。现先做跨平台词法检查，拒绝绝对路径、drive/
   rooted Windows 路径及 `..`，之后仍做实际 resolve 和 symlink 包含检查。新增四类
   路径测试，以拦截器证明拒绝前不会 resolve；修复前 4 failed，修复后通过。没有尝试
   连接真实 UNC 主机、读取工作区外文件或修改网络。

文档入口统一到[可用基线](../current-baseline.md)，修正 README 和补丁记录的本地 Bridge
版本身份；旧 0.8 升级与实验记录保留为历史，不改写成新实测。AGENTS/catalog 不再错误地
写成“仅实现反相器”或图片全未验收，但继续明确 L5B、fresh-create 和图面/性能边界。

## 实际本地验证

| 检查 | 结果 | 边界 |
| --- | --- | --- |
| 修改前完整 pytest | 1122 passed，1 skipped，8.58 s | 主环境缺 Bridge 包 |
| 修改后完整 pytest | **1129 passed，1 skipped，8.82 s** | 同一个 Bridge 依赖模块跳过 |
| Bridge 环境补跑 drawing adapter + stability evidence | **39 passed，1.19 s** | mock/本地 API 单测，不是远端 smoke；与主回归重叠，不累加 |
| `catalog`、反相器 demo `plan` | 成功，plan token `815e068f84bd532e` | 仅计划，没有执行 |
| 本地示例 `schematic-compile` | review + 非原生 SVG 预览生成 | 示例/inferred 几何；没有输出执行任务，不是用户图片复现或原生 OA |
| 旧真实 N28 STB bundle 的 `stability-audit` | verified、native matched | 只读已有字节和保留的 Bridge 解码样本，没有新 PSF 波形独立解析/仿真/当前 OA 核对 |
| 五份新增示例 JSON | 解析通过 | 本地描述/草案，不提供远端授权 |
| 待提交文本敏感值形状扫描、相对文档链接、Git whitespace | 未见命中/断链/空白错误 | 不是通用安全审计或远端验证 |
| 无网络依赖的 wheel 构建尝试 | **未完成**：环境缺 `setuptools.build_meta` | 未安装/更新依赖，不能宣称可分发 wheel 已验证 |

完整回归命令：

```powershell
.\.venv\Scripts\python.exe -X utf8 -m pytest -o addopts='-p no:cacheprovider' -q -ra `
  --basetemp artifacts/test-temp/baseline-closeout-final `
  --junitxml=artifacts/baseline-closeout-20261023/pytest-final.xml
```

其余本地记录位于同一 `artifacts/baseline-closeout-20261023/`：`pytest-before.xml`、
`pytest-bridge-local.xml`、`window-regression-before.xml`、`window-regression-after.xml`、
`path-regression-before.xml`、`guard-regressions-after.xml`、`stability-audit.json/.md`。
示例输出位于该目录的 `schematic-compiled.json/` 子目录（CLI `--output` 指定输出目录，
即使目录名有 `.json` 后缀也不是单文件）。这些运行资料保持 Git 忽略，不提交日志或原始数据。

复用 STB 文件仍得到 native PM `85.8245951486°`、VDA `85.8321733062°`；原生与重提取的
unity frequency 分别约 `8.097762669/8.097037259 GHz`。GM 无有限值并保留原因，不按无穷
处理。上述值属于原作业，不是本轮新增电路性能或全局稳定性结论。

## 保留的边界及后续

L5A 执行基础保留，L5B 通用化仍在开发中，未宣告质量闭环。正式 STB worker 新路径远端
验收、ADE STB GUI、其他 PDK/PVT/多环、图片 fresh-create 单次全链和完整人工图面确认
没有在本轮补跑。Windows TCP reset 继续暂缓；Codex 游戏卡顿不纳入 VDA。

后续优先在用户实际电路、具体规格和已授权范围内使用已有能力；新问题再定最小验证，
不为重复证明工作流另跑一堆电路。若需推送、真实 OA/EDA 或 GUI 动作，另行明确范围并
授权。本地版本可回退不代表远端 OA 会自动回退，也不授权批量清理会话或删除证据。
