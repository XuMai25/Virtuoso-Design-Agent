# 0002：默认使用晶圆厂 CMOS PDK

状态：已接受（2026-07-21）

## 背景

VDA 当前目标是晶体管级模拟/混合信号单模块设计。日常设计更常使用 TSMC、SMIC 等晶圆厂 PDK；TSV、hybrid bonding 等封装/3D 集成 PDK 的器件、规则和验证目标不同，不能反向决定普通 schematic、器件参数或 Spectre testbench 的默认假设。

## 决策

- 未显式声明 `pdk_profile` 时，使用当前已验证的 `nics4304_tsmc28` profile；其工艺身份是 TSMC N28，Virtuoso tech library 为 `tsmcN28`。
- 后续 TSMC、SMIC 或其他晶圆厂工艺以独立、版本化 profile 接入，并分别完成 library、器件、model section、默认电压、路径和 smoke 验证。
- TSV、hybrid-bonding 或其他封装/3D PDK 只能由任务显式选择；不得作为缺省 profile、自动 fallback，或被混入晶圆厂 CMOS 电路模板的默认参数。
- profile 是“工艺 + 当前执行环境”的绑定。切换晶圆厂、节点、PDK 版本、model section 或服务器时必须重新验证，不能沿用另一 profile 的性能证据。

## 影响

当前代码默认值保持 `nics4304_tsmc28`，但由单一常量同时供任务模型和 CLI doctor 使用，避免两处默认漂移。该决策不删除 Bridge 的任何能力，也不排除未来加入 TSV/HB 流程；它只规定普通晶体管级设计的默认起点和证据边界。
