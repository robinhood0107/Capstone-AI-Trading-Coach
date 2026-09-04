import type { ReactNode } from 'react';

interface PanelProps {
  contract?: string;
  title: string;
  hint?: string;
  actions?: ReactNode;
  children: ReactNode;
}

export function Panel({ title, hint, actions, children }: PanelProps) {
  return (
    <section className="min-w-0 rounded-panel bg-panel shadow-card">
      <header className="flex items-start justify-between gap-4 px-6 pb-4 pt-5">
        <div className="min-w-0">
          <h2 className="text-[16px] font-semibold tracking-tight text-ink">{title}</h2>
          {hint ? <p className="mt-1.5 text-[13px] leading-5 text-muted">{hint}</p> : null}
        </div>
        <div className="flex shrink-0 items-center gap-2">{actions}</div>
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
