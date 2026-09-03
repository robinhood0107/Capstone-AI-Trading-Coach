import type { ReactNode } from 'react';

interface PanelProps {
  /** 이 패널이 소비하는 계약 이름. 장식이 아니라 팀 간 추적 단위다. */
  contract?: string;
  title: string;
  hint?: string;
  actions?: ReactNode;
  children: ReactNode;
}

/**
 * 카드 한 장 = 계약 하나.
 * 개편 전에는 테두리로 영역을 잘랐고, 지금은 여백 + 아주 옅은 그림자로 띄운다.
 * contract는 지우지 않되(팀 간 추적 단위) 사용자 시선에서 내려 넓은 화면에서만 노출한다.
 */
export function Panel({ contract, title, hint, actions, children }: PanelProps) {
  return (
    <section className="min-w-0 rounded-panel bg-panel shadow-card">
      <header className="flex items-start justify-between gap-4 px-6 pb-4 pt-5">
        <div className="min-w-0">
          <h2 className="text-[16px] font-semibold tracking-tight text-ink">{title}</h2>
          {hint ? <p className="mt-1.5 text-[13px] leading-5 text-muted">{hint}</p> : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">
          {actions}
          {contract ? (
            <span
              title="이 패널이 사용하는 API 계약"
              className="hidden rounded-full bg-subtle px-2.5 py-1 font-mono text-[10px] text-faint xl:inline-block"
            >
              {contract}
            </span>
          ) : null}
        </div>
      </header>
      <div className="min-w-0 px-6 pb-6">{children}</div>
    </section>
  );
}

export function PageHeader({
  eyebrow,
  title,
  description,
}: {
  eyebrow: string;
  title: string;
  description: string;
}) {
  return (
    <header>
      <p className="text-eyebrow font-semibold uppercase text-faint">{eyebrow}</p>
      <h1 className="mt-2 text-[30px] font-semibold leading-[1.15] tracking-tight text-ink">
        {title}
      </h1>
      <p className="mt-3 max-w-2xl text-[15px] leading-7 text-muted">{description}</p>
    </header>
  );
}
