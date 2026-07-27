# 2026-07-27 standalone Spectre 轻量拓扑预评估本地 Gate

## 结论

VDA 已实现 `circuit: netlist_preview`：把理论或人工明确给出的少量 MOS/R/C/独立电压源
结构直接编译为 standalone foundry-model Spectre DC/AC deck，用于 OA 之前的低成本
拓扑 A/B。该路径不启动 Virtuoso、`si` 或 Maestro，不读取或写入 OA，也没有修改
`virtuoso-bridge-lite`。

当前状态是：

**structured standalone Spectre topology-preview locally implemented and worker-mock verified; live nics4304 EDA evidence pending**

后续真实 Gate 已于同日完成，见
[`2026-07-27-standalone-netlist-preview-live.md`](2026-07-27-standalone-netlist-preview-live.md)。
本文件保留实现时的本地证据与当时边界，不用事后 live 结果改写历史测试结论。

本轮没有执行远端计算，因此没有新的真实性能数值，不能把本地 mock 或既有 run 的来源
hash 表述成新的 EDA Gate。

## 为什么使用 Spectre 而不是增加 HSPICE

早期拓扑判断确实不需要先建 OA、跑 `si` 或准备 ADE。但现有 Bridge 已公开
`SpectreSimulator`，VDA 也已有 PSFASCII parser、远端 timeout guard、SSH 资源清理和
TSMC N28 profile。直接网表 Spectre 与 HSPICE 对 agent 都是文本接口，复杂度和延迟属于
同一量级；前者还能沿用最终验证所用的 foundry model/section 和现有证据链。因此本 Gate
只复用 Bridge 的 SSH/Spectre 能力，没有引入第二套 simulator runner、license 条件或
结果 parser。

## 契约与安全边界

- operation 固定为 `simulation.run`，analysis 只允许显式 `dc` 或 `ac`；AC 必须声明
  sweep。
- 任务省略 OA target，拒绝 `allow_remote_write`、`replace_existing`、ADE、topology
  delta、参数应用、搜索空间和任意 raw deck。
- 元件名、节点、model 和 numeric Spectre literal 都经过 schema 校验；MOS 的
  `w/l/nf/m/multi` 由独立字段控制，不能经 model parameter 覆盖。
- 一组 shared sources/R/C 会原样出现在每个变体 deck；变体只声明自己的 MOS、附加元件
  和输出节点。worker 还会以 DC 结果重新核对所有声明电压源的实际节点差。
- 真实执行仍要求 plan token 与 `allow_remote_compute: true`。远端根必须位于
  `/data/xum`，使用随机 nonce 并先断言不存在；没有 OA library/cell/view 或覆盖语义。

## 执行与证据

executor 使用 `bridge.spectre.probe`，跳过 `bridge.probe` 的 SKILL 往返和
`schematic.inspect.before`。worker 复用 Bridge 默认 SSH connection 和
`SpectreSimulator`，每个变体独立运行一份 deterministic deck，并保留：

- deck SHA-256；
- DC、operating-point、AC 原始文件定位与 SHA-256；
- 下载 bundle 的逐文件大小/SHA-256 和聚合 manifest hash；
- Spectre version、warning 和远端 simulator path；
- timeout/TERM/KILL guard 的生命周期证据。

DC 指标覆盖输入/输出电压、逐 MOS Id/VGS/VDS/VBS/VDSAT/gm/gds/gmb、饱和余量、
intrinsic-gain 代理、供电电流/功耗和 gate-area proxy。AC 从声明输入源与输出差分节点的
复数波形提取低频 gain/phase、首个 −3 dB bandwidth、GBW 和 unity。空波形、非有限值、
源电压不一致或未包围 bandwidth 会失败或使 analysis incomplete。

每个变体的原始/连续 simulator 指标标为 `eda_result`；饱和布尔值、面积代理和相对第一个
变体的 delta/ratio 标为 `software_inference`；任务与可选 source hash 标为
`user_input`。结果明确记录：

```text
oa_access_performed=false
oa_write_performed=false
si_netlisting_performed=false
maestro_access_performed=false
```

性能指标中没有 OA `bridge_readback`，因为该路径没有 OA 状态可回读；独立的 Spectre
环境探针和后续远端资源 inventory 仍按 `bridge_readback` 记录。胜出结构仍需后续
OA→`si`→Spectre 或人工 ADE 验证。

## 共源/共栅级联示例

`examples/tasks/common-source-cascode-netlist-preview.bridge.json` 让两个变体共享：

- VDD = 0.9 V；
- VIN = 0.35 V，AC magnitude = 1 V；
- RLOAD = 20 kΩ；
- CLOAD = 2 fF；
- 下管 MN0 W/L = 1.0 µm/0.03 µm。

第二个变体只增加 MNCAS W/L = 0.75 µm/0.03 µm、内部 NCAS 和 VCAS = 0.545 V。
这些条件绑定既有共源/cascode OP 与 AC run record 的 SHA-256，目的是避免随手列值；
worker 当前只把绑定当 `user_input` provenance，并不会用旧结果替代本次 Spectre。

当前计划 token 为 `ad0a3006e33ab934`。计划只有：只读 Spectre probe、结构化 deck
render、remote compute、A/B compare 和本地 evidence persist；`requires_remote_write=false`。

## 进程与磁盘边界

新路径没有新增 PowerShell wrapper。主 VDA 仍直接启动隐藏的 Bridge Python worker；
worker 只创建一个 Bridge SSH client，关闭 persistent shell，并由统一 resource registry/
`finally` 关闭。远端 Spectre 延续已有 timeout guard；本地 worker 延续 cancel marker、
父进程 watchdog 和 Windows Job Object。worker mock 已断言两变体都使用 bounded lifecycle。

因为 run record 只保存 JSON 和哈希，真实 raw evidence root 会有意保留在唯一
`/data/xum/.../vda_netlist_preview_*` 下。这是显式 evidence retention，不是进程泄漏；
live Gate 仍要在前后运行 `vda resources --remote`，证明 Spectre/si/Maestro 进程数回到
基线，并记录新增目录大小。删除 retained evidence 仍需独立用户决定。

## 本地验证

- `python -m pytest`：`687 passed`。
- 新增 preview 专项：`13 passed`，覆盖 schema 注入/重复/断连拒绝、相同条件 deck、
  无 OA planner/executor、subprocess route、DC 指标与 evidence source、空 AC 波形，以及
  两变体真实 worker mock 的 gain/BW/GBW/delta 解析。
- `examples/tasks/*.json`：`196/196` 可 plan。
- 新示例 `vda plan`：通过，`remote_write=false`、`remote_compute=true`。
- `vda catalog`、`compileall` 和 `git diff --check`：通过。

## 下一道 Gate

只做一次已固定示例的只读 live smoke，不扩展候选数量：

- OA target：无；
- OA 写入：否；
- 远端计算：是；
- 预计根路径：
  `/data/xum/virtuoso_bridge_smoke/vda_netlist_preview_common-source-cascode-netlist-preview_<nonce>/`；
- 子路径：`common_source/` 与 `cascode_common_source/`；
- 覆盖风险：无，随机 nonce 且运行前要求根不存在。

该 Gate 要证明真实 TSMC N28 deck、DC OP、两份复数 AC 和 bandwidth 包围都有效，并在
运行前后完成资源 inventory。若成功，结果只能升级为 standalone preview live verified；
不能替代已经存在的 OA→`si` 共源/共栅 Gate，也不自动触发 OA 写入或完整 quality sweep。
