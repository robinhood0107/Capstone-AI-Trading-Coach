import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import './globals.css';
import { AppShell } from '@/shared/ui/AppShell';
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
        {/* 소개를 거치지 않은 직접 접속에도 같은 로컬 글꼴을 제공한다. */}
        {/* eslint-disable-next-line @next/next/no-css-tags */}
        <link rel="stylesheet" href="/ui-fonts.css" />
        <script dangerouslySetInnerHTML={{ __html: THEME_BOOT_SCRIPT }} />
      </head>
      <body className="min-h-screen bg-surface font-sans antialiased">
        <a
          href="#main"
          className="sr-only focus:not-sr-only focus:absolute focus:left-4 focus:top-4 focus:z-50 focus:rounded-full focus:bg-brand focus:px-4 focus:py-2 focus:text-on-brand"
        >
          본문으로 건너뛰기
        </a>
        {/* 로그인 상태에 따라 소개 페이지와 대시보드로 갈린다. */}
        <AppShell>{children}</AppShell>
      </body>
    </html>
  );
}
