import type { CSSProperties } from 'react';
import { cx } from './cx';

/**
 * 스크롤 안내.
 *
 * 본문 글자 위에 앉으므로 뒤에 지면색 스크림을 깐다(`.cuewrap::after`). 아이콘 뒤만
 * 가리면 좌우 글자가 그대로 부딪힌다.
 *
 * `flow` 는 절대배치 대신 문서 흐름에 얹는 변형이다. 2막 끝처럼 화면 바닥에 고정할
 * 자리가 없는 곳에서 쓴다.
 */
export function ScrollCue({ flow = false, style }: { flow?: boolean; style?: CSSProperties }) {
  const cue = (
    <div
      className={flow ? cx('cue', 'cue--flow') : cx('cue')}
      aria-hidden="true"
      style={flow ? style : undefined}
    >
      <span className={cx('cue__m')} />
      <svg
        className={cx('cue__c')}
        viewBox="0 0 24 24"
        fill="none"
        stroke="currentColor"
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="m6 9 6 6 6-6" />
      </svg>
      <span className={cx('cue__t')}>Scroll</span>
    </div>
  );
  if (flow) return cue;
  return (
    <div className={cx('cuewrap')} aria-hidden="true">
      {cue}
    </div>
  );
}
