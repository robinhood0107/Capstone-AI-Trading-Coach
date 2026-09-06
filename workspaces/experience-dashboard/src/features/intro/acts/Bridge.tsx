import type { ReactNode } from 'react';
import { cx } from './cx';

/**
 * 두 막 사이의 다리.
 *
 * 화면 하나를 넘는 빈 구간이다. 여기를 지나는 동안 배경 세 층이 교차하고, 문구는 구간
 * 한가운데서 가장 진해진다(`--bo`, useIntroStage 가 매 스크롤마다 다시 계산한다).
 */
export function Bridge({ index, children }: { index: 1 | 2; children: ReactNode }) {
  return (
    <div className={cx('bridge', `bridge--${index}`)}>
      <p>{children}</p>
    </div>
  );
}
