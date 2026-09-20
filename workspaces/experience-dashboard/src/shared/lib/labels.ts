/**
 * 저장소 코드를 사람 말로 옮기는 공용 층.
 *
 * 화면마다 각자 라벨 맵을 들고 있으면 같은 코드가 화면마다 다르게 읽힌다. 실제로
 * `PARTIALLY_FILLED` 는 주문 화면에서 영문 그대로, 자동운용 화면에서는 "부분 체결"로
 * 나오고 있었다. 한곳에 모은다.
 *
 * 규칙이 둘 있다.
 *   1) **모르는 코드는 그대로 돌려준다.** 서버가 새 상태를 추가했을 때 화면이 빈칸이
 *      되는 것보다 낯선 코드가 보이는 편이 낫다 - 빈칸은 "값이 없다"는 거짓말이다.
 *   2) 상태 이름만 옮기지 않고 **지금 무슨 뜻인지**(`meaning`)를 함께 준다. "SUBMITTED"를
 *      "제출됨"으로 바꿔 봐야 처음 보는 사람에게는 여전히 아무 정보가 아니다.
 */

export interface CodeLabel {
  label: string;
  meaning?: string;
}

const ORDER_STATUS: Record<string, CodeLabel> = {
  DRAFT: { label: '작성 중', meaning: '아직 증권사로 보내지 않았습니다.' },
  SUBMITTED: { label: '접수됨', meaning: '증권사에 주문을 냈고 체결을 기다리는 중입니다.' },
  ACCEPTED: { label: '접수 확인', meaning: '증권사가 주문을 받아들였고 아직 체결 전입니다.' },
  PARTIALLY_FILLED: {
    label: '일부 체결',
    meaning: '주문한 수량 중 일부가 체결됐습니다. 체결된 만큼은 이미 장부에 들어갔습니다.',
  },
  FILLED: { label: '체결 완료', meaning: '주문한 수량이 모두 체결됐습니다.' },
  CANCELLED: { label: '취소됨', meaning: '체결되지 않은 수량은 취소됐습니다.' },
  REJECTED: { label: '거부됨', meaning: '증권사가 이 주문을 받지 않았습니다.' },
  EXPIRED: { label: '기한 만료', meaning: '장이 끝날 때까지 체결되지 않아 사라졌습니다.' },
};

const SEVERITY: Record<string, CodeLabel> = {
  BLOCK: { label: '차단', meaning: '이 항목 때문에 주문을 내지 않습니다.' },
  WARN: { label: '주의', meaning: '주문은 나가지만 확인이 필요합니다.' },
  INFO: { label: '참고' },
  ALLOW: { label: '통과' },
};

/**
 * 위험 점검 항목 코드.
 *
 * 여기 없는 코드는 그대로 보여 준다. 규칙 코드는 서버에서 계속 늘어나므로 사전이 완전할
 * 수 없고, 완전한 척하면 새 코드가 조용히 사라진다.
 */
const RISK_CODE: Record<string, CodeLabel> = {
  DAILY_LOSS_LIMIT: { label: '일일 손실 한도', meaning: '오늘 정해 둔 손실 한도에 닿았습니다.' },
  MAX_OPEN_POSITIONS: { label: '보유 종목 수 상한', meaning: '동시에 들고 있을 수 있는 종목 수를 넘습니다.' },
  CAPITAL_LIMIT: { label: '투입 자본 한도', meaning: '이 주문까지 더하면 정해 둔 투자 금액을 넘습니다.' },
  MANAGEMENT_ISSUE: { label: '관리종목', meaning: '거래소가 관리종목으로 지정한 종목입니다.' },
  NO_REMAINING_RETURN: {
    label: '남은 기대수익 없음',
    meaning: '지금 값에 사면 수수료를 빼고 남는 기대 수익이 없다고 판단했습니다.',
  },
  QUOTE_STALE: { label: '시세가 오래됨', meaning: '최신 시세를 확인하지 못해 주문을 멈췄습니다.' },
  BUY_WINDOW_CLOSED: { label: '매수 시간 종료', meaning: '오늘 새로 살 수 있는 시간이 지났습니다.' },
  ORDER_BUDGET_EXHAUSTED: { label: '오늘 주문 횟수 소진', meaning: '하루에 낼 수 있는 주문 수를 다 썼습니다.' },
  KILL_SWITCH_ACTIVE: { label: '긴급 정지 켜짐', meaning: '사람이 긴급 정지를 켜 두어 주문이 나가지 않습니다.' },
};

function lookup(table: Record<string, CodeLabel>, code: string | null | undefined): CodeLabel {
  if (!code) return { label: '미상' };
  return table[code] ?? { label: code };
}

export const orderStatusLabel = (code: string | null | undefined): CodeLabel =>
  lookup(ORDER_STATUS, code);
export const severityLabel = (code: string | null | undefined): CodeLabel => lookup(SEVERITY, code);
export const riskCodeLabel = (code: string | null | undefined): CodeLabel => lookup(RISK_CODE, code);

/** 켜짐/꺼짐을 한국어로. 영문 ACTIVE/OFF 가 화면에 그대로 나오고 있었다. */
export const onOffLabel = (active: boolean): CodeLabel =>
  active
    ? { label: '켜짐', meaning: '지금 모든 자동 주문이 멈춰 있습니다.' }
    : { label: '꺼짐', meaning: '긴급 정지가 걸려 있지 않습니다.' };
