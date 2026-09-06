'use client';

import Link from 'next/link';
import { IntroExperience } from '@/features/intro/IntroExperience';

/**
 * 로그인 여부와 무관하게 소개 전체를 보여 준다.
 *
 * `/` 는 로그인 상태로 갈라져 인증된 사용자는 소개를 다시 볼 수 없다. 시연이나 화면 캡처처럼
 * 소개만 필요한 경우가 있어 고정 주소를 따로 둔다.
 *
 * `AppShell` 은 이 경로에 크롬도 로그인 판단도 씌우지 않는다(`BARE_ROUTES`). 소개는 화면
 * 전체를 쓰는 화면이라 `<main>` 의 최대 폭 안에 넣으면 레이아웃이 무너진다.
 */
export default function IntroPage() {
  return (
    <IntroExperience endLabel="대시보드">
      <div className="mx-auto w-full max-w-[420px] text-center">
        <h2 className="text-[24px] font-semibold tracking-tight text-ink">여기까지가 소개입니다</h2>
        <p className="mt-2 text-[14px] leading-6 text-muted">
          실제 화면은 로그인 후 대시보드에서 볼 수 있습니다.
        </p>
        <Link
          href="/"
          className="tap mt-6 inline-block rounded-control bg-brand px-5 py-3 text-[15px] font-semibold text-on-brand hover:opacity-90"
        >
          대시보드로 가기
        </Link>
      </div>
    </IntroExperience>
  );
}
