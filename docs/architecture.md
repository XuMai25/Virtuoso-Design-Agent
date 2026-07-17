# 架构与扩展边界

## 当前数据流

1. `VaultScanner` 递归读取 Markdown，并跳过 `.obsidian`、缓存、临时目录和模板目录。
2. parser 解析 frontmatter、标题、别名和 wiki links；对 Obsidian 可接受的简单非标准 YAML 采用保守的平面字段回退。
3. resolver 以完整相对路径、来源相对路径、文件名、标题和 frontmatter aliases 解析双链，并分别记录失效与歧义。
4. SQLite 在单个事务中替换当前索引，同时保留扫描历史摘要。旧索引只有在新扫描数据准备完毕后才被替换。
5. `IndexService` 生成项目、文献、知识、实验、今日工作台、整理建议和复盘视图。
6. FastAPI 提供 JSON API，并直接托管 Vite 生产构建。

## 事实分层

- **Vault 记录**：frontmatter、正文、路径和文件修改时间。
- **外部状态**：本机对 `external_path` / `output_path` 等路径的只读存在性检查。
- **软件推断**：收件箱去向、可能关联、长期未更新和复盘候选。

API 与前端必须保留来源标签。文件修改时间不等于“项目已推进”，路径存在不等于实验已验证。

## 写入边界

`AppConfig` 在启动时验证应用数据目录和报告目录都不位于 Vault 内。当前 Obsidian adapter 没有写接口；建议与复盘只返回字符串。未来若加入 Vault 写入，必须另建受控 command 层，并满足：diff 预览、用户确认、原子替换、备份/操作记录和失败恢复。

## Adapter 边界

- Zotero：未来只通过官方/本地接口 adapter 获取状态、题录和附件覆盖；不直接操作 Zotero SQLite。写操作使用独立 command 并逐次确认。
- 外部项目：未来 adapter 可读取 Git 状态，但返回值必须与 Vault 项目状态分栏展示。
- 实验 runner：未来 WSL、SSH、仿真、综合和 EDA runner 先生成执行计划；远程执行需单独授权，并记录环境、目录、命令和产物路径。
- Codex Board：不设 adapter，不读取内部状态。若用户配置 URL，只渲染普通链接。

## SQLite

SQLite 保存扫描历史、当前笔记索引、链接、问题和外部路径检查。数据库位于 `%LOCALAPPDATA%\ResearchWorkbench` 或显式 `--data-dir`，不会写进 Vault。扫描重建采用 `BEGIN IMMEDIATE` 事务，避免前端读到半更新状态。
