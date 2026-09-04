import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import './globals.css';
import { NavRail } from '@/shared/ui/NavRail';
import { StatusBar } from '@/shared/ui/StatusBar';
import { LoginGate } from '@/shared/ui/LoginGate';
import { THEME_BOOT_SCRIPT } from '@/shared/ui/ThemeToggle';

export const metadata: Metadata = {
  title: '투자 원칙 기반 AI 트레이딩 코치',
  description:
    '투자 원칙과 위험통제를 자동매매 흐름에 결합한 의사결정 지원 대시보드 (Experience Dashboard)',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="min-h-screen bg-surface font-sans antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-full focus:bg-brand focus:px-4 focus:py-2 focus:text-on-brand"
        >
          본문으로 건너뛰기
        </a>
        <div className="flex min-h-screen">
          <aside className="hidden w-[264px] shrink-0 lg:block">
            <div className="sticky top-0 flex h-screen flex-col px-4 py-6">
              <div className="flex items-center gap-3 px-2">
                <span
                  aria-hidden
                  className="grid h-10 w-10 shrink-0 place-items-center rounded-control bg-brand text-[13px] font-semibold tracking-tight text-on-brand"
                >
                  AI
                </span>
                <span className="min-w-0">
                  <span className="block text-[15px] font-semibold leading-tight tracking-tight text-ink">
                    트레이딩 코치
                  </span>
                  <span className="block text-[12px] leading-tight text-faint">투자 원칙 기반 운용</span>
                </span>
              </div>

              <div className="mt-8 min-h-0 flex-1 overflow-y-auto">
                <NavRail />
              </div>
            </div>
          </aside>

          <div className="flex min-w-0 flex-1 flex-col">
            <StatusBar />
            <div className="border-b border-line bg-panel px-4 py-3 lg:hidden">
              <NavRail />
            </div>
            <main id="main" className="mx-auto w-full max-w-[1240px] flex-1 px-5 py-8 sm:px-8 sm:py-10">
              <LoginGate>{children}</LoginGate>
            </main>
            <footer className="mx-auto w-full max-w-[1240px] px-5 pb-10 pt-2 text-[11px] leading-5 text-faint sm:px-8">
              KIS 모의투자 환경 기준입니다. 이 시스템은 어떤 수익도 보장하지 않으며 투자 판단과 책임은
              사용자에게 있습니다.
            </footer>
          </div>
        </div>
      </body>
    </html>
  );
}
