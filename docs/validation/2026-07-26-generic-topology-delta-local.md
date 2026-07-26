# 2026-07-26 通用 topology-delta 可逆契约本地 Gate

> 同日 follow-up 已把契约接入 `existing_schematic` 的真实 Bridge/OA 执行边界，并在全新 cellview 上完成 forward、独立回读、自动 `si`/Spectre DC、inverse 和最终恢复。见[通用 topology-delta 真实同源 Gate](2026-07-26-generic-topology-delta-live.md)。本页保留本地 Gate 当时的范围与结论。

## 范围

本 Gate 只实现并验证 VDA 本地结构契约，没有连接 nics4304、没有运行 Spectre、没有写 OA，也没有修改 `virtuoso-bridge-lite`。它使用本地结构模型、demo adapter 和既有真实 Bridge run record 的只读 `schematic.inspect.before/after`。

因此本记录能证明“结构 delta 可表达、可序列化、可做旧状态校验、可从完整回读核对、可生成逆向 patch”；不能证明通用 delta 已经能安全写入任意真实 cellview。

## 实现

新增 `src/virtuoso_design_agent/topology_delta.py`：

- 将 Bridge/demo 的 `instances/nets/pins` 规范化为排序稳定的 topology snapshot。
- 结构指纹覆盖 instance 名称、master library/cell/view、端子连接、位置及其他结构属性，以及 net/pin；实例 CDF `parameters/params` 明确排除，继续由现有参数写回与回读契约负责。
- 首版只允许八类 operation：`add/remove_instance`、`reconnect_terminal`、`replace_master`、`add/remove_net`、`add/remove_pin`。
- 删除、重连、替换均带 exact 旧状态 CAS；添加要求对象不存在且引用 net 已存在；删除 net 要求不再被端子或 pin 引用。
- 编译时先在本地应用前向 patch、计算 after SHA-256，再逆序生成 inverse patch并证明恢复完整 before snapshot。
- 实际验证时重新规范化 after readback，与本地预测的完整结构逐项相等；不是只检查对象计数或 return code。

执行器保留已有各模板的专用语义断言，并在其通过后增加 `schematic.transform.topology-delta.audit`。专用断言仍负责“这是不是正确的源退化/尾管/电流镜”等电路意图；通用审计负责“声明的图操作是否完整、确定、可逆”。两层没有互相替代。

## 测试覆盖

本地测试覆盖：

- 共源级 `MN0.S: VSS -> NSRC`、新增 `NSRC` 和 `RS0` 的最小三操作推导。
- 八类 operation 的正向 apply、结构指纹与逆向恢复。
- JSON round-trip 与不同 readback 顺序下的稳定指纹。
- 参数值变化不污染 topology fingerprint，结构 no-op 可显式记录。
- stale before fingerprint、端子旧值冲突、缺失 after 对象、悬空 net、未开放的 placement move 均拒绝。
- 现有 demo `schematic.transform` 全套执行器回归，并确认 run record 新增统一 audit，证据源为 `software_inference`。

验证命令：

```powershell
.\.venv\Scripts\python.exe -m pytest tests\test_topology_delta.py tests\test_executor.py
.\.venv\Scripts\python.exe -m pytest
```

结果：定向 `111 passed`；全量 `616 passed`。`vda catalog` 与
`vda plan examples/tasks/inverter-close-loop.demo.json` 也成功完成。

## 既有真实回读迁移

以下检查只读取旧 run record，不重放远端操作：

### 共源源极退化

- 来源：`artifacts/runs/common-source-topology-patch-add/live-20260723.json`
- 原始 before/after：真实 `bridge_readback`
- 推导 operations：`add_net(NSRC)`、`reconnect_terminal(MN0.S, VSS -> NSRC)`、`add_instance(RS0)`
- before SHA-256：`c23ac5e7e5540359e770cf37dd5071948fe27be49319329e9dcec5f7ebf5ebb0`
- after SHA-256：`8a964225f02855905a6a49cde440351ca511e84bad44b38bdb30df14053f5d9f`
- forward 完整匹配：通过
- inverse 恢复 before：通过

### 差分对电流镜负载

- 来源：`artifacts/runs/differential-pair-current-mirror-gate6/09-current-mirror-mp-transform-20260723.json`
- 原始 before/after：真实 `bridge_readback`
- 推导 operations：删除 `RD0/RD1`，添加 `MP0/MP1`
- before SHA-256：`482be725952705813d8640d25c78d4bfb8dd317e2b72133b9e0dec06fac2b888`
- after SHA-256：`22146f67d2f855ffb3a379d079bde6222eee817e554af14a14de007dd02fe4de`
- forward 完整匹配：通过
- inverse 恢复 before：通过

上述 SHA、operation 推导和 inverse 验证属于 `software_inference`；它们绑定的原始 OA 结构来自旧记录中的 `bridge_readback`。本 Gate 没有产生新的 `eda_result`。

## 尚未闭合

- 尚未把任意预声明 topology-delta 编译为 Bridge/OA editor 调用；远端仍只开放现有专用 transform。
- 尚未在新 cellview 做真实 forward → 独立 readback → inverse → 独立恢复回读。
- snapshot 当前抽象 instance/net/pin 结构，不替代 Bridge 对 wire、label、shape 和 placement 的更细恢复证据。
- 保存成功但 post-readback 失败时，尚无通用自动回滚；不能把本地 inverse 证明表述成远端事务。
- topology fingerprint 有意不含 CDF 参数；master replacement 后的参数合法性、callback 和网表生效仍需现有参数证据链及后续真实 Gate。

下一道 Gate 是只在用户明确授权的新 `vda_` cellview 上，复用 Bridge 现有 editor/reader 执行一份预声明契约，完成 before fingerprint、逐操作写入、after 完整回读、`si` 网表结构核对、inverse 恢复和最终独立 readback。默认 `replace_existing=false`，不得以本地 inverse 存在作为覆盖或远端写入授权。
