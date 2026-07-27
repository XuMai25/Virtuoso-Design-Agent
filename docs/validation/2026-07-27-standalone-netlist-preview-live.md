# 2026-07-27 standalone Spectre 轻量拓扑预评估 live Gate

## 结论

在用户明确批准的只读远端计算范围内，`circuit: netlist_preview` 首次完成真实
nics4304/TSMC N28 `top_tt` smoke。普通共源和共栅级联两份受校验结构化电路图直接生成
standalone Spectre DC/AC deck；全过程没有 OA target，没有启动 `si` 或 Maestro，也没有
创建、修改或覆盖 cellview。

两份仿真都返回 241 点非空复数 AC、完整 DC operating point、已包围的首个 −3 dB
bandwidth 和 unity crossing。每个变体保留 8 项 deck/PSF/log/guard 文件的 size 与 SHA-256
清单，运行前后远端 `spectre=0`、`si=0`、Maestro session=0。状态可以升级为：

**structured standalone TSMC N28 topology preview live verified for one common-source/cascode A/B; quantitative OA equivalence and design closure remain out of scope**

## 授权与执行边界

- OA target：无；
- OA write：否；
- remote compute：是；
- `replace_existing=false`；
- 计划 token：`ad0a3006e33ab934`；
- Bridge：`0.7.0`，doctor 的 SKILL probe 为 `3`；
- Spectre：`21.1.0.612.isr15`，路径 `/tools/Cadence/SPECTRE211/bin/spectre`；
- PDK/profile：`nics4304_tsmc28`，TSMC N28，`top_tt`，27 ℃；
- VDD/VIN/VCAS：`0.9/0.35/0.545 V`；
- 共享负载：`20 kΩ` 与 `2 fF`；
- AC：`10 kHz–1 THz`，30 point/decade；
- 没有修改 `virtuoso-bridge-lite` 或 Obsidian Vault。

Bridge tunnel 在执行前处于停止状态。沙箱内第一次启动因不能解析
`nics4304-cad1` 而失败，没有创建本次远端 scratch；随后在获准的网络边界外使用 Bridge
原生 `start` 成功建立既有 tunnel。该事件属于 `system_event`，不是电路不可行。

## 输入来源与非覆盖路径

任务文件为
`examples/tasks/common-source-cascode-netlist-preview.bridge.json`。其 W/L/VCAS 与条件绑定到
两份既有真实记录：

- 共源 seed OP：SHA-256
  `80efd1aa541a47d99569758f86fbe78d38a5b1871b1890edf52848a0206dc766`；
- 共栅 9 点 OA→`si`→Spectre AC：SHA-256
  `11aed37b22f1028fc43e43835a6069280b4048ddf4bba32fe3b0617bfb83e060`。

worker 对随机路径做 `absent` 预检后创建：

```text
/data/xum/virtuoso_bridge_smoke/vda_netlist_preview_common-source-cascode-netlist-preview_188354991c0f/
  common_source/
  cascode_common_source/
```

执行后资源 inventory 回读该 root 为 313,855 B。它有意保留为 evidence，不是临时进程或
自动 GC 候选的删除授权。

## 真实结果

| 指标 | 普通共源 | 共栅级联 | 级联/共源 |
|---|---:|---:|---:|
| 低频增益 | 4.0031 V/V | 6.1585 V/V | 1.5384× |
| 低频增益 | 12.0479 dB | 15.7895 dB | +3.7416 dB |
| −3 dB bandwidth | 8.1952 GHz | 4.0115 GHz | 0.4895× |
| GBW | 32.8063 GHz | 24.7049 GHz | 0.7531× |
| unity-gain frequency | 31.8641 GHz | 23.6965 GHz | 0.7437× |
| DC supply power | 31.1976 µW | 27.7246 µW | 0.8887× |
| gate-area proxy | 0.0300 µm² | 0.0525 µm² | 1.7500× |
| 最小饱和余量 | 104.192 mV | 14.918 mV | 0.1432× |

两种结构的所有 MOS 都按 `VDS >= VDSAT` 判为 saturation，但级联管仅剩 14.918 mV
余量。因此这次 preview 没有得到“级联全面更优”的结论：它以 75% 更高 gate-area proxy
换取 3.74 dB 增益，同时 bandwidth 下降约 51.1%、GBW 下降约 24.7%，且工作区余量明显
变脆弱。这个结果适合把当前偏置/尺寸标成需要改进或淘汰，而不是直接进入 OA 写回。

## 与既有 OA→si 结果的交叉核对

preview 没有复制目标 OA 的完整 CDF/扩散几何参数，所以它不是同源数值复现。用已有
普通共源恢复态 AC 与共栅最佳点 AC 做只读对照：

| 指标 | preview 级联/共源 | OA→si 级联/共源 | preview 对共源绝对值误差 | preview 对级联绝对值误差 |
|---|---:|---:|---:|---:|
| gain (V/V) | 1.5384× | 1.4038× | −12.758% | −4.389% |
| bandwidth | 0.4895× | 0.5343× | +21.193% | +11.038% |
| GBW | 0.7531× | 0.7500× | +5.732% | +6.164% |
| DC power | 0.8887× | 0.8221× | +9.828% | +18.715% |

四个主要 A/B 方向一致：级联提高增益、降低 bandwidth/GBW，并在当前条件下降低功耗。
但绝对值误差最高超过 20%，所以 preview 只能作方向性 shortlist/falsification；不能替代
OA→`si` 或 ADE，也不能把 preview 数值写成最终规格证据。

## 原始证据与完整性

run record：

```text
artifacts/runs/common-source-cascode-netlist-preview/run-20260726T232507Z.json
SHA-256 10edbfa179dcd60b47630897582365e9963733fb303aeb743b3c34aea01bf2b7
```

普通共源：

- deck SHA-256：`1d3a7fd861aa650b537a5ba691db232007fc0d983f73dc43916cd4743da667d7`；
- manifest SHA-256：`3c039ddf28a1c193b6960c68a6149e61c823b8b4d27106e5a6f892228094d1c8`；
- AC PSF：101,737 B，SHA-256
  `5dfbb6bd28f603d21390ad3f815e5f283d277d5a91a53cca2f348d4752efb38c`；
- 8 项 artifact，analysis complete，241 个 AC sample。

共栅级联：

- deck SHA-256：`510feb6da82a84d0fe88d5df2a0f4fa32bb99b9397e8d3a1707c5df29b51bf31`；
- manifest SHA-256：`2c16aa94d753b895f3e8f4b21488c6e888959cfe6d2453847d4211d9fc72cb8f`；
- AC PSF：115,207 B，SHA-256
  `64d2bc669a1b89a8562a6cb5169c3685026c19f910b694313ac887833b513fd7`；
- 8 项 artifact，analysis complete，241 个 AC sample。

两者都使用已安装并 hash-matched 的远端 guard：SHA-256
`bea4acc9180141c66bf4ef28cbf4f8e602afde7b0367b3d96c5b47cf1337a802`，任务 timeout
600 s、TERM/KILL 间隔 10 s、Bridge wait 615 s。Spectre 各返回 0 error、3 个已保留的
`SFE-1131 scalefactor` warning；warning 没有被静默删除，且没有造成空波形或不完整分析。

首次 run 的环境 probe 还暴露一个独立证据缺陷：该服务器上的 `spectre -V` 不输出版本，
旧解析器因此把唯一包含 `spectre` 的 executable path 误记为 `spectre_version_line`。
这不影响从真实 `spectre.out` 解析的 `tool_version=21.1.0.612.isr15` 或任何 PSF/指标。
follow-up 已把 probe 收紧为只核对 exact executable path，并返回
`spectre_version_source=guarded_simulation_log`；修正后的真实只读 probe 已通过，结果为
`/tools/Cadence/SPECTRE211/bin/spectre`、无 OA access/write。一次探索性的 `spectre -W`
因没有快速返回而被终止；随后 direct exact-name process audit 得到零 Spectre，未留下孤儿。
该事件保留为 `system_event`，没有重跑两份 AC。

修正后本地验证为：preview 专项 `14 passed`、全量 `688 passed`、全部 task example
`196/196` 可 plan；`catalog`、`compileall` 和 `git diff --check` 通过。

## 资源与证据分类

执行前：

- `spectre=0`；
- `si=0`；
- Maestro session=0；
- 既有 `virtuoso=2`。

执行后：

- 本地已知 VDA temp=0；
- `spectre=0`；
- `si=0`；
- Maestro session=0，VDA-managed=0；
- `virtuoso=2`，与执行前一致；
- deletion performed=false。

最终 follow-up 后再次得到同一零 Spectre/si/Maestro 结果；本轮为 smoke 启动的 Bridge
tunnel 随后显式停止，恢复到执行前的 `NOT running` 状态。远端 retained evidence root
没有删除。

原始 DC/OP/AC、连续指标、deck/PSF/log hash 是 `eda_result`；Bridge/Spectre probe、远端
路径和进程 inventory 是 `bridge_readback`；饱和布尔值、面积代理、A/B delta/ratio 与
OA 对照误差是 `software_inference`；任务条件、source bindings 与本轮执行授权是
`user_input`；沙箱 DNS 失败是 `system_event`。

## 保留边界与下一 Gate

- 本 Gate 只证明当前 TSMC N28 `top_tt`、两种结构、一个偏置/负载点的轻量 preview。
- 没有 OA schematic、`si` netlist 或 ADE setup，因此没有同源闭环，也没有 OA 写回授权。
- 绝对数值与 OA→`si` 存在可见差异；preview 的正式用途是少量候选的快速方向性筛选。
- 下一个有价值的实现 Gate 是让理论/结构候选自动编译到同一 preview contract，并只把
  方向稳定或可能改变 objective 的少数候选升级到 OA。若需要更高定量一致性，应显式绑定
  exact device parameter signature，而不是扩大随机 sweep 或加入 HSPICE 平行链路。
