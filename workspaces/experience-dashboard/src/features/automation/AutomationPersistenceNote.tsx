import type { AutomationStatusV2 } from '@/shared/api/wire';

export function AutomationPersistenceNote({ status }: { status: AutomationStatusV2 }) {
  const halted = status.projectionState === 'HALTED';
  const disarmed = status.controlState === 'DISARMED';

  const tone = halted ? 'text-block' : disarmed ? 'text-muted' : 'text-ink';
  const message = halted
    ? '안전 중단은 자동으로 재시작하지 않습니다. 중단 사유를 확인한 뒤 다시 시작해 주세요. 재기동해도 중단 상태는 그대로 유지됩니다.'
    : disarmed
      ? '꺼진 상태는 재기동 뒤에도 그대로 유지됩니다. 다시 시작하려면 위의 자동운용 시작을 누르세요.'
      : // 기동은 두 단계다. 08:55 에 그날 일정을 잡고(scripts/p1_mock_next_session.py 의 08:55
        // 일회성 타이머), 실제 매매는 09:30 개장 경계에서 시작한다(automation_runtime.py 의
        // _OPEN_BOUNDARY). 앞단만 적으면 08:55 부터 주문이 나가는 것처럼 읽힌다.
        '켜 둔 상태는 재기동 뒤에도 유지됩니다. 거래일마다 개장 전 08:55에 그날 일정을 잡고, 실제 매매는 09:30 개장에 시작합니다. 매일 손댈 필요가 없습니다.';

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
