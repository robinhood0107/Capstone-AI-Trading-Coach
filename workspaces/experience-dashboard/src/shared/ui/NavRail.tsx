'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

interface NavItem {
  href: string;
  label: string;
  note: string;
  /** 최종 명세서 5.1 한 줄 흐름에서의 차례. 시연 동선을 화면에 그대로 드러낸다. */
  step?: string;
  /** 같은 경로군에서 활성으로 볼 하위 경로. */
  matches?: string[];
}

const PRIMARY: NavItem[] = [
  { href: '/', label: '현황', note: '오늘 상태' },
  { href: '/principles', label: '내 원칙', note: '기준 정하기', step: '01' },
  {
    href: '/strategy',
    label: '전략 검증',
    note: '모델 비교 · 백테스트',
    step: '02',
    matches: ['/model-evaluation', '/backtest'],
  },
  { href: '/order-review', label: '주문 검토', note: '판정과 근거', step: '03' },
  { href: '/automation', label: '자동운용', note: '예산 · 중단' },
  { href: '/rag', label: '금융 가이드', note: '근거 있는 설명' },
];

const SECONDARY: NavItem[] = [
  { href: '/report', label: '보고서 캡처', note: 'Report' },
  { href: '/settings', label: '설정', note: 'Strong LLM' },
];

/**
 * 데스크톱에서는 세로 레일, 모바일에서는 가로 스크롤 pill.
 * 01·02·03은 명세서 5.3 사용자 시나리오의 순서이며, 시연 대본과 같은 순서다.
 * 보고서/설정은 사용자 동선이 아니므로 아래 그룹으로 내린다.
 */
export function NavRail() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="주요 화면"
      className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1 lg:mx-0 lg:flex-col lg:overflow-x-visible lg:px-0 lg:pb-0"
    >
      {PRIMARY.map((item) => (
        <NavLink key={item.href} item={item} pathname={pathname} />
      ))}

      <span aria-hidden className="mx-1 w-px shrink-0 bg-line lg:my-3 lg:h-px lg:w-auto" />

      {SECONDARY.map((item) => (
        <NavLink key={item.href} item={item} pathname={pathname} muted />
      ))}
    </nav>
  );
}

function NavLink({
  item,
  pathname,
  muted = false,
}: {
  item: NavItem;
  pathname: string;
  muted?: boolean;
}) {
  const active = pathname === item.href || (item.matches ?? []).includes(pathname);
  return (
    <Link
      href={item.href}
      aria-current={active ? 'page' : undefined}
      className={`shrink-0 rounded-tile px-3.5 py-2.5 lg:shrink ${
        active
          ? 'bg-panel text-ink shadow-card'
          : `${muted ? 'text-faint' : 'text-muted'} hover:bg-panel/70 hover:text-ink`
      }`}
    >
      <span className="flex items-center gap-2 whitespace-nowrap">
        {item.step ? (
          <span className={`tnum text-[11px] font-semibold ${active ? 'text-navy' : 'text-faint'}`}>
            {item.step}
          </span>
        ) : null}
        <span className={`text-[14px] ${active ? 'font-semibold' : 'font-medium'}`}>{item.label}</span>
      </span>
      <span className="mt-0.5 hidden text-[11px] leading-4 text-faint lg:block">{item.note}</span>
    </Link>
  );
}
