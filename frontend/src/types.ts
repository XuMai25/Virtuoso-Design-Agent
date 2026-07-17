export type Severity = "error" | "warning" | "info";

export interface Summary {
  indexed: boolean;
  scan_id?: number;
  vault_path: string;
  started_at?: string;
  finished_at?: string;
  markdown_files?: number;
  projects?: number;
  literature?: number;
  concepts?: number;
  experiments?: number;
  inbox?: number;
  broken_links?: number;
  ambiguous_links?: number;
  invalid_external_paths?: number;
  error?: number;
  warning?: number;
  info?: number;
}

export interface Note {
  id: string;
  path: string;
  title: string;
  type: string;
  status?: string | null;
  domain?: string | null;
  project?: string | null;
  reading_stage?: string | null;
  reading_value?: string | null;
  collection?: string | null;
  zotero_key?: string | null;
  bibtex_key?: string | null;
  modified_at: string;
  excerpt: string;
  properties: Record<string, unknown>;
  headings: string[];
  aliases: string[];
  content?: string;
  outgoing_links?: Array<Record<string, unknown>>;
  backlinks?: Array<Record<string, unknown>>;
  issues?: Issue[];
  external_paths?: ExternalPath[];
}

export interface Project extends Note {
  goal?: string | null;
  next_step?: string | null;
  completion_criteria?: string | null;
  external_path?: string | null;
  external_path_status?: string | null;
  source_label: string;
}

export interface Literature extends Note {
  fulltext_coverage?: string | null;
  needs_close_reading?: string | null;
  source_label: string;
}

export interface Experiment extends Note {
  environment?: string | null;
  command?: string | null;
  output_path?: string | null;
  output_path_status?: string | null;
  evidence?: string | null;
  judgment?: string | null;
  next_step?: string | null;
  source_label: string;
}

export interface Issue {
  id?: number;
  note_id?: string | null;
  path?: string | null;
  severity: Severity;
  code: string;
  message: string;
  field?: string | null;
}

export interface ExternalPath {
  field: string;
  value: string;
  status: string;
}

export interface Dashboard {
  summary: Summary | null;
  active_projects: Project[];
  waiting_or_blocked: Project[];
  recent_notes: Note[];
  inbox: Note[];
  reading_queue: Literature[];
  recent_experiments: Experiment[];
  review_items: Array<{
    id: string;
    title: string;
    modified_at: string;
    message: string;
    source: string;
  }>;
  next_actions: Array<{
    project_id: string;
    project: string;
    status?: string | null;
    next_step: string;
    source: string;
  }>;
}

export interface InboxSuggestion {
  note: Note;
  suggested_destination: string;
  reason: string;
  confidence: string;
  related: Array<{ id: string; title: string; type: string; score: string }>;
  write_action: string;
}

export interface Facets {
  types: string[];
  statuses: string[];
  domains: string[];
  reading_stages: string[];
  collections: string[];
}

export type PageKey =
  | "today"
  | "inbox"
  | "projects"
  | "literature"
  | "knowledge"
  | "experiments"
  | "review"
  | "settings";
