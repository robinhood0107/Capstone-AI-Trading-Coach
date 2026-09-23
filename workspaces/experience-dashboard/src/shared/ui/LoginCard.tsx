'use client';

import { LocalPasswordLoginCard } from './LocalPasswordLoginCard';

/** The full product delegates identity verification to Google's OIDC code flow. */
export function LoginCard() {
  if (process.env.NEXT_PUBLIC_MARS_PRODUCT === 'demo') return null;
  if (process.env.NEXT_PUBLIC_MARS_PRODUCT !== 'full') return <LocalPasswordLoginCard />;
  return (
    <div className="mx-auto w-full max-w-[420px]">
      <div className="rounded-panel border border-line bg-panel px-7 py-8 shadow-card">
        <span
          aria-hidden
          className="grid h-11 w-11 place-items-center rounded-control bg-brand text-[14px] font-semibold text-on-brand"
        >
          AI
        </span>
        <h1 className="mt-5 text-[24px] font-semibold tracking-tight text-ink">시작하기</h1>
        <p className="mt-2 text-[14px] leading-6 text-muted">
          Google 계정으로 로그인하면 본인 투자 원칙과 운용 현황을 볼 수 있습니다.
        </p>
        <a
          href="/api/v1/auth/oidc/start/google"
          className="tap mt-7 block w-full rounded-control bg-brand px-4 py-3 text-center text-[15px] font-semibold text-on-brand hover:opacity-90"
        >
          Google로 계속
        </a>
      </div>
    </div>
  );
}
