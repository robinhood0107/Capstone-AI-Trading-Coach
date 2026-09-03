import type { AutomationStatusV2 } from '@/shared/api/wire';

/**
 * 현재 상태가 재기동 뒤에 어떻게 되는지, 그리고 무엇이 사람 손을 요구하는지 알려준다.
 *
 * 왜 필요한가. 화면은 상태 이름만 보여준다 - '시작 대기' / '실행 중' / '꺼짐' / '안전 중단'.
 * 그런데 운영자가 실제로 궁금한 것은 "이 상태가 내일도 유지되는가"와 "내가 뭘 해야 하는가"다.
 *
 * 세 가지가 화면에 없었다.
 *   1. 켜 둔 상태가 재기동 뒤에도 유지된다는 사실. control_state 가 DB 행이고 up 이 저장된
 *      의도와 실제를 맞추므로 실제로 유지되지만, 그것을 화면이 말하지 않으면 매번 확인해야 한다.
 *   2. 매 세션 08:55 에 스스로 시작한다는 사실. roll_schedule 이 정상 종료 시 다음 세션을
 *      예약한다.
 *   3. 안전 중단은 자동으로 재시작하지 않는다는 사실. 이건 fail-closed 이고 사람이 원인을
 *      봐야 하는 신호인데, 화면에 안내가 없으면 "왜 안 도나"로 읽힌다.
 */
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
    <p className={`mt-3 border-t border-line pt-3 text-[12px] leading-5 ${tone}`}>{message}</p>
  );
}
