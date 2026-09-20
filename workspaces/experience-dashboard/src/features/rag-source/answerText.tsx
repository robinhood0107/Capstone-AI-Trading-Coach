/*
 * 답변 본문은 강한 LLM 이 만든 글이라 `**강조**` 같은 마크다운이 섞여 온다. 지금까지는
 * 그대로 뿌려서 화면에 별표가 노출됐다.
 *
 * 마크다운 라이브러리를 들이지 않는다. 실제로 섞여 오는 문법은 굵게 하나뿐이고,
 * 라이브러리는 HTML 을 그리는 경로를 새로 여는 것이라 답변이 외부 모델에서 온다는 점에서
 * 위험이 이득보다 크다. 여기서는 문자열을 쪼개 React 요소로만 만든다 - HTML 을 해석하지
 * 않으므로 주입될 수 있는 것이 없다.
 */
import type { ReactNode } from 'react';

const BOLD = /\*\*([^*]+)\*\*/g;

export function renderAnswerText(text: string): ReactNode[] {
  const parts: ReactNode[] = [];
  let cursor = 0;
  let key = 0;
  for (const match of text.matchAll(BOLD)) {
    const start = match.index ?? 0;
    if (start > cursor) parts.push(text.slice(cursor, start));
    parts.push(
      <strong key={`b${key}`} className="font-semibold text-ink">
        {match[1]}
      </strong>,
    );
    key += 1;
    cursor = start + match[0].length;
  }
  if (cursor < text.length) parts.push(text.slice(cursor));
  return parts.length > 0 ? parts : [text];
}
