import type { SVGProps } from "react";

export type IconName =
  | "today"
  | "inbox"
  | "projects"
  | "literature"
  | "knowledge"
  | "experiments"
  | "review"
  | "settings"
  | "search"
  | "refresh"
  | "sun"
  | "moon"
  | "close"
  | "arrow"
  | "check"
  | "warning";

const paths: Record<IconName, React.ReactNode> = {
  today: <><path d="M4 5.5h16v14H4z"/><path d="M8 3v5M16 3v5M4 10h16"/></>,
  inbox: <><path d="M4 5h16l-2 14H6L4 5Z"/><path d="M4.8 13h4l1.4 2h3.6l1.4-2h4"/></>,
  projects: <><path d="M4 6h6l2 2h8v11H4z"/><path d="M4 6V4h6l2 2"/></>,
  literature: <><path d="M5 4h5a3 3 0 0 1 3 3v13a3 3 0 0 0-3-3H5z"/><path d="M19 4h-3a3 3 0 0 0-3 3v13a3 3 0 0 1 3-3h3z"/></>,
  knowledge: <><circle cx="12" cy="12" r="3"/><path d="M12 3v3M12 18v3M3 12h3M18 12h3M5.6 5.6l2.1 2.1M16.3 16.3l2.1 2.1M18.4 5.6l-2.1 2.1M7.7 16.3l-2.1 2.1"/></>,
  experiments: <><path d="M9 3h6M10 3v6l-5 9a2 2 0 0 0 1.8 3h10.4A2 2 0 0 0 19 18l-5-9V3"/><path d="M7.5 15h9"/></>,
  review: <><path d="M6 4h12v17H6z"/><path d="M9 8h6M9 12h6M9 16h4"/><path d="m3 8 1 1 2-2"/></>,
  settings: <><circle cx="12" cy="12" r="3"/><path d="M19 13.5v-3l-2-.7a7 7 0 0 0-.8-1.8l.9-1.9L15 4l-1.9.9a7 7 0 0 0-1.8-.8L10.5 2h-3l-.7 2.1a7 7 0 0 0-1.8.8L3.1 4 1 6.1 2 8a7 7 0 0 0-.8 1.8l-2 .7v3l2 .7A7 7 0 0 0 2 16l-1 1.9L3.1 20l1.9-.9a7 7 0 0 0 1.8.8l.7 2.1h3l.7-2.1a7 7 0 0 0 1.8-.8l2 .9 2.1-2.1-.9-1.9a7 7 0 0 0 .8-1.8z" transform="translate(3 -1) scale(.75)"/></>,
  search: <><circle cx="10.5" cy="10.5" r="6.5"/><path d="m15.5 15.5 5 5"/></>,
  refresh: <><path d="M20 7v5h-5"/><path d="M18.5 16a8 8 0 1 1 .8-7L20 12"/></>,
  sun: <><circle cx="12" cy="12" r="4"/><path d="M12 2v2M12 20v2M2 12h2M20 12h2M5 5l1.4 1.4M17.6 17.6 19 19M19 5l-1.4 1.4M6.4 17.6 5 19"/></>,
  moon: <path d="M20 15.5A8 8 0 0 1 8.5 4 8.5 8.5 0 1 0 20 15.5Z"/>,
  close: <path d="m6 6 12 12M18 6 6 18"/>,
  arrow: <path d="M5 12h14M14 7l5 5-5 5"/>,
  check: <path d="m5 12 4 4L19 6"/>,
  warning: <><path d="M12 3 2.5 20h19z"/><path d="M12 9v5M12 17.5v.1"/></>,
};

export function Icon({ name, ...props }: { name: IconName } & SVGProps<SVGSVGElement>) {
  return (
    <svg
      viewBox="0 0 24 24"
      fill="none"
      stroke="currentColor"
      strokeWidth="1.7"
      strokeLinecap="round"
      strokeLinejoin="round"
      aria-hidden="true"
      {...props}
    >
      {paths[name]}
    </svg>
  );
}
