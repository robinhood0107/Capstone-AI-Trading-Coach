import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import './globals.css';
import { NavRail } from '@/shared/ui/NavRail';
import { StatusBar } from '@/shared/ui/StatusBar';
import { LoginGate } from '@/shared/ui/LoginGate';

export const metadata: Metadata = {
  title: '투자 원칙 기반 AI 트레이딩 코치',
  description:
    '투자 원칙과 위험통제를 자동매매 흐름에 결합한 의사결정 지원 대시보드 (Experience Dashboard)',
};

export default function RootLayout({ children }: { children: ReactNode }) {
  return (
    <html lang="ko">
      <body className="min-h-screen bg-surface font-sans antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:bg-navy focus:px-3 focus:py-2 focus:text-white"
        >
          본문으로 건너뛰기
        </a>
        <div className="flex min-h-screen">
          <aside className="hidden w-60 shrink-0 border-r border-rule bg-surface lg:block">
            <div className="border-b border-rule px-4 py-5">
              <p className="font-mono text-eyebrow uppercase text-faint">Experience Dashboard</p>
              <p className="mt-2 text-[15px] font-semibold leading-6 tracking-tight text-ink">
                투자 원칙 기반
                <br />
                AI 트레이딩 코치
              </p>
            </div>
            <div className="py-3">
              <NavRail />
            </div>
            <p className="px-4 py-6 text-[11px] leading-5 text-faint">
              이 화면은 판정을 만들지 않습니다. Decision Platform이 낸 결과를 읽기 쉽게 옮겨 보여줍니다.
            </p>
          </aside>

          <div className="flex min-w-0 flex-1 flex-col">
            <StatusBar />
            <div className="border-b border-rule bg-surface px-6 py-3 lg:hidden">
              <NavRail />
            </div>
            <main id="main" className="mx-auto w-full max-w-[1180px] flex-1 px-6 py-8">
              <LoginGate>{children}</LoginGate>
            </main>
            <footer className="border-t border-rule px-6 py-4 text-[11px] leading-5 text-faint">
              KIS 모의투자 환경 기준입니다. 이 시스템은 어떤 수익도 보장하지 않으며 투자 판단과 책임은
              사용자에게 있습니다.
            </footer>
          </div>
        </div>
      </body>
    </html>
  );
}
