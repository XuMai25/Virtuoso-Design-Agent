import { useApi } from "../hooks";
import type { Dashboard } from "../types";
import { Badge, NoteRow, PageTitle, Section, StatePanel, StatusBadge } from "../components/Ui";
import { Icon } from "../components/Icons";

export function TodayPage({ refreshKey, onOpen }: { refreshKey: number; onOpen: (id: string) => void }) {
  const state = useApi<Dashboard>("/api/dashboard", refreshKey);
  return (
    <>
      <PageTitle
        eyebrow="TODAY"
        title="今天继续什么？"
        description="从项目的明确下一步开始，再处理等待、阅读和实验。所有内容来自只读索引。"
      />
      {state.loading && <StatePanel kind="loading" title="正在整理今日工作台" detail="首次扫描较大 Vault 时可能需要几秒。" />}
      {state.error && <StatePanel kind="error" title="今日工作台加载失败" detail={state.error} />}
      {state.data && (
        <div className="today-layout">
          <Section title="优先继续" hint="项目页记录的下一步" className="focus-section">
            {state.data.next_actions.length === 0 ? (
              <StatePanel kind="empty" title="还没有明确下一步" detail="可在项目笔记中补充“下一步”章节；本应用不会替你写入。" />
            ) : (
              <div className="action-list">
                {state.data.next_actions.map((action, index) => (
                  <button className="action-card" type="button" key={action.project_id} onClick={() => onOpen(action.project_id)}>
                    <span className="action-index">{String(index + 1).padStart(2, "0")}</span>
                    <span className="action-copy">
                      <span className="action-project">{action.project}</span>
                      <strong>{action.next_step}</strong>
                      <span className="source-note">来源：{action.source}</span>
                    </span>
                    <StatusBadge value={action.status} />
                    <Icon name="arrow" />
                  </button>
                ))}
              </div>
            )}
          </Section>

          <div className="two-column-grid">
            <Section title="等待与阻塞" hint={`${state.data.waiting_or_blocked.length} 项`}>
              {state.data.waiting_or_blocked.length ? (
                <div className="compact-stack">
                  {state.data.waiting_or_blocked.map((project) => (
                    <NoteRow key={project.id} note={project} onOpen={onOpen} meta={<StatusBadge value={project.status} />} />
                  ))}
                </div>
              ) : <StatePanel kind="empty" title="没有记录的等待或阻塞" />}
            </Section>
            <Section title="待处理收件箱" hint="只生成整理建议">
              {state.data.inbox.length ? (
                <div className="compact-stack">{state.data.inbox.slice(0, 4).map((note) => <NoteRow key={note.id} note={note} onOpen={onOpen} />)}</div>
              ) : <StatePanel kind="empty" title="收件箱已清空" />}
            </Section>
          </div>

          <div className="two-column-grid">
            <Section title="下一篇值得看什么" hint="待筛选 / AI 已预读 / 阅读中">
              {state.data.reading_queue.length ? (
                <div className="compact-stack">
                  {state.data.reading_queue.slice(0, 5).map((note) => (
                    <NoteRow
                      key={note.id}
                      note={note}
                      onOpen={onOpen}
                      meta={<><StatusBadge value={note.reading_stage} />{note.reading_value && <Badge>{note.reading_value}</Badge>}</>}
                    />
                  ))}
                </div>
              ) : <StatePanel kind="empty" title="没有待读文献" detail="只按 Obsidian 文献笔记判断，未查询 Zotero。" />}
            </Section>
            <Section title="最近实验" hint="证据与下一步">
              {state.data.recent_experiments.length ? (
                <div className="compact-stack">
                  {state.data.recent_experiments.map((note) => (
                    <NoteRow key={note.id} note={note} onOpen={onOpen} meta={<StatusBadge value={note.output_path_status} />} />
                  ))}
                </div>
              ) : <StatePanel kind="empty" title="还没有实验记录" />}
            </Section>
          </div>

          <Section title="最近编辑的知识内容" hint="按文件修改时间">
            <div className="recent-grid">
              {state.data.recent_notes.map((note) => (
                <button type="button" className="recent-card" key={note.id} onClick={() => onOpen(note.id)}>
                  <Badge>{note.type}</Badge>
                  <strong>{note.title}</strong>
                  <span>{note.path}</span>
                </button>
              ))}
            </div>
          </Section>

          {state.data.review_items.length > 0 && (
            <Section title="本周需要复盘" hint="软件检查，不是 Vault 事实">
              <div className="review-reminders">
                {state.data.review_items.map((item) => (
                  <button type="button" key={item.id} onClick={() => onOpen(item.id)}>
                    <Icon name="warning" /><span><strong>{item.title}</strong><small>{item.message}</small></span>
                  </button>
                ))}
              </div>
            </Section>
          )}
        </div>
      )}
    </>
  );
}
