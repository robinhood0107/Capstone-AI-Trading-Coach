'use client';

import { useEffect, useState } from 'react';

/**
 * 밝게 / 어둡게 / 시스템 따름.
 *
 * 고른 값은 `localStorage` 에 남긴다. 토큰과 달리 이것은 비밀이 아니라 표시 취향이므로
 * 보관해도 되고, 탭을 새로 열어도 같은 화면이 나오는 편이 낫다.
 *
 * 실제 적용은 `<html data-theme>` 하나로 끝난다. 값이 없으면 속성을 지워 OS 설정
 * (`prefers-color-scheme`)으로 돌아간다. 이 규칙은 globals.css 와 짝이다.
 */
export { THEME_BOOT_SCRIPT } from '@/shared/lib/theme';
import { applyTheme, syncThemeDocument, THEME_EVENT, type ThemeChoice } from '@/shared/lib/theme';

const OPTIONS: { value: ThemeChoice; label: string }[] = [
  { value: 'light', label: '밝게' },
  { value: 'dark', label: '어둡게' },
  { value: 'system', label: '시스템' },
];

export function ThemeToggle() {
  const [choice, setChoice] = useState<ThemeChoice>('system');

  // 서버 render 에는 localStorage 가 없다. mount 뒤에 맞춰 hydration 을 어긋내지 않는다.
  useEffect(() => {
    const sync = (event?: Event) => setChoice(event instanceof CustomEvent ? event.detail : syncThemeDocument());
    sync();
    window.addEventListener(THEME_EVENT, sync);
    window.addEventListener('storage', sync);
    return () => {
      window.removeEventListener(THEME_EVENT, sync);
      window.removeEventListener('storage', sync);
    };
  }, []);

  return (
    <div
      role="radiogroup"
      aria-label="화면 테마"
      className="inline-flex items-center gap-0.5 rounded-full bg-subtle p-0.5"
    >
      {OPTIONS.map((option) => {
        const active = choice === option.value;
        return (
          <button
            key={option.value}
            type="button"
            role="radio"
            aria-checked={active}
            onClick={() => {
              setChoice(option.value);
              applyTheme(option.value);
            }}
            className={`tap rounded-full px-2.5 py-1 text-[12px] transition-colors ${
              active ? 'bg-panel font-semibold text-navy' : 'font-medium text-faint hover:text-ink'
            }`}
          >
            {option.label}
          </button>
        );
      })}
    </div>
  );
}
