import { useState } from "react";
import { queryString } from "../api";
import { useApi } from "../hooks";
import type { Facets, Project } from "../types";
import { Badge, FilterSelect, PageTitle, PathText, StatePanel, StatusBadge } from "../components/Ui";
import { Icon } from "../components/Icons";

export function ProjectsPage({ refreshKey, onOpen }: { refreshKey: number; onOpen: (id: string) => void }) {
  const [status, setStatus] = useState("");
  const [domain, setDomain] = useState("");
  const [query, setQuery] = useState("");
  const facets = useApi<Facets>("/api/facets", refreshKey);
  const state = useApi<Project[]>(`/api/projects${queryString({ status, domain, q: query })}`, refreshKey);
  return (
    <>
      <PageTitle
        eyebrow="PROJECTS"
        title="项目推进"
        description="把 Vault 中记录的状态、外部目录现状与软件检查分开呈现。"
      />
      <div className="filter-bar">
        <label className="filter-field grow"><span>筛选项目</span><input value={query} onChange={(e) => setQuery(e.target.value)} placeholder="标题、正文或路径" /></label>
        <FilterSelect label="状态" value={status} options={facets.data?.statuses ?? []} onChange={setStatus} />
        <FilterSelect label="领域" value={domain} options={facets.data?.domains ?? []} onChange={setDomain} />
      </div>
      {state.loading && <StatePanel kind="loading" title="正在读取项目" />}
      {state.error && <StatePanel kind="error" title="项目加载失败" detail={state.error} />}
      {state.data?.length === 0 && <StatePanel kind="empty" title="没有匹配项目" detail="调整筛选条件，或检查项目笔记是否使用 type: project。" />}
      {state.data && (
        <div className="object-grid project-grid">
          {state.data.map((project) => (
            <article className="object-card project-card" key={project.id}>
              <header>
                <div><span className="object-kind">{project.domain || "未分类领域"}</span><h2>{project.title}</h2></div>
                <StatusBadge value={project.status} />
              </header>
              <div className="object-field"><small>目标</small><p>{project.goal || "Vault 项目页未记录目标。"}</p></div>
              <div className="next-step-box"><small>下一步</small><strong>{project.next_step || "尚未记录明确下一步"}</strong></div>
              <div className="object-field"><small>完成标准</small><p>{project.completion_criteria || "未记录"}</p></div>
              <div className="path-state">
                <div><small>外部路径</small><PathText>{project.external_path}</PathText></div>
                {project.external_path && <StatusBadge value={project.external_path_status || "未检查"} />}
              </div>
              <footer>
                <Badge>{project.source_label}</Badge>
                {project.external_path && <Badge>外部状态：本机只读检查</Badge>}
                <button type="button" onClick={() => onOpen(project.id)}>查看详情 <Icon name="arrow" /></button>
              </footer>
            </article>
          ))}
        </div>
      )}
    </>
  );
}
