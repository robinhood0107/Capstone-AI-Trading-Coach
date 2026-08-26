import type { DashboardEnvelope } from '@/shared/api/wire';

/**
 * 상태 계약: 모든 ViewModel은 loading / empty / error / stale 네 상태를 구분해 표현한다.
 * "데이터 없음"과 "불러오기 실패"를 화면에서 절대 같은 모습으로 보여주지 않는다.
 */
export type ViewState<T> =
  | { kind: 'loading' }
  | { kind: 'empty'; title: string; detail: string }
  | { kind: 'error'; code: string; message: string; retryable: boolean; requestId: string | null }
  | { kind: 'stale'; data: T; asOf: string | null; detail: string }
  | { kind: 'ready'; data: T; asOf: string | null };

export function ready<T>(data: T, asOf: string | null = null): ViewState<T> {
  return { kind: 'ready', data, asOf };
}

export function empty<T>(title: string, detail: string): ViewState<T> {
  return { kind: 'empty', title, detail };
}

export function stale<T>(data: T, asOf: string | null, detail: string): ViewState<T> {
  return { kind: 'stale', data, asOf, detail };
}

export function hasData<T>(
  state: ViewState<T>,
): state is
  | { kind: 'ready'; data: T; asOf: string | null }
  | { kind: 'stale'; data: T; asOf: string | null; detail: string } {
  return state.kind === 'ready' || state.kind === 'stale';
}

/**
 * Dashboard endpoint는 viewState(READY/EMPTY/STALE)를 서버가 이미 판정해서 내려준다.
 * 프론트는 그 판정을 다시 계산하지 않고 그대로 옮긴다.
 */
export function fromDashboard<TView, TOut>(
  envelope: DashboardEnvelope<TView>,
  map: (view: TView) => TOut,
  emptyCopy: { title: string; detail: string },
): ViewState<TOut> {
  if (envelope.viewState === 'EMPTY' || envelope.view === null) {
    return empty(emptyCopy.title, emptyCopy.detail);
  }
  const mapped = map(envelope.view);
  if (envelope.viewState === 'STALE') {
    return stale(
      mapped,
      envelope.asOf,
      '서버가 이 자료를 지연 상태로 표시했습니다. 최신 값이 아닐 수 있습니다.',
    );
  }
  return ready(mapped, envelope.asOf);
}

/**
 * Dashboard가 아닌 일반 endpoint용. asOf가 허용 지연을 넘으면 stale로 강등한다.
 * 값은 유지하되 화면에 "지연됨"을 명시한다.
 */
export function withFreshness<T>(
  data: T,
  asOf: string | null,
  maxAgeMinutes: number,
  detail: string,
): ViewState<T> {
  if (!asOf) return ready(data, null);
  const observed = Date.parse(asOf);
  if (Number.isNaN(observed)) return ready(data, asOf);
  const ageMinutes = (Date.now() - observed) / 60_000;
  return ageMinutes > maxAgeMinutes ? stale(data, asOf, detail) : ready(data, asOf);
}
