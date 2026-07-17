import type { ReactNode } from "react";
import type { Note, Severity } from "../types";
import { Icon } from "./Icons";

export function PageTitle({
  eyebrow,
  title,
  description,
  actions,
}: {
  eyebrow?: string;
  title: string;
  description: string;
  actions?: ReactNode;
}) {
  return (
    <header className="page-title">
      <div>
        {eyebrow && <div className="eyebrow">{eyebrow}</div>}
        <h1>{title}</h1>
        <p>{description}</p>
      </div>
      {actions && <div className="page-actions">{actions}</div>}
    </header>
  );
}

export function StatePanel({
  kind,
  title,
  detail,
  action,
}: {
  kind: "loading" | "empty" | "error";
  title: string;
  detail?: string;
  action?: ReactNode;
}) {
  return (
    <div className={`state-panel state-${kind}`} role={kind === "error" ? "alert" : "status"}>
      <div className="state-symbol" aria-hidden="true">
        {kind === "loading" ? <span className="spinner" /> : kind === "error" ? "!" : "·"}
      </div>
      <strong>{title}</strong>
      {detail && <p>{detail}</p>}
      {action}
    </div>
  );
}

export function Badge({ children, tone = "neutral" }: { children: ReactNode; tone?: string }) {
  return <span className={`badge badge-${tone}`}>{children}</span>;
}

export function StatusBadge({ value }: { value?: string | null }) {
  if (!value) return <Badge>未记录</Badge>;
  const tone =
    value === "阻塞" || value === "error"
      ? "danger"
      : value === "等待中" || value === "warning" || value === "待复查"
        ? "warning"
        : value === "进行中" || value === "AI已预读" || value === "阅读中"
          ? "active"
          : value === "完成" || value === "已读完" || value === "exists"
            ? "success"
            : "neutral";
  return <Badge tone={tone}>{value}</Badge>;
}

export function PathText({ children }: { children?: string | null }) {
  return <span className="path-text">{children || "未记录"}</span>;
}

export function NoteRow({
  note,
  onOpen,
  meta,
}: {
  note: Note;
  onOpen: (id: string) => void;
  meta?: ReactNode;
}) {
  return (
    <button className="note-row" type="button" onClick={() => onOpen(note.id)}>
      <div className="note-row-main">
        <div className="note-row-title">
          <span>{note.title}</span>
          {note.status && <StatusBadge value={note.status} />}
        </div>
        <p>{note.excerpt || "这条笔记暂无正文摘要。"}</p>
        <PathText>{note.path}</PathText>
      </div>
      <div className="note-row-meta">
        {meta}
        <Icon name="arrow" />
      </div>
    </button>
  );
}

export function FilterSelect({
  label,
  value,
  options,
  onChange,
}: {
  label: string;
  value: string;
  options: string[];
  onChange: (value: string) => void;
}) {
  return (
    <label className="filter-field">
      <span>{label}</span>
      <select value={value} onChange={(event) => onChange(event.target.value)}>
        <option value="">全部</option>
        {options.map((option) => (
          <option value={option} key={option}>{option}</option>
        ))}
      </select>
    </label>
  );
}

export function SeverityMark({ severity }: { severity: Severity }) {
  return (
    <span className={`severity severity-${severity}`}>
      <span aria-hidden="true">{severity === "error" ? "×" : severity === "warning" ? "!" : "i"}</span>
      {severity}
    </span>
  );
}

export function Section({
  title,
  hint,
  children,
  className = "",
}: {
  title: string;
  hint?: string;
  children: ReactNode;
  className?: string;
}) {
  return (
    <section className={`content-section ${className}`}>
      <div className="section-heading">
        <h2>{title}</h2>
        {hint && <span>{hint}</span>}
      </div>
      {children}
    </section>
  );
}

export function formatTime(value?: string | null): string {
  if (!value) return "尚未扫描";
  return new Intl.DateTimeFormat("zh-CN", {
    month: "numeric",
    day: "numeric",
    hour: "2-digit",
    minute: "2-digit",
  }).format(new Date(value));
}
