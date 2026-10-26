# Existing-schematic 参数 binding 自动发现真实 Gate

日期：2026-08-01

## 结论

在 `vb_pdk_smoke/vda_cs_cascode_gate_001/schematic` 上完成了获授权的可逆
`MNCAS.Wfg: 750.0n -> 800n -> 750.0n` probe。一次成功 action 生成并保存 baseline、probe、
restored 三份真实 OA→`si` 网表；没有运行 Spectre，没有创建或替换 cellview。

真实差分证明主绑定为：

```text
MNCAS.Wfg -> MNCAS.w
```

PDK callback 同时重算 `ad/as/nrd/nrs/pd/ps`，这些字段在 OA 与 `si` 中逐名、逐值一致；OA 中还
回读到 `multiwd/w/w_ov_l` 等 callback 变化。当前分类因此是
`direct_literal_binding_with_derived_callbacks`，不是“所有参数彼此独立”的 direct-only 关系。
跨实例变化、model/node/instance 结构变化、多个 literal 主候选或无法由同名 OA callback 数值
解释的额外 netlist 变化仍拒绝提升。

状态应表述为：

> flat TSMC N28 OA-CDF to si primary binding with derived callback effects live verified;
> arbitrary-field and cross-PDK binding coverage pending

这不是 Spectre 性能验证，也不是 L5B 单模块设计质量闭环。

## 授权边界

- plan token：`74c70848fbfc9317`；
- OA target：`vb_pdk_smoke/vda_cs_cascode_gate_001/schematic`；
- 唯一请求字段：`MNCAS.Wfg`；
- 临时写入：`750.0n -> 800n -> 750.0n`；
- 远端计算：三次 `si -batch` netlisting；
- Spectre：未运行；
- `replace_existing=false`，未新建/替换 cellview；
- 远端工作范围：`/data/xum/virtuoso_bridge_smoke/vda_binding-discovery-mncas-wfg-live-20260731_*`。

## 执行与故障证据

第一次执行在 `bridge.probe` 前置阶段因 Windows SSH tunnel 冷启动失败，run record 为
`artifacts/runs/binding-discovery/mncas-wfg-discovery-live-20260801.json`。action source 是
`system_event`，没有触及 OA。显式隐藏启动 tunnel 后，Bridge 0.7.0、Virtuoso 6.1.8 和
Spectre 21.1.0 状态健康。

第一次重试完成 baseline netlist 后，在清理时被 Bridge 的
`VirtuosoClient.run_shell_command()` 报为 `csh returned nil`。只读检查第三方实现确认该 API 会把
无输出的 csh 返回视作失败；VDA 改为复用 Bridge 已公开的 `ssh_runner`，按真实 return code 判断
`rm` 和 `test`。没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`。

第二次重试得到真实 `rm: Directory not empty`。精确目录只剩一个 0-byte `.nfs*` 文件，`fuser`
证明持有者是长期 Virtuoso CIW，而非 `si` 或 Spectre；下一次 `simInitEnvWithArgs` 会释放前一次
句柄。根因是 CIW 在 NFS run directory 中保持 `si.foregnd.log` 打开，不是短暂清理延迟。

VDA 随后把自动 `simInitEnvWithArgs` 包在 SKILL 动态 `let` 中，只在该调用期间把
`simForeGndLogFile` 设为 `/dev/null`。真实诊断证明：

- `si.env/control/spectre.inp/spectre.sim` 仍写入唯一 `/data/xum` 目录；
- Virtuoso 不再持有该目录的 FD；
- 调用退出后全局 `simForeGndLogFile` 回到 `"si.foregnd.log"`；
- 诊断目录可以立即删除并通过不存在检查。

第三次重试完整执行 `parameters.binding.discover`，action 时长 `208.096 s`，总 run 时长约
`210.627 s`。执行时的旧分类器把七个变化字段统一记为 `ambiguous_netlist_change`，所以原 run
record 状态保持 `partial`，没有被篡改为成功。后续用同一份不可变 EDA evidence 运行纯本地
`vda binding-reclassify`：它重新校验三份 manifest/原始 bytes、完整 OA 表和恢复签名，不写 OA、
不连接远端，输出可提升的新分类。这样无需为了软件分类规则更新再次执行三次 `si`。

## 三阶段同源证据

| stage | canonical signature | raw netlist SHA-256 | 远端清理 |
| --- | --- | --- | --- |
| baseline | `4d3430b5...4230d` | `4a2b4d21...de7b` | exact path absent |
| probe | `d56252d7...8d64f` | `5adf710c...7052` | exact path absent |
| restored | `4d3430b5...4230d` | `4a2b4d21...de7b` | exact path absent |

baseline 与 restored 的 canonical signature 和 raw netlist SHA-256 都完全相同。probe 的 OA/`si`
变化为：

| 类型 | 变化 |
| --- | --- |
| primary | `Wfg 750.0n -> 800n`；`w 750.0n -> 800n` |
| derived area | `ad/as 5.625e-14 -> 6e-14` |
| derived resistance squares | `nrd/nrs 0.441142 -> 0.373548` |
| derived perimeter | `pd/ps 1.65u -> 1.75u` |
| OA-only callback/readback | `multiwd`、`w_ov_l` 等完整留证 |

六个额外 netlist 变化全部限于 `MNCAS`，且存在同名 OA callback 字段、before/after 两端都按
Spectre scalar 语义等值。主候选只有 `w` 一个；没有 model、node、instance set 或其他实例变化。

三份 stage 各保存 `oa_netlist.scs`、`si_batch_stdout.log` 和 manifest。任务外复算确认全部文件的
size/SHA-256 与 run record 一致；三个成功远端目录又通过独立 `test ! -e`。原真实 run：

- `artifacts/runs/binding-discovery/mncas-wfg-discovery-live-20260801-retry3.json`；
- SHA-256：`fd50e0902bbed3a1843ecfea0dba0dd8915001f7105d56de7f65697085950f20`。

纯本地重分类记录：

- `artifacts/runs/binding-discovery/mncas-wfg-discovery-reclassification-20260801.json`；
- SHA-256：`61a6f6aeb50564e38e95e774f273fb263d1b6a9fe762a2efe8be1307d4638f51`；
- `source=software_inference`、`remote_execution_performed=false`、`oa_write_performed=false`；
- 原 run 的 `eda_result` 和 `bridge_readback` 不被改写。

## OA 恢复与资源

成功 action 内部已要求完整 233 项 CDF 表和 canonical netlist 恢复。任务外独立 inspect 又得到：

- `MNCAS.Wfg=750.0n`、`MNCAS.w=750.0n`；
- CDF count `233`；
- CDF SHA-256 `320ef706ec51d47ffd3e009316e34aca3111fad1daad51d10684486d03c5d812`；
- topology SHA-256 `2e27d1c68012bc1e7adc0da936a648febde4b685cfa1391512a2d401fb291a51`；
- placement SHA-256 `a1c08ba5a1ccadeb3cc20be1dbd5d1f9c0649628386adef62d77c16abd0f94fa`。

这些值与 probe 前 fresh inspect 完全一致。最终只读资源盘点为 `si=0`、`spectre=0`、
VDA-managed Maestro session `0`、本地 transient resource `0`。三个成功 stage scratch 均不存在；
两次失败各保留一个 10-byte 空目录作为失败路径证据，不含网表。共享 tunnel 已显式 `stop`，最终
`vda bridge status` 为 NOT running。

## 代码与证据来源

- OA baseline/probe/restored 完整 CDF：`bridge_readback`；
- 三份 raw `si` netlist/log：`eda_result`；
- primary/derived callback 分类与本地重分类：`software_inference`；
- tunnel 失败、恢复、远端清理和进程盘点：`system_event`；
- probe 字段、值、target 和 token：`user_input`。

本轮只修改 VDA 的 worker、分类器、executor、CLI、测试和文档。第三方 Bridge 保持未修改。

## 未验证边界与下一 Gate

- 只验证了 flat TSMC N28 `nch_lvt_mac.MNCAS.Wfg`；没有证明任意 CDF 字段、PMOS、其他 PDK 或
  深层 hierarchy 都满足同一 callback 模式；
- derived callback promotion 只接受同一目标实例、唯一 literal 主候选、所有额外 netlist 字段均有
  同名 OA before/after 等值证据；不会把任意多字段变化放行；
- 没有运行 Spectre，因此没有新的电路指标或规格结论；
- 没有并发人工 editor Gate，也没有证明旧 ADE L state 迁移。

下一道有产品价值的 Gate 不是再随机 probe 更多 Wfg 点，而是把这份 hash-bound promoted binding
编译进正常 onboarding/generic tuning TaskSpec，同时保留 derived callback 清单；然后只用一个最小
OA→`si` DC/AC 任务证明后续工作流能直接消费自动发现结果，不再手工复制 `Wfg -> w`。

## 本地验证

定向测试覆盖 silent csh cleanup、SSH exit-code failure、NFS foreground-log 动态作用域、
derived callback promotion、跨实例/未镜像副作用拒绝，以及无远端重跑的 artifact reclassification。
完整回归命令：

```powershell
.\.venv\Scripts\python.exe -m pytest
```

结果：`895 passed in 8.13s`。`vda catalog`、原任务 `vda plan ... --json`、
`vda binding-reclassify --help` 和 `git diff --check` 同时通过；plan token 仍为
`74c70848fbfc9317`。
