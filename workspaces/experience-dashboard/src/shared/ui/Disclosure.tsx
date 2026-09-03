'use client';

import { useState, type ReactNode } from 'react';

/**
 * 첫인상을 가볍게 만들기 위한 접기/펼치기.
 * 정보를 삭제하는 것이 아니라 순서를 주는 장치다.
 * 명세서 10.4가 요구하는 항목은 펼쳤을 때 전부 그대로 있어야 한다.
 */
export function Disclosure({
  label,
  hint,
  count,
  defaultOpen = false,
  children,
}: {
  label: string;
  hint?: string;
  count?: number;
  defaultOpen?: boolean;
  children: ReactNode;
}) {
  const [open, setOpen] = useState(defaultOpen);

  return (
    <div>
      <button
        type="button"
        onClick={() => setOpen((value) => !value)}
        aria-expanded={open}
        className="flex w-full items-center justify-between gap-4 rounded-tile bg-subtle px-5 py-3.5 text-left hover:bg-line/50"
      >
        <span className="min-w-0">
          <span className="flex items-center gap-2 text-[14px] font-semibold text-ink">
            {label}
            {typeof count === 'number' ? (
              <span className="tnum rounded-full bg-panel px-2 py-0.5 text-[11px] font-medium text-muted">
                {count}
              </span>
            ) : null}
          </span>
          {hint ? <span className="mt-0.5 block text-[12px] leading-5 text-muted">{hint}</span> : null}
        </span>
        <span aria-hidden className={`shrink-0 text-[12px] text-faint ${open ? 'rotate-180' : ''}`}>
          ▼
        </span>
      </button>
      {open ? <div className="mt-4">{children}</div> : null}
    </div>
  );
}
