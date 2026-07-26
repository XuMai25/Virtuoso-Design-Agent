# 2026-07-26 topology post-save 恢复与 master/CDF 迁移本地 Gate

状态：**exact-state topology recovery and controlled symbol-master/CDF migration locally verified; live OA/si verification pending**。

## 范围

本 Gate 的实现与故障注入只修改 VDA 仓库；随后额外连接 nics4304 做了目标 PDK master 的只读兼容性探针。没有新建或修改 OA 对象，没有运行 `si` 或 Spectre，也没有修改 `C:\Users\aknigsesl\tools\virtuoso-bridge-lite`。

它闭合两个此前明确保留的实现缺口：

1. topology editor 已保存，但后续 CDF、结构或保留参数审计失败时的受限自动 inverse；
2. 单实例 `replace_master` 与显式 CDF 参数子集迁移。

本页不把本地生成的 SKILL、return code 或 demo 结果称为真实 EDA 证据。

## 保存后恢复协议

编辑 batch 抛错时仍沿用旧边界：只 purge 目标 cellview 的未保存 edit，不调用 `dbSave`。

只有 editor 正常退出、因而可能已保存之后发生失败，worker 才进入恢复判定：

- 再做一次独立 Bridge schematic readback；
- 若 readback 失败，记录 `state_unknown_no_write`，不做第二次 OA 写入；
- 若实际 topology SHA 与本次方向的预期输出不同，记录 `unexpected_topology_no_write`，不猜测差异、不部分 inverse；
- 只有二者完全相等，才执行同一 immutable contract 的相反方向；
- inverse 后独立回读，要求 topology SHA 恢复输入值，并要求所有实例的完整参数表与初始 Bridge readback 相等；
- 原请求仍返回失败，并附带恢复状态。`restored` 只表示失败后的 OA 状态已恢复，不表示原变换成功。

这不是数据库事务。进程或网络中断后若无法重新确认状态，VDA 选择停止写入并保留不确定性。

## `replace_master` 边界

真实 worker 的新 compiler 只接受 instance-scoped `symbol` master 替换：

- 写前完整 topology fingerprint 必须命中 contract 输入；
- 目标 master library 仍受“原结构、profile tech library、`analogLib`”边界约束；
- read-only preflight 验证目标 master 存在；
- 新旧 master 的端子名称、数量、方向及每个 pin figure 的本地 bBox 必须完全一致，防止旧 label/stub 因 symbol pin 位移而脱网；
- 写命令按实例名唯一选择，再次核对旧 library/cell/view，随后只设置该实例的 `master`，并立即核对新 master；
- 最终结构是否正确仍由保存后的完整独立 readback 决定。

VDA 通过 Bridge 的通用 SKILL channel 和现有 append editor 执行上述命令。没有复制或修改 Bridge 的 SSH、editor、保存、reader 或 transport 实现。

## TSMC N28 只读 master 探针

真实 Virtuoso 6.1.8 read-only SKILL 返回：

- `tsmcN28` 的普通 NMOS 候选包含 `nch_lvt_mac`、`nch_mac`、`nch_hvt_mac`、`nch_ehvt_mac` 等；没有把本地测试里使用的占位名 `nch_rvt_mac` 当成真实 PDK cell。
- `nch_lvt_mac -> nch_mac`：端子均为 `B/D/G/S`，名称、direction 和四个 pin bBox 全部相等；目标 cell CDF 含 `Wfg/l/fingers/m`。
- `pch_lvt_mac -> pch_mac`：端子均为 `B/D/G/S`，名称和四个 pin bBox 相等；目标 cell CDF 含 `Wfg/l/fingers/m`。本次 PMOS 探针没有单独输出 direction 比较，所以不把它记成已证实。

探针只打开 PDK symbol/cell CDF 的只读对象。两个临时本地脚本及其 `.pyc` 均已删除，Bridge client 在 `finally` 中关闭。随后 `vda resources --remote --json` 得到 `spectre=0`、`si=0`、VDA managed Maestro session=`0`；两只已有远端 Virtuoso 进程属于共享 Cadence 环境，未由本探针新增。direct SSH inventory 遇到一次 DNS 解析错误，Bridge SKILL fallback 仍完成远端只读进程盘点。

为复用现有 common-source/inverter 的严格 parser，而不是为 smoke 写一次性 Spectre deck，新增 `nics4304_tsmc28_svt` profile。它只继承默认 N28 的工艺、model、corner 和远端路径，并显式把器件 master 改为 `nch_mac/pch_mac`；默认 `nics4304_tsmc28` 及其 LVT master 不变。切换 profile 后必须重新建立 OA/`si`/Spectre 证据。

## CDF 迁移契约

含 `replace_master` 的真实 execution 必须为每个被替换实例提供且只提供一条 `master_parameter_migrations`：

```json
{
  "instance": "MN0",
  "expected_parameters": {"Wfg": "1u", "l": "30n"},
  "parameters": {"Wfg": "1u", "l": "30n"},
  "undeclared_parameter_policy": "record_only"
}
```

- `expected_parameters` 是写前逐字段 CDF CAS；
- `parameters` 是换 master 后必须通过 callback 持久化并独立定向回读的新值；
- inverse 自动交换两组值，因此同一 contract 可恢复旧 master 与旧 CDF 值；
- 两组参数名可以不同，用于明确的 CDF 名称映射；
- `undeclared_parameter_policy` 必须显式为 `record_only`。未列字段的完整前后表会保存在 `bridge_readback`，但 VDA 不声称它们保持、可写或已经迁移。

参数仍不进入 topology SHA。迁移对象本身进入 task JSON、计划 token 和 contract JSON，避免共享结构 hash 被误当成参数已经绑定。参数写入复用 Bridge 公共 `set_instance_params(..., param_filters=None)`；首次 targeted readback 不一致时最多按声明顺序逐字段重放一次。

## 测试覆盖

新增或收紧的测试包括：

- migration JSON round-trip、孤立 migration 拒绝、CLI contract 编译；
- planner 明确披露旧 CDF CAS、callback 写入和独立回读；
- instance-scoped master CAS SKILL，不使用会影响同 master 全部实例的 header 替换；
- symbol 端子数量、名称、方向、pin bBox 和目标 CDF 字段预检；
- 缺少 migration 的真实 worker 在写前拒绝；
- master+CDF 正向成功的结构与 targeted readback；
- demo forward/inverse 同时迁移并恢复声明参数，证据保持 `software_inference`；
- 已保存且 topology 精确时，保留参数审计失败触发 inverse 并恢复完整参数表；
- CDF callback 失败后恢复旧 master 与旧参数；
- 出现未声明 topology 漂移时不执行 inverse；
- post-save readback 与恢复 readback 都失败时不执行第二次写入；
- 原有未保存 command failure 仍只 purge unsaved edit。

验证命令与结果：

```powershell
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\vda.exe catalog
.\.venv\Scripts\vda.exe plan examples\tasks\inverter-close-loop.demo.json
git diff --check
```

- Python：`648 passed`；
- 全部 `182` 份 example task 均可生成计划；
- catalog：成功，并明确 master/recovery 为 local、live 待验证；
- inverter demo plan：成功；
- whitespace check：通过。

## 证据分类

- contract、结构 SHA、inverse 决策、故障注入和 profile 继承：`software_inference`；
- 合成 transport/readback 失败：`system_event` 测试替身；
- migration 中声明的旧/新 CDF 字符串与 `record_only` 策略：任务输入，`user_input`；
- PDK master inventory、端子/pin bBox 与 CDF 字段探针、远端资源盘点：`bridge_readback`；
- 本 Gate 没有新的 `eda_result`。只有后续 `si`/Spectre smoke 才会产生。

## 尚未闭合与下一 Gate

- `rbInst~>master = rbMaster` 在当前 Virtuoso 6.1.8/TSMC N28 PCell 上尚未 live；
- 新 master 的 CDF callback、subMaster 生成及完整参数表变化尚未真实观测；
- `si` 是否读取新 model、W/L/fingers/m，以及 Spectre 是否得到可解释结果尚未验证；
- 自动 inverse 尚未在真实已保存失败点触发；
- `record_only` 不等于全 CDF 迁移；
- pin add/remove、不同 symbol pin 几何、wire/label/shape 通用 snapshot、并发 editor 仍拒绝或待 Gate。

下一 Gate 已固定为全新且不覆盖的 `vb_pdk_smoke/vda_master_migration_001/schematic`。一次真实 `existing_schematic` inspect 已按预期返回 “target schematic does not exist”，对应失败记录为 `artifacts/runs/common-source-master-migration/00-target-absence-preflight.json`；这证明执行前目标空缺，不是写入失败。其后资源审计仍为 `spectre=0`、`si=0`、VDA Maestro session=`0`。

获授权后的顺序为：创建 LVT 共源级 → before OA/CDF → LVT `si`/Spectre AC 基线 → `MN0: nch_lvt_mac -> nch_mac` forward → SVT OA/CDF → SVT profile 下的 `si` master/model/W/L 与 Spectre AC → inverse → 最终 LVT OA/CDF/`si`/AC 恢复。远端仿真 scratch 只允许 `/data/xum/virtuoso_bridge_smoke/vda_common-source-master-migration-{lvt-ac,svt-ac,restored-lvt-ac}_<run-id>`。故障恢复 live smoke 应在正常 round-trip 之后单独设计可控注入，不能为了测试恢复而破坏唯一基线。
