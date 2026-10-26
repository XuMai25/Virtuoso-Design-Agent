# 2026-07-31 existing-schematic onboarding resolution 本地 Gate

状态：**hash-bound onboarding draft plus explicit user intent compiled into normal safe
flat simulation and one-level hierarchy tuning TaskSpecs; retained evidence replay and planner
validation passed; no new EDA execution**。

本 Gate 没有启动 Bridge、没有访问或写入 OA、没有运行 `si`/Spectre/Maestro，也没有修改
`virtuoso-bridge-lite`。输入是上一 Gate 从真实 Bridge inspect 生成的两份本地草案；本轮只新增
resolution 编译、失败门和普通 `TaskSpec`/planner 验证。

## 新命令

```text
vda onboarding-resolve DRAFT RESOLUTION --output TASK
```

resolution 不能覆盖 target、PDK、topology SHA、冻结对象、child scope 或 safety。编译器从草案
继承这些字段，并固定输出：

- `circuit=existing_schematic`；
- `create_if_missing=false`；
- `allow_remote_compute=false`；
- `allow_remote_write=false`；
- `allowed_library=<draft target library>`；
- `replace_existing=false`。

当前 operation allowlist 只有 `simulation.run` 和 `design.tune`。生成的文件是现有模型接受的普通
TaskSpec，不需要新 planner 或 executor。

## Flat retained-evidence compilation

输入草案来自 `vb_pdk_smoke/vda_l5b_multi_alt_gate_001/schematic`：

| artifact | SHA-256 |
|---|---|
| onboarding draft | `959b50cbf529cc5b41ad37244bcf9d895f28a809addea69ed73ef8e1c1e185ae` |
| explicit resolution | `ad5bf4461daa309cca9c90fbf0ca7dbc7c322ae5e191532b2f495e936cf75da1` |
| generated TaskSpec | `8ab8b4ee219efe40d8e849139f5a18d3030848664840d16fa75607818aef75b5` |

编译结果：

- operation 为 `simulation.run`、analysis 为 AC；
- target/PDK/topology SHA 与草案一致；
- 7 个确认角色全部存在于 top OA graph；
- `MN0.Wfg/l/nf/simM` 与 `RD0.r` 均来自 235 字段 inventory；
- 五个 permission 与五个 OA→`si` binding 完全相同；
- source/load/transfer/DC/OP/AC metric 均通过现有 schema；
- planner token 为 `2edc16bd357b2dc6`；
- 两个远端安全开关均为 false。

## One-level hierarchy retained-evidence compilation

输入草案来自 `vb_pdk_smoke/vda_l5b_hier_top_001/schematic` 和已绑定 child：

| artifact | SHA-256 |
|---|---|
| onboarding draft | `2a0bb654cb17dbddd8138491a432efbe7069a07466722acd3058ecfcde5bacc8` |
| explicit resolution | `923329ae1565801162568beb974c23caccc1807e87ce1563285a5e90add3ac17` |
| generated TaskSpec | `58e7e49542f1d6a3de8497463f29d6f7ae1a00d4c108ab208731175f46c1a85b` |

编译结果：

- operation 为 `design.tune`；
- exact `XAMP` child topology/placement scope 从草案继承；
- hierarchy binding 的 library/cell/view 与 child inspect 相同；
- resolution 明确给出 `IN, OUT, VDD, VSS` terminal order，编译器只核对完整集合，不推断顺序；
- `XAMP/MN0.Wfg`、`XAMP/RD0.r` 均来自 scoped inventory 并有一对一 netlist binding；
- 两个原子候选进入既有 shared-netlist DC→AC 状态机；
- planner token 为 `74dcb84202d70fb9`；
- 两个远端安全开关均为 false。

## Failure-first coverage

新增测试明确拒绝：

- resolution 的 draft SHA-256 与输入文件不一致；
- final role 仍标为 `software_inference`；
- role 引用未知 instance/net/pin 或错误 terminal→net；
- source、load、transfer 或 voltage metric 引用未知 top node；
- operating-point metric 引用未知 top instance；
- permission/binding 字段不在完整 CDF inventory；
- permission 与 OA→`si` binding 集合不完全相等；
- hierarchy child 未 inspect、target 不一致或 terminal order 缺失/多出 terminal；
- resolution 尝试使用未开放 operation 或增加未声明字段；
- `simulation.run` 携带不会被该 operation 应用的参数写入、搜索、objective 或多次迭代；
- 生成任务违反现有 analysis/metric/candidate/budget 规则。

onboarding 测试为 `23 passed`；全量回归为 `845 passed`。`compileall`、`vda catalog`、
`vda onboarding-resolve --help`、两份真实历史草案编译及两个生成任务的 `vda plan` 均通过。

## 证据边界

- 草案内 OA topology、CDF、pins 和 placement 的来源仍是先前 `bridge_readback`；
- fixture resolution 复用先前显式任务中的角色、testbench 和候选，标为 `user_input`；
- draft hash、inventory membership、角色/节点/terminal 比较和 TaskSpec 编译是
  `software_inference`；
- 本轮没有新的 `eda_result`、`bridge_readback` 或 `system_event`；
- planner 成功只证明任务契约完整，不证明电路规格、参数写回或仿真结果。

下一道 Gate 是对首个用户实际、非 retained fixture 的单模块运行完整接入链：只读 inspect → draft →
基于用户给定拓扑/规格生成 resolution → 最小 DC/AC TaskSpec → 真实 OA→`si` 验证。先验证 DC
operating point；只有需要时才开启 tuning、额外 analysis 或可选 PVT。任何真实计算或 OA 写入仍须
使用生成任务的新 token 和明确安全开关，且不得覆盖已有 cellview。
