'use client';

/**
 * 쉬운 설명 모드.
 *
 * 자동운용·전략검증에는 MDD, Sharpe, ATR, Fama-MacBeth 같은 말이 그대로 나온다. 처음
 * 보는 사람에게는 화면이 읽히지 않고, 매번 괄호로 풀어 쓰면 이미 아는 사람에게는 화면이
 * 지저분해진다. 그래서 **켜고 끄는 설명**으로 둔다.
 *
 * 저장은 브라우저에 한다. 이건 그 사람의 읽기 취향이지 계정에 묶인 운용 설정이 아니고,
 * 서버에 두면 status 계약을 올려야 한다(`automation-policy` 를 v3 로 올리는 것과 같은
 * breaking change). 취향 하나 때문에 API 경계를 깨지 않는다.
 *
 * 탭이 여러 개일 수 있으므로 같은 창 안에서는 커스텀 이벤트로, 다른 탭과는 `storage`
 * 이벤트로 맞춘다.
 */

import { useCallback, useEffect, useState } from 'react';

const KEY = 'p1.explainMode';
const EVENT = 'p1:explain-mode';

/** 기본값은 **켬**이다. 모르는 말을 만났을 때 도움이 필요한 쪽이 기본이어야 한다. */
const DEFAULT = true;

export function readExplainMode(): boolean {
  if (typeof window === 'undefined') return DEFAULT;
  try {
    const stored = window.localStorage.getItem(KEY);
    if (stored === null) return DEFAULT;
    return stored === 'on';
  } catch {
    // 사파리 프라이빗 모드 등에서 localStorage 접근 자체가 던진다. 읽기 취향 하나 때문에
    // 화면이 깨지면 안 된다.
    return DEFAULT;
  }
}

export function writeExplainMode(enabled: boolean): void {
  if (typeof window === 'undefined') return;
  try {
    window.localStorage.setItem(KEY, enabled ? 'on' : 'off');
  } catch {
    // 저장하지 못해도 이번 세션 동안은 동작해야 한다.
  }
  window.dispatchEvent(new CustomEvent<boolean>(EVENT, { detail: enabled }));
}

/**
 * 설명 모드를 구독한다.
 *
 * 서버 렌더와 첫 클라이언트 렌더는 반드시 같아야 하므로(hydration mismatch) 처음에는
 * 기본값으로 그리고, 마운트한 뒤에 저장된 값으로 바꾼다.
 */
export function useExplainMode(): [boolean, (next: boolean) => void] {
  const [enabled, setEnabled] = useState(DEFAULT);

  useEffect(() => {
    setEnabled(readExplainMode());

    function onCustom(event: Event) {
      setEnabled((event as CustomEvent<boolean>).detail);
    }
    function onStorage(event: StorageEvent) {
      if (event.key === KEY) setEnabled(readExplainMode());
    }
    window.addEventListener(EVENT, onCustom);
    window.addEventListener('storage', onStorage);
    return () => {
      window.removeEventListener(EVENT, onCustom);
      window.removeEventListener('storage', onStorage);
    };
  }, []);

  const update = useCallback((next: boolean) => {
    setEnabled(next);
    writeExplainMode(next);
  }, []);

  return [enabled, update];
}
