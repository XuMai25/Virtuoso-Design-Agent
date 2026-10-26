# 2026-07-26 通用 topology-delta 真实同源 Gate

## 结论

VDA 已把预声明 topology-delta 接入 `existing_schematic` 的真实 Bridge/OA 边界，并在一个全新、不覆盖的 TSMC N28 cellview 上完成：

```text
nominal OA
  -> exact forward topology delta
  -> 独立 OA 回读
  -> RS0 参数写入与回读
  -> si 自动网表
  -> Spectre DC 与 KCL/工作区解析
  -> exact inverse topology delta
  -> 独立 OA 恢复回读
  -> 恢复后 si/Spectre DC
```

这条结果可以称为 **bounded generic topology-delta OA execution, same-source netlisting, and exact inverse restoration verified**。它不是任意 schematic 编辑器，不是远端事务，也不证明任意新拓扑已完成设计质量闭环。

## 授权与目标

- PDK/profile：`nics4304_tsmc28`，nominal `top_tt`、27 ℃。
- 目标：`vb_pdk_smoke/vda_generic_topology_delta_001/schematic`。
- 创建、forward、RS0 参数写入和 inverse：`allow_remote_write=true`。
- forward/restored DC：`allow_remote_compute=true`。
- library whitelist：`vb_pdk_smoke`；cell 前缀：`vda_`。
- `replace_existing=false`；创建前两次只读 inspect 均确认目标不存在，第二次在 worker 的显式 existence check 下干净失败。
- 远端产物：唯一 `/data/xum/virtuoso_bridge_smoke/vda_<task>_<nonce>/`，不写 `/home/xum`。

用户在执行前确认了上述完整 bundle；本 Gate 没有把该确认扩展到其他 cellview。结束时目标保留为恢复后的 nominal 共源级，未删除 cellview。

## 执行契约

新增 task 面只接受：

- `operation: schematic.transform`；
- `circuit: existing_schematic`；
- `target.view: schematic`；
- `topology_delta.direction: forward|inverse`；
- 一份完整、不可变的 before/after SHA-256 与 operations/inverse operations。

它拒绝 semantic parameters、`replace_existing=true`、非 schematic view，以及把 topology contract 附到其他 operation/circuit。`vda topology-compile` 只从成功的 `schematic.inspect` 和显式 operation JSON 生成 contract；planner 在计划中显示方向、操作数和 exact 输入/输出 SHA。

worker 没有复制 Bridge 的 SSH、OA 保存、reader 或文件传输实现，也没有接受用户提供的 SKILL 字符串。它只把当前真实开放的 operation 编译到 Bridge 现有 append editor：

- `add_instance` / `remove_instance`；
- `reconnect_terminal`；
- `add_net` / `remove_net`，由端子 label/stub 在 OA 中物化并由完整回读证明。

新增实例必须有有限 `xy`、明确 orientation 和 `numInst=1`。master library 只允许来自原结构、profile tech library 或 `analogLib`。保留实例的全部回读参数在写前/写后逐项相等。`replace_master` 和 `add/remove_pin` 虽可序列化、本地 apply 和生成 inverse，但真实 OA compiler 会明确拒绝，等待独立 CDF/pin-geometry Gate。

## 真实 forward 与结构证据

基线 cell 由既有 common-source creator 建立，独立 inspect 得到：

- 实例：`MN0`、`RD0`；
- nets/pins：`IN/OUT/VDD/VSS`；
- topology SHA-256：`4471c93914bf888a3c17df01ca3b97625353e8e0e5b3e2f5110e4d5684d4dea9`。

`03-contract-20260726.json` 声明三项 forward：

1. 添加 `NSRC`；
2. `MN0.S: VSS -> NSRC`；
3. 在 `[0.0,-1.3]` 添加 `analogLib/res` 实例 `RS0(PLUS=NSRC, MINUS=VSS)`。

编译器自动生成逆序 inverse：删除精确匹配的 `RS0`、把 `MN0.S` 接回 `VSS`、删除已无引用的 `NSRC`。预测 after SHA 为 `68e0cbcd7d00900fde010e7b44c354d4cd73cc525434cff943db4274845a67ed`。

`04-forward-20260726.json` 的 Bridge worker 和 executor 分别完成两层检查：

- `bridge_readback`：输入 SHA、预期/实际输出 SHA 完全一致，4 条 editor command，`append_mode=true`、`replace_existing=false`，保留实例参数未改变；
- `software_inference`：用任务中预声明 contract 对 before/after 两次完整 inspect 重算，`output_readback_match=true`、`roundtrip_restored_input=true`。

随后 `05-forward-independent-inspect-20260726.json` 由独立任务再次读到 `MN0/RD0/RS0` 与 `NSRC`，没有依赖 transform 自己返回的对象。

## 参数进入同源网表与 DC

`06-rs0-apply-20260726.json` 通过现有通用实例参数面把 `RS0.r` 设置为 `1K`，完成 callback 后立即回读及独立 after 回读。参数证据不混入 topology fingerprint。

`10-forward-dc-network-20260726.json` 随后从目标 OA 自动运行 `si`，结构化网表包含：

```text
MN0 OUT IN NSRC VSS
RD0 VDD OUT 20K
RS0 NSRC VSS 1K
```

- topology：`source_degenerated_common_source`；
- `si` netlist SHA-256：`b17a2cecd83cceb2ee97add0d32f25d47addfc9b2e2fdda297cb47d455cd8bbd`；
- raw DC SHA-256：`30a4469a8cdacb6978b9ad9314c6a8b11be7a687b3d84f71742bf7b04d4c7f44`；
- opinfo SHA-256：`ed2f39dc89e6be360aa73e208d873844f1d882334bbcb6897f565bfa981b688e`；
- OA/netlist structure 与参数一致性：matched；
- analysis completeness：complete。

主要 `eda_result`：

| 指标 | 结果 |
|---|---:|
| `Id` | 24.819604773 µA |
| `V(NSRC)` / RS drop | 24.8189289 mV |
| RS current | 24.818928917 µA |
| source-current mismatch | 0.002723% |
| `VDS` | 0.378802 V |
| `VDSAT` | 0.096121 V |
| saturation margin | 0.282681 V |
| supply power | 22.3370488 µW |

这些值只证明源退化结构、参数和 DC/KCL 已同源执行；本任务没有声明 gain、BW、noise 或线性度规格，不能据此称设计完成。

## inverse 与恢复后同源复核

`12-inverse-network-20260726.json` 消费同一 contract 的 `inverse` 方向：

- 输入 SHA 精确等于 forward after SHA；
- 删除 `RS0`、恢复 `MN0.S=VSS`、删除 `NSRC`；
- 实际输出 SHA 精确等于原 before SHA；
- 保留实例参数未改变。

`13-restored-independent-inspect-20260726.json` 独立读回仅有 `MN0/RD0` 与 `IN/OUT/VDD/VSS`；重新规范化后的 topology SHA 是原值 `4471c939...d4dea9`。

`14-restored-dc-20260726.json` 再次运行 OA→`si`→Spectre：

- topology：`common_source`；
- 网表只含 `MN0/RD0`；
- netlist SHA-256：`88a70295c99dc84f873fba27e5f605647a8354fa0796425ed5c22eb60dc73486`；
- raw DC/opinfo SHA-256：`a4a91818953017c6dedad52622d1ffe6e99c52c0dd39fa069eaa5021a321d2a4` / `811170e6ed7a340b929f61a73c6b63a430ee4f4ad574de03bf3c52cfa5a7c159`；
- `Id=31.5618672 µA`、`VDS=0.268755 V`、`VDSAT=0.105276 V`、saturation margin `0.163479 V`、supply power `28.4060196 µW`；
- analysis completeness：complete。

恢复结论同时依赖完整 OA topology fingerprint 与新的同源网表/EDA 结果，不是只看 inverse 命令返回 0。

## 失败、恢复与资源边界

两次 forward DC 和第一次 inverse 在当前本地网络沙箱内因 OpenSSH DNS 被重写为 `codexsandboxoffline` 而失败。它们都在 Spectre/transform 前停止，记录为 `system_event`，不是电路不可行：

- forward 写入后的独立 inspect 证明 OA 仍停在已知 forward checkpoint；没有重放不确定 payload；
- 在已授权的真实网络上下文重跑后，DC 成功；
- inverse 首次只在 `bridge.probe` 失败，确认没有 transform action 后才重跑。

`15-resource-audit-20260726.json` 为只读 dry-run：远端 `spectre=0`、`si=0`、Maestro session=0、VDA-managed session=0；`virtuoso=2` 是既有 Cadence/Bridge 进程，不属于本 Gate。三个本次 scratch root 只被列为证据/审查对象，未删除。

本地首次 post-Gate 盘点把父 worker 已退出的两条 `ssh.exe` 误判为孤儿并在逐 PID/命令行核对后终止。复查既有生命周期 Gate 后确认它们实际是 Bridge 有意跨请求复用的一条 jump-host tunnel 链，而非逐 request 泄漏。随后连续三次独立只读 inspect 均成功；第一次重建后，三次请求前后固定为相同 PID `61436/60988`、相同启动时间，两条链没有增加，Python/SCP/Spectre/si 均为 0。`19-local-resource-audit-20260726.json` 另确认 VDA transient/cancel marker 为 0，删除执行为 false。

Bridge 在新建 tunnel 时会在 `%TEMP%` 留下一个 `vb_tunnel_stderr_*.log`；普通复用请求不会继续新增，但历史 tunnel 重建已累积多份 0–134 B 小文件。这属于第三方 Bridge 的临时日志生命周期债务。本轮没有修改第三方仓库，也没有获得删除这些历史文件的授权；后续若修 Bridge，必须继续使用其已存在的 backup ref、`codex/vda-transport-recovery` 隔离分支和 `LOCAL_VDA_PATCH.md` 变更记录。

## 证据来源

- OA existence、结构、参数、transform 前后 readback：`bridge_readback`。
- `si` 网表、Spectre raw/opinfo、DC/KCL/工作区指标：`eda_result`。
- contract 编译、SHA 规范化、inverse 证明、保留/资源判读：`software_inference`。
- target、operation、参数值、授权范围、analysis 条件：`user_input`。
- DNS 沙箱失败、重试/checkpoint 与本地进程事件：`system_event`。

## 主要本地证据

| 文件 | SHA-256 |
|---|---|
| `03-contract-20260726.json` | `2561deb4f77fcd0ccc461c4d0fec61d1d0421e5dc1b752b9eaec3a1249d70319` |
| `04-forward-20260726.json` | `d7a8279eaa7d20c5f63c45a3967593b17af27444478249c78946f15bf8e45f12` |
| `10-forward-dc-network-20260726.json` | `14a516abd03a27ee863679e9753f88616026cae0b14a1a7b4502857df180164e` |
| `12-inverse-network-20260726.json` | `7c5b4fc61679bbe2506fcfdb34986a13b848f420015903edef8847d21869e829` |
| `13-restored-independent-inspect-20260726.json` | `ec9aab23008aade725279c1e9779ce0345261289780c1c4ba2d619b595f58d3b` |
| `14-restored-dc-20260726.json` | `5962d5d213f933e7dd3b3283c9e9f6825765e3bc153e5727e6f35ad78855b514` |
| `15-resource-audit-20260726.json` | `1f8e3438203690d2dd8e389925f3fa27e32b59ad8f5b149917c79b2c04215e80` |

所有路径都位于 `artifacts/runs/generic-topology-delta-gate/`。run record 不是随 package 提交的产品代码，但其文件 hash 固化在本验证记录中。

## 本地回归与未验证边界

实现新增了 model、catalog、planner、CLI、demo、executor、subprocess 和 worker 测试，覆盖：

- forward/inverse 序列化、计划 token 和 task 边界；
- stale input SHA、完整 output drift、伪造 adapter 证据；
- worker action 路由、master library 边界、有限 placement；
- 保留实例参数不变；
- editor 中途失败时丢弃未保存 edit；
- `replace_master`/pin operation 在真实 compiler 明确拒绝；
- 目标不存在时 inspect 干净失败。

最终本地验证结果：

- `pytest`：`628 passed`；
- `examples/tasks/*.json`：`162/162` 均可生成计划；
- `python -m compileall -q src tests`：通过；
- `vda catalog`、反相器 demo plan、generic forward/inverse plan：通过，plan token 与 live 执行时一致。

第三方 `virtuoso-bridge-lite` 仍停在其既有隔离分支 `codex/vda-transport-recovery`，本 Gate 未产生任何 Bridge 源码、测试或文档改动。

仍未闭合：

- 通用 `replace_master` 的 CDF/master callback 与参数迁移；
- pin 创建/删除的方向、位置、wire/shape 证据；
- 通用 wire、label、shape、instance move/reshape snapshot；
- 保存成功但 post-readback/transport 失败后的自动 inverse；
- 并发 editor/session CAS 与任意 PDK/master；
- 把任意图自动分类为可仿真的 testbench，以及通用 DC/AC/noise/PSRR 激励契约；
- 当前 generic live 只跑了共源 DC，没有以它证明 AC、noise、transient 或规格选优。

下一道拓扑 Gate 应新建一个不覆盖的差分对 cellview，用同一 topology contract 在 PMOS 电流镜有源负载差分对上增量加入对称 `RS0/RS1`，先完成 forward/readback/`si`/inverse，再按 DC→差模 AC→共模 AC/CMRR→noise→transient/THD→ICMR 顺序迁移既有分析。前一层失败即停止，PVT 保持可选，不先做大规模搜索。
