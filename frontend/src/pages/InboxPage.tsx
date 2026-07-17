import { useApi } from "../hooks";
import type { InboxSuggestion } from "../types";
import { Badge, PageTitle, PathText, StatePanel } from "../components/Ui";
import { Icon } from "../components/Icons";

export function InboxPage({ refreshKey, onOpen }: { refreshKey: number; onOpen: (id: string) => void }) {
  const state = useApi<InboxSuggestion[]>("/api/inbox/suggestions", refreshKey);
  return (
    <>
      <PageTitle
        eyebrow="INBOX"
        title="整理收件箱"
        description="查看待整理内容和软件建议。第一阶段只生成方案，不移动、不改写任何 Vault 文件。"
      />
      <div className="boundary-note"><Icon name="check" /><span><strong>当前为建议模式</strong> 没有写入按钮；未来写操作必须经过 diff 预览和明确确认。</span></div>
      {state.loading && <StatePanel kind="loading" title="正在生成整理建议" />}
      {state.error && <StatePanel kind="error" title="无法读取收件箱" detail={state.error} />}
      {state.data?.length === 0 && <StatePanel kind="empty" title="收件箱里没有待整理笔记" />}
      {state.data && state.data.length > 0 && (
        <div className="suggestion-list">
          {state.data.map((item) => (
            <article className="suggestion-card" key={item.note.id}>
              <button className="suggestion-source" type="button" onClick={() => onOpen(item.note.id)}>
                <span><strong>{item.note.title}</strong><PathText>{item.note.path}</PathText></span><Icon name="arrow" />
              </button>
              <p>{item.note.excerpt}</p>
              <div className="suggestion-decision">
                <div><small>建议去向</small><strong>{item.suggested_destination}</strong></div>
                <div><small>判断依据</small><span>{item.reason}</span></div>
                <Badge>置信度 {item.confidence}</Badge>
              </div>
              {item.related.length > 0 && (
                <div className="related-line"><span>可能关联：</span>{item.related.map((related) => (
                  <button type="button" key={related.id} onClick={() => onOpen(related.id)}>{related.title}</button>
                ))}</div>
              )}
              <footer>{item.write_action}</footer>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
