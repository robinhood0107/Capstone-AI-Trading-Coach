'use client';

import { useEffect, useState } from 'react';
import { api } from '@/shared/api/endpoints';

/** Resolves the latest verified owner-scoped dashboard run. */
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
        if (live) setState({ runId: null, pending: false, failed: true });
      });
    return () => {
      live = false;
    };
  }, [kind]);

  return state;
}
