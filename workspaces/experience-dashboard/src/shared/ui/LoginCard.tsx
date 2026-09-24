'use client';

import Image from 'next/image';

import { LocalPasswordLoginCard } from './LocalPasswordLoginCard';

/** FULL uses provider-hosted login; first successful login provisions a USER automatically. */
export function LoginCard() {
  if (process.env.NEXT_PUBLIC_MARS_PRODUCT === 'demo') return null;
  if (process.env.NEXT_PUBLIC_MARS_PRODUCT !== 'full') return <LocalPasswordLoginCard />;
  return (
    <div className="mx-auto w-full max-w-[420px]">
      <div className="rounded-panel border border-line bg-panel px-7 py-8 shadow-card">
        <h1 className="text-center text-[24px] font-semibold tracking-tight text-ink">계정으로 시작하기</h1>
        <p className="mt-2 text-center text-[14px] leading-6 text-muted">
          Google 또는 카카오 계정으로 로그인하세요.
        </p>
        <div className="my-7 flex items-center gap-3 text-[12px] text-faint" role="separator" aria-label="간편 로그인">
          <span className="h-px flex-1 bg-line" />
          <span>간편 로그인</span>
          <span className="h-px flex-1 bg-line" />
        </div>
        <div className="flex justify-center gap-4">
          <a
            href="/api/v1/auth/oidc/start/google"
            aria-label="Google로 로그인"
            className="tap flex min-h-20 min-w-32 flex-col items-center justify-center gap-2 rounded-xl border border-line bg-white px-4 py-3 text-[12px] font-medium text-ink shadow-sm transition hover:border-muted hover:bg-canvas focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
          >
            <Image
              src="/auth/google-g-logo.png"
              alt=""
              width={50}
              height={51}
              className="h-[22px] w-auto shrink-0"
              priority
            />
            <span>Google로 계속</span>
          </a>
          <a
            href="/api/v1/auth/oidc/start/kakao"
            aria-label="카카오로 로그인"
            className="tap flex min-h-20 min-w-32 flex-col items-center justify-center gap-2 rounded-xl bg-[#FEE500] px-4 py-3 text-[12px] font-medium text-black/85 shadow-sm transition hover:brightness-95 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
          >
            <Image src="/auth/kakao-talk-symbol.svg" alt="" width={24} height={24} />
            <span>카카오로 계속</span>
          </a>
        </div>
        <p className="mt-6 text-center text-[12px] leading-5 text-muted">
          첫 로그인에서 계정이 자동으로 만들어져 별도 회원가입이나 비밀번호가 필요하지 않습니다.
        </p>
      </div>
    </div>
  );
}
