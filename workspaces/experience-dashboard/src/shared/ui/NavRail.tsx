'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

interface NavItem {
  href: string;
  label: string;
  note: string;
  /** 같은 경로군에서 활성으로 볼 하위 경로. */
  matches?: string[];
}

const SECTIONS: NavItem[] = [
  { href: '/', label: '현황', note: '오늘 계좌와 판정' },
  { href: '/rag', label: '금융 Agent', note: '근거 있는 금융 설명' },
  { href: '/principles', label: '내 원칙', note: '지킬 기준 정하기' },
  {
    href: '/strategy',
    label: '전략 검증',
    note: '모델 비교와 과거 성과',
    matches: ['/model-evaluation', '/backtest'],
  },
  {
    href: '/automation',
    label: '자동운용',
    note: '주문 검토와 예산·중단',
    matches: ['/order-review'],
  },
  { href: '/journal', label: '학습일지', note: '판단과 배운 점 기록' },
];

export function NavRail() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="주요 화면"
      className="-mx-1 flex gap-1 overflow-x-auto px-1 pb-1 lg:mx-0 lg:flex-col lg:overflow-x-visible lg:px-0 lg:pb-0"
    >
      {SECTIONS.map((item) => (
        <NavLink key={item.href} item={item} pathname={pathname} />
      ))}
    </nav>
  );
}

function NavLink({ item, pathname }: { item: NavItem; pathname: string }) {
  const active = pathname === item.href || (item.matches ?? []).includes(pathname);
  return (
    <Link
      href={item.href}
      aria-current={active ? 'page' : undefined}
      className={`shrink-0 rounded-tile px-3.5 py-2.5 lg:shrink ${
        active ? 'bg-panel text-ink shadow-card' : 'text-muted hover:bg-panel/70 hover:text-ink'
      }`}
    >
      <span className="flex items-center gap-2 whitespace-nowrap">
        <span className={`text-[14px] ${active ? 'font-semibold' : 'font-medium'}`}>{item.label}</span>
      </span>
      <span className="mt-0.5 hidden text-[11px] leading-4 text-faint lg:block">{item.note}</span>
    </Link>
  );
}

const TOOLS: NavItem[] = [
  { href: '/report', label: '보고서', note: '요약 내려받기' },
  { href: '/settings', label: '설정', note: '연결과 옵션' },
];

export function UtilityNav() {
  const pathname = usePathname();
  return (
    <nav aria-label="도구" className="flex items-center gap-0.5">
      {TOOLS.map((item) => {
        const active = pathname === item.href;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? 'page' : undefined}
            className={`rounded-full px-3 py-1.5 text-[12px] ${
              active
                ? 'bg-subtle font-semibold text-ink'
                : 'font-medium text-muted hover:bg-subtle hover:text-ink'
            }`}
          >
            {item.label}
          </Link>
        );
      })}
    </nav>
  );
}
