# Research Workbench 协作约束

## 产品边界

Research Workbench（科研工作台）是独立本地应用，不是 Codex Board 的页面或子模块。不得读取、修改或依赖 Codex Board 的代码、运行状态、后端、数据模型、导航、名称或端口。

默认生产地址是 `http://127.0.0.1:4280`；不得占用 Codex Board 使用的 `4173`。

## 数据安全

- Obsidian Vault 永远通过只读 adapter 扫描。不得在 Vault 中创建索引、缓存、日志、报告或临时文件。
- 第一阶段不得修改、创建、移动、重命名或删除 Vault 文件。
- 第一阶段不得写入 Zotero、直接读取 Zotero SQLite 数据库，或自动运行 WSL、SSH、仿真、综合、EDA 命令。
- SQLite、缓存和日志写入应用数据目录；检查报告只写入本项目 `reports/`。
- 展示外部项目时必须区分“Vault 记录”“外部目录只读检查”“软件推断”。
- 建议和复盘只能预览；未来写操作必须走“预览变更 → Markdown diff → 用户明确确认 → 原子写入 → 备份/操作记录”。

## 实现原则

- Python 代码位于 `backend/src/research_workbench/`，按 adapter、indexing、storage、domain、api 分层。
- React 页面位于 `frontend/src/pages/`，公共交互组件放 `frontend/src/components/`。
- 保持本地单体应用，不拆微服务，不为尚未启用的 adapter 引入实现依赖。
- 中文优先，长中文路径和论文标题必须可换行或省略后在详情中完整查看。
- 状态不得只靠颜色表达；所有状态徽标必须带文字。

## 验证入口

```powershell
.\.venv\Scripts\python.exe -m pytest
cd frontend
npm test
npm run build
```

真实 Vault 扫描前后应比较文件指纹或 Git 状态，确认零写入。生产 smoke test 必须检查 `127.0.0.1:4280`，并确认 `4173` 监听状态未改变。
