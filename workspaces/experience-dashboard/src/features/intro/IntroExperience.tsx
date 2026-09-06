'use client';

import { useEffect, useRef, useState, type ReactNode } from 'react';
import styles from './intro.module.css';
import { cx } from './acts/cx';
import { Bridge } from './acts/Bridge';
import { LightAct1 } from './acts/LightAct1';
import { LightAct2 } from './acts/LightAct2';
import { DarkAct1 } from './acts/DarkAct1';
import { DarkAct2 } from './acts/DarkAct2';
import { IntroDock } from './IntroDock';
import { useIntroStage } from './useIntroStage';
import { useIntroGate } from './useIntroGate';
import { rememberIntroSet, resolveIntroSet, type IntroSet } from './introSet';

/**
 * 소개 페이지 — 2막을 지나 다음 화면으로 이어지는 한 스크롤.
 *
 * 세트 둘은 색만 다른 게 아니라 **내용 자체가 다르다.** 비활성 세트는 DOM 에 남기되
 * `display:none` 으로 감춘다(전환이 즉시 끝난다).
 *
 * 마지막 `children` 이 소개 다음에 오는 화면이다 — 미인증이면 로그인 카드, `/intro` 면
 * 대시보드로 가는 안내. 소개와 그 화면 사이 **경계 한 곳에서만** 휠을 가로챈다
 * (`useIntroGate`). 그 뒤로는 자유롭게 스크롤된다.
 */
export function IntroExperience({
  children,
  endLabel = '대시보드',
}: {
  children: ReactNode;
  endLabel?: string;
}) {
  const rootRef = useRef<HTMLDivElement>(null);
  const endRef = useRef<HTMLElement>(null);

  // 서버 render 에는 창이 없다. 첫 마크업은 밝게로 맞추고 마운트 뒤에 실제 값을 반영한다.
  const [introSet, setIntroSet] = useState<IntroSet>('light');
  useEffect(() => {
    setIntroSet(resolveIntroSet());
  }, []);

  const { stage, atEnd } = useIntroStage(rootRef, endRef, introSet);
  const { locked, go } = useIntroGate(endRef, introSet);

  function changeSet(next: IntroSet) {
    setIntroSet(next);
    rememberIntroSet(next);
  }

  return (
    <div
      ref={rootRef}
      className={styles.introRoot}
      data-intro-set={introSet}
      data-stage={stage}
      data-gate={locked ? 'on' : 'off'}
    >
      {/*
        소개 전용 웹폰트(392KB, @font-face 668개). 로컬 파일만 참조하므로 외부 요청이 없고,
        인터넷이 끊겨도 글자가 깨지지 않는다. 한글은 unicode-range 서브셋이라 브라우저가
        필요한 조각만 내려받는다.

        번들에 import 하지 않고 여기서 링크한다 — 그러면 소개를 띄울 때만 값을 치르고,
        대시보드의 critical CSS 는 그대로 작다. React 19 가 이 태그를 <head> 로 올린다.
      */}
      {/* eslint-disable-next-line @next/next/no-css-tags */}
      <link rel="stylesheet" href="/fonts.css" precedence="default" />

      <div className={cx('bg')} aria-hidden="true">
        <div className={cx('bg__1')} />
        <div className={cx('bg__2')} />
        <div className={cx('bg__3')} />
      </div>

      <div className={cx('introMain')} id="introTop">
        {/* 밝게 세트 · Tally → NAJM */}
        <div className={cx('set--light')}>
          <LightAct1 />
          <Bridge index={1}>
            그래서 <em>열네 줄</em>을 먼저 만들었습니다.
          </Bridge>
          <LightAct2 />
          <Bridge index={2}>
            이제 <em>당신의 화면</em>입니다.
          </Bridge>
        </div>

        {/* 어둡게 세트 · Wayfare → Press */}
        <div className={cx('set--dark')}>
          <DarkAct1 />
          <Bridge index={1}>
            이번엔 <em>직접</em> 움직여 보세요.
          </Bridge>
          <DarkAct2 />
          <Bridge index={2}>
            이제 <em>당신의 화면</em>입니다.
          </Bridge>
        </div>

        <section ref={endRef} className={cx('endAct')} id="introEnd">
          {children}
        </section>
      </div>

      <IntroDock
        introSet={introSet}
        onChangeSet={changeSet}
        atEnd={atEnd}
        endLabel={endLabel}
        onJump={() => go(!atEnd)}
      />
    </div>
  );
}
