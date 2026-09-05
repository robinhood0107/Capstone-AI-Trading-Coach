'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';
import type { ReactNode } from 'react';

interface NavItem {
  href: string;
  label: string;
  note: string;
  icon?: ReactNode;
  /** 같은 경로군에서 활성으로 볼 하위 경로. */
  matches?: string[];
}

const ICON_PROPS = {
  viewBox: '0 0 24 24',
  fill: 'none',
  stroke: 'currentColor',
  strokeWidth: 1.8,
  strokeLinecap: 'round' as const,
  strokeLinejoin: 'round' as const,
  'aria-hidden': true as const,
};

const SECTIONS: NavItem[] = [
  {
    href: '/',
    label: '현황',
    note: '오늘 계좌와 판정',
    icon: (
      <svg {...ICON_PROPS}>
        <rect x="3.5" y="3.5" width="7" height="7" />
        <rect x="13.5" y="3.5" width="7" height="7" />
        <rect x="3.5" y="13.5" width="7" height="7" />
        <rect x="13.5" y="13.5" width="7" height="7" />
      </svg>
    ),
  },
  {
    href: '/rag',
    label: '금융 Agent',
    note: '근거 있는 금융 설명',
    icon: (
      <svg {...ICON_PROPS}>
        <path d="M4 5h16v11H9l-4 4V5Z" />
        <path d="M8 9h8M8 12h5" />
      </svg>
    ),
  },
  {
    href: '/principles',
    label: '내 원칙',
    note: '지킬 기준 정하기',
    icon: (
      <svg {...ICON_PROPS}>
        <path d="M12 3.5 5 6v6c0 4.2 3 7.4 7 8.5 4-1.1 7-4.3 7-8.5V6l-7-2.5Z" />
        <path d="m9 12 2 2 4-4" />
      </svg>
    ),
  },
  {
    href: '/strategy',
    label: '전략 검증',
    note: '모델 비교와 과거 성과',
    matches: ['/model-evaluation', '/backtest'],
    icon: (
      <svg {...ICON_PROPS}>
        <path d="M4 20V11M9.5 20V6.5M15 20v-7M20 20V4" />
      </svg>
    ),
  },
  {
    href: '/automation',
    label: '자동운용',
    note: '주문 검토와 예산·중단',
    matches: ['/order-review'],
    icon: (
      <svg {...ICON_PROPS}>
        <path d="M12.5 3 5 13.5h5.5L11 21l7.5-10.5H13l-.5-7.5Z" />
      </svg>
    ),
  },
  {
    href: '/journal',
    label: '학습일지',
    note: '판단과 배운 점 기록',
    icon: (
      <svg {...ICON_PROPS}>
        <path d="M6 4.5h10.5A1.5 1.5 0 0 1 18 6v14l-3-2-3 2-3-2-3 2V6A1.5 1.5 0 0 1 6 4.5Z" />
        <path d="M8.5 9h7M8.5 12.5h4.5" />
      </svg>
    ),
  },
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
      className={`tap flex shrink-0 items-center gap-3 rounded-control px-3.5 py-2.5 transition-colors lg:shrink ${
        active ? 'bg-subtle text-navy' : 'text-muted hover:bg-subtle/70 hover:text-ink'
      }`}
    >
      <span aria-hidden className={`h-[18px] w-[18px] shrink-0 ${active ? 'text-navy' : 'text-faint'}`}>
        {item.icon}
      </span>
      <span className="min-w-0">
        <span className="flex items-center gap-2 whitespace-nowrap">
          <span className={`text-[14px] ${active ? 'font-semibold' : 'font-medium'}`}>{item.label}</span>
        </span>
        <span className="mt-0.5 hidden text-[11px] leading-4 text-faint lg:block">{item.note}</span>
      </span>
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
            className={`tap rounded-control px-3 py-1.5 text-[12px] transition-colors ${
              active
                ? 'bg-subtle font-semibold text-navy'
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
