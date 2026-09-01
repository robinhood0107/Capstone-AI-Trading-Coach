import type { ReactNode } from 'react';

interface PanelProps {
  /** 이 패널이 소비하는 계약 이름. 장식이 아니라 팀 간 추적 단위다. */
  contract?: string;
  title: string;
  hint?: string;
  actions?: ReactNode;
  children: ReactNode;
}

export function Panel({ contract, title, hint, actions, children }: PanelProps) {
  return (
    <section className="min-w-0 border border-line bg-panel">
      <header className="flex items-start justify-between gap-4 border-b border-line px-5 py-4">
        <div className="min-w-0">
          {contract ? (
            <p className="font-mono text-eyebrow uppercase text-faint">{contract}</p>
          ) : null}
          <h2 className="mt-1 text-[15px] font-semibold tracking-tight text-ink">{title}</h2>
          {hint ? <p className="mt-1 text-[13px] leading-5 text-muted">{hint}</p> : null}
        </div>
        {actions ? <div className="shrink-0">{actions}</div> : null}
      </header>
      <div className="min-w-0 px-5 py-5">{children}</div>
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
    <header className="border-b border-rule pb-6">
      <p className="font-mono text-eyebrow uppercase text-navy">{eyebrow}</p>
      <h1 className="mt-2 text-2xl font-semibold tracking-tight text-ink">{title}</h1>
      <p className="mt-2 max-w-3xl text-sm leading-6 text-muted">{description}</p>
    </header>
  );
}
