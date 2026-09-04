import type { ReactNode } from 'react';

/** Shared presentation for empty, blocked, and unavailable content. */
export function EmptyState({
  title,
  description,
  action,
}: {
  title: string;
  description?: string;
  action?: ReactNode;
}) {
  return (
    <div className="border border-dashed border-rule px-4 py-6">
      <p className="text-[13px] font-medium leading-5 text-ink">{title}</p>
      {description ? (
        <p className="mt-1 max-w-[62ch] text-[13px] leading-6 text-muted">{description}</p>
      ) : null}
      {action ? <div className="mt-3">{action}</div> : null}
    </div>
  );
}

type Tone = 'allow' | 'warn' | 'hold' | 'block' | 'abstain' | 'neutral';

const TONE: Record<Tone, string> = {
  allow: 'border-allow text-allow',
  warn: 'border-warn text-warn',
  hold: 'border-hold text-hold',
  block: 'border-block text-block',
  abstain: 'border-abstain text-abstain',
  neutral: 'border-rule text-muted',
};

/** Central status-to-color mapping. */
export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}

/** Shared table layout with numeric-column alignment. */
export function DataTable({
  caption,
  head,
  children,
}: {
  caption?: string;
  head: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className="overflow-x-auto">
      <table className="w-full min-w-[36rem] border-collapse text-[13px]">
        {caption ? <caption className="sr-only">{caption}</caption> : null}
        <thead>
          <tr className="border-b border-rule text-left align-bottom">{head}</tr>
        </thead>
        <tbody>{children}</tbody>
      </table>
    </div>
  );
}

export function Th({
  children,
  numeric = false,
}: {
  children: ReactNode;
  numeric?: boolean;
}) {
  return (
    <th
      scope="col"
      className={`px-3 py-2 text-[12px] font-medium text-muted ${numeric ? 'text-right' : 'text-left'}`}
    >
      {children}
    </th>
  );
}

export function Td({
  children,
  numeric = false,
}: {
  children: ReactNode;
  numeric?: boolean;
}) {
  return (
    <td
      className={`border-b border-line px-3 py-2 align-top text-ink ${numeric ? 'tnum text-right font-mono' : ''}`}
    >
      {children}
    </td>
  );
}
