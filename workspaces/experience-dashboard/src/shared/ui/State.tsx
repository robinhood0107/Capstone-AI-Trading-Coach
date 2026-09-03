import type { ReactNode } from 'react';

/**
 * 비어 있음 / 막힘 / 오래됨을 한 가지 형태로 표현한다.
 *
 * 이전에는 같은 "데이터 없음"이 화면마다 border-dashed px-4 py-6, py-5, py-4 등
 * 5가지 마크업으로 갈려 있었다. 빈 화면은 사용자가 가장 자주 마주치는 상태이므로
 * 여기서 형태를 고정하고, 각 화면은 무엇이 없고 다음에 무엇을 하면 되는지만 채운다.
 */
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

/**
 * 상태 badge. 같은 상태가 화면마다 다른 색으로 보이면 신뢰를 잃으므로
 * tone 매핑을 한 곳에서만 정의한다.
 */
export function Badge({ tone = 'neutral', children }: { tone?: Tone; children: ReactNode }) {
  return (
    <span
      className={`inline-flex items-center border px-2 py-0.5 font-mono text-[11px] uppercase tracking-wide ${TONE[tone]}`}
    >
      {children}
    </span>
  );
}

/**
 * 표 껍데기. 5개 화면이 각자 table 마크업을 손으로 짜고 있어 헤더 정렬과
 * 셀 padding이 제각각이었다. 숫자 열은 우측 정렬 + tabular-nums가 기본이다.
 */
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
