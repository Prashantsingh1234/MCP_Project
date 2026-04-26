import type { ReactNode } from "react";

type IconProps = { className?: string; title?: string };

function Wrap({ children, title }: { children: ReactNode; title?: string }) {
  return (
    <span aria-hidden="true" title={title} style={{ display: "inline-flex", alignItems: "center" }}>
      {children}
    </span>
  );
}

export function IconDashboard(props: IconProps) {
  return (
    <Wrap title={props.title}>
      <svg className={props.className} width="18" height="18" viewBox="0 0 24 24" fill="none">
        <path
          d="M4 13h7V4H4v9Zm9 7h7V11h-7v9ZM4 20h7v-5H4v5Zm9-11h7V4h-7v5Z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
        />
      </svg>
    </Wrap>
  );
}

export function IconChat(props: IconProps) {
  return (
    <Wrap title={props.title}>
      <svg className={props.className} width="18" height="18" viewBox="0 0 24 24" fill="none">
        <path
          d="M7 8h10M7 12h7M6 18l-2 2V6a2 2 0 0 1 2-2h12a2 2 0 0 1 2 2v10a2 2 0 0 1-2 2H6Z"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinejoin="round"
          strokeLinecap="round"
        />
      </svg>
    </Wrap>
  );
}

export function IconMetrics(props: IconProps) {
  return (
    <Wrap title={props.title}>
      <svg className={props.className} width="18" height="18" viewBox="0 0 24 24" fill="none">
        <path
          d="M5 19V9m7 10V5m7 14v-7"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
      </svg>
    </Wrap>
  );
}

export function IconLogs(props: IconProps) {
  return (
    <Wrap title={props.title}>
      <svg className={props.className} width="18" height="18" viewBox="0 0 24 24" fill="none">
        <path
          d="M7 7h10M7 12h10M7 17h6"
          stroke="currentColor"
          strokeWidth="1.6"
          strokeLinecap="round"
        />
        <path d="M5 4h14v16H5V4Z" stroke="currentColor" strokeWidth="1.6" strokeLinejoin="round" />
      </svg>
    </Wrap>
  );
}

