/**
 * 모델 비교 ViewModel.
 *
 *  1) GET /api/v1/dashboard/model-evaluations/{runId} — 모델별 성과 지표 (권위)
 *  2) GET /api/v2/signals/{symbol}                    — 현재 신호 상태 (선택, 종목을 입력했을 때만)
 *
 * 두 계약은 축이 다르다. runId는 학습/평가 실행 단위이고 symbol은 현재 시점 신호다.
 * 억지로 한 표에 합치지 않고 나란히 보여준다.
 */
import { api } from '@/shared/api/endpoints';
import type {
  DashboardMetrics,
  DashboardModelEvaluationView,
  DashboardModelId,
  SignalV3Runtime,
} from '@/shared/api/wire';
import { fromDashboard, type ViewState } from '@/shared/lib/viewState';

export interface ModelRow {
  modelId: DashboardModelId;
  displayName: string;
  status: 'AVAILABLE' | 'ABSTAIN';
  metrics: DashboardMetrics;
}

export interface ModelEvaluationView {
  runId: string;
  rows: ModelRow[];
  timeline: { at: string; value: number }[];
  sourceRunIds: string[];
  /** ABSTAIN이 아닌 모델이 하나도 없으면 성과 비교 자체가 성립하지 않는다. */
  comparableCount: number;
}

const DISPLAY: Partial<Record<DashboardModelId, string>> = {
  BASELINE: '규칙 baseline',
  LSTM: 'LSTM',
};

export function toModelEvaluationView(view: DashboardModelEvaluationView): ModelEvaluationView {
  const rows: ModelRow[] = view.models
    .filter((model) => model.modelId in DISPLAY)
    .map((model) => ({
      modelId: model.modelId,
      displayName: DISPLAY[model.modelId] ?? model.modelId,
      status: model.status,
      metrics: model.metrics,
    }));

  return {
    runId: view.runId,
    rows,
    timeline: view.timeline,
    sourceRunIds: view.sourceRunIds,
    comparableCount: rows.filter((row) => row.status === 'AVAILABLE').length,
  };
}

export async function loadModelEvaluationView(
  runId: string,
): Promise<ViewState<ModelEvaluationView>> {
  const { data } = await api.dashboardModelEvaluation(runId);
  return fromDashboard<DashboardModelEvaluationView, ModelEvaluationView>(
    data,
    toModelEvaluationView,
    {
      title: '이 실행 ID에 저장된 평가 결과가 없습니다',
      detail:
        '모델 평가 artifact가 아직 등록되지 않았습니다. 실행 ID를 확인하거나 데모 데이터를 먼저 준비하세요.',
    },
  );
}

/* ------------------------------------------------------------ 현재 신호 */

export type SignalSlot =
  | {
      key: string;
      displayName: string;
      status: 'AVAILABLE';
      signal: 'BUY' | 'HOLD' | 'SELL' | null;
      regimeState: string | null;
      predictedReturn: number | null;
      asOf: string;
      sourceWorkspace: string;
    }
  | {
      key: string;
      displayName: string;
      status: 'ABSTAIN';
      reason: string;
      sourceWorkspace: string;
    };

export interface SignalView {
  symbol: string;
  timeframe: string;
  asOf: string | null;
  composite:
    | { status: 'AVAILABLE'; signal: 'BUY' | 'HOLD' | 'SELL' }
    | { status: 'ABSTAIN'; reason: string };
  slots: SignalSlot[];
  disagrees: boolean;
  distinctSignals: string[];
  warnings: string[];
}

const ABSTAIN_REASON_KR: Record<string, string> = {
  MISSING_EVIDENCE: '검증된 근거가 아직 없습니다',
  ARTIFACT_DRIFT: '학습 시점과 입력 분포가 달라졌습니다',
  CALIBRATION_FAILED: '보정 검증을 통과하지 못했습니다',
  POSTERIOR_BELOW_THRESHOLD: '판정 기준에 못 미칩니다',
  PRODUCER_FAILED: '생성 과정이 실패했습니다',
  STALE_EVIDENCE: '근거가 허용 지연을 넘었습니다',
  UNIDENTIFIABLE_OUTPUT: '출력을 식별할 수 없습니다',
  REQUIRED_COMPONENT_UNAVAILABLE: '필수 구성요소가 없어 종합하지 않았습니다',
};

function readWarning(warning: string): string {
  return warning.includes('current P1 production authority')
    ? '현재 운용 판단에는 규칙 baseline과 LSTM만 사용합니다.'
    : warning;
}

export function readAbstainReason(reason: string): string {
  return ABSTAIN_REASON_KR[reason] ?? reason;
}

const SLOT_NAMES: [keyof SignalV3Runtime['components'], string][] = [
  ['ruleBaseline', '규칙 baseline'],
  ['lstm', 'LSTM'],
  ['hmmRegime', 'HMM 시장국면'],
];

export function toSignalView(signal: SignalV3Runtime): SignalView {
  const slots: SignalSlot[] = SLOT_NAMES.map(([key, displayName]) => {
    const component = signal.components[key];
    if (component.status === 'ABSTAIN') {
      return {
        key,
        displayName,
        status: 'ABSTAIN',
        reason: readAbstainReason(component.reason),
        sourceWorkspace: component.sourceWorkspace,
      };
    }
    const isRegime = 'state' in component;
    return {
      key,
      displayName,
      status: 'AVAILABLE',
      signal: isRegime ? null : component.signal,
      regimeState: isRegime ? component.state : null,
      predictedReturn: isRegime ? null : (component.predictedReturn ?? null),
      asOf: component.asOf,
      sourceWorkspace: component.sourceWorkspace,
    };
  });

  // 불일치는 AVAILABLE인 예측 모델끼리만 센다. ABSTAIN은 "반대 의견"이 아니다.
  const predictive = slots.filter(
    (slot): slot is Extract<SignalSlot, { status: 'AVAILABLE' }> =>
      slot.status === 'AVAILABLE' && slot.signal !== null,
  );
  const distinct = Array.from(new Set(predictive.map((slot) => slot.signal as string)));

  return {
    symbol: signal.symbol,
    timeframe: signal.timeframe,
    asOf: signal.asOf ?? null,
    composite:
      signal.composite.status === 'AVAILABLE'
        ? {
            status: 'AVAILABLE',
            signal: signal.composite.signal,
          }
        : { status: 'ABSTAIN', reason: readAbstainReason(signal.composite.reason) },
    slots,
    disagrees: distinct.length > 1,
    distinctSignals: distinct,
    warnings: signal.warnings.map(readWarning),
  };
}

export async function loadSignalView(symbol: string): Promise<ViewState<SignalView>> {
  const { data } = await api.signal(symbol);
  return { kind: 'ready', data: toSignalView(data), asOf: data.asOf ?? null };
}
