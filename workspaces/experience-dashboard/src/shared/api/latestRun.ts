'use client';

import { useCallback, useEffect, useState } from 'react';
import { api } from '@/shared/api/endpoints';
import { toErrorState } from '@/shared/lib/useResource';

/** Resolves the latest verified owner-scoped dashboard run. */
export type LatestRunKind = 'model-evaluations' | 'backtests';

export interface LatestRunState {
  runId: string | null;
  pending: boolean;
  failed: boolean;
  /** 실패 사유. `failed` 만으로는 화면이 사용자에게 무엇이 잘못됐는지 말할 수 없다. */
  errorMessage: string | null;
  reload: () => void;
}

/**
 * 최신 run 을 해석한다.
 *
 * `failed` 를 반드시 읽어야 한다. 이 값을 버리면 조회 실패가 `runId === null` 하나로 뭉개져
 * 화면이 "아직 등록된 결과가 없습니다"라고 거짓말한다. viewState 계약이 "'데이터 없음'과
 * '불러오기 실패'를 화면에서 절대 같은 모습으로 보여주지 않는다"고 못 박은 그 위반이다.
 */
export function useLatestRun(kind: LatestRunKind): LatestRunState {
  const [nonce, setNonce] = useState(0);
  const [state, setState] = useState<Omit<LatestRunState, 'reload'>>({
    runId: null,
    pending: true,
    failed: false,
    errorMessage: null,
  });

  const reload = useCallback(() => setNonce((value) => value + 1), []);

  useEffect(() => {
    let live = true;
    setState({ runId: null, pending: true, failed: false, errorMessage: null });
    api
      .dashboardLatestRun(kind)
      .then(({ data }) => {
        if (live) setState({ runId: data.runId, pending: false, failed: false, errorMessage: null });
      })
      .catch((cause: unknown) => {
        if (!live) return;
        const error = toErrorState<never>(cause);
        setState({
          runId: null,
          pending: false,
          failed: true,
          errorMessage: error.kind === 'error' ? error.message : '최신 결과를 확인하지 못했습니다.',
        });
      });
    return () => {
      live = false;
    };
  }, [kind, nonce]);

  return { ...state, reload };
}
