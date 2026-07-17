import { useEffect } from "react";
import { useApi } from "../hooks";
import type { Note } from "../types";
import { Icon } from "./Icons";
import { PathText, SeverityMark, StatePanel, StatusBadge } from "./Ui";

export function NoteDrawer({ noteId, onClose }: { noteId: string | null; onClose: () => void }) {
  const state = useApi<Note>(noteId ? `/api/notes/${noteId}` : "/api/health");

  useEffect(() => {
    if (!noteId) return;
    const handleKey = (event: KeyboardEvent) => {
      if (event.key === "Escape") onClose();
    };
    window.addEventListener("keydown", handleKey);
    return () => window.removeEventListener("keydown", handleKey);
  }, [noteId, onClose]);

  if (!noteId) return null;
  return (
    <div className="drawer-layer" role="presentation" onMouseDown={(event) => {
      if (event.target === event.currentTarget) onClose();
    }}>
      <aside className="note-drawer" role="dialog" aria-modal="true" aria-label="笔记详情">
        <button className="icon-button drawer-close" type="button" onClick={onClose} aria-label="关闭详情">
          <Icon name="close" />
        </button>
        {state.loading && <StatePanel kind="loading" title="正在读取笔记详情" />}
        {state.error && <StatePanel kind="error" title="无法读取笔记" detail={state.error} />}
        {state.data && (
          <>
            <div className="drawer-header">
              <div className="eyebrow">{state.data.type}</div>
              <h2>{state.data.title}</h2>
              <div className="drawer-badges">
                <StatusBadge value={state.data.status} />
                {state.data.domain && <span className="badge">{state.data.domain}</span>}
              </div>
              <PathText>{state.data.path}</PathText>
            </div>

            <div className="detail-block">
              <h3>属性</h3>
              <dl className="property-grid">
                {Object.entries(state.data.properties).map(([key, value]) => (
                  <div key={key}>
                    <dt>{key}</dt>
                    <dd>{Array.isArray(value) ? value.join("、") : String(value ?? "")}</dd>
                  </div>
                ))}
              </dl>
            </div>

            {state.data.external_paths && state.data.external_paths.length > 0 && (
              <div className="detail-block">
                <h3>外部路径检查</h3>
                {state.data.external_paths.map((item) => (
                  <div className="path-check" key={`${item.field}-${item.value}`}>
                    <StatusBadge value={item.status} />
                    <PathText>{item.value}</PathText>
                    <small>{item.field} · 本机只读检查</small>
                  </div>
                ))}
              </div>
            )}

            {state.data.issues && state.data.issues.length > 0 && (
              <div className="detail-block">
                <h3>检查问题</h3>
                <div className="issue-stack">
                  {state.data.issues.map((issue, index) => (
                    <div className="issue-line" key={`${issue.code}-${index}`}>
                      <SeverityMark severity={issue.severity} />
                      <span>{issue.message}</span>
                    </div>
                  ))}
                </div>
              </div>
            )}

            <div className="detail-block">
              <h3>正文（只读）</h3>
              <pre className="markdown-source">{state.data.content || "暂无正文"}</pre>
            </div>

            <div className="detail-columns">
              <div className="detail-block">
                <h3>反向链接</h3>
                {state.data.backlinks?.length ? state.data.backlinks.map((item, index) => (
                  <div className="link-line" key={index}><PathText>{String(item.source_path)}</PathText></div>
                )) : <p className="muted">暂无反向链接。</p>}
              </div>
              <div className="detail-block">
                <h3>出站链接</h3>
                {state.data.outgoing_links?.length ? state.data.outgoing_links.map((item, index) => (
                  <div className="link-line" key={index}>
                    <StatusBadge value={String(item.status)} />
                    <span>{String(item.alias || item.target_text)}</span>
                  </div>
                )) : <p className="muted">暂无出站链接。</p>}
              </div>
            </div>
          </>
        )}
      </aside>
    </div>
  );
}
