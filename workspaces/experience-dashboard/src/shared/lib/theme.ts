/** 소개와 로그인, 대시보드가 공유하는 표시 설정. 인증정보를 보관하지 않는다. */
export type ThemeChoice = 'light' | 'dark' | 'system';
export const THEME_KEY = 'capstone.theme.v2';
export const THEME_EVENT = 'capstone-theme-change';

export function readThemeChoice(): ThemeChoice {
  try {
    const current = localStorage.getItem(THEME_KEY);
    if (current === 'light' || current === 'dark' || current === 'system') return current;
    const legacy = [localStorage.getItem('capstone.intro.set.v1'), localStorage.getItem('capstone.theme.v1')].find((value) => value === 'light' || value === 'dark');
    const choice = legacy === 'light' || legacy === 'dark' ? legacy : 'system';
    localStorage.setItem(THEME_KEY, choice);
    return choice;
  } catch { return 'system'; }
}

/** 실제 문서에 적용된 색을 소개에서도 사용한다. 저장이 막혀도 현재 선택은 유지된다. */
export function resolvedTheme(): 'light' | 'dark' {
  const theme = document.documentElement.getAttribute('data-theme');
  return theme === 'light' || theme === 'dark' ? theme : (window.matchMedia('(prefers-color-scheme: dark)').matches ? 'dark' : 'light');
}

/** 다른 탭의 저장 이벤트에도 문서와 라디오 선택을 함께 맞춘다. */
export function syncThemeDocument(): ThemeChoice {
  const choice = readThemeChoice();
  if (choice === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', choice);
  return choice;
}

export function applyTheme(choice: ThemeChoice): void {
  if (choice === 'system') document.documentElement.removeAttribute('data-theme');
  else document.documentElement.setAttribute('data-theme', choice);
  try { localStorage.setItem(THEME_KEY, choice); } catch { /* 저장이 실패해도 표시는 유지한다. */ }
  window.dispatchEvent(new CustomEvent(THEME_EVENT, { detail: choice }));
}

// 첫 paint 전에도 같은 이관 순서를 적용해 로그인 화면의 색 반전을 막는다.
export const THEME_BOOT_SCRIPT = `(function(){try{var k='capstone.theme.v2',v=localStorage.getItem(k);if(v!=='light'&&v!=='dark'&&v!=='system'){v=localStorage.getItem('capstone.intro.set.v1');if(v!=='light'&&v!=='dark')v=localStorage.getItem('capstone.theme.v1');v=v==='light'||v==='dark'?v:'system';localStorage.setItem(k,v);}if(v==='light'||v==='dark')document.documentElement.setAttribute('data-theme',v);else document.documentElement.removeAttribute('data-theme');}catch(e){}})();`;
