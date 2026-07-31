# 2026-08-01 onboarding discovered-binding promotion live Gate

## 结论

`parameters.binding.discover` 得到的 TSMC N28 `MNCAS.Wfg -> w` 证据，已经不经人工抄写进入普通
`existing_schematic simulation.run`，并真实完成一次 OA readback、一次 `si` netlisting 和共享网表
DC→AC。主字段及 `ad/as/nrd/nrs/pd/ps` 六个 callback 在实际 OA 与实际 `si` 网表中逐项匹配，
两阶段结果和 artifact manifest 完整，任务没有写 OA。

状态升级为：**discovered OA-CDF binding promoted into a normal shared-netlist OA-to-si-to-Spectre run,
with runtime derived-callback checks live verified on one flat nominal TSMC N28 NMOS instance**。

这仍不是任意字段、任意 PDK 或 L5B 设计质量闭环。

## 已授权边界

- target：`vb_pdk_smoke/vda_cs_cascode_gate_001/schematic`
- operation：`simulation.run`
- PDK profile：`nics4304_tsmc28`
- OA write：否
- remote compute：是
- `replace_existing: false`
- cellview create/replace：无
- remote root：
  `/data/xum/virtuoso_bridge_smoke/vda_onboarding-cascode-discovered-binding-dc-ac_e1926e8f33ac`
- execution TaskSpec SHA-256：
  `98e80b66e61b562a0866fb6ba6713659d5e5aa5f2b5db0b521cfad96c8547ddf`
- plan token：`abec578d3742d07a`

安全源 TaskSpec 的 SHA-256 为
`62564c46d99ba0a0212384ebbc30e861461ce026a9869a7cbf2bb2bc737a850e`，token 为
`06b9987ea5b404df`。本次运行后新增的 `vda execution-scope` 从该关闭开关的 TaskSpec 自动推导
最小 compute-only 权限，确定性重建出与实际执行文件逐字节相同的 TaskSpec、SHA 和 token；它不执行
任务，也明确保留 `user_confirmation_required=true`。因此后续不再靠手工改 JSON 安全开关。

## 真实执行与证据

- run status：`succeeded`
- adapter：`virtuoso-bridge-subprocess`
- run record SHA-256：
  `cb39711d565862f4623f631b1da0f6dbc366b5601e6f9fafcbbf6a6facfa38c6`
- total elapsed：`201.827 s`
- shared simulation action：`200.197 s`
- OA readback count：`1`
- `si` netlist generation count：`1`
- netlist SHA-256：
  `4a2b4d21c32557788cfc9ae537f53427ead757ebe3f115044af1ac95f7b4de7b`
- DC manifest：11 files，SHA-256
  `445ff28f0d1c6464cd341e885e94e0ea1f80809e8aa7e42ba7c4bd1eca4f93bb`
- AC manifest：8 files，SHA-256
  `9ca4ad5a0f4d46b95592724dcc05a5c56ac9b4f01796fd6cd80d7f4e80deec00`
- Spectre：`21.1.0.612.isr15`
- OA write：两阶段均为 `false`

证据来源保持分开：OA topology/parameter 是 `bridge_readback`；`si` 网表、PSF、Spectre log 和原始
manifest 是 `eda_result`；shared-netlist 复用、callback 等值与规格复算是 `software_inference`；
任务角色、testbench 和规格是 `user_input`。

## OA/si 参数一致性

primary：

```text
MNCAS.Wfg = 750.0n
MNCAS.w   = 750.0n
```

derived callbacks：

| OA field | si field | OA value | si value |
| --- | --- | ---: | ---: |
| `ad` | `ad` | `5.625e-14` | `5.625e-14` |
| `as` | `as` | `5.625e-14` | `5.625e-14` |
| `nrd` | `nrd` | `0.441142` | `0.441142` |
| `nrs` | `nrs` | `0.441142` | `0.441142` |
| `pd` | `pd` | `1.65u` | `1.65u` |
| `ps` | `ps` | `1.65u` | `1.65u` |

任一字段缺失或工程单位不等价，worker 会在 Spectre 规格判定前失败。本次普通运行返回
`derived_callback_consistency=matched`，不是从 discovery 说明文字推断。

## DC/AC 结果

| Metric | Result | Constraint |
| --- | ---: | ---: |
| output DC | `0.3810253 V` | `0.1–0.8 V`，pass |
| low-frequency gain | `6.441244 V/V` (`16.1794 dB`) | `>= 3 V/V`，pass |
| −3 dB bandwidth | `3.612727 GHz` | `>= 1 GHz`，pass |
| GBW | `23.270457 GHz` | reported, not constrained |
| unity-gain frequency | `22.189521 GHz` | reported, not constrained |

本次 netlist SHA 与 2026-07-28 的独立 generic DC/AC run 完全相同；两次共同的 12 个指标最大相对
差为 `9.84e-16`，仅为浮点序列化差异。这证明新增 handoff 没有改变原有通用仿真语义。

## 确定性执行与事后审计

新增两个本地命令：

1. `vda execution-scope`：只按计划中的 `remote_compute/remote_write` 推导最小安全开关，拒绝已启用、
   `replace_existing=true` 或不需要远端权限的输入，输出等待确认的新 token；不执行任务。
2. `vda onboarding-binding-audit`：绑定 promotion record、execution-scope record、精确执行 TaskSpec 和
   real-Bridge run，重新核对 task/run SHA、plan token、OA topology、主 binding、全部 callback、
   一次 readback/netlist、stage 顺序、manifest identity、有限指标和全部规格。

本次 audit status 为 `passed`：

- execution-scope record SHA-256：
  `9493d282e3beb8940f1b625c8dd8982e65c4b3e4f580af6d2f6e8b031e59c8e2`
- audit artifact SHA-256：
  `898c1b6cf702ae500b3bebef451e377911d40e6ead682c2321ca366bc256c87d`

audit 只复判已保存证据，不重跑 EDA，也不把 manifest 字符串包装成重新下载并逐字节复核远端文件。

## 生命周期审计

运行前 `vda resources --remote`：`si=0`、`spectre=0`、Maestro session `0`；两条既有长期 Virtuoso
进程不是本次新增。运行后共享 tunnel 在资源复查阶段发生一次 `WinError 10054`，因此不能把该次
Bridge 资源命令包装成成功证据；它记录为独立 transport `system_event`。

随后通过已验证的 key-only SSH 做只读精确进程名计数：`spectre=0`、`si=0`、`maestro=0`。本地
`vda resources` 为 transient resource `0`、cancel marker `0`，Bridge lifecycle 最终为 stopped。
远端 run root 保留 `76,534 bytes` 的 `si` control/map/netlist、wrapper 与 guard 审计文件；下载后的
DC/AC raw PSF 目录已清理。该小型 evidence root 位于声明的 `/data/xum` 工作区，不是运行中的进程或
无限增长的波形缓存，本 Gate 未删除它。

## 回归与边界

```text
904 passed in 5.30s
vda execution-scope: current live SHA/token reproduced exactly
vda onboarding-binding-audit: passed
git diff --check: passed
```

未覆盖：其他 OA 字段、PMOS、其他 foundry PDK、深层 hierarchy、shared-child per-instance override、
并发人工 editor、多个 callback-bearing 字段的联合 OA write/tuning，以及 callback 公式跨 PVT 的稳定性。
下一次远端使用应绑定用户真实模块或真实的新字段需求，而不是继续在该 fixture 增加随机候选。
