import type { AutomationControlState, AutomationProjectionState } from '@/shared/api/wire';

/**
 * 재기동을 넘겨 상태가 유지되는지 알려 준다.
 *
 * 필요한 것은 두 상태값뿐이라 계약 버전에 묶지 않는다 — v2 든 v3 든 그대로 받는다.
 */
export function AutomationPersistenceNote({
  status,
}: {
  status: { controlState: AutomationControlState; projectionState: AutomationProjectionState };
}) {
  const halted = status.projectionState === 'HALTED';
  const disarmed = status.controlState === 'DISARMED';

  const tone = halted ? 'text-block' : disarmed ? 'text-muted' : 'text-ink';
  const message = halted
    ? '안전 중단은 자동으로 재시작하지 않습니다. 중단 사유를 확인한 뒤 다시 시작해 주세요. 재기동해도 중단 상태는 그대로 유지됩니다.'
    : disarmed
      ? '꺼진 상태는 재기동 뒤에도 그대로 유지됩니다. 다시 시작하려면 위의 자동운용 시작을 누르세요.'
      : '켜 둔 상태는 재기동 뒤에도 유지됩니다. 컨테이너가 실행 중이면 거래일 09:30에 자동으로 평가하고, 정상 종료 뒤 다음 거래일 일정을 이어갑니다. 매일 다시 시작할 필요가 없습니다.';

  return (
    <p
      role="note"
      aria-label="자동운용 지속성"
      className={`mt-3 border-t border-line pt-3 text-[12px] leading-5 ${tone}`}
    >
      {message}
    </p>
  );
}
