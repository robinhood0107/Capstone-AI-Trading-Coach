'use client';

import { useEffect, useState, type RefObject } from 'react';
import styles from './intro.module.css';

/** 이만큼 내리면 "Scroll" 안내가 사라진다. */
const CUE_FADE_PX = 260;

function clamp(value: number): number {
  return value < 0 ? 0 : value > 1 ? 1 : value;
}

/** 요소가 화면을 지나간 정도. 0 = 아직 아래, 1 = 완전히 위로 지나감. */
function progress(el: Element): number {
  const rect = el.getBoundingClientRect();
  return clamp((window.innerHeight - rect.top) / (rect.height + window.innerHeight));
}

export type Stage = '1' | '2' | '3';

/**
 * 배경 세 층의 교차와 현재 막 판정.
 *
 * 진행도는 두 다리가 화면을 지나간 정도의 합이다(0 = 1막, 1 = 2막, 2 = 마지막 화면).
 * 그 값이 `--o1/--o2/--o3` 로 나가 배경 층의 불투명도가 된다.
 *
 * **불투명도는 상태가 아니라 커스텀 프로퍼티로 직접 쓴다.** 스크롤마다 setState 를 부르면
 * 소개 전체가 다시 렌더된다. React 상태로 올리는 건 실제로 마크업이 바뀌는 두 가지
 * (현재 막, 마지막 화면 도달 여부)뿐이다.
 *
 * `requestAnimationFrame` 으로 코얼레싱하지 않는다. 탭이 숨겨지거나 throttling 되면 콜백이
 * 한 번도 오지 않아 효과가 통째로 멈춘다(프리뷰 패널에서 실제로 재현됐다). 이 계산은
 * `getBoundingClientRect` 두 번 + 커스텀 프로퍼티 몇 개라 스크롤마다 불러도 가볍다.
 */
export function useIntroStage(
  rootRef: RefObject<HTMLElement | null>,
  endRef: RefObject<HTMLElement | null>,
  introSet: 'light' | 'dark',
): { stage: Stage; atEnd: boolean } {
  const [stage, setStage] = useState<Stage>('1');
  const [atEnd, setAtEnd] = useState(false);

  useEffect(() => {
    const root = rootRef.current;
    const end = endRef.current;
    if (!root || !end) return;

    // 감춰진 세트는 높이가 0이라 섞이면 진행도가 망가진다. 활성 세트에서만 고른다.
    const set = root.querySelector(`.${styles[`set--${introSet}`]}`);
    const bridges = set
      ? ([
          set.querySelector(`.${styles['bridge--1']}`),
          set.querySelector(`.${styles['bridge--2']}`),
        ] as (HTMLElement | null)[])
      : [];
    if (!bridges[0] || !bridges[1]) return;

    function paint() {
      const [first, second] = bridges as [HTMLElement, HTMLElement];
      const p1 = progress(first);
      const p2 = progress(second);
      const s = p1 + p2;

      root!.style.setProperty('--o1', clamp(1 - s).toFixed(3));
      root!.style.setProperty('--o2', clamp(1 - Math.abs(s - 1)).toFixed(3));
      root!.style.setProperty('--o3', clamp(s - 1).toFixed(3));
      root!.style.setProperty('--cue-o', clamp(1 - window.scrollY / CUE_FADE_PX).toFixed(3));

      // 다리 문구는 그 구간 한가운데서 가장 진하다.
      first.style.setProperty('--bo', clamp(1 - Math.abs(p1 - 0.5) * 3.4).toFixed(3));
      second.style.setProperty('--bo', clamp(1 - Math.abs(p2 - 0.5) * 3.4).toFixed(3));

      setStage(s < 0.5 ? '1' : s < 1.5 ? '2' : '3');
      // 마지막 화면 도달은 진행도가 아니라 그 요소의 실제 위치로 정한다. 진행도로 정하면
      // 아직 관문 앞에 서 있는데도 "처음으로" 라벨이 떠 버린다.
      setAtEnd(end!.getBoundingClientRect().top <= 1);
    }

    paint();
    window.addEventListener('scroll', paint, { passive: true });
    window.addEventListener('resize', paint);
    window.addEventListener('pageshow', paint);
    document.addEventListener('visibilitychange', paint);
    return () => {
      window.removeEventListener('scroll', paint);
      window.removeEventListener('resize', paint);
      window.removeEventListener('pageshow', paint);
      document.removeEventListener('visibilitychange', paint);
    };
  }, [rootRef, endRef, introSet]);

  return { stage, atEnd };
}
