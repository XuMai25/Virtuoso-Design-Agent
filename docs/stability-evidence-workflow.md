# STB 正式路径：原始证据留存、原生对照和离线复核

本入口服务已声明反馈和探针的 `existing_schematic` 任务，不自动找环路、重连 OA，
不从普通 AC 冒充相位裕量。支持单独 `simulation.run`、分析阶段和 winner-only STB。
Bridge 仍负责 netlisting 之外的传输/可靠执行、Spectre 和波形 PSF 解码；VDA 负责
测量契约、提取一致性、证据留存与规格判定，没有修改第三方库。

## 1. 正常执行不增加仿真次数

仍用已有任务、计划 token、白名单和 `allow_remote_compute` 授权。STB 不产生额外 OA
写权限；若设计任务本身调参，仍另受原来的写入、checkpoint 和回读约束。

正式 adapter 为每次 STB worker 请求分配唯一的本地目录，默认：

```text
<VDA项目>/artifacts/simulations/<task-id>/<request-uuid>/<analysis>_<unique-id>/
```

同批含 STB 的阶段保留各自目录；共用原一次 OA→si，STB 目录复制同批已验证的 si 输入，
不为打包证据再 netlist。各请求/条件的目录分离，不依赖 GUI 焦点或共享结果目录。
其他没有 STB 的既有 DC/AC 等任务继续原路径，不被强制升级为完整闭环。

目录保留：原始 OA 网表、实际 Spectre include、`si.env`、si log、typed wrapper、
Spectre log、DC/OP、`stb.stb`、`stb.margin.stb`，以及：

- `spectre-submission.json`：调用前记录的父 run 路径、wrapper hash、timeout；只表示
  可能已提交，不证明引擎是否启动或完成。
- `spectre-execution-receipt.json`：Bridge 返回的有限结果元数据，不保存环境/凭据或完整 stdout。
- `lifecycle-start.json` / `lifecycle-finish.json`：本地阶段状态。`returned` 不等于指标达标。
- `stability-response.json`：Bridge 解码的原始复数样本、显式 spec/sweep、输入绑定。
- `stability-evidence.json`：核心文件角色、大小/SHA-256 清单及绑定 SHA。

清单是打包时的文件快照；bundle 本身由外层 run 记录的 SHA 绑定，稍后生成的生命周期
finish 回执不在该快照内，避免自引用或收尾后哈希失效。原始日志可能含运行环境信息，
证据目录保持 Git 忽略，不整份提交或展示。没有自动删除历史证据；需要释放磁盘时先用
已有 resource audit 和保留策略审查，不因一次任务结束就删除设计依据。

Bridge 原有远端清理策略及 VDA timeout/进程收尾不变；本次不以长驻服务保存结果。
中断或失败时保留本地部分文件和状态，**先核对原请求/作业再决定如何继续，不自动重提**。
硬中断可能只有 start/submission、没有 finish/receipt，此时是未决，不推断没有执行。

## 2. 原生 margin 是独立一致性门

除波形非空、有限、频率递增、sweep 端点和交点检查外，还要求同一下载目录下的
`stb.margin.stb`。核对两文件的 simulator/version/date/analysis name/probe/OP producer；
probe 必须等于任务声明。仅支持现场验证的 Spectre ASCII `stb`/`margin.stb` 格式，
陌生格式或缺文件显式 incomplete，不静默退回仅波形通过。

Bridge 的通用 reader 继续解码复数波形。其 native margin `nan` 和字符串处理不足由
VDA 的窄范围 scalar/metadata reader 补足，不复制通用 PSF parser、不更改 Bridge。
PM、unity frequency、有限 GM 及 phase crossover 分别对照，单位或可用性矛盾会阻止通过。
`nan` GM 必须保留明确原因；绝不转换为 GM=∞。负 PM/GM 允许作为真实测量，再由规格判失败。

一致性容差位于 `generic_simulation.stability.native_margin_tolerances`，缺省为：

```json
{
  "phase_margin_abs_deg": 0.1,
  "frequency_relative": 0.005,
  "gain_margin_abs_db": 0.1
}
```

这是提取一致性门，不是放大器设计规格；频率容差为 0.5%。不同网格/版本需要调整时必须
在运行前显式声明并重新规划，不能看过误差后自动放宽。旧 STB 任务因新增默认契约字段，
应重新计算计划与测量 context；旧 token 不自动沿用。没有 STB 的旧任务不引入该字段。

## 3. 出现疑点时先离线复核，不重跑电路

run action 的 `evidence.simulation.stability_artifacts` 提供完整路径与 `evidence_sha256`：

```powershell
.\.venv\Scripts\vda.exe stability-audit <stability-evidence.json> `
  --expected-sha256 <原run记录的evidence_sha256> `
  --output <新的audit.json> --markdown <新的audit.md>
```

纯本地入口，不要求 Bridge 安装或连接，不提交计算、不写 OA；输出文件不得覆盖。
成功返回 0，不完整返回 2；保留诊断。先核对清单和所有核心字节，再从已保存的 Bridge
解码样本重算 margin，并重读 native scalar 文件。它**不是另一个独立 PSF 波形解析器**。
省略 expected SHA 时只能审查当前 bundle 自洽性，不能声称已与原 run 精确绑定；
`design-review` 使用原 action 中的 SHA 做强绑定。

`design-review` 展示 candidate/winner 的原生对照和文件状态；`design-iterate` /
`design-next` 在原始文件缺失、变动、probe/sweep/输入绑定或原生对照不匹配时要求先核对，
不形成新的完整可行锚点、不用旧 PM 补齐当前轮次。有限条件必须逐点有对应 bundle，
不能用一个 nominal bundle 凑齐 PVT。历史 STB 没有 bundle 时保留为未验证，既不伪造
bundle，也不自动要求重新仿真。

## 已验证与仍未验证

2026-10-23 新代码在本地完成正常/失败/中断、共享一次 si、多条件身份检查和 winner-only
复盘回归；重用旧 N28 原始文件，通过真实 PSF 解码及 native scalar 离线重放。
见[本地接入验收](validation/2026-10-23-stb-evidence-integration-local.md)。
这不是修改后正式 worker 的新远端实测；该链路留到下一次正常设计需求、明确授权后使用，
不为重复证明而再跑一批电路。旧真实作业见[单点原生对照](validation/2026-10-23-n28-stb-native-acceptance.md)。
ADE L/Explorer 的 STB state 保存/Load/重跑、其他 PDK、有限 PVT、多环和全局稳定性仍未
因此通过。用户人工 ADE 能力不被取消；完整 L5B 单模块质量闭环也不由此宣告完成。
