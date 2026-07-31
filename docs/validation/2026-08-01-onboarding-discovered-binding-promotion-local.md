# 2026-08-01 onboarding discovered-binding promotion local Gate

## 结论

本 Gate 已在本地打通 `parameters.binding.discover` 证据到普通 onboarding TaskSpec 的确定性
handoff：不再人工抄写 `MNCAS.Wfg -> w`，也不会丢失 TSMC N28 CDF callback 同时引起的
`ad/as/nrd/nrs/pd/ps` 变化。输出仍关闭远端计算与 OA 写入；本记录不包含新的 Spectre 结果，
也不把本地编译成功表述成设计闭环。

## 证据输入

- onboarding draft：`artifacts/runs/binding-discovery/fresh-inspect-draft-20260731.json`
- resolution：
  `examples/onboarding/existing-schematic-cascode-discovered-binding-dc-ac-resolution.json`
- discovery task：`artifacts/runs/binding-discovery/mncas-wfg-discovery.live.json`
- discovery run：
  `artifacts/runs/binding-discovery/mncas-wfg-discovery-live-20260801-retry3.json`
- discovery task ID：`binding-discovery-mncas-wfg-live-20260731`
- discovery plan token：`74c70848fbfc9317`
- discovery task SHA-256：
  `f748b774e6b996ee496149d15a142718bb98c34e26e30414fc441ade055f516e`
- discovery run SHA-256：
  `fd50e0902bbed3a1843ecfea0dba0dd8915001f7105d56de7f65697085950f20`
- target topology SHA-256：
  `2e27d1c68012bc1e7adc0da936a648febde4b685cfa1391512a2d401fb291a51`
- complete CDF baseline SHA-256：
  `320ef706ec51d47ffd3e009316e34aca3111fad1daad51d10684486d03c5d812`
- complete CDF field count：`233`

编译器没有信任 run 中保存的旧分类标签，而是用当前分类器重新校验三阶段本地 artifact、manifest、
baseline/probe/restore 签名和完整 CDF。原 live run 仍原样保留当时的 `partial` 状态；本次提升属于
`software_inference`，没有改写历史 `eda_result`。

## 编译命令

```powershell
.\.venv\Scripts\vda.exe onboarding-resolve-bindings `
  artifacts\runs\binding-discovery\fresh-inspect-draft-20260731.json `
  examples\onboarding\existing-schematic-cascode-discovered-binding-dc-ac-resolution.json `
  --binding-source `
    artifacts\runs\binding-discovery\mncas-wfg-discovery.live.json `
    artifacts\runs\binding-discovery\mncas-wfg-discovery-live-20260801-retry3.json `
  --output artifacts\runs\binding-discovery\mncas-wfg-promoted-dc-ac.safe.json `
  --record-output artifacts\runs\binding-discovery\mncas-wfg-promoted-dc-ac-compilation.json
```

## 编译结果

- task ID：`onboarding-cascode-discovered-binding-dc-ac`
- target：`vb_pdk_smoke/vda_cs_cascode_gate_001/schematic`
- PDK profile：`nics4304_tsmc28`
- operation：`simulation.run`
- stage：一次 shared-netlist，依次执行 DC、AC
- primary binding：`MNCAS.Wfg -> w`
- derived callback checks：`ad/as/nrd/nrs/pd/ps`
- compiled safe task canonical UTF-8/LF SHA-256：
  `62564c46d99ba0a0212384ebbc30e861461ce026a9869a7cbf2bb2bc737a850e`
- compiled safe plan token：`06b9987ea5b404df`
- `allow_remote_compute: false`
- `allow_remote_write: false`
- `replace_existing: false`

binding 中保存 discovery task/run 哈希、原 classification、topology/CDF 哈希和完整 callback 清单，
因此后续修改发现证据会改变普通任务的 canonical identity。旧 TaskSpec 没有这些可选字段时，字段以
`None` 省略，既有 discovery task 的 plan token 复算仍为 `74c70848fbfc9317`。

普通 OA→`si` parser 现在不仅检查 `Wfg` 与 `w`，还会在同一实例上逐项比较六个 callback 的 OA
readback 与实际 netlist 参数。缺项或工程单位不等价会在 Spectre 结果判定前失败；通过时分别记录
`bridge_readback` 和 `eda_result`，一致性结论记录为 `software_inference`。

## 本地拒绝与回归

新增测试覆盖：

- callback 全部匹配时允许继续；
- 任一 callback 缺失或不一致时拒绝；
- callback 字段重复或与 primary 字段冲突时拒绝；
- discovery 缺少完整镜像 callback 时拒绝提升；
- resolution 已手填冲突 mapping 时拒绝；
- 重复 evidence source 拒绝，以及 discovery task/run 来源哈希绑定；
- 同一 handoff 可编译普通 `simulation.run`，也可编译两点 `design.tune`；
- 旧 task canonical JSON/token 保持兼容。

Windows CLI 明确按 UTF-8/LF 字节写入 task 和 compilation record，避免文本模式把换行改成 CRLF、
从而令落盘 task 与 compilation 中的 SHA-256 不一致。

实际执行：

```text
900 passed in 5.97s
vda catalog: passed
vda plan <compiled-safe-task>: token 06b9987ea5b404df
vda onboarding-resolve-bindings --help: passed
git diff --check: passed
```

## 尚未验证的边界与下一 Gate

本 Gate 没有连接远端、没有写 OA、没有运行 Spectre。下一步应只做一次直接下游验证：把同一编译
结果仅开启 `allow_remote_compute`，对现有目标执行一次 OA readback、一次 `si` netlisting 和共享
DC/AC，确认运行期 callback 检查消费的是实际 OA 与网表，而不是 fixture。该 smoke 不需要 OA 写入，
不创建或替换 cellview；执行前仍须用新 TaskSpec 重算 token 并明确授权远端计算。

任意其他 OA 字段、PMOS、其他 PDK、层次化 per-instance callback、并发人工编辑以及 callback
公式跨 PVT 的稳定性仍未由本记录覆盖。
