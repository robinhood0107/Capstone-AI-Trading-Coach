'use client';

import Link from 'next/link';
import { usePathname } from 'next/navigation';

const ITEMS = [
  { href: '/', label: '현황', note: '오늘 상태' },
  { href: '/principles', label: '내 원칙', note: 'Principle' },
  { href: '/automation', label: '자동운용', note: 'Budget · Exit' },
  { href: '/order-review', label: '주문 검토', note: 'Decision · Risk' },
  { href: '/model-evaluation', label: '모델 비교', note: 'Signal v2' },
  { href: '/backtest', label: '백테스트 리포트', note: 'Backtest' },
  { href: '/rag', label: '금융 가이드', note: 'RAG' },
  { href: '/report', label: '보고서 캡처', note: 'Report' },
];

export function NavRail() {
  const pathname = usePathname();

  return (
    <nav aria-label="주요 화면" className="flex flex-col gap-px">
      {ITEMS.map((item) => {
        const active = pathname === item.href;
        return (
          <Link
            key={item.href}
            href={item.href}
            aria-current={active ? 'page' : undefined}
            className={`block border-l-2 px-4 py-3 ${
              active
                ? 'border-navy bg-panel text-ink'
                : 'border-transparent text-muted hover:border-rule hover:bg-panel/60 hover:text-ink'
            }`}
          >
            <span className="block text-[14px] font-medium">{item.label}</span>
            <span className="mt-0.5 block font-mono text-eyebrow uppercase text-faint">{item.note}</span>
          </Link>
        );
      })}
    </nav>
  );
}
