'use client';

import Image from 'next/image';

import { LocalPasswordLoginCard } from './LocalPasswordLoginCard';

/** FULL uses provider-hosted login; first successful login provisions a USER automatically. */
export function LoginCard() {
  if (process.env.NEXT_PUBLIC_MARS_PRODUCT === 'demo') return null;
  if (process.env.NEXT_PUBLIC_MARS_PRODUCT !== 'full') return <LocalPasswordLoginCard />;
  return (
    <div className="mx-auto w-full max-w-[420px]">
      <section
        aria-labelledby="provider-login-title"
        aria-describedby="provider-login-note"
        className="rounded-panel border border-line bg-panel px-6 py-8 shadow-card sm:px-8"
      >
        <h1 id="provider-login-title" className="text-center text-[24px] font-semibold tracking-tight text-ink">
          계정으로 계속하기
        </h1>
        <p className="mt-2 text-center text-[14px] leading-6 text-muted">
          Google 또는 카카오 계정으로 로그인하세요.
        </p>
        <div className="mt-7 grid gap-3" role="group" aria-label="로그인 제공자">
          <a
            href="/api/v1/auth/oidc/start/google"
            data-provider="google"
            aria-label="Google로 로그인"
            className="tap provider-signin-button google-signin-button flex items-center justify-center rounded-full border px-3 focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
            style={{ color: 'var(--google-signin-text)' }}
          >
            <span aria-hidden="true" className="absolute left-3 top-1/2 grid h-6 w-6 shrink-0 -translate-y-1/2 place-items-center rounded-full bg-white">
              <Image src="/auth/google-g-logo.png" alt="" width={18} height={18} className="h-[18px] w-[18px]" />
            </span>
            <span className="w-full text-center">Google로 계속</span>
          </a>
          <a
            href="/api/v1/auth/oidc/start/kakao"
            data-provider="kakao"
            aria-label="카카오로 로그인"
            className="tap provider-signin-button kakao-signin-button flex items-center justify-center gap-[10px] rounded-xl bg-[#FEE500] px-3 text-[14px] font-medium leading-[18px] focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-brand"
            style={{ color: 'rgba(0, 0, 0, 0.85)' }}
          >
            <Image src="/auth/kakao-talk-symbol.svg" alt="" width={32} height={32} className="h-8 w-8 shrink-0" />
            <span>카카오 로그인</span>
          </a>
        </div>
        <p id="provider-login-note" className="mt-6 text-center text-[12px] leading-5 text-muted">
          처음 로그인하면 MARS 계정이 자동으로 만들어집니다. 별도 가입이나 비밀번호는 필요하지 않습니다.
        </p>
      </section>
    </div>
  );
}
