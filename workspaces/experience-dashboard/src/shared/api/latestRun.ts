'use client';

import { useEffect, useState } from 'react';
import { api } from '@/shared/api/endpoints';

/**
 * 화면이 볼 실행을 스스로 고른다.
 *
 * 전에는 `run_...` 을 입력받는 칸이 화면 맨 위에 있었다. 그 값은 산출물 적재 파이프라인의
 * 내부 식별자라 쓰는 사람이 알 수 없고, 결국 화면은 늘 비어 있거나 시연 때 손으로 채워졌다.
 * 서버가 "가장 최근에 검증된 실행"을 알고 있으므로 그것을 물어보고 끝낸다.
 *
 * 산출물이 하나도 없으면 `runId` 는 null 로 남고, 화면은 그 사실을 빈 상태로 말한다 -
 * 없는 것을 있는 것처럼 만들지 않는다.
 */
export type LatestRunKind = 'model-evaluations' | 'backtests';

export interface LatestRunState {
  runId: string | null;
  pending: boolean;
  failed: boolean;
}

export function useLatestRun(kind: LatestRunKind): LatestRunState {
  const [state, setState] = useState<LatestRunState>({
    runId: null,
    pending: true,
    failed: false,
  });

  useEffect(() => {
    let live = true;
    setState({ runId: null, pending: true, failed: false });
    api
      .dashboardLatestRun(kind)
      .then(({ data }) => {
        if (live) setState({ runId: data.runId, pending: false, failed: false });
      })
      .catch(() => {
        // 404 는 "아직 없음"이고 그 밖은 조회 실패다. 둘 다 화면에서는 빈 상태로 같다.
        if (live) setState({ runId: null, pending: false, failed: true });
      });
    return () => {
      live = false;
    };
  }, [kind]);

  return state;
}
