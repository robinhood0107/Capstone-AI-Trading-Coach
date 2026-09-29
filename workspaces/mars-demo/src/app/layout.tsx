import type { Metadata } from 'next';
import type { ReactNode } from 'react';
import '../../../experience-dashboard/src/app/globals.css';
import { AppShell } from '@/shared/ui/AppShell';
import { THEME_BOOT_SCRIPT } from '@/shared/ui/ThemeToggle';
import { DemoLoginCard } from '@demo/client/DemoLoginCard';
import { DemoAuthProvider } from '@demo/adapters/session';
import { currentDemoSession } from '@demo/server/session-server';

export const dynamic = 'force-dynamic';

export const metadata: Metadata = {
  title: '투자 원칙 기반 AI 트레이딩 코치',
  description: '투자 원칙과 위험통제를 자동매매 흐름에 결합한 의사결정 지원 대시보드 (Experience Dashboard)',
  robots: { index: false, follow: false },
};

export default async function RootLayout({ children }: { children: ReactNode }) {
  const authenticated = Boolean(await currentDemoSession());
  return (
    <html lang="ko" suppressHydrationWarning>
      <head>
        {/* This is the same local typeface sheet used by the FULL dashboard. */}
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
        <DemoAuthProvider authenticated={authenticated}>
          <AppShell loginCard={<DemoLoginCard />}>{children}</AppShell>
        </DemoAuthProvider>
      </body>
    </html>
  );
}
