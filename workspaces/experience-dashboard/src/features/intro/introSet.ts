'use client';

/**
 * 소개 세트 — 밝게(Tally → NAJM) / 어둡게(Wayfare → Press).
 *
 * **대시보드의 `data-theme` 과 다른 축이다.** 대시보드는 `<html data-theme>` 하나로 테마를
 * 적용하고, 값이 **없는 상태(= OS 설정 따름)가 정상 상태**다(`shared/ui/ThemeToggle.tsx`).
 * 그 속성으로 소개 세트를 고르면 시스템 모드에서 두 세트가 동시에 렌더된다. 그래서 소개는
 * 자기 뿌리 요소에 `data-intro-set` 을 따로 단다.
 *
 * 첫 값은 지금 실제로 보이는 테마에서 가져온다 — 대시보드가 어두우면 소개도 어둡게 시작하는
 * 편이 자연스럽다. 그 뒤로는 사용자가 도크에서 고른 값을 따른다.
 */
export type IntroSet = 'light' | 'dark';

export const INTRO_SET_KEY = 'capstone.intro.set.v1';

/** 서버 render 에는 창이 없다. 마운트 뒤에 맞춰 hydration 을 어긋내지 않는다. */
export function resolveIntroSet(): IntroSet {
  try {
    const stored = localStorage.getItem(INTRO_SET_KEY);
    if (stored === 'light' || stored === 'dark') return stored;
  } catch {
    /* 저장소를 못 읽어도 화면은 정상 */
  }
  try {
    const theme = document.documentElement.getAttribute('data-theme');
    if (theme === 'light' || theme === 'dark') return theme;
    return window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light';
  } catch {
    return 'light';
  }
}

export function rememberIntroSet(value: IntroSet): void {
  try {
    localStorage.setItem(INTRO_SET_KEY, value);
  } catch {
    /* 저장 실패해도 화면은 정상 */
  }
}
