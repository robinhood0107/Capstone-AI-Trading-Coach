'use client';

import type { ReactNode } from 'react';
import Image from 'next/image';
import { usePathname } from 'next/navigation';
import { apiMode } from '@/shared/api/client';
import { useSession } from '@/shared/api/session';
import { NavRail } from '@/shared/ui/NavRail';
import { StatusBar } from '@/shared/ui/StatusBar';
import { LoginCard } from '@/shared/ui/LoginCard';
import { IntroExperience } from '@/features/intro/IntroExperience';

/**
 * 로그인 상태로 갈라지는 바깥 껍데기.
 *
 * - **미인증** — 소개 페이지 한 장. 2막을 지나 관문을 넘으면 로그인 카드가 나온다.
 *   대시보드 크롬(좌측 내비·상단 상태바)은 아예 렌더하지 않는다. 소개는 화면 전체를
 *   쓰는 화면이라 `<main>` 의 최대 폭 안에 넣으면 레이아웃이 무너진다.
 * - **인증** 또는 **mock 모드** — 지금까지와 똑같은 대시보드.
 *
 * 예전에는 `LoginGate` 가 `<main>` 안에서 이 판단을 했다. 소개를 full-bleed 로 두려면
 * 판단이 크롬 바깥으로 올라와야 해서 여기로 옮겼다. 로그인 폼 자체는 그대로 재사용한다.
 */
/** 크롬도 로그인 판단도 씌우지 않고 그대로 내보내는 라우트. */
const BARE_ROUTES = new Set(['/intro']);

export function AppShell({ children }: { children: ReactNode }) {
  const { authenticated } = useSession();
  const pathname = usePathname();

  // `/intro` 는 자기가 소개 전체를 그린다. 크롬을 씌우면 `<main>` 최대 폭에 갇힌다.
  if (BARE_ROUTES.has(pathname)) return <>{children}</>;

  // mock 모드는 서버가 없으므로 로그인 자체를 건너뛴다.
  if (apiMode() !== 'mock' && !authenticated) {
    return (
      <IntroExperience endLabel="로그인">
        <LoginCard />
      </IntroExperience>
    );
  }

  return <DashboardChrome>{children}</DashboardChrome>;
}

function DashboardChrome({ children }: { children: ReactNode }) {
  return (
    <div className="flex min-h-screen">
      <aside className="hidden w-[264px] shrink-0 lg:block">
        <div className="sticky top-0 flex h-screen flex-col px-4 py-6">
          <div className="flex items-center gap-3 px-2">
            <span
              aria-hidden
              className="relative block h-10 w-10 shrink-0 overflow-hidden rounded-control"
            >
              <Image
                src="/mascot.png"
                alt=""
                fill
                sizes="40px"
                quality={90}
                className="object-cover"
                priority
              />
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
          {children}
        </main>
        <footer className="mx-auto w-full max-w-[1240px] px-5 pb-10 pt-2 text-[11px] leading-5 text-faint sm:px-8">
          이 시스템은 어떤 수익도 보장하지 않으며 투자 판단과 책임은 사용자에게 있습니다.
        </footer>
      </div>
    </div>
  );
}
