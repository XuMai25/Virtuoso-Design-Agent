# 2026-07-31 existing-schematic 自动接入本地 Gate

状态：**hash-bound read-only existing-schematic onboarding draft compiler locally verified
against retained real Bridge flat and one-level hierarchy inspections; intent resolution to
executable task pending**。

本 Gate 没有启动 Bridge、没有访问或写入 OA、没有运行 `si`/Spectre/Maestro，也没有修改
`virtuoso-bridge-lite`。它只读取仓库中已有任务和 Git 忽略的真实 Bridge run record，验证自动接入
编译器能整理真实结构与完整 CDF 面，同时保持非执行状态。

## 新命令与约束

命令为：

```text
vda onboarding-draft INSPECT_TASK INSPECT_RUN --id ID
  [--child-inspection TOP_INSTANCE CHILD_TASK CHILD_RUN] --output OUTPUT
```

编译器同时绑定 inspect task/run 的 SHA-256、task ID、当前重算 plan token、target、PDK profile、
唯一成功 `schematic.inspect` action 和 `bridge_readback` 来源。任何 demo adapter、plan token 漂移、
写/计算开关为 true、额外 action、缺 placement、参数表与 topology 实例不一致都会拒绝。

输出默认：

- `status=needs_user_intent`；
- `read_only=true`；
- `generic_simulation_draft.executable=false`；
- 全部实例、网络和 pins 冻结；
- `instance_parameter_permissions=[]`；
- 所有回读 CDF 字段原样保留且 `permission_default=not_authorized`；
- pin 角色与参数类型提示标为 `software_inference` 并要求确认。

## Retained real-Bridge flat replay

来源是 `vb_pdk_smoke/vda_l5b_multi_alt_gate_001/schematic` 的既有只读 inspect：

| artifact | SHA-256 |
|---|---|
| inspect task | `604d48cdb10f94732cdd266238f4e7ba92f5deb30b5e2d072e3de9695812ecda` |
| inspect run | `67cda9ff1f8e4a9658e89517ddee75a663b3b48d1246b967fe93b7046016d05f` |
| onboarding draft | `959b50cbf529cc5b41ad37244bcf9d895f28a809addea69ed73ef8e1c1e185ae` |

task ID 为 `existing-schematic-multi-alternative-inspect-live`，plan token 为
`fa8cd472e1672c80`。编译结果：

- topology SHA：`a0bb019a7c81a0d4422e90dca0b5ade814d265cd41544ef245104cb3c7856a41`；
- placement SHA：`b59661b2047674c4a7da81b56fe745a76bac56082e08bdd92d0e7d2f6433ea35`；
- 2 个 top 实例；
- 235 个未过滤 CDF 字段；
- 0 个 hierarchy candidate；
- 0 个参数权限；
- non-executable generic simulation draft。

## Retained real-Bridge hierarchy replay

top 为 `vb_pdk_smoke/vda_l5b_hier_top_001/schematic`，child 为
`vb_pdk_smoke/vda_l5b_hier_child_001/schematic`：

| artifact | SHA-256 |
|---|---|
| top inspect task | `e875cf03950e2857a89a8c93dfb771fe7fb9122a0f9891912c186afd95c449db` |
| top inspect run | `7560fccf615b926fbc73976c7db8c62b76635cf0f9c1a6245bfc742eb2b76682` |
| child inspect task | `0a959166b6f9c0b3c9e4d16ab3f3414c820676b3501eee52f91fab323ff4a0a8` |
| child inspect run | `fa4a0ef709a6dc03f72c413f63268943c176a7ebd948ec7ef87b3f7b3070b002` |
| onboarding draft | `2a0bb654cb17dbddd8138491a432efbe7069a07466722acd3058ecfcde5bacc8` |

top task ID 为 `hierarchy-top-inspect-live`，plan token 为 `a7615c7e6b03fda6`。编译结果：

- top topology/placement SHA 分别为 `d558b418...fd6a`、`8899f1d6...6b52`；
- child topology/placement SHA 分别为 `a0bb019a...6a41`、`b59661b2...ea35`；
- inventory 含 top `XAMP` 与 scoped `XAMP/MN0`、`XAMP/RD0`，共 235 个 CDF 字段；
- 一个 `HierarchyParameterScope` 精确绑定 `XAMP`；
- `IN/OUT/VDD/VSS` 分别成为 input/output/positive-supply/return 候选；
- suggested subcircuit 为 child cell 名，但 terminal order 仍要求确认；
- executable `hierarchy_bindings` 保持为空，未把候选包装成 `si` 事实。

## 失败路径与测试

本地测试覆盖：

- flat 输出确定性与自定义 CDF 字段不丢失；
- role/supply 候选、默认冻结和零权限；
- top/child target、PDK、pin/terminal set 与两类指纹绑定；
- task/run plan token 漂移；
- demo adapter 与非 `bridge_readback` action；
- 开启远端写入的 source task；
- child target 不匹配；
- 同一 child 被多个 top instance 复用时拒绝 scoped write 语义；
- CLI JSON 输出。

相关测试集为 `41 passed`；全量回归为 `830 passed`。实际输出保存在
`artifacts/runs/onboarding/`，属于 Git 忽略的本地派生产物，没有提交 60–70 KiB 的 CDF inventory。

## 证据和边界

- 原始 OA topology、pins、CDF 与 placement 是 `bridge_readback`；
- hash、角色候选、参数类型提示和 hierarchy candidate 是 `software_inference`；
- 最终设计角色、权限、testbench、analysis、规格和预算仍必须来自 `user_input`；
- 本 Gate 重放真实回读，但本轮没有新远端动作，因此不称为新的 live OA/Spectre Gate；
- 它没有证明深层 hierarchy、派生 CDF、shared-child per-instance override、自动 terminal order、
  executable task generation 或 L5B 规格闭环。

后续的显式 onboarding resolution 已在
[`2026-07-31-existing-schematic-onboarding-resolution-local.md`](2026-07-31-existing-schematic-onboarding-resolution-local.md)
闭合：它把 hash-bound draft 与确认后的角色、权限、testbench 和 analysis 编译为普通 safe
TaskSpec。下一道 Gate 转为首个用户实际、非 fixture 模块的最小只读 DC/AC 接入。
