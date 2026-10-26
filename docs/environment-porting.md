# 换机、换账号、换服务器：给 AI agent 的适配清单

目标是复用上游 Bridge 的连接与部署能力，替换环境事实，不重做 SSH/SKILL/Spectre。
本清单不授权连接、部署、写 OA 或计算。不得把维护者的目录／账号当作自己的可写工作区。

## 先区分换了什么

| 情况 | 通常需要适配 | 不能沿用 |
| --- | --- | --- |
| 只换客户端，远端账号与 EDA/PDK 未变 | Python 环境、Bridge 安装、SSH key/连接 profile、VDA Bridge Python 路径 | 本地文件路径、缓存、进程号、窗口号 |
| 换远端账号或目录，同一服务器/PDK | 上述项目＋自己获准的工作区、cds.lib、部署/token/scratch 路径和目标库 | 他人工作目录、认证材料、他人的 OA 写权限 |
| 换服务器、PDK/节点/版本/金属栈 | 上述项目＋Cadence 环境、model include/section、master/CDF/端子几何和网表绑定 | 旧性能证据、尺寸比、留出模型、原始输入指纹 |

Bridge 本身是公开工具。优先按[上游](https://github.com/Arcadia-1/virtuoso-bridge-lite)文档配置。
VDA 的本地修改另见[兼容参考包](../references/bridge-compat/README.md)；不要假设公开版本缺少所有修复，
也不要假设版本字符串相同就与维护者补丁完全等价。

## 需要用户提供／agent 核实的最小环境表

只记录非秘密元数据；密码、私钥、token 和 license 配置不进入表或聊天。

- 客户端 OS、Python 可执行文件、VDA commit。
- Bridge 来源 URL、实际 commit/version、独立 Python 路径、使用哪个连接 profile。
- SSH host alias／跳板机关系、远端账号；密钥由用户在自己的 SSH 配置中管理。
- Virtuoso/Spectre/si 所在执行 host，实际 Cadence 环境脚本；GUI DISPLAY/会话归属（按需）。
- 管理员允许的远端写入根；Bridge 部署、认证文件、scratch 和证据保留位置。
- 自己的 cds.lib、PDK library/master/model、模型 include/section、节点/金属栈。
- 目标 library/cell/view、是否已有人工设计／未保存编辑、允许的写入和恢复方式。
- 当前已知的缺项、未验收部分与本次授权范围。

## A. 改 Bridge 配置即可的连接层项目

1. **安装与隔离。** 用上游支持的安装方式建立独立 Bridge 环境；VDA 不内置或复制它。
   核对该环境能 import `virtuoso_bridge`，以实际版本的 help 为准，不猜安装参数。
2. **原生连接配置。** 在 Bridge 原生配置里设置 SSH、jump host、远端用户、GUI/EDA host、
   端口、认证和远端部署路径。用该版本的配置模板／文档，不把维护者 .env 复制过去。
3. **明确 profile。** 当前已核实版本支持进程环境 `VB_PROFILE`，也支持自己的虚拟环境
   profile binding。VDA worker 使用 Bridge 的 `VirtuosoClient.from_env()`／`SSHClient.from_env()`。
   `vda bridge start -p NAME --env FILE` 只给该生命周期子进程传参数，**不会自动给随后 run 设置 profile 或 env**。
   让启动与 worker 使用同一套原生配置；未知版本先核对其解析规则。
4. **VDA 解释器路径。** `run`、`doctor`、`resources` 与 `bridge start/status/stop` 都有
   `--bridge-python`；新机器一律显式传自己的绝对路径。
   `VDA_BRIDGE_PYTHON` 可供 adapter 与 local_validation 使用，但当前 lifecycle 不读它；
   所以不要靠同一个环境变量推断所有入口已经统一。旧默认路径位于
   `adapters/subprocess_bridge.py::DEFAULT_BRIDGE_PYTHON`，仅为维护者兼容默认。
5. **安全部署。** 配置自己的认证文件位置和权限，保留 token/HMAC 与 loopback，不部署公网监听。
   上游版本是否需要兼容补丁先检查实际代码；不得删掉 VDA bind guard 来“打通”连接。
   已有 Virtuoso/daemon 的升级需要独立授权；安装 Python 包不等于远端资源已经同步。

非默认 Bridge Python 的用法（先替换占位符；这些远端探测须获准后才执行）：

```powershell
$bridgePython = "ABSOLUTE/PATH/TO/BRIDGE/python.exe"
$env:VB_PROFILE = "YOUR_CONNECTION_PROFILE"
./.venv/Scripts/python.exe -m virtuoso_design_agent bridge status --bridge-python $bridgePython
./.venv/Scripts/python.exe -m virtuoso_design_agent doctor --adapter bridge --bridge-python $bridgePython --pdk-profile YOUR_PDK_PROFILE
```

profile 的凭据应由 Bridge 在本机安全加载；不要打印配置正文来验证。上面的两个命令仍会访问远端，
doctor 还会检查 EDA 可用性；它们不是离线检查，也不是性能仿真。

## B. VDA 侧仍需适配的环境事实

| 缺项／位置 | Agent 应做什么 | 最小验证 |
| --- | --- | --- |
| `profiles.py`、`resources/pdks/*.json` | 新建独立命名 profile，保留原 profile；明确 tech/master/model、CDF width 约定、include/section、corner、环境脚本、cds.lib、run root | 本地 PdkProfile 校验及 plan；真实参数/端子需只读回读 |
| `models.py::PdkProfile.validate_remote_write_paths` | 当前把 cds.lib/run root 限在 `/data/xum/`；为新用户设计明确允许根，不能简单放开为任意绝对路径 | 合法新根、目录边界／越界／路径穿越拒绝测试，旧环境回归 |
| `models.py` 的 ADE resume 路径校验 | 同步迁移确切 scratch/resume 根的检查 | 正确 checkpoint 可用，旧/外部根不误放行 |
| `adapters/bridge_worker.py` 的运行根与清理守卫 | 搜索 `/data/xum`，覆盖 ADE runtime/manifest、si、Spectre guard、characterization、preview 和资源审计；统一到明确任务根及其后代 | 逃逸拒绝、只清理本任务、超时/中断证据；不能只替换 profile JSON |
| `models.py::DEFAULT_PDK_PROFILE` 与任务 `pdk_profile` | 新工程显式选新 profile；不要全局替换历史默认 | 新任务 plan 使用正确 facts；旧任务仍可追溯 |
| `examples/tasks/*.bridge.json` | 只参考 schema，重建自己的 target、权限、预算和 testbench；inspect 应从最小只读任务开始 | plan/token、白名单、前缀、非覆盖和状态检查不放松 |
| `design_context`、CDF→si binding、symbol geometry | 从自己的 master/实际回读构建；不猜端子顺序、callback 后值或参数语义 | inspect、实际网表一致性；图片图面另作人工检查 |
| 依赖历史文件的请求 | 换成自己真实产生且 exact-hash 绑定的记录；不修改旧证据以“兼容” | 缺原文件必须保留为缺项，不硬拟合、不编造 SHA |
| 本地 launcher／清理与 GUI | 新 OS 上核对解释器/子进程退出；GUI 坐标不是配置 | 先离线生命周期测试；GUI 操作另获准、按现场窗口身份执行 |

这些是本次核对到的**实际移植工作**，不是 Bridge 连接层缺功能。
当前没有 `--pdk-file` 或通用“改工作区根”配置入口；agent 需要在自己的 VDA 分支中适配并测试上述守卫，
不能用符号链接指向维护者目录或把检查删除。新 profile JSON 被 package data 收录后，重新安装/构建确认它被包含。

## C. 最少而足够的分层验收

1. **离线：** 新 clone 安装 → catalog → plan → demo run/review；无 Bridge、无私有 artifacts。
2. **静态 Bridge 兼容：** 核对接口与导入、patch applicability、版本与 Python；不初始化 client、不连接。
3. **获准后连接：** 原生 Bridge 配置＋选定的既有 Virtuoso 会话，检查认证、实际 loopback bind、
   owner/session 和只读表达式。不能为了消除错误自动关闭安全机制、改网络或重启他人会话。
4. **已有设计只读：** 明确 library/cell/view，inspect 实例/master/端子/参数/pins；
   需要自动 netlist 时单独声明其 scratch 写入。优先复用已有电路。
5. **仅在真实需求时：** 一个非覆盖目标、一次明确分析或一次受控参数变更，验证 actual OA→si 输入、
   指标、写后回读与恢复。不要为“换机”重跑全部电路／所有分析。

每层分别报告通过、未验证和失败。完整双环境软件验收仍按[原入口](local-validation.md)；
缺历史数据或平台 skip 不能改成全通过。也不以此要求对方取得维护者的私人资料。

## 完成后交付什么

让 agent 输出：实际路径/profile/版本的非秘密摘要、改过的文件与原因、
本地测试、最小真实验收证据、剩余边界、恢复命令及普通任务入口。
新环境自己的配置／token／数据留在其本地，公共仓库只保留脱敏模板和通用实现。
