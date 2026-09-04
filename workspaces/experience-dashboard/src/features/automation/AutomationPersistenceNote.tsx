import type { AutomationStatusV2 } from '@/shared/api/wire';

export function AutomationPersistenceNote({ status }: { status: AutomationStatusV2 }) {
  const halted = status.projectionState === 'HALTED';
  const disarmed = status.controlState === 'DISARMED';

  const tone = halted ? 'text-block' : disarmed ? 'text-muted' : 'text-ink';
  const message = halted
    ? '안전 중단은 자동으로 재시작하지 않습니다. 중단 사유를 확인한 뒤 다시 시작해 주세요. 재기동해도 중단 상태는 그대로 유지됩니다.'
    : disarmed
      ? '꺼진 상태는 재기동 뒤에도 그대로 유지됩니다. 다시 시작하려면 위의 자동운용 시작을 누르세요.'
      : '켜 둔 상태는 재기동 뒤에도 유지되고, 거래일마다 개장 전 08:55에 스스로 시작합니다. 매일 손댈 필요가 없습니다.';

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
