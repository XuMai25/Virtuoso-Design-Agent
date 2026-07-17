import { useState, type ReactNode } from "react";
import { queryString } from "../api";
import { useApi } from "../hooks";
import type { Note, PageKey, Summary } from "../types";
import { Icon, type IconName } from "./Icons";
import { PathText, StatusBadge, formatTime } from "./Ui";

const navigation: Array<{ key: PageKey; label: string; icon: IconName }> = [
  { key: "today", label: "今日工作台", icon: "today" },
  { key: "inbox", label: "收件箱", icon: "inbox" },
  { key: "projects", label: "项目", icon: "projects" },
  { key: "literature", label: "文献", icon: "literature" },
  { key: "knowledge", label: "知识", icon: "knowledge" },
  { key: "experiments", label: "实验", icon: "experiments" },
  { key: "review", label: "复盘", icon: "review" },
  { key: "settings", label: "集成与设置", icon: "settings" },
];

export function Layout({
  page,
  onNavigate,
  theme,
  onToggleTheme,
  summary,
  scanning,
  onScan,
  refreshKey,
  onOpenNote,
  children,
}: {
  page: PageKey;
  onNavigate: (page: PageKey) => void;
  theme: "light" | "dark";
  onToggleTheme: () => void;
  summary: Summary | null;
  scanning: boolean;
  onScan: () => void;
  refreshKey: number;
  onOpenNote: (id: string) => void;
  children: ReactNode;
}) {
  const [search, setSearch] = useState("");
  const [focused, setFocused] = useState(false);
  const searchState = useApi<Note[]>(
    `/api/notes${queryString({ q: search || "__no_search__", limit: 7 })}`,
    refreshKey,
  );
  const showResults = focused && search.trim().length >= 2;

  return (
    <div className="app-shell">
      <aside className="sidebar">
        <div className="brand">
          <div className="brand-mark" aria-hidden="true"><span>R</span><span>W</span></div>
          <div>
            <strong>科研工作台</strong>
            <small>Research Workbench</small>
          </div>
        </div>
        <nav aria-label="一级导航">
          {navigation.map((item) => (
            <button
              type="button"
              key={item.key}
              className={page === item.key ? "nav-item active" : "nav-item"}
              onClick={() => onNavigate(item.key)}
              aria-current={page === item.key ? "page" : undefined}
            >
              <Icon name={item.icon} />
              <span>{item.label}</span>
            </button>
          ))}
        </nav>
        <div className="sidebar-foot">
          <span className="readonly-indicator"><Icon name="check" /> Vault 只读</span>
          <PathText>{summary?.vault_path}</PathText>
        </div>
      </aside>

      <div className="main-column">
        <header className="topbar">
          <div className="global-search">
            <Icon name="search" />
            <input
              value={search}
              onChange={(event) => setSearch(event.target.value)}
              onFocus={() => setFocused(true)}
              onBlur={() => window.setTimeout(() => setFocused(false), 150)}
              placeholder="搜索标题、正文或路径…"
              aria-label="全局搜索"
            />
            {showResults && (
              <div className="search-results">
                {searchState.loading && <div className="search-message">正在搜索…</div>}
                {searchState.error && <div className="search-message error">{searchState.error}</div>}
                {searchState.data?.length === 0 && <div className="search-message">没有匹配笔记</div>}
                {searchState.data?.map((note) => (
                  <button type="button" key={note.id} onMouseDown={() => onOpenNote(note.id)}>
                    <span><strong>{note.title}</strong><small>{note.type} · {note.path}</small></span>
                    <Icon name="arrow" />
                  </button>
                ))}
              </div>
            )}
          </div>
          <div className="topbar-actions">
            <button className="icon-button" type="button" onClick={onToggleTheme} aria-label="切换明暗主题">
              <Icon name={theme === "dark" ? "sun" : "moon"} />
            </button>
            <button className="scan-button" type="button" onClick={onScan} disabled={scanning}>
              <Icon name="refresh" className={scanning ? "rotating" : ""} />
              {scanning ? "扫描中" : "重新扫描"}
            </button>
          </div>
        </header>

        <div className="health-strip" aria-label="索引状态">
          <span>最近扫描 <strong>{formatTime(summary?.finished_at)}</strong></span>
          <span>Markdown <strong>{summary?.markdown_files ?? "—"}</strong></span>
          <span className="health-problems">
            <StatusBadge value="error" /> <strong>{summary?.error ?? "—"}</strong>
            <StatusBadge value="warning" /> <strong>{summary?.warning ?? "—"}</strong>
            <StatusBadge value="info" /> <strong>{summary?.info ?? "—"}</strong>
          </span>
        </div>

        <main className="page-content">{children}</main>
      </div>
    </div>
  );
}
