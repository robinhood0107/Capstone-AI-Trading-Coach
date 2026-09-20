import type { ReactNode } from 'react';

interface PanelProps {
  /**
   * 이 패널이 묶여 있는 API 계약.
   *
   * 39개 호출부가 이 값을 넘기는데 컴포넌트가 받지도 않아 전부 버려지고 있었다. 화면에
   * API 경로를 보여 줄 이유는 없지만, 버릴 정보도 아니다 - `data-contract` 로 DOM 에
   * 남겨 화면과 계약의 연결을 기계로 확인할 수 있게 한다.
   */
  contract?: string;
  title: string;
  hint?: string;
  actions?: ReactNode;
  children: ReactNode;
}

export function Panel({ contract, title, hint, actions, children }: PanelProps) {
  return (
    <section data-contract={contract} className="min-w-0 rounded-panel bg-panel shadow-card">
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
