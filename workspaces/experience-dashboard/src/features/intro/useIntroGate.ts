'use client';

import { useCallback, useEffect, useRef, useState, type RefObject } from 'react';

/*
 * 관문 — 소개와 그 다음 화면 사이 경계 딱 한 곳의 휠 페이저.
 *
 * 넥슨 튜토리얼(nexon-tutorial.com)은 페이지 전체를 휠 가로채기 페이저로 만든다. 문서 높이가
 * 뷰포트와 같아 네이티브 스크롤이 아예 없고, wheel 을 preventDefault 로 잡아 한 동작에 한
 * 섹션씩 넘긴다. 그래서 "절반만 걸친 상태"가 생길 수 없다.
 *
 * 우리 뒷장(대시보드)은 한 화면을 넘는 긴 문서라 전체를 그렇게 만들 수 없다. 그래서 경계
 * 한 곳에서만 같은 규칙을 쓴다. 소개의 마지막 화면에서 스크롤이 멈추고, 휠 동작을 한 번 더
 * 줘야 넘어간다. 넘어간 뒤에는 풀려서 자유롭게 스크롤된다.
 *
 * 아래 세 상수는 넥슨 쪽 소스를 직접 읽어서 가져온 값이다.
 */
const WHEEL_MIN = 15; // |deltaY| 가 이보다 작으면 무시한다
const LOCK_MS = 150; // 한 동작 뒤 이만큼 잠근다
const TOUCH_MIN = 30; // 터치는 이만큼 끌어야 한 동작으로 친다

const GLIDE_MAX_MS = 1400; // 도착 감지가 끝내 안 되면 여기서 포기한다
const GATE_EPS = 2; // 목표와 이 안이면 이미 도착한 것으로 본다
const FREE_MS = 700; // 관문 반대 방향으로 밀었을 때 놓아 주는 시간

export interface IntroGate {
  /** 관문이 걸려 휠이 막혀 있는가. */
  locked: boolean;
  /** 관문을 통과해 이동한다. 이동 버튼과 본문 CTA 가 쓴다. */
  go: (toEnd: boolean) => void;
}

/**
 * @param endRef 소개 다음에 오는 화면. 이 요소의 윗변이 경계다.
 * @param resetKey 값이 바뀌면 관문 상태를 새 좌표로 다시 잡는다(테마 세트 전환).
 */
export function useIntroGate(
  endRef: RefObject<HTMLElement | null>,
  resetKey: unknown,
): IntroGate {
  const [locked, setLocked] = useState(false);
  const lockedRef = useRef(false);
  const busyRef = useRef(false);
  const lastActionRef = useRef(0);
  const freeUntilRef = useRef(0);

  const setLock = useCallback((on: boolean) => {
    if (lockedRef.current === on) return;
    lockedRef.current = on;
    setLocked(on);
  }, []);

  const go = useCallback(
    (toEnd: boolean) => {
      const end = endRef.current;
      if (!end || busyRef.current) return;

      const target = toEnd ? end.offsetTop : Math.max(0, end.offsetTop - window.innerHeight);
      busyRef.current = true;
      setLock(false);
      freeUntilRef.current = 0;

      const reduce = window.matchMedia('(prefers-reduced-motion: reduce)').matches;
      window.scrollTo({ top: target, behavior: reduce ? 'auto' : 'smooth' });

      // 고정 타이머 대신 실제 도착을 본다. 부드러운 스크롤이 예상보다 오래 걸려도 비행 중에
      // 잠금이 풀리지 않는다. rAF 를 쓰지 않는 이유는 useIntroStage 쪽 주석과 같다.
      const deadline = Date.now() + GLIDE_MAX_MS;
      const tick = () => {
        if (Math.abs(window.scrollY - target) <= GATE_EPS || Date.now() > deadline) {
          busyRef.current = false;
          return;
        }
        window.setTimeout(tick, 40);
      };
      tick();
    },
    [endRef, setLock],
  );

  useEffect(() => {
    const end = endRef.current;
    if (!end) return;
    const reduce = window.matchMedia('(prefers-reduced-motion: reduce)');

    const endTop = () => end.offsetTop;
    const landingEnd = () => Math.max(0, end.offsetTop - window.innerHeight);

    /**
     * 경계의 어느 쪽에 서 있나. 플래그로 들고 있지 않고 매번 위치에서 구한다.
     *
     * 예전에는 이동이 세팅하는 플래그였는데, 앵커 점프나 스크롤 복원처럼 관문을 거치지 않고
     * 뒷장에 도착하는 길이 있어서 플래그가 사실과 어긋났다. 그 상태로 armGate 가 돌면 뒷장
     * 상단에서 소개 끝(한 화면 위)으로 통째로 되돌려 버린다 — 덜덜거림의 원인이었다.
     */
    const past = () => window.scrollY > (landingEnd() + endTop()) / 2;

    /**
     * 목표에 붙인다. **이미 목표면 아무것도 하지 않는다.**
     *
     * scrollTo 를 부르면 그게 다시 scroll 이벤트를 낳아 armGate 가 또 돌고, 좁은 데드존과
     * 맞물려 진동한다. 실제로 넘어야 할 때만 한 번 부른다.
     */
    const stickTo = (target: number) => {
      if (Math.abs(window.scrollY - target) > GATE_EPS) window.scrollTo(0, target);
      setLock(true);
    };

    const armGate = () => {
      if (reduce.matches || busyRef.current) return;
      if (Date.now() < freeUntilRef.current) {
        setLock(false);
        return;
      }
      const y = window.scrollY;
      if (!past() && y >= landingEnd() - GATE_EPS) stickTo(landingEnd());
      else if (past() && y <= endTop() + GATE_EPS) stickTo(endTop());
      else setLock(false);
    };

    /** 관문 반대 방향으로 밀면 잠깐 놓아 준다. 안 그러면 경계에 갇힌다. */
    const free = () => {
      freeUntilRef.current = Date.now() + FREE_MS;
      setLock(false);
    };

    const gesture = (dir: number) => {
      const now = Date.now();
      if (now - lastActionRef.current < LOCK_MS) return;
      lastActionRef.current = now;
      if (dir > 0 && !past()) go(true);
      else if (dir < 0 && past()) go(false);
      else free();
    };

    const onWheel = (event: WheelEvent) => {
      if (!lockedRef.current) return;
      // overflow:hidden 은 쓰지 않는다. 문서 높이가 접혔다 펴지면서 해제 직후 스크롤 위치가
      // 어긋난다(실측 21px). 넥슨도 preventDefault 만 쓴다.
      if (event.cancelable) event.preventDefault();
      if (Math.abs(event.deltaY) < WHEEL_MIN) return;
      gesture(event.deltaY > 0 ? 1 : -1);
    };

    // 휠만 막으면 키보드·터치 사용자가 갇힌다. 같은 관문을 통과할 길을 함께 연다.
    const onKey = (event: KeyboardEvent) => {
      if (!lockedRef.current) return;
      if (event.key === 'ArrowDown' || event.key === 'PageDown' || event.key === ' ') {
        event.preventDefault();
        gesture(1);
      } else if (event.key === 'ArrowUp' || event.key === 'PageUp') {
        event.preventDefault();
        gesture(-1);
      }
    };

    let touchStartY = 0;
    const onTouchStart = (event: TouchEvent) => {
      const touch = event.touches[0];
      if (touch) touchStartY = touch.clientY;
    };
    const onTouchMove = (event: TouchEvent) => {
      if (!lockedRef.current) return;
      const touch = event.touches[0];
      if (!touch) return;
      const dy = touchStartY - touch.clientY;
      if (Math.abs(dy) < TOUCH_MIN) return;
      if (event.cancelable) event.preventDefault();
      gesture(dy > 0 ? 1 : -1);
    };

    // 본문의 "#introEnd" 링크도 같은 길로 보낸다. 네이티브 앵커 점프로 두면 스크롤이 경계를
    // 지나는 도중 armGate 에 잡혀 되돌려진다.
    const onClick = (event: MouseEvent) => {
      const target = event.target;
      if (!(target instanceof Element)) return;
      if (!target.closest('a[href="#introEnd"]')) return;
      event.preventDefault();
      go(true);
    };

    // 테마 세트를 바꾸면 소개 길이가 통째로 달라진다. 새 좌표로 다시 잡는다.
    busyRef.current = false;
    freeUntilRef.current = 0;
    setLock(false);
    armGate();

    window.addEventListener('scroll', armGate, { passive: true });
    window.addEventListener('wheel', onWheel, { passive: false });
    window.addEventListener('keydown', onKey);
    window.addEventListener('touchstart', onTouchStart, { passive: true });
    window.addEventListener('touchmove', onTouchMove, { passive: false });
    document.addEventListener('click', onClick);
    return () => {
      window.removeEventListener('scroll', armGate);
      window.removeEventListener('wheel', onWheel);
      window.removeEventListener('keydown', onKey);
      window.removeEventListener('touchstart', onTouchStart);
      window.removeEventListener('touchmove', onTouchMove);
      document.removeEventListener('click', onClick);
      setLock(false);
    };
  }, [endRef, go, setLock, resetKey]);

  return { locked, go };
}
