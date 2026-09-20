'use client';

/**
 * 다른 화면에서 학습일지로 넘기는 인계.
 *
 * `journals` 표와 API 는 처음부터 판단·주문·답변과의 연결을 들고 있었는데, 화면에
 * **연결을 만들 방법이 하나도 없었다.** 그래서 다섯 개 연결 컬럼이 전부 비어 있었다.
 *
 * 인계는 `sessionStorage` 를 쓴다. URL 질의 문자열에 답변 식별자를 실으면 그 값이
 * 브라우저 기록과 공유 링크에 남는다. 이 값은 그 사람 계정의 기록을 가리키므로 주소창에
 * 둘 이유가 없다. 탭을 닫으면 사라지는 것도 맞는 성질이다 - 인계는 한 번 쓰고 버린다.
 */

const KEY = 'p1.journalHandoff';

export interface JournalHandoff {
  title: string;
  content: string;
  /** 지금은 금융 Agent 답변만 넘긴다. 다른 화면이 붙으면 여기에 더한다. */
  ragAnswerId?: string;
}

export function putJournalHandoff(handoff: JournalHandoff): void {
  if (typeof window === 'undefined') return;
  try {
    window.sessionStorage.setItem(KEY, JSON.stringify(handoff));
  } catch {
    // 저장하지 못하면 인계 없이 빈 화면이 열린다. 그 편이 화면이 깨지는 것보다 낫다.
  }
}

/** 한 번만 읽고 지운다. 뒤로 가기로 돌아왔을 때 같은 초안이 다시 뜨면 안 된다. */
export function takeJournalHandoff(): JournalHandoff | null {
  if (typeof window === 'undefined') return null;
  try {
    const raw = window.sessionStorage.getItem(KEY);
    if (!raw) return null;
    window.sessionStorage.removeItem(KEY);
    const parsed: unknown = JSON.parse(raw);
    if (typeof parsed !== 'object' || parsed === null) return null;
    const value = parsed as Record<string, unknown>;
    if (typeof value.title !== 'string' || typeof value.content !== 'string') return null;
    return {
      title: value.title,
      content: value.content,
      ragAnswerId: typeof value.ragAnswerId === 'string' ? value.ragAnswerId : undefined,
    };
  } catch {
    return null;
  }
}
