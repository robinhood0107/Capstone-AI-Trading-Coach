import type { ReactNode } from 'react';

/** Shared login presentation. Product applications provide their own auth actions. */
export function LoginCardFrame({
  headingId = 'login-card-title',
  title,
  description,
  eyebrow,
  className = '',
  children,
}: {
  headingId?: string;
  title: string;
  description: string;
  eyebrow?: string;
  className?: string;
  children: ReactNode;
}) {
  return (
    <div className="mx-auto w-full max-w-[420px] pb-40 sm:pb-0">
      <section
        aria-labelledby={headingId}
        className={`rounded-panel border border-line bg-panel px-6 py-8 sm:px-8 ${className}`.trim()}
      >
        {eyebrow ? <p className="text-center text-[11px] font-semibold uppercase tracking-[0.12em] text-faint">{eyebrow}</p> : null}
        <h1 id={headingId} className={`${eyebrow ? 'mt-3' : ''} text-center text-[24px] font-semibold tracking-tight text-ink`}>
          {title}
        </h1>
        <p className="mt-2 text-center text-[14px] leading-6 text-muted">{description}</p>
        {children}
      </section>
    </div>
  );
}
